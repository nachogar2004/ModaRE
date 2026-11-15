"""Puente entre la app de gestión y la librería de rutas (paquete `modare`).

Es el ÚNICO módulo de la app que lanza la librería. Si se sustituye por otra versión, basta con adaptar:

  lanzar(semana, ...)   ejecuta la optimización en segundo plano (plan en la tabla planes y copia en code/salidas/)
  importar_plan(semana) plan de la semana -> viajes, camiones asignados, reposiciones

No hay que exportar nada: la librería lee la red, los vetos, los parámetros, los vehículos, las tarifas y las
distancias directamente de la base de datos (modare.datos.cargar_datos), así que optimiza con lo que haya en la
app en ese momento. Los pedidos de la semana ya los ha volcado `logistica.cerrar_semana` a la tabla
pedidos_optimizador.
"""
from __future__ import annotations

import threading
import traceback
from collections import defaultdict

from modare import config as C
from modare import bd as BD
from modare.planificador import cargar_plan

from .modelos import Almacen, CamionReparto, CamionTroncal, LineaReposicion, Pedido, Reposicion, Viaje, db


# ----------------------------------------------------------------------------- 0. plan de la red actual (la app)
def calcular_rutas() -> bool:
    """Calcula en un hilo el plan único de la red tal como está ahora: los kg/semana de todas las tiendas activas,
    con los parámetros, vehículos y tarifas de la base. Se guarda como C.PLAN_ACTUAL (no crea viajes ni
    reposiciones: la parte operativa no se usa). False si ya hay un cálculo en marcha."""
    if not BD.reclamar_trabajo(C.PLAN_ACTUAL):
        return False

    def _run():
        from modare.planificador import planificar
        try:
            plan = planificar(C.PLAN_ACTUAL, fuente="red", segundos=C.SEGUNDOS_BUSQUEDA,
                              precio_co2=C.PRECIO_CO2_EUR_T, log=lambda *a: None)
            r = plan["resumen"]
            BD.poner_trabajo(C.PLAN_ACTUAL, "ok", f"{r['rutas']} rutas para {r['tiendas_con_demanda']} tiendas "
                                                  f"en {r['segundos_calculo']:.0f} s")
        except Exception as e:  # noqa: BLE001 — se enseña en la web
            traceback.print_exc()
            BD.poner_trabajo(C.PLAN_ACTUAL, "error", str(e))

    threading.Thread(target=_run, daemon=True).start()
    return True


# ----------------------------------------------------------------------------- 1. ejecutar (ciclo semanal antiguo)
def lanzar(app, semana: str, *, asignacion: str = "optimo", plantas_futuras: bool = False) -> bool:
    """Lanza la optimización en un hilo (con los datos que haya en la base en ese momento) e importa el plan."""
    # el estado vive en la base de datos: lo ve cualquier proceso del servidor y sobrevive a un reinicio
    if not BD.reclamar_trabajo(semana):
        return False

    def _run():
        from modare.planificador import planificar
        try:
            planificar(semana, fuente="pedidos", asignacion=asignacion, segundos=C.SEGUNDOS_BUSQUEDA,
                       precio_co2=C.PRECIO_CO2_EUR_T,
                       plantas_futuras=plantas_futuras, log=lambda *a: None)
            with app.app_context():
                res = importar_plan(semana)
            BD.poner_trabajo(semana, "ok", res)
        except Exception as e:  # noqa: BLE001 — se enseña en la web
            traceback.print_exc()
            BD.poner_trabajo(semana, "error", str(e))

    threading.Thread(target=_run, daemon=True).start()
    return True


# ----------------------------------------------------------------------------- 2. importar
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
    troncal_de = {}     # (almacén, planta de origen) -> viaje: un almacén puede recibir de varias plantas
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
            if v.escalon == "troncal":
                troncal_de.setdefault((p["tienda"], origen), v)

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
        # la reposición se registra a nombre de la planta principal del almacén (la que más kg le envía)
        v = troncal_de.get((a_id, planta)) or almacen_viaje.get(a_id, (None, None))[0]
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
