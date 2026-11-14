"""Reglas de negocio: pedidos automáticos, ventas, movimientos de stock y cierre de semana.

Ciclo de una semana W (los pedidos se cierran el viernes de la semana anterior):
  1. Pedidos automáticos (según ventas y frecuencia de cada tienda) + adicionales  -> estado 'pendiente'
  2. Cierre de la semana W   -> 'confirmado' y se vuelcan a la tabla que lee el optimizador (modare.pedidos)
  3. Optimizar e importar el plan (gestion.optimizador) -> rutas, camiones y reposiciones -> 'planificado'
  4. Planta expide la reposición -> almacén la recibe (cajas) -> almacén carga el pedido ('en_reparto')
     -> la tienda confirma la recepción ('entregado') y su stock sube.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

from sqlalchemy import func

from modare import config as C
from modare import pedidos as PED

from .modelos import (Ajuste, Almacen, CamionReparto, CamionTroncal, Categoria, LineaPedido, MovimientoStock,
                      Pedido, Planta, Reposicion, StockPlanta, StockTienda, Tienda, Venta, Viaje, ahora, db,
                      semana_iso)

PERIODO_DIAS = {"diaria": 1, "semanal": 7, "mensual": 30}
VENTANA_MEDIA_DIAS = 28              # las ventas medias se calculan sobre las últimas 4 semanas
MIN_KG_PEDIDO_AUTO = C.KG_POR_CAJA / 2   # por debajo de media caja no se genera pedido automático
MAX_DIAS_RECUPERAR = 31
ACTIVOS = ("pendiente", "confirmado", "planificado", "en_reparto")


class ErrorNegocio(Exception):
    """Error que se muestra al usuario tal cual."""


# ----------------------------------------------------------------------------- calendario
def semana_entrega_para(fecha: dt.date) -> str:
    """Los pedidos se cierran el viernes: lo pedido de lunes a viernes llega la semana siguiente,
    lo pedido en fin de semana, la otra."""
    lunes_sig = fecha + dt.timedelta(days=7 - fecha.weekday())
    if fecha.weekday() >= 5:
        lunes_sig += dt.timedelta(days=7)
    return semana_iso(lunes_sig)


def semana_cerrada(semana: str) -> bool:
    return Ajuste.get(f"cerrada:{semana}") == "1"


def lunes_de(semana: str) -> dt.date:
    return dt.date.fromisoformat(f"{semana}-1")


def toca_pedido(t: Tienda, fecha: dt.date) -> bool:
    if t.frecuencia_pedido == "diaria":
        return fecha.weekday() < 5
    if t.frecuencia_pedido == "semanal":
        return fecha.weekday() == (t.dia_pedido or 0) % 7
    if t.frecuencia_pedido == "mensual":
        return fecha.day == min(max(t.dia_pedido or 1, 1), 28)
    return False


# ----------------------------------------------------------------------------- consultas
def venta_media_diaria(tienda_id: str, hasta: dt.date, dias: int = VENTANA_MEDIA_DIAS) -> dict[str, float]:
    """Prendas vendidas por día natural (incluye domingos) y categoría en los `dias` anteriores a `hasta`."""
    desde = hasta - dt.timedelta(days=dias)
    filas = (db.session.query(Venta.categoria_id, func.sum(Venta.unidades))
             .filter(Venta.tienda_id == tienda_id, Venta.fecha >= desde, Venta.fecha < hasta)
             .group_by(Venta.categoria_id).all())
    return {c: u / dias for c, u in filas}


def en_camino(tienda_id: str) -> dict[str, int]:
    """Prendas ya pedidas que aún no han llegado a la tienda."""
    filas = (db.session.query(LineaPedido.categoria_id, func.sum(LineaPedido.unidades))
             .join(Pedido).filter(Pedido.tienda_id == tienda_id, Pedido.estado.in_(ACTIVOS))
             .group_by(LineaPedido.categoria_id).all())
    return dict(filas)


def propuesta_automatica(t: Tienda, fecha: dt.date) -> dict[str, int]:
    """Prendas a pedir = venta media × (días hasta el próximo pedido + días de seguridad) − stock − en camino."""
    media = venta_media_diaria(t.id, fecha)
    stock = {s.categoria_id: s.unidades for s in t.stock}
    pend = en_camino(t.id)
    dias = PERIODO_DIAS.get(t.frecuencia_pedido, 7) + (t.dias_seguridad or 0)
    out = {}
    for cat, m in media.items():
        n = math.ceil(m * dias - stock.get(cat, 0) - pend.get(cat, 0))
        if n > 0:
            out[cat] = n
    return out


def kg_de(lineas: dict[str, int]) -> float:
    cats = {c.id: c for c in Categoria.query}
    return sum(u * cats[c].kg_unidad for c, u in lineas.items())


def cajas_de_kg(kg: float) -> int:
    return math.ceil(kg / C.KG_POR_CAJA) if kg > 0 else 0


# ----------------------------------------------------------------------------- pedidos
def _crear_pedido(tienda_id, tipo, lineas, semana, *, fecha=None, comentario="", urgente=False, usuario=None):
    p = Pedido(tienda_id=tienda_id, tipo=tipo, semana_entrega=semana, fecha_generacion=fecha,
               comentario=comentario or None, urgente=urgente, creado_por=usuario,
               lineas=[LineaPedido(categoria_id=c, unidades=int(u)) for c, u in lineas.items() if int(u) > 0])
    db.session.add(p)
    return p


def generar_automaticos(hasta: dt.date | None = None, log=lambda *a: None) -> int:
    """Genera los pedidos automáticos que tocaban desde la última ejecución hasta `hasta` (incluido).
    Idempotente: guarda el último día procesado en ajustes. Devuelve cuántos pedidos ha creado."""
    hasta = hasta or dt.date.today()
    ultimo = Ajuste.get("auto_hasta")
    desde = dt.date.fromisoformat(ultimo) + dt.timedelta(days=1) if ultimo else hasta
    desde = max(desde, hasta - dt.timedelta(days=MAX_DIAS_RECUPERAR))
    creados = 0
    tiendas = Tienda.query.filter_by(activa=True, pedido_auto_activo=True).all()
    f = desde
    while f <= hasta:
        semana = semana_entrega_para(f)
        if not semana_cerrada(semana):
            for t in tiendas:
                if not toca_pedido(t, f):
                    continue
                if Pedido.query.filter_by(tienda_id=t.id, tipo="automatico", fecha_generacion=f).first():
                    continue
                lineas = propuesta_automatica(t, f)
                if kg_de(lineas) < MIN_KG_PEDIDO_AUTO:
                    continue
                _crear_pedido(t.id, "automatico", lineas, semana, fecha=f,
                              comentario=f"Automático {t.frecuencia_pedido} por ventas")
                db.session.flush()       # para que en_camino() vea este pedido en el siguiente día
                creados += 1
        f += dt.timedelta(days=1)
    Ajuste.set("auto_hasta", hasta.isoformat())
    db.session.commit()
    if creados:
        log(f"{creados} pedidos automáticos generados hasta {hasta}")
    return creados


def crear_pedido_adicional(tienda: Tienda, lineas: dict[str, int], semana: str, comentario: str,
                           urgente: bool, usuario) -> Pedido:
    lineas = {c: u for c, u in lineas.items() if u > 0}
    if not lineas:
        raise ErrorNegocio("Indica al menos una prenda.")
    if semana_cerrada(semana):
        raise ErrorNegocio(f"La semana {semana} ya está cerrada: elige una semana posterior.")
    if semana < semana_entrega_para(dt.date.today()):
        raise ErrorNegocio(f"Los pedidos para {semana} ya se cerraron. La primera semana disponible es "
                           f"{semana_entrega_para(dt.date.today())}.")
    p = _crear_pedido(tienda.id, "adicional", lineas, semana, comentario=comentario, urgente=urgente, usuario=usuario)
    db.session.commit()
    return p


def cancelar_pedido(p: Pedido):
    if p.estado != "pendiente":
        raise ErrorNegocio("Solo se pueden cancelar pedidos pendientes (la semana aún no se ha cerrado).")
    p.estado = "cancelado"
    db.session.commit()


# ----------------------------------------------------------------------------- stock
def _mov(entidad, entidad_id, delta, motivo, *, categoria=None, ref=None, usuario=None):
    db.session.add(MovimientoStock(entidad=entidad, entidad_id=entidad_id, categoria_id=categoria, delta=delta,
                                   motivo=motivo, referencia=ref, usuario=usuario))


def _stock_tienda(t: Tienda, cat: str) -> StockTienda:
    s = next((s for s in t.stock if s.categoria_id == cat), None)
    if s is None:
        s = StockTienda(tienda_id=t.id, categoria_id=cat, unidades=0)
        t.stock.append(s)
    return s


def _stock_planta(p: Planta, cat: str) -> StockPlanta:
    s = next((s for s in p.stock if s.categoria_id == cat), None)
    if s is None:
        s = StockPlanta(planta_id=p.id, categoria_id=cat, unidades=0)
        p.stock.append(s)
    return s


def registrar_ventas(t: Tienda, lineas: dict[str, int], fecha: dt.date, usuario) -> list[str]:
    """Resta del stock lo vendido y guarda la venta. Devuelve avisos (si se vende más de lo que consta)."""
    avisos = []
    for cat, u in lineas.items():
        if u <= 0:
            continue
        s = _stock_tienda(t, cat)
        if u > s.unidades:
            avisos.append(f"{cat}: vendidas {u} pero constaban {s.unidades}; el stock queda a 0 (revisa el inventario).")
        delta = -min(u, s.unidades)
        s.unidades += delta
        db.session.add(Venta(tienda_id=t.id, categoria_id=cat, fecha=fecha, unidades=u))
        _mov("tienda", t.id, delta, "venta", categoria=cat, usuario=usuario)
    db.session.commit()
    return avisos


def ajustar_stock_prendas(entidad: Tienda | Planta, lineas: dict[str, int], motivo: str, usuario) -> list[str]:
    """Entradas (+) o salidas (−) manuales de prendas en una tienda o planta (inventario, mermas, donaciones…)."""
    es_tienda = isinstance(entidad, Tienda)
    avisos = []
    for cat, d in lineas.items():
        if not d:
            continue
        s = _stock_tienda(entidad, cat) if es_tienda else _stock_planta(entidad, cat)
        nuevo = max(0, s.unidades + d)
        _mov("tienda" if es_tienda else "planta", entidad.id, nuevo - s.unidades, motivo or "ajuste",
             categoria=cat, usuario=usuario)
        s.unidades = nuevo
    db.session.flush()
    if not es_tienda and entidad.stock_prendas > entidad.stock_max_prendas:
        avisos.append(f"La planta supera su stock máximo ({entidad.stock_prendas:,} / {entidad.stock_max_prendas:,} prendas).")
    if es_tienda and entidad.capacidad_max_prendas and entidad.stock_prendas > entidad.capacidad_max_prendas:
        avisos.append(f"La tienda supera su capacidad ({entidad.stock_prendas:,} / {entidad.capacidad_max_prendas:,} prendas).")
    db.session.commit()
    return avisos


def ajustar_cajas_almacen(a: Almacen, delta: int, motivo: str, usuario) -> list[str]:
    nuevo = max(0, a.stock_cajas + delta)
    _mov("almacen", a.id, nuevo - a.stock_cajas, motivo or "ajuste", usuario=usuario)
    a.stock_cajas = nuevo
    db.session.commit()
    return [f"El almacén supera su stock máximo ({a.stock_cajas} / {a.stock_max_cajas} cajas)."] \
        if a.stock_cajas > a.stock_max_cajas else []


# ----------------------------------------------------------------------------- cierre de semana
def cerrar_semana(semana: str) -> dict:
    """Confirma los pedidos pendientes de la semana y los vuelca a la tabla que lee el optimizador."""
    generar_automaticos()
    pedidos = Pedido.query.filter(Pedido.semana_entrega == semana, Pedido.estado.in_(("pendiente", "confirmado"))).all()
    for p in pedidos:
        p.estado = "confirmado"
    por_tienda = defaultdict(lambda: {"kg": 0.0, "n": 0, "coment": []})
    for p in pedidos:
        x = por_tienda[p.tienda_id]
        x["kg"] += p.kg
        x["n"] += 1
        if p.comentario and p.tipo == "adicional":
            x["coment"].append(p.comentario)
    for tid in PED.de_semana(semana):
        PED.borrar(semana, tid)
    for tid, x in por_tienda.items():
        if x["kg"] > 0:
            PED.guardar(semana, tid, cajas_de_kg(x["kg"]), round(x["kg"], 1),
                        f"{x['n']} pedido(s) app gestión. " + " | ".join(x["coment"]))
    Ajuste.set(f"cerrada:{semana}", "1")
    db.session.commit()
    return {"pedidos": len(pedidos), "tiendas": len(por_tienda), "kg": round(sum(x["kg"] for x in por_tienda.values()), 1)}


def reabrir_semana(semana: str):
    if Viaje.query.filter(Viaje.semana == semana, Viaje.estado != "planificado").first():
        raise ErrorNegocio("Ya hay viajes en curso o completados en esa semana: no se puede reabrir.")
    if Reposicion.query.filter(Reposicion.semana == semana, Reposicion.estado != "planificada").first():
        raise ErrorNegocio("Ya hay reposiciones expedidas en esa semana: no se puede reabrir.")
    Pedido.query.filter(Pedido.semana_entrega == semana, Pedido.estado.in_(("confirmado", "planificado"))) \
        .update({"estado": "pendiente", "viaje_id": None}, synchronize_session=False)
    Reposicion.query.filter_by(semana=semana).delete()
    Viaje.query.filter_by(semana=semana).delete()
    Ajuste.set(f"cerrada:{semana}", "0")
    db.session.commit()


# ----------------------------------------------------------------------------- operaciones físicas
def _iniciar_viaje(v: Viaje | None):
    if v and v.estado == "planificado":
        v.estado = "en_curso"
        if v.camion:
            v.camion.estado = "en_ruta"


def _quizas_cerrar_viaje(v: Viaje | None):
    """El viaje acaba cuando todo lo que lleva está entregado/recibido: el camión vuelve a estar disponible."""
    if not v or v.estado == "completado":
        return
    if v.escalon == "troncal":
        pend = Reposicion.query.filter(Reposicion.viaje_id == v.id, Reposicion.estado != "recibida").count()
    else:
        pend = Pedido.query.filter(Pedido.viaje_id == v.id, Pedido.estado.notin_(("entregado", "cancelado"))).count()
    if pend == 0:
        v.estado = "completado"
        if v.camion:
            v.camion.estado = "disponible"
            v.camion.km_acumulados = (v.camion.km_acumulados or 0) + (v.km or 0)


def expedir_reposicion(r: Reposicion, usuario):
    """La planta carga el camión troncal: su stock de prendas baja."""
    if r.estado != "planificada":
        raise ErrorNegocio("Esta reposición ya se ha expedido.")
    p = r.planta
    faltan = []
    for l in r.lineas:
        s = _stock_planta(p, l.categoria_id)
        if s.unidades < l.unidades:
            faltan.append(f"{l.categoria.nombre}: faltan {l.unidades - s.unidades}")
    if faltan:
        raise ErrorNegocio("Stock insuficiente en planta. " + "; ".join(faltan))
    for l in r.lineas:
        _stock_planta(p, l.categoria_id).unidades -= l.unidades
        _mov("planta", p.id, -l.unidades, "expedicion", categoria=l.categoria_id, ref=r.codigo, usuario=usuario)
    r.estado, r.expedida = "expedida", ahora()
    _iniciar_viaje(r.viaje)
    db.session.commit()


def recibir_reposicion(r: Reposicion, usuario) -> list[str]:
    """El almacén descarga el camión troncal: suma cajas (avisa si supera su máximo)."""
    if r.estado != "expedida":
        raise ErrorNegocio("Solo se puede recibir una reposición expedida por la planta.")
    a = r.almacen
    a.stock_cajas += r.cajas
    _mov("almacen", a.id, r.cajas, "recepcion", ref=r.codigo, usuario=usuario)
    r.estado, r.recibida = "recibida", ahora()
    _quizas_cerrar_viaje(r.viaje)
    db.session.commit()
    return [f"¡Atención! El almacén queda por encima de su máximo: {a.stock_cajas} / {a.stock_max_cajas} cajas."] \
        if a.stock_cajas > a.stock_max_cajas else []


def expedir_pedido(p: Pedido, usuario):
    """El almacén carga el pedido en el camión de reparto: resta sus cajas."""
    if p.estado not in ("planificado", "confirmado"):
        raise ErrorNegocio("El pedido no está listo para salir (debe estar confirmado o planificado).")
    a = p.almacen or p.tienda.almacen
    if a is None:
        raise ErrorNegocio("El pedido no tiene almacén asignado.")
    if a.stock_cajas < p.cajas:
        raise ErrorNegocio(f"El almacén solo tiene {a.stock_cajas} cajas y el pedido necesita {p.cajas}.")
    a.stock_cajas -= p.cajas
    p.almacen = a
    _mov("almacen", a.id, -p.cajas, "expedicion", ref=p.codigo, usuario=usuario)
    p.estado = "en_reparto"
    _iniciar_viaje(p.viaje)
    db.session.commit()


def recibir_pedido(p: Pedido, usuario) -> list[str]:
    """La tienda confirma la recepción: su stock sube con el contenido del pedido."""
    if p.estado != "en_reparto":
        raise ErrorNegocio("Solo se puede recibir un pedido que esté en reparto.")
    t = p.tienda
    for l in p.lineas:
        _stock_tienda(t, l.categoria_id).unidades += l.unidades
        _mov("tienda", t.id, l.unidades, "recepcion", categoria=l.categoria_id, ref=p.codigo, usuario=usuario)
    p.estado, p.entregado = "entregado", ahora()
    _quizas_cerrar_viaje(p.viaje)
    db.session.commit()
    if t.capacidad_max_prendas and t.stock_prendas > t.capacidad_max_prendas:
        return [f"La tienda supera su capacidad ({t.stock_prendas:,} / {t.capacidad_max_prendas:,} prendas)."]
    return []


# ----------------------------------------------------------------------------- alertas
def alertas(rol: str = "admin", entidad_id: str | None = None) -> list[tuple[str, str]]:
    """(nivel, texto) para el panel. nivel: 'err' o 'warn'."""
    out = []
    hoy = dt.date.today()
    if rol in ("admin", "almacen"):
        q = Almacen.query.filter_by(activo=True)
        if rol == "almacen":
            q = q.filter_by(id=entidad_id)
        for a in q:
            if a.stock_cajas > a.stock_max_cajas:
                out.append(("err", f"{a.nombre}: {a.stock_cajas} cajas, supera el máximo de {a.stock_max_cajas}."))
            elif a.ocupacion >= 0.9:
                out.append(("warn", f"{a.nombre}: al {a.ocupacion:.0%} de su capacidad ({a.stock_cajas}/{a.stock_max_cajas} cajas)."))
            pend = sum(r.cajas for r in Reposicion.query.filter_by(almacen_id=a.id).filter(
                Reposicion.estado != "recibida"))
            if pend and a.stock_cajas + pend > a.stock_max_cajas:
                out.append(("warn", f"{a.nombre}: con las reposiciones pendientes ({pend} cajas) superaría su máximo."))
    if rol in ("admin", "planta"):
        q = Planta.query.filter_by(activa=True)
        if rol == "planta":
            q = q.filter_by(id=entidad_id)
        for p in q:
            if p.stock_prendas > p.stock_max_prendas:
                out.append(("err", f"{p.nombre}: {p.stock_prendas:,} prendas, supera el máximo de {p.stock_max_prendas:,}."))
            elif p.ocupacion >= 0.9:
                out.append(("warn", f"{p.nombre}: al {p.ocupacion:.0%} de su stock máximo."))
            pend = defaultdict(int)
            for r in Reposicion.query.filter_by(planta_id=p.id, estado="planificada"):
                for l in r.lineas:
                    pend[l.categoria_id] += l.unidades
            stock = {s.categoria_id: s.unidades for s in p.stock}
            falta = [c for c, u in pend.items() if u > stock.get(c, 0)]
            if falta:
                out.append(("err", f"{p.nombre}: no hay stock suficiente para las reposiciones planificadas ({', '.join(falta)})."))
    if rol == "tienda":
        t = db.session.get(Tienda, entidad_id)
        agotadas = [s.categoria.nombre for s in t.stock if s.unidades == 0]
        if agotadas:
            out.append(("warn", "Sin stock: " + ", ".join(agotadas)))
    if rol == "admin":
        for M, base in ((CamionTroncal, "planta_id"), (CamionReparto, "almacen_id")):
            for c in M.query.filter(M.itv_vence.isnot(None), M.estado != "baja"):
                if c.itv_vence < hoy:
                    out.append(("err", f"Camión {c.matricula} ({getattr(c, base)}): ITV caducada el {c.itv_vence}."))
                elif c.itv_vence <= hoy + dt.timedelta(days=30):
                    out.append(("warn", f"Camión {c.matricula} ({getattr(c, base)}): ITV vence el {c.itv_vence}."))
        sin = Viaje.query.filter(Viaje.estado == "planificado", Viaje.camion_troncal_id.is_(None),
                                 Viaje.camion_reparto_id.is_(None)).count()
        if sin:
            out.append(("warn", f"{sin} rutas planificadas sin camión asignado (ver Planificación)."))
    return out
