"""Carga inicial de la base de datos de gestión a partir de los CSV de data/.

    .venv/bin/python -m gestion.semilla            # prepara la base (MODARE_DB) y la rellena si está vacía
    .venv/bin/python -m gestion.semilla --reset    # BORRA todo y la vuelve a crear con datos de ejemplo

Los CSV de data/ son SOLO la semilla: después la base de datos es la única fuente, tanto de la app como del
optimizador de rutas. Lo que viene de los CSV es el dato del reto: plantas, almacenes, tiendas y los datos del
optimizador (parámetros, tipos de vehículo con sus emisiones, tarifas y distancias por carretera).
Lo que se inventa aquí está marcado como SINTÉTICO y se puede cambiar desde la app:
catálogo de categorías (peso y precio por prenda), stock inicial, historial de ventas de 8 semanas,
flota de camiones (matrículas ficticias) y usuarios con contraseña inicial.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import random
import re
import unicodedata

import pandas as pd
from sqlalchemy import insert

from modare import config as C
from modare.datos import kg_semana_estimado, valor_parametro

from .modelos import (SEGMENTOS, TIPOS_PRENDA, Ajuste, Almacen, CamionReparto, CamionTroncal, Categoria, Distancia,
                      Parametro, Planta, StockPlanta, StockTienda, Tarifa, Tienda, TipoVehiculo, Usuario, Venta, db)

PASSWORD_INICIAL = os.environ.get("MODARE_PASSWORD_INICIAL", "modare2026")
PASSWORD_ADMIN = os.environ.get("MODARE_PASSWORD_ADMIN", "admin")

# --- catálogo SINTÉTICO -----------------------------------------------------------
_TIPO = {  # código, kg/prenda, €/prenda, cuota dentro del segmento
    "Partes de arriba": ("ARR", 0.25, 6.0, 0.38),
    "Partes de abajo": ("ABA", 0.45, 9.0, 0.27),
    "Abrigos": ("ABR", 1.10, 18.0, 0.12),
    "Zapatos": ("ZAP", 0.80, 12.0, 0.13),
    "Bolsos": ("BOL", 0.50, 10.0, 0.10),
}
_SEG = {"Mujer": ("MUJ", 1.0, 1.0, 0.50), "Hombre": ("HOM", 1.1, 1.0, 0.30), "Niños": ("NIN", 0.5, 0.6, 0.20)}
DIAS_HISTORIAL = 56
TIPOS_TRONCAL = ["VEH-R12", "VEH-R18", "VEH-ART"]
TIPOS_REPARTO = ["VEH-FUR", "VEH-R75", "VEH-R12", "VEH-R18"]


def _csv(nombre, **kw):
    return pd.read_csv(C.DATA_DIR / nombre, encoding="utf-8", **kw)


def _txt(v, defecto=None):
    return defecto if pd.isna(v) else str(v)


def _num(v):
    return None if pd.isna(v) else float(v)


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.upper() if ch.isalnum())


def parametros() -> dict:
    """Parámetros del optimizador leídos con la sesión de la app (ve también lo aún no confirmado)."""
    return {p.id: valor_parametro(p.valor) for p in Parametro.query}


def capacidad_vehiculos() -> dict[str, dict]:
    """carga útil, palés y cajas de cada tipo de vehículo (misma regla que modare.datos)."""
    P = parametros()
    m3_caja = C.KG_POR_CAJA / P["densidad_kg_m3"]
    out = {}
    for r in TipoVehiculo.query.order_by(TipoVehiculo.carga_util_kg):
        out[r.id] = {"descripcion": r.descripcion, "carga_util_kg": float(r.carga_util_kg),
                     "capacidad_palets": int(r.capacidad_palets),
                     "capacidad_cajas": int(min(r.capacidad_palets * P["kg_por_palet"] / C.KG_POR_CAJA,
                                                r.volumen_m3 / m3_caja))}
    return out


# ----------------------------------------------------------------------------- datos del optimizador
def crear_datos_optimizador(log=print):
    """Rellena desde los CSV las tablas del optimizador que estén vacías: parámetros, tipos de vehículo (con
    emisiones), tarifas y distancias. Lo que ya tenga datos no se toca (lo manda la base, no el CSV)."""
    hechos = []
    if not db.session.query(Parametro.id).first():
        for r in _csv("parametros.csv", dtype=str).itertuples():
            db.session.add(Parametro(id=r.clave, valor=r.valor, unidad=_txt(r.unidad), descripcion=_txt(r.descripcion),
                                     etiqueta=_txt(r.etiqueta), fuente=_txt(r.fuente)))
        hechos.append("parámetros")
    if not db.session.query(TipoVehiculo.id).first():
        emi = _csv("emisiones.csv").set_index("id_tipo")
        for r in _csv("vehiculos.csv").itertuples():
            e = emi.loc[r.id_tipo]
            db.session.add(TipoVehiculo(
                id=r.id_tipo, descripcion=r.descripcion, mma_t=_num(r.mma_t), carga_util_kg=float(r.carga_util_kg),
                volumen_m3=float(r.volumen_m3), capacidad_palets=int(r.capacidad_palets), combustible=_txt(e.combustible),
                kgco2e_km_vacio=float(e.kgco2e_km_vacio), kgco2e_km_lleno=float(e.kgco2e_km_lleno),
                etiqueta=_txt(r.etiqueta), fuente=_txt(r.fuente), fuente_emisiones=_txt(e.fuente)))
        db.session.flush()
        hechos.append("tipos de vehículo")
    if not db.session.query(Tarifa.id).first():
        for r in _csv("tarifas.csv").itertuples():
            db.session.add(Tarifa(
                id=r.id_tarifa, operador=r.operador, tipo_vehiculo=r.id_tipo, coste_fijo_dia_eur=_num(r.coste_fijo_dia_eur),
                coste_km_eur=_num(r.coste_km_eur), coste_hora_eur=_num(r.coste_hora_eur),
                unidades_disponibles=None if pd.isna(r.unidades_disponibles) else int(r.unidades_disponibles),
                etiqueta=_txt(r.etiqueta), fuente=_txt(r.fuente)))
        hechos.append("tarifas")
    if not db.session.query(Distancia.origen).first():
        df = _csv("distancias.csv")
        fecha = {f: _fecha_en(f) for f in df.fuente.dropna().unique()}
        filas = [{"origen": r.origen, "destino": r.destino, "distancia_km": float(r.distancia_km),
                  "duracion_min": _num(r.duracion_min), "duracion_camion_min": float(r.duracion_camion_min),
                  "metodo": _txt(r.metodo), "calculada": fecha.get(r.fuente)} for r in df.itertuples()]
        db.session.flush()
        db.session.execute(insert(Distancia), filas)
        hechos.append(f"{len(filas)} distancias")
    if hechos:
        db.session.commit()
        log("Datos del optimizador cargados desde data/*.csv: " + ", ".join(hechos) + ".")


def kg_semana_por_ventas(ventas_anuales_eur: float | None, P: dict | None = None) -> float:
    """Estimación de la ropa que necesita una tienda a la semana: ventas ÷ precio medio por kg ÷ semanas de venta."""
    P = P or parametros()
    return round((ventas_anuales_eur or 0) / P["precio_medio_eur_kg"] / P["semanas_venta_anio"], 1)


def completar_kg_semana(log=print):
    """Las tiendas sin kg/semana (bases anteriores a ese campo) reciben la estimación por sus ventas."""
    sin = Tienda.query.filter(Tienda.kg_semana.is_(None)).all()
    if not sin:
        return
    P = parametros()
    for t in sin:
        t.kg_semana = kg_semana_por_ventas(t.ventas_anuales_eur, P)
    db.session.commit()
    log(f"kg/semana estimados por ventas para {len(sin)} tiendas.")


def _fecha_en(texto: str) -> dt.date | None:
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(texto))
    return dt.date.fromisoformat(m.group()) if m else None


def completar_red_desde_csv(log=print):
    """Una sola vez, en bases creadas antes de que el optimizador leyera de la base: copia de los CSV los datos
    de la red que la app no guardaba (costes y descarga de los almacenes, muestra y planta actual de las tiendas)."""
    if Ajuste.get("red_completada_desde_csv") == "1":
        return
    alm = _csv("almacenes.csv").set_index("id_almacen")
    for a in Almacen.query:
        if a.id in alm.index:
            r = alm.loc[a.id]
            a.tiempo_descarga_min = int(r.tiempo_descarga_min)
            if a.coste_fijo_eur_semana is None:
                a.coste_fijo_eur_semana = _num(r.coste_fijo_eur_semana)
            if a.coste_manipulacion_eur_kg is None:
                a.coste_manipulacion_eur_kg = _num(r.coste_manipulacion_eur_kg)
    tie = _csv("tiendas.csv").set_index("id_tienda")
    for t in Tienda.query:
        if t.id in tie.index:
            r = tie.loc[t.id]
            t.en_muestra = bool(r.en_muestra)
            t.planta_actual = t.planta_actual or _txt(r.planta_actual)
    Ajuste.set("red_completada_desde_csv", "1")
    db.session.commit()
    log("Red completada con los datos de data/*.csv que faltaban (costes de almacén, muestra, planta actual).")


def crear_categorias():
    orden = 0
    for seg in SEGMENTOS:
        cs, fkg, fprecio, cuota_seg = _SEG[seg]
        for tipo in TIPOS_PRENDA:
            ct, kg, precio, cuota_t = _TIPO[tipo]
            orden += 1
            db.session.add(Categoria(id=f"{cs}-{ct}", segmento=seg, tipo=tipo, kg_unidad=round(kg * fkg, 3),
                                     precio_unidad=round(precio * fprecio, 2), cuota_ventas=cuota_seg * cuota_t,
                                     orden=orden))
    db.session.flush()


def _reparto_por_categoria(total_prendas: float, cats) -> dict[str, int]:
    return {c.id: int(round(total_prendas * c.cuota_ventas)) for c in cats}


def crear_red(rng: random.Random):
    P = parametros()
    cats = Categoria.query.order_by(Categoria.orden).all()
    kg_medio = sum(c.cuota_ventas * c.kg_unidad for c in cats)   # kg por prenda vendida, en media

    for r in _csv("plantas.csv").itertuples():
        stock_max = int(r.capacidad_salida_kg_semana * 3 / kg_medio)      # SINTÉTICO: 3 semanas de salida
        p = Planta(id=r.id_planta, nombre=r.nombre, direccion=_txt(r.direccion), municipio=r.municipio,
                   provincia=r.provincia, lat=r.lat, lon=r.lon, capacidad_salida_kg_semana=r.capacidad_salida_kg_semana,
                   stock_max_prendas=stock_max, hora_inicio_carga=r.hora_inicio_carga, hora_fin_carga=r.hora_fin_carga,
                   activa=bool(r.activa), apertura_prevista=_txt(r.apertura_prevista))
        if p.activa:
            p.stock = [StockPlanta(categoria_id=k, unidades=v)
                       for k, v in _reparto_por_categoria(stock_max * 0.6, cats).items()]
        db.session.add(p)

    for r in _csv("almacenes.csv").itertuples():
        maxc = int(math.ceil(2 * r.capacidad_kg_semana / C.KG_POR_CAJA / 10) * 10)   # SINTÉTICO: 2 semanas
        db.session.add(Almacen(id=r.id_almacen, nombre=r.nombre, provincia=r.provincia, poligono=_txt(r.poligono),
                               municipio=_txt(r.municipio), lat=r.lat, lon=r.lon, stock_max_cajas=maxc,
                               stock_cajas=int(maxc * 0.25), capacidad_kg_semana=r.capacidad_kg_semana,
                               hora_inicio=r.hora_inicio_carga, hora_fin=r.hora_fin_carga,
                               dias_recepcion=r.dias_recepcion, tiempo_descarga_min=int(r.tiempo_descarga_min),
                               coste_fijo_eur_semana=_num(r.coste_fijo_eur_semana),
                               coste_manipulacion_eur_kg=_num(r.coste_manipulacion_eur_kg), activo=bool(r.activo)))
    db.session.flush()     # sin vetos: cualquier planta repone cualquier almacén y cualquier almacén sirve a cualquier tienda

    hoy = dt.date.today()
    filas_venta = []
    for r in _csv("tiendas.csv").itertuples():
        t_ser = pd.Series(r._asdict())
        kg_sem = kg_semana_estimado(t_ser, P)
        ventas_eur = r.ventas_periodo if r.ventas_unidad == "EUR" else r.ventas_periodo / 100 * P["ventas_media_eur_tienda"]
        prendas_dia = kg_sem / kg_medio / 6                     # se vende de lunes a sábado
        cat_tam = r.categoria_tamano
        t = Tienda(id=r.id_tienda, nombre=r.nombre, subtipo=_txt(r.subtipo), direccion=_txt(r.direccion),
                   municipio=r.municipio, provincia=r.provincia, ccaa=_txt(r.ccaa), lat=r.lat, lon=r.lon,
                   acceso_carretera=bool(r.acceso_carretera), superficie_m2=None if pd.isna(r.superficie_m2) else r.superficie_m2,
                   categoria_tamano=cat_tam, ventas_anuales_eur=float(ventas_eur), kg_semana=round(kg_sem, 1),
                   dias_recepcion=_txt(r.dias_recepcion, "L-V"), ventana_inicio=_txt(r.ventana_inicio, "10:00"),
                   ventana_fin=_txt(r.ventana_fin, "14:00"), tiempo_descarga_min=int(r.tiempo_descarga_min),
                   vehiculo_max=_txt(r.vehiculo_max),
                   # SINTÉTICO: las pequeñas piden al mes, el resto cada jueves (se cierran pedidos el viernes)
                   frecuencia_pedido="mensual" if cat_tam == "pequeña" else "semanal",
                   dia_pedido=1 if cat_tam == "pequeña" else 3, dias_seguridad=3,
                   capacidad_max_prendas=int(prendas_dia * 6 * 5),
                   en_muestra=bool(r.en_muestra), planta_actual=_txt(r.planta_actual),
                   activa=bool(r.activa), fecha_alta=pd.to_datetime(r.fecha_alta).date() if not pd.isna(r.fecha_alta) else hoy)
        t.stock = [StockTienda(categoria_id=k, unidades=v)
                   for k, v in _reparto_por_categoria(prendas_dia * 4, cats).items()]   # ~4 días de venta
        db.session.add(t)
        for d in range(DIAS_HISTORIAL, 0, -1):
            f = hoy - dt.timedelta(days=d)
            if f.weekday() == 6:
                continue
            for c in cats:
                u = _poisson(rng, prendas_dia * c.cuota_ventas)
                if u:
                    filas_venta.append({"tienda_id": r.id_tienda, "categoria_id": c.id, "fecha": f,
                                        "unidades": u, "sintetica": True})
    db.session.flush()
    db.session.execute(insert(Venta), filas_venta)


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:                                    # aproximación normal
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def crear_flota(rng: random.Random):
    """Flota SINTÉTICA: 3 camiones troncales por planta activa y 1-2 vehículos de reparto por almacén."""
    cap = capacidad_vehiculos()
    letras = "BCDFGHJKLMNPRSTVWXYZ"
    n = [0]

    def matricula():
        n[0] += 1
        return f"{(n[0] * 7919) % 10000:04d} {''.join(rng.choice(letras) for _ in range(3))}"

    for p in Planta.query.filter_by(activa=True):
        tipos = ["VEH-R12", "VEH-R12", "VEH-R18"] + (["VEH-ART"] if p.id == "PL-BCN" else [])
        for tipo in tipos:
            db.session.add(CamionTroncal(planta_id=p.id, matricula=matricula(), tipo_vehiculo=tipo,
                                         operador="Operador A", notas="SINTÉTICO", **_cap(cap, tipo)))
    for a in Almacen.query.filter_by(activo=True):
        tipos = ["VEH-FUR"] + (["VEH-R75"] if a.capacidad_kg_semana >= 3000 else [])
        for tipo in tipos:
            db.session.add(CamionReparto(almacen_id=a.id, matricula=matricula(), tipo_vehiculo=tipo,
                                         operador="Operador A", notas="SINTÉTICO", **_cap(cap, tipo)))


def _cap(cap, tipo):
    c = cap[tipo]
    return {"carga_util_kg": c["carga_util_kg"], "capacidad_cajas": c["capacidad_cajas"],
            "capacidad_palets": c["capacidad_palets"]}


def crear_usuarios():
    admin = Usuario(username="admin", nombre="Administrador", rol="admin")
    admin.set_password(PASSWORD_ADMIN)
    db.session.add(admin)
    # Un hash por usuario es lento (scrypt): se calcula una vez y se reutiliza para la contraseña inicial
    tmp = Usuario()
    tmp.set_password(PASSWORD_INICIAL)
    h = tmp.password_hash
    for t in Tienda.query:
        db.session.add(Usuario(username=t.id.lower(), nombre=t.nombre, rol="tienda", tienda_id=t.id, password_hash=h))
    for p in Planta.query:
        db.session.add(Usuario(username=p.id.lower(), nombre=p.nombre, rol="planta", planta_id=p.id, password_hash=h))
    for a in Almacen.query:
        db.session.add(Usuario(username=a.id.lower(), nombre=a.nombre, rol="almacen", almacen_id=a.id, password_hash=h))


def sembrar(log=print):
    from modare.bd import url_bd
    real = (os.environ.get("MODARE_ENV", "").lower() in ("produccion", "producción", "production")
            or not url_bd().startswith("sqlite"))        # una base PostgreSQL es una base de verdad
    faltan = [v for v in ("MODARE_PASSWORD_ADMIN", "MODARE_PASSWORD_INICIAL") if not os.environ.get(v)]
    if real and faltan:
        raise RuntimeError(f"Faltan {' y '.join(faltan)}: en una base real (PostgreSQL o producción) no se crean usuarios "
                           "con las contraseñas de ejemplo. Defínelas con export en ESTA terminal y repite el comando.")
    rng = random.Random(2026)
    crear_datos_optimizador(log)       # antes que la red: la semilla usa parámetros y tipos de vehículo
    crear_categorias()
    crear_red(rng)
    crear_flota(rng)
    crear_usuarios()
    Ajuste.set("red_completada_desde_csv", "1")
    db.session.commit()
    log(f"Base de datos creada: {Tienda.query.count()} tiendas, {Planta.query.count()} plantas, "
        f"{Almacen.query.count()} almacenes, {CamionTroncal.query.count()} camiones troncales, "
        f"{CamionReparto.query.count()} de reparto, {Venta.query.count()} registros de ventas, "
        f"{Usuario.query.count()} usuarios.")
    log(f"Acceso: admin / {PASSWORD_ADMIN}  ·  tiendas td-001…, plantas pl-mad…, almacenes al-madrid… "
        f"con contraseña inicial '{PASSWORD_INICIAL}' (cámbiala en 'Mi cuenta').")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="BORRA todas las tablas y las vuelve a crear con datos de ejemplo")
    a = ap.parse_args()
    from .basedatos import app_bd, borrar_todo, preparar_bd
    app = app_bd()
    with app.app_context():
        if a.reset:
            borrar_todo()
        preparar_bd()
