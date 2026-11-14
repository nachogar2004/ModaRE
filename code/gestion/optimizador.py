"""Puente entre la app de gestión y la librería de rutas (paquete `modare`).

Es el ÚNICO módulo de la app que conoce la librería. Si se sustituye por otra versión, basta con
adaptar estas tres funciones:

  exportar_maestros()   BD -> data/tiendas.csv, plantas.csv, almacenes.csv y vetos_*.csv  (lo que lee la librería)
  lanzar(semana, ...)   ejecuta la optimización en segundo plano (escribe code/salidas/<semana>/)
  importar_plan(semana) code/salidas/<semana>/plan.json -> viajes, camiones asignados, reposiciones

Los pedidos de la semana ya los ha volcado `logistica.cerrar_semana` a la tabla `pedidos` de modare.
"""
from __future__ import annotations

import threading
import traceback
from collections import defaultdict

import pandas as pd

from modare import config as C
from modare.planificador import cargar_plan

from .modelos import (Almacen, CamionReparto, CamionTroncal, LineaReposicion, Pedido, Planta, Reposicion, Tienda,
                      Viaje, db)

TRABAJOS: dict[str, dict] = {}       # semana -> {"estado": "calculando"|"ok"|"error", "msg": str}
ETIQUETA_APP = "APP"                  # etiqueta de las filas creadas/editadas desde la app


# ----------------------------------------------------------------------------- 1. exportar
def _igual(viejo, nuevo) -> bool:
    """Evita reescribir valores iguales (1500 vs 1500.0) para no ensuciar el diff de data/ en git."""
    if pd.isna(viejo) and (nuevo is None or nuevo == "" or (isinstance(nuevo, float) and pd.isna(nuevo))):
        return True
    try:
        return float(viejo) == float(nuevo)
    except (TypeError, ValueError):
        return str(viejo) == str(nuevo)


def _sincronizar_csv(fichero: str, clave: str, filas: list[dict]):
    """Actualiza las columnas indicadas de cada fila (por id) y añade las nuevas. El resto de columnas
    del CSV (fuentes, etiquetas…) se conserva; las ids que ya no existen en la BD se marcan inactivas."""
    ruta = C.DATA_DIR / fichero
    df = pd.read_csv(ruta, encoding="utf-8", dtype=object)
    col_activa = "activo" if "activo" in df.columns else "activa"
    df = df.set_index(clave)
    ids_bd = set()
    for f in filas:
        i = f.pop(clave)
        ids_bd.add(i)
        if i not in df.index:
            df.loc[i] = pd.NA
        for k, v in f.items():
            if k in df.columns and not _igual(df.at[i, k], v):
                df.at[i, k] = v
    for i in set(df.index) - ids_bd:
        if not _igual(df.at[i, col_activa], 0):
            df.at[i, col_activa] = "0"
    df.reset_index().to_csv(ruta, index=False, encoding="utf-8")


def exportar_maestros():
    """Vuelca plantas, almacenes y tiendas de la BD a los CSV que lee la librería de rutas.
    Nota: si se añade un punto nuevo, la librería usa línea recta × factor_rodeo hasta que se regenere
    data/distancias.csv con `python calcular_distancias.py`."""
    _sincronizar_csv("plantas.csv", "id_planta", [
        {"id_planta": p.id, "nombre": p.nombre, "direccion": p.direccion, "municipio": p.municipio,
         "provincia": p.provincia, "lat": p.lat, "lon": p.lon, "activa": int(p.activa),
         "capacidad_salida_kg_semana": p.capacidad_salida_kg_semana, "hora_inicio_carga": p.hora_inicio_carga,
         "hora_fin_carga": p.hora_fin_carga, "apertura_prevista": p.apertura_prevista}
        for p in Planta.query])
    _sincronizar_csv("almacenes.csv", "id_almacen", [
        {"id_almacen": a.id, "nombre": a.nombre, "provincia": a.provincia, "poligono": a.poligono,
         "municipio": a.municipio, "lat": a.lat, "lon": a.lon, "capacidad_kg_semana": a.capacidad_kg_semana,
         "hora_inicio_carga": a.hora_inicio, "hora_fin_carga": a.hora_fin, "dias_recepcion": a.dias_recepcion,
         "tiempo_descarga_min": 45, "activo": int(a.activo)}
        for a in Almacen.query])
    filas = []
    for t in Tienda.query:
        filas.append({
            "id_tienda": t.id, "nombre": t.nombre, "subtipo": t.subtipo, "direccion": t.direccion,
            "municipio": t.municipio, "provincia": t.provincia, "ccaa": t.ccaa, "lat": t.lat, "lon": t.lon,
            "acceso_carretera": int(t.acceso_carretera), "superficie_m2": t.superficie_m2,
            "categoria_tamano": t.categoria_tamano, "ventas_periodo": t.ventas_anuales_eur or 0,
            "ventas_unidad": "EUR", "dias_recepcion": t.dias_recepcion, "ventana_inicio": t.ventana_inicio,
            "ventana_fin": t.ventana_fin, "tiempo_descarga_min": t.tiempo_descarga_min,
            "vehiculo_max": t.vehiculo_max, "activa": int(t.activa),
            "fecha_alta": t.fecha_alta.isoformat() if t.fecha_alta else None})
    # las tiendas nuevas necesitan las columnas obligatorias que la librería espera
    df = pd.read_csv(C.DATA_DIR / "tiendas.csv", encoding="utf-8", usecols=["id_tienda"])
    for f in filas:
        if f["id_tienda"] not in set(df.id_tienda):
            f.update(en_muestra=0, ubicacion_etiqueta=ETIQUETA_APP, recepcion_etiqueta=ETIQUETA_APP,
                     ventas_etiqueta=ETIQUETA_APP, periodo_ventas="anual")
    _sincronizar_csv("tiendas.csv", "id_tienda", filas)
    exportar_vetos()


def exportar_vetos():
    """data/vetos_almacen_planta.csv y data/vetos_tienda_almacen.csv: lo que cada almacén y cada tienda ha
    vetado. La asignación de la librería lo respeta; todo lo demás está permitido."""
    vetos = {
        "vetos_almacen_planta.csv": (["id_almacen", "id_planta"],
                                     sorted((a.id, p.id) for a in Almacen.query for p in a.plantas_vetadas)),
        "vetos_tienda_almacen.csv": (["id_tienda", "id_almacen"],
                                     sorted((t.id, a.id) for t in Tienda.query for a in t.almacenes_vetados)),
    }
    for fichero, (cols, filas) in vetos.items():
        pd.DataFrame(filas, columns=cols).to_csv(C.DATA_DIR / fichero, index=False, encoding="utf-8")


# ----------------------------------------------------------------------------- 2. ejecutar
def lanzar(app, semana: str, *, segundos: int = 30, asignacion: str = "optimo", precio_co2: float = 0.0,
           plantas_futuras: bool = False) -> bool:
    """Exporta los maestros y lanza la optimización en un hilo. Al terminar importa el plan."""
    if TRABAJOS.get(semana, {}).get("estado") == "calculando":
        return False
    exportar_maestros()
    TRABAJOS[semana] = {"estado": "calculando"}

    def _run():
        from modare.planificador import planificar
        try:
            planificar(semana, fuente="pedidos", asignacion=asignacion, segundos=segundos, precio_co2=precio_co2,
                       plantas_futuras=plantas_futuras, log=lambda *a: None)
            with app.app_context():
                res = importar_plan(semana)
            TRABAJOS[semana] = {"estado": "ok", "msg": res}
        except Exception as e:  # noqa: BLE001 — se enseña en la web
            traceback.print_exc()
            TRABAJOS[semana] = {"estado": "error", "msg": str(e)}

    threading.Thread(target=_run, daemon=True).start()
    return True


# ----------------------------------------------------------------------------- 3. importar
def _solapa(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _asignar_camiones(viajes: list[Viaje], ocupados: dict[int, list]) -> int:
    """Asigna a cada ruta un camión de su base que esté libre en ese intervalo: primero del mismo tipo,
    si no, el más pequeño que tenga capacidad (en cajas y kg). Devuelve cuántas rutas quedan sin camión."""
    sin = 0
    for v in sorted(viajes, key=lambda v: v.t_salida or 0):
        if v.escalon == "troncal":
            flota = CamionTroncal.query.filter_by(planta_id=v.origen)
        else:
            flota = CamionReparto.query.filter_by(almacen_id=v.origen)
        flota = [c for c in flota if c.estado in ("disponible", "en_ruta")]
        intervalo = (v.t_salida or 0, v.t_vuelta or 0)
        libres = [c for c in flota if not any(_solapa(intervalo, o) for o in ocupados[c.id])]
        aptos = [c for c in libres if c.tipo_vehiculo == v.tipo_vehiculo] or sorted(
            (c for c in libres if c.capacidad_cajas >= (v.cajas or 0) and c.carga_util_kg >= (v.kg or 0)),
            key=lambda c: c.capacidad_cajas)
        if not aptos:
            sin += 1
            continue
        c = aptos[0]
        ocupados[c.id].append(intervalo)
        if v.escalon == "troncal":
            v.camion_troncal = c
        else:
            v.camion_reparto = c
    return sin


def importar_plan(semana: str) -> str:
    """Lee el plan de la librería y crea los viajes, asigna camiones y genera las reposiciones planta→almacén."""
    plan = cargar_plan(semana)
    if not plan:
        raise FileNotFoundError(f"No hay plan para {semana} en {C.SALIDAS_DIR / semana}")
    if Viaje.query.filter(Viaje.semana == semana, Viaje.estado != "planificado").first() or \
            Reposicion.query.filter(Reposicion.semana == semana, Reposicion.estado != "planificada").first():
        raise RuntimeError("La semana ya tiene viajes en curso: no se reimporta el plan.")

    Pedido.query.filter(Pedido.semana_entrega == semana, Pedido.estado == "planificado") \
        .update({"estado": "confirmado", "viaje_id": None}, synchronize_session=False)
    Reposicion.query.filter_by(semana=semana).delete()
    Viaje.query.filter_by(semana=semana).delete()
    db.session.flush()

    viajes, tienda_viaje, almacen_viaje = [], {}, {}
    for r in plan["rutas"]:
        origen = r.get("origen", r["planta"])
        v = Viaje(semana=semana, nombre=r["nombre"], escalon=r.get("escalon", "reparto"), origen=origen,
                  tipo_vehiculo=r["tipo"], operador=r.get("operador"), salida=r["salida"], vuelta=r["vuelta"],
                  t_salida=int(r["t_salida"]), t_vuelta=int(r["t_vuelta"]), km=r["km"], kg=r["kg"], cajas=r["cajas"],
                  coste_eur=r["coste_eur"], co2_kg=r.get("co2_kg"),
                  recorrido=" > ".join([origen, *(p["tienda"] for p in r["paradas"]), origen]))
        db.session.add(v)
        viajes.append(v)
        destino = tienda_viaje if v.escalon == "reparto" else almacen_viaje
        for p in r["paradas"]:
            destino.setdefault(p["tienda"], (v, origen))

    ocupados = defaultdict(list)
    sin_camion = _asignar_camiones(viajes, ocupados)
    db.session.flush()

    # pedidos -> ruta de reparto y almacén que los sirve
    pedidos = Pedido.query.filter_by(semana_entrega=semana, estado="confirmado").all()
    sin_ruta = 0
    for p in pedidos:
        if p.tienda_id in tienda_viaje:
            v, alm = tienda_viaje[p.tienda_id]
            p.viaje, p.almacen_id, p.estado = v, alm, "planificado"
        else:
            sin_ruta += 1          # islas o visitas que el optimizador no pudo servir

    # reposiciones troncales: la mezcla de prendas de los pedidos que reparte cada almacén
    planta_de = {a["id"]: a["planta"] for a in plan.get("almacenes", [])}
    por_alm = defaultdict(lambda: defaultdict(int))
    for p in pedidos:
        if p.estado == "planificado":
            for l in p.lineas:
                por_alm[p.almacen_id][l.categoria_id] += l.unidades
    kg_alm, cajas_alm = defaultdict(float), defaultdict(int)
    for p in pedidos:
        if p.estado == "planificado":       # cajas = suma de las del pedido: lo que el almacén luego expide
            kg_alm[p.almacen_id] += p.kg
            cajas_alm[p.almacen_id] += p.cajas
    n_rep = 0
    for a_id, lineas in por_alm.items():
        planta = planta_de.get(a_id)
        if not planta:
            continue
        alm = db.session.get(Almacen, a_id)
        alm.planta_id = planta
        v = almacen_viaje.get(a_id, (None, None))[0]
        kg = round(kg_alm[a_id], 1)
        db.session.add(Reposicion(semana=semana, planta_id=planta, almacen_id=a_id, viaje=v, kg=kg,
                                  cajas=cajas_alm[a_id],
                                  lineas=[LineaReposicion(categoria_id=c, unidades=u) for c, u in lineas.items()]))
        n_rep += 1
    db.session.commit()
    res = plan["resumen"]
    return (f"{len(viajes)} rutas importadas ({res['troncal']['rutas']} troncales + {res['reparto']['rutas']} de "
            f"reparto), {n_rep} reposiciones, coste {res['coste_eur']:,.0f} €. "
            f"{sin_camion} rutas sin camión disponible, {sin_ruta} pedidos sin ruta.")


def reasignar_camion(v: Viaje, camion_id: int | None):
    if v.escalon == "troncal":
        v.camion_troncal_id = camion_id
    else:
        v.camion_reparto_id = camion_id
    db.session.commit()
