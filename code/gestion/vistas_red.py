"""Paneles de tienda, planta y almacén. Cada usuario solo ve el suyo; el administrador puede ver todos."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import comprobar_acceso, rol_requerido
from . import logistica as L
from .modelos import (FRECUENCIAS, SEGMENTOS, TIPOS_PRENDA, Almacen, Categoria, Pedido, Planta, Reposicion, Tienda,
                      Viaje, db)


def _orden(lista_base, valores):
    """Primero en el orden habitual (Mujer, Hombre, Niños…), luego los que haya añadido el admin."""
    return [v for v in lista_base if v in valores] + [v for v in valores if v not in lista_base]

bp_tienda = Blueprint("tienda", __name__, url_prefix="/tienda")
bp_planta = Blueprint("planta", __name__, url_prefix="/planta")
bp_almacen = Blueprint("almacen", __name__, url_prefix="/almacen")


# ----------------------------------------------------------------------------- utilidades
def categorias():
    return Categoria.query.order_by(Categoria.orden).all()


def matriz(cats):
    """{"tipos": [...], "filas": {segmento: [categoría o None por tipo]}} para la tabla segmento × tipo.
    Se construye con las categorías que haya en la BD (el admin puede añadir segmentos o tipos nuevos)."""
    segs = _orden(SEGMENTOS, list(dict.fromkeys(c.segmento for c in cats)))
    tipos = _orden(TIPOS_PRENDA, list(dict.fromkeys(c.tipo for c in cats)))
    filas = {s: [None] * len(tipos) for s in segs}
    for c in cats:
        filas[c.segmento][tipos.index(c.tipo)] = c
    return {"tipos": tipos, "filas": filas}


def leer_lineas(prefijo: str) -> dict[str, int]:
    out = {}
    for c in categorias():
        v = (request.form.get(f"{prefijo}_{c.id}") or "").strip()
        if v:
            try:
                out[c.id] = int(v)
            except ValueError:
                abort(400, f"Cantidad no válida en {c.nombre}")
    return out


def _avisar(avisos):
    for a in avisos:
        flash(a, "error")


def _accion(fn, ok_msg, destino):
    """Ejecuta una operación de negocio y muestra su error al usuario en vez de romper."""
    try:
        avisos = fn()
        flash(ok_msg)
        _avisar(avisos or [])
    except L.ErrorNegocio as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(destino)


def proxima_fecha_auto(t: Tienda, desde: dt.date) -> dt.date | None:
    if not t.pedido_auto_activo:
        return None
    for i in range(1, 62):
        f = desde + dt.timedelta(days=i)
        if L.toca_pedido(t, f):
            return f
    return None


# ============================================================================= TIENDA
@bp_tienda.route("/", endpoint="mia")
@login_required
@rol_requerido("tienda")
def tienda_mia():
    return redirect(url_for("tienda.panel", tid=current_user.tienda_id))


@bp_tienda.route("/<tid>", endpoint="panel")
@login_required
@rol_requerido("tienda")
def tienda_panel(tid):
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    hoy = dt.date.today()
    cats = categorias()
    stock = {s.categoria_id: s.unidades for s in t.stock}
    media = L.venta_media_diaria(t.id, hoy + dt.timedelta(days=1))
    semana_pasada = L.venta_media_diaria(t.id, hoy + dt.timedelta(days=1), dias=7)
    pedidos = Pedido.query.filter_by(tienda_id=t.id).order_by(Pedido.creado.desc(), Pedido.id.desc()).limit(40).all()
    umbral = max(3, t.dias_seguridad or 0)
    cobertura = {}
    for c, v in media.items():
        if v > 0:
            d = stock.get(c, 0) / v
            cobertura[c] = (f"{d:.0f} días" if d < 99 else "> 99 días", d < umbral)
    return render_template(
        "gestion/tienda.html", t=t, cats=cats, m=matriz(cats), stock=stock, media=media, cobertura=cobertura,
        ventas_7d={k: round(v * 7) for k, v in semana_pasada.items()},
        en_camino=L.en_camino(t.id), propuesta=L.propuesta_automatica(t, hoy), pedidos=pedidos,
        proxima_auto=proxima_fecha_auto(t, hoy),
        alertas=L.alertas("tienda", t.id), hoy=hoy,
        almacenes=Almacen.query.filter_by(activo=True).order_by(Almacen.provincia).all(),
        por_recibir=[p for p in pedidos if p.estado == "en_reparto"],
        activos=sum(1 for p in pedidos if p.estado in L.ACTIVOS))


@bp_tienda.post("/<tid>/ventas", endpoint="ventas")
@login_required
@rol_requerido("tienda")
def tienda_ventas(tid):
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    try:
        fecha = dt.date.fromisoformat(request.form.get("fecha") or dt.date.today().isoformat())
    except ValueError:
        flash("Fecha no válida.", "error")
        return redirect(url_for("tienda.panel", tid=tid))
    if fecha > dt.date.today():
        flash("No se pueden registrar ventas futuras.", "error")
        return redirect(url_for("tienda.panel", tid=tid))
    lineas = {k: v for k, v in leer_lineas("v").items() if v > 0}
    if not lineas:
        flash("No has indicado ninguna prenda vendida.", "error")
        return redirect(url_for("tienda.panel", tid=tid))
    return _accion(lambda: L.registrar_ventas(t, lineas, fecha, current_user),
                   f"Ventas del {fecha:%d/%m} registradas ({sum(lineas.values())} prendas).",
                   url_for("tienda.panel", tid=tid))


@bp_tienda.post("/<tid>/ajuste", endpoint="ajuste")
@login_required
@rol_requerido("tienda")
def tienda_ajuste(tid):
    """Poner/quitar prendas a mano: inventario, mermas, donaciones recibidas, prendas retiradas…"""
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    lineas = leer_lineas("a")
    return _accion(lambda: L.ajustar_stock_prendas(t, lineas, request.form.get("motivo") or "ajuste", current_user),
                   "Stock actualizado.", url_for("tienda.panel", tid=tid))


@bp_tienda.post("/<tid>/pedido", endpoint="pedido")
@login_required
@rol_requerido("tienda")
def tienda_pedido(tid):
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    lineas = leer_lineas("p")
    return _accion(lambda: [] if L.crear_pedido_adicional(t, lineas, request.form.get("comentario", ""),
                                                          bool(request.form.get("urgente")), current_user) else [],
                   "Pedido adicional registrado: entrará en la próxima planificación de rutas.",
                   url_for("tienda.panel", tid=tid))


@bp_tienda.post("/<tid>/config", endpoint="config")
@login_required
@rol_requerido("tienda")
def tienda_config(tid):
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    f = request.form.get("frecuencia_pedido")
    if f not in FRECUENCIAS:
        abort(400)
    try:
        dia = int(request.form.get("dia_pedido") or 0)
        seg = int(request.form.get("dias_seguridad") or 0)
    except ValueError:
        flash("El día de pedido y los días de seguridad deben ser números enteros.", "error")
        return redirect(url_for("tienda.panel", tid=tid))
    if f == "semanal" and not 0 <= dia <= 6:
        flash("Para pedido semanal el día va de 0 (lunes) a 6 (domingo).", "error")
        return redirect(url_for("tienda.panel", tid=tid))
    if f == "mensual" and not 1 <= dia <= 28:
        flash("Para pedido mensual el día del mes va de 1 a 28.", "error")
        return redirect(url_for("tienda.panel", tid=tid))
    if not 0 <= seg <= 60:
        flash("Los días de seguridad van de 0 a 60.", "error")
        return redirect(url_for("tienda.panel", tid=tid))
    t.frecuencia_pedido, t.dia_pedido, t.dias_seguridad = f, dia, seg
    t.pedido_auto_activo = bool(request.form.get("pedido_auto_activo"))
    db.session.commit()
    flash("Configuración de pedidos automáticos guardada.")
    return redirect(url_for("tienda.panel", tid=tid))


def _pedido_de(tid, pid) -> Pedido:
    comprobar_acceso("tienda", tid)
    p = db.get_or_404(Pedido, pid)
    if p.tienda_id != tid:
        abort(404)
    return p


@bp_tienda.post("/<tid>/pedido/<int:pid>/cancelar", endpoint="cancelar")
@login_required
@rol_requerido("tienda")
def tienda_cancelar(tid, pid):
    p = _pedido_de(tid, pid)
    return _accion(lambda: L.cancelar_pedido(p), f"Pedido {p.codigo} cancelado.", url_for("tienda.panel", tid=tid))


@bp_tienda.post("/<tid>/pedido/<int:pid>/recibir", endpoint="recibir")
@login_required
@rol_requerido("tienda")
def tienda_recibir(tid, pid):
    p = _pedido_de(tid, pid)
    return _accion(lambda: L.recibir_pedido(p, current_user), f"Pedido {p.codigo} recibido: stock actualizado.",
                   url_for("tienda.panel", tid=tid))


# ============================================================================= PLANTA
@bp_planta.route("/<pid>", endpoint="panel")
@login_required
@rol_requerido("planta")
def planta_panel(pid):
    comprobar_acceso("planta", pid)
    p = db.get_or_404(Planta, pid)
    cats = categorias()
    reps = Reposicion.query.filter_by(planta_id=pid).order_by(Reposicion.semana.desc(), Reposicion.almacen_id).limit(80).all()
    viajes = Viaje.query.filter_by(escalon="troncal", origen=pid).order_by(Viaje.semana.desc(), Viaje.t_salida).limit(40).all()
    return render_template("gestion/planta.html", p=p, cats=cats, m=matriz(cats),
                           stock={s.categoria_id: s.unidades for s in p.stock}, reps=reps, viajes=viajes,
                           alertas=L.alertas("planta", pid))


@bp_planta.post("/<pid>/ajuste", endpoint="ajuste")
@login_required
@rol_requerido("planta")
def planta_ajuste(pid):
    """Entrada de prendas clasificadas (+) o salidas/mermas (−)."""
    comprobar_acceso("planta", pid)
    p = db.get_or_404(Planta, pid)
    return _accion(lambda: L.ajustar_stock_prendas(p, leer_lineas("a"), request.form.get("motivo") or "ajuste",
                                                   current_user),
                   "Stock de planta actualizado.", url_for("planta.panel", pid=pid))


@bp_planta.post("/<pid>/reposicion/<int:rid>/expedir", endpoint="expedir")
@login_required
@rol_requerido("planta")
def planta_expedir(pid, rid):
    comprobar_acceso("planta", pid)
    r = db.get_or_404(Reposicion, rid)
    if r.planta_id != pid:
        abort(404)
    return _accion(lambda: L.expedir_reposicion(r, current_user),
                   f"Reposición {r.codigo} expedida a {r.almacen.nombre}.", url_for("planta.panel", pid=pid))


# ============================================================================= ALMACÉN
@bp_almacen.route("/<aid>", endpoint="panel")
@login_required
@rol_requerido("almacen")
def almacen_panel(aid):
    comprobar_acceso("almacen", aid)
    a = db.get_or_404(Almacen, aid)
    entrantes = Reposicion.query.filter_by(almacen_id=aid).order_by(Reposicion.semana.desc()).limit(30).all()
    # el almacén que sirve cada pedido lo decide el optimizador en cada lote
    salientes = (Pedido.query.filter(Pedido.almacen_id == aid,
                                     Pedido.estado.in_(("confirmado", "planificado", "en_reparto", "entregado")))
                 .order_by(Pedido.semana_entrega.desc(), Pedido.tienda_id).limit(150).all())
    # último lote en el que este almacén sirvió a alguna tienda
    ultima = (db.session.query(Pedido.semana_entrega).filter(Pedido.almacen_id == aid, Pedido.semana_entrega.isnot(None))
              .order_by(Pedido.creado.desc()).limit(1).scalar())
    servidas = (Tienda.query.join(Pedido, Pedido.tienda_id == Tienda.id)
                .filter(Pedido.almacen_id == aid, Pedido.semana_entrega == ultima).distinct().order_by(Tienda.nombre).all()
                if ultima else [])
    viajes = Viaje.query.filter_by(escalon="reparto", origen=aid).order_by(Viaje.semana.desc(), Viaje.t_salida).limit(40).all()
    previsto_in = sum(r.cajas for r in entrantes if r.estado != "recibida")
    previsto_out = sum(p.cajas for p in salientes if p.estado in ("confirmado", "planificado"))
    return render_template("gestion/almacen.html", a=a, entrantes=entrantes, salientes=salientes, viajes=viajes,
                           previsto_in=previsto_in, previsto_out=previsto_out, alertas=L.alertas("almacen", aid),
                           servidas=servidas, ultima=ultima,
                           plantas=Planta.query.order_by(Planta.activa.desc(), Planta.id).all())


@bp_almacen.post("/<aid>/ajuste", endpoint="ajuste")
@login_required
@rol_requerido("almacen")
def almacen_ajuste(aid):
    comprobar_acceso("almacen", aid)
    a = db.get_or_404(Almacen, aid)
    try:
        delta = int(request.form.get("delta") or 0)
    except ValueError:
        abort(400)
    return _accion(lambda: L.ajustar_cajas_almacen(a, delta, request.form.get("motivo") or "ajuste", current_user),
                   "Stock del almacén actualizado.", url_for("almacen.panel", aid=aid))


@bp_almacen.post("/<aid>/reposicion/<int:rid>/recibir", endpoint="recibir")
@login_required
@rol_requerido("almacen")
def almacen_recibir(aid, rid):
    comprobar_acceso("almacen", aid)
    r = db.get_or_404(Reposicion, rid)
    if r.almacen_id != aid:
        abort(404)
    return _accion(lambda: L.recibir_reposicion(r, current_user), f"Reposición {r.codigo} recibida (+{r.cajas} cajas).",
                   url_for("almacen.panel", aid=aid))


@bp_almacen.post("/<aid>/pedido/<int:pid>/expedir", endpoint="expedir")
@login_required
@rol_requerido("almacen")
def almacen_expedir(aid, pid):
    comprobar_acceso("almacen", aid)
    p = db.get_or_404(Pedido, pid)
    if p.almacen_id != aid:
        abort(404)
    return _accion(lambda: L.expedir_pedido(p, current_user), f"Pedido {p.codigo} cargado en el camión.",
                   url_for("almacen.panel", aid=aid))


@bp_almacen.post("/<aid>/viaje/<int:vid>/expedir", endpoint="expedir_viaje")
@login_required
@rol_requerido("almacen")
def almacen_expedir_viaje(aid, vid):
    """Carga de una vez todos los pedidos de una ruta de reparto."""
    comprobar_acceso("almacen", aid)
    v = db.get_or_404(Viaje, vid)
    if v.origen != aid or v.escalon != "reparto":
        abort(404)
    pendientes = Pedido.query.filter_by(viaje_id=v.id, estado="planificado").all()
    if not pendientes:
        flash("Esta ruta no tiene pedidos pendientes de cargar.", "error")
        return redirect(url_for("almacen.panel", aid=aid))
    cajas = sum(p.cajas for p in pendientes)
    a = db.session.get(Almacen, aid)
    if a.stock_cajas < cajas:
        flash(f"La ruta {v.nombre} necesita {cajas} cajas y el almacén solo tiene {a.stock_cajas}.", "error")
        return redirect(url_for("almacen.panel", aid=aid))
    for p in pendientes:
        L.expedir_pedido(p, current_user)
    flash(f"Ruta {v.nombre}: {len(pendientes)} pedidos ({cajas} cajas) cargados en el camión.")
    return redirect(url_for("almacen.panel", aid=aid))


# ============================================================================= VETOS
@bp_tienda.post("/<tid>/vetos", endpoint="vetos")
@login_required
@rol_requerido("tienda")
def tienda_vetos(tid):
    """La tienda decide de qué almacenes no quiere recibir ropa (por defecto, de ninguno)."""
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    ids = request.form.getlist("almacenes_vetados")
    t.almacenes_vetados = Almacen.query.filter(Almacen.id.in_(ids)).all() if ids else []
    db.session.commit()
    flash(f"Vetos guardados: {len(ids)} almacén(es) vetado(s). Se aplicarán en la próxima planificación." if ids
          else "Sin vetos: puedes recibir de cualquier almacén.")
    _avisar(t_ for _, t_ in L.alertas("tienda", tid) if "vetado" in t_)
    return redirect(url_for("tienda.panel", tid=tid) + "#vetos")


@bp_almacen.post("/<aid>/vetos", endpoint="vetos")
@login_required
@rol_requerido("almacen")
def almacen_vetos(aid):
    """El almacén decide de qué plantas no quiere recibir ropa (por defecto, de ninguna)."""
    comprobar_acceso("almacen", aid)
    a = db.get_or_404(Almacen, aid)
    ids = request.form.getlist("plantas_vetadas")
    a.plantas_vetadas = Planta.query.filter(Planta.id.in_(ids)).all() if ids else []
    db.session.commit()
    flash(f"Vetos guardados: {len(ids)} planta(s) vetada(s). Se aplicarán en la próxima planificación." if ids
          else "Sin vetos: puedes recibir de cualquier planta.")
    if a.sin_planta:
        flash("Has vetado todas las plantas activas: el almacén no recibirá mercancía.", "error")
    return redirect(url_for("almacen.panel", aid=aid) + "#vetos")
