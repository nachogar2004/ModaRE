"""Etapa 2 — VRP heterogéneo con ventanas horarias (una instancia por depósito), con OR-Tools.

Se usa en los dos escalones:
  * troncal:  depósito = planta,   visitas = reposiciones de almacenes, camiones grandes (tipos_troncal)
  * reparto:  depósito = almacén,  visitas = entregas a tiendas, vehículos según vehiculo_max de la tienda

Modelo (ver CLAUDE.md para la formulación completa):
  * Nodos: el depósito + una visita por entrega (una tienda grande genera 2).
  * Vehículos: un *pool* de rutas candidatas por (operador, tipo de camión). El solver
    decide cuáles usar -> el nº de camiones NO es fijo, sale de minimizar el coste.
  * Coste (céntimos):  coste fijo por ruta (operador B)
                     + km × (€/km de la tarifa + precio_CO2 × kgCO2/km del camión)
                     + horas trabajadas × €/h (operador A)
                     + tiempo fuera de planta × coste_pernocta/24h (desincentiva rutas largas)
  * Restricciones:
      - capacidad en kg (carga útil) y en cajas (palés/volumen) de cada camión
      - vehículo máximo admitido por la tienda (centros urbanos ≤ 7,5 t)
      - ventana de recepción de la tienda, solo en sus días (L-V / M-V)
      - salida de planta dentro del horario de carga, con 45 min de carga
      - pausas de 20 min cada 200 km (sumadas al tiempo de cada tramo)
      - jornada (13 h) y conducción (9 h) por día × días máximos de ruta
      - cada visita se sirve una vez o se paga una penalización enorme (=> se reporta)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from . import config as C
from .datos import Datos, Visita, hhmm_a_min


@dataclass
class Vehiculo:
    id: str
    tipo: str
    operador: str
    tarifa: str
    cap_kg: int
    cap_cajas: int
    coste_km: float
    coste_hora: float
    coste_fijo_dia: float
    co2_vacio: float
    co2_lleno: float
    rango: int


def construir_pool(d: Datos, planta: str, visitas: list[Visita], excluir: frozenset = frozenset(),
                   tipos: set[str] | None = None) -> list[Vehiculo]:
    """Rutas candidatas. Operador A: sin límite real -> se acota por la demanda.
    Operador B: unidades_disponibles por depósito y día (supuesto: cada unidad hace 1 ruta/día).
    tipos: tipos permitidos (None = todos salvo los excluidos del reparto)."""
    H = int(d.P["horizonte_planificacion_dias"])
    tabla = d.tipos.set_index("id_tipo")
    total_cajas = sum(v.cajas for v in visitas)
    pool = []
    for tr in d.tarifas.itertuples():
        if tr.id_tipo in excluir:
            continue
        if (tipos is not None and tr.id_tipo not in tipos) or (tipos is None and tr.id_tipo in C.TIPOS_EXCLUIDOS_REPARTO):
            continue
        t = tabla.loc[tr.id_tipo]
        if math.isnan(tr.unidades_disponibles):
            n = min(len(visitas), max(3, math.ceil(total_cajas / t.cap_cajas) + 2), 25)
        else:
            n = min(int(tr.unidades_disponibles) * H, len(visitas))
        for k in range(n):
            pool.append(Vehiculo(
                id=f"{planta}/{tr.id_tarifa}/{k + 1}", tipo=tr.id_tipo, operador=tr.operador,
                tarifa=tr.id_tarifa, cap_kg=int(t.carga_util_kg), cap_cajas=int(t.cap_cajas),
                coste_km=float(tr.coste_km_eur), coste_hora=float(tr.coste_hora_eur),
                coste_fijo_dia=float(tr.coste_fijo_dia_eur),
                co2_vacio=float(t.kgco2e_km_vacio), co2_lleno=float(t.kgco2e_km_lleno), rango=int(t.rango),
            ))
    return pool


def minutos_pausas(km: float) -> float:
    """Aproximación lineal de las pausas (20 min / 200 km) para el solver.
    La simulación posterior las coloca exactamente."""
    return km / C.PAUSA_CADA_KM * C.PAUSA_MIN


# Portafolio de configuraciones: la búsqueda local de OR-Tools se queda atascada según
# la solución inicial (p.ej. empieza con furgonetas y no consolida en camiones), así que
# cada planta se resuelve con varias y se elige la mejor. Ver CLAUDE.md.
PORTAFOLIO = [
    ("LOCAL_CHEAPEST_INSERTION", frozenset()),
    ("SAVINGS", frozenset()),
    ("PARALLEL_CHEAPEST_INSERTION", frozenset()),
    ("LOCAL_CHEAPEST_INSERTION", frozenset({"VEH-FUR"})),
    ("SAVINGS", frozenset({"VEH-FUR"})),
]


def resolver_planta(d: Datos, planta: str, visitas: list[Visita], segundos: int = 30,
                    precio_co2_eur_t: float = C.PRECIO_CO2_EUR_T,
                    estrategia: str = "LOCAL_CHEAPEST_INSERTION", excluir: frozenset = frozenset(),
                    tipos: set[str] | None = None, dias_max: int | None = None,
                    previo: list[dict] | None = None) -> dict:
    """planta = id del depósito (planta en el troncal, almacén en el reparto).

    previo: rutas de un plan anterior de este depósito, [{"id": id de vehículo, "tarifa": ..., "visitas": [ids]}].
    Se usan como solución inicial (arranque en caliente) si siguen siendo válidas; si no, se descartan."""
    P = d.P
    H = int(P["horizonte_planificacion_dias"])
    if dias_max is None:
        dias_max = int(P["dias_max_ruta"]) if P.get("permitir_rutas_multidia", 0) else 1
    carga = int(P["tiempo_carga_planta_min"])
    p_ini, p_fin = d.horario_deposito(planta)

    pool = construir_pool(d, planta, visitas, excluir, tipos)
    if not pool:   # OR-Tools se cae (access violation) con 0 vehículos
        return {"planta": planta, "estado": "SIN_VEHICULOS", "rutas": [], "no_servidas": [v.id for v in visitas],
                "objetivo_eur": float("inf"), "config": f"{estrategia} sin vehículos"}
    lugares = [planta] + [v.tienda for v in visitas]
    n = len(lugares)
    KM = [[0.0] * n for _ in range(n)]
    DR = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            km, mins = d.dist(lugares[i], lugares[j])
            KM[i][j], DR[i][j] = km, int(round(mins))
    servicio = [carga] + [v.descarga for v in visitas]
    T = [[servicio[i] + DR[i][j] + int(round(minutos_pausas(KM[i][j]))) if i != j else 0
          for j in range(n)] for i in range(n)]

    man = pywrapcp.RoutingIndexManager(n, len(pool), 0)
    rt = pywrapcp.RoutingModel(man)

    # --- coste de arco por vehículo (céntimos) -------------------------------------------
    cache = {}
    for vi, veh in enumerate(pool):
        co2_medio = (veh.co2_vacio + veh.co2_lleno) / 2
        eur_km = veh.coste_km + precio_co2_eur_t / 1000 * co2_medio
        key = round(eur_km, 6)
        if key not in cache:
            M = [[int(round(KM[i][j] * eur_km * 100)) for j in range(n)] for i in range(n)]
            cache[key] = rt.RegisterTransitCallback(
                lambda a, b, M=M: M[man.IndexToNode(a)][man.IndexToNode(b)])
        rt.SetArcCostEvaluatorOfVehicle(cache[key], vi)
        rt.SetFixedCostOfVehicle(int(round(veh.coste_fijo_dia * 100)), vi)

    # --- capacidades: kg y cajas ---------------------------------------------------------
    kg = [0] + [int(math.ceil(v.kg)) for v in visitas]
    cj = [0] + [v.cajas for v in visitas]
    cb_kg = rt.RegisterUnaryTransitCallback(lambda a: kg[man.IndexToNode(a)])
    cb_cj = rt.RegisterUnaryTransitCallback(lambda a: cj[man.IndexToNode(a)])
    rt.AddDimensionWithVehicleCapacity(cb_kg, 0, [v.cap_kg for v in pool], True, "kg")
    rt.AddDimensionWithVehicleCapacity(cb_cj, 0, [v.cap_cajas for v in pool], True, "cajas")

    # --- tiempo (reloj de la semana, minuto 0 = lunes 00:00) ------------------------------
    cb_t = rt.RegisterTransitCallback(lambda a, b: T[man.IndexToNode(a)][man.IndexToNode(b)])
    horizonte = (H + 1) * 1440          # se permite volver a planta el sábado
    rt.AddDimension(cb_t, 3 * 1440, horizonte, False, "tiempo")
    tiempo = rt.GetDimensionOrDie("tiempo")
    # trabajo efectivo (sin esperas) y conducción
    rt.AddDimension(cb_t, 0, int(P["jornada_max_h"] * 60 * dias_max), True, "trabajo")
    trabajo = rt.GetDimensionOrDie("trabajo")
    cb_dr = rt.RegisterTransitCallback(lambda a, b: DR[man.IndexToNode(a)][man.IndexToNode(b)])
    rt.AddDimension(cb_dr, 0, int(P["conduccion_max_dia_h"] * 60 * dias_max), True, "conduccion")

    pernocta_cmin = int(round(P["coste_pernocta_eur"] / 1440 * 100))  # céntimos/min fuera de planta
    for vi, veh in enumerate(pool):
        hora_cmin = int(round(veh.coste_hora / 60 * 100))
        tiempo.SetSpanCostCoefficientForVehicle(pernocta_cmin, vi)
        trabajo.SetSpanCostCoefficientForVehicle(max(0, hora_cmin - pernocta_cmin), vi)
        tiempo.SetSpanUpperBoundForVehicle(dias_max * 1440, vi)
        # salida: carga dentro del horario de planta, cualquier día laborable
        s = tiempo.CumulVar(rt.Start(vi))
        s.SetRange(p_ini, (H - 1) * 1440 + p_fin - carga)
        for dd in range(H - 1):
            s.RemoveInterval(dd * 1440 + p_fin - carga + 1, (dd + 1) * 1440 + p_ini - 1)

    # --- descanso nocturno obligatorio (11 h, empieza entre 19:00 y 22:00) ------------------
    # Si la ruta pasa la noche fuera, el conductor no puede circular ni descargar durante
    # el descanso. Los descansos antes de salir o después de volver no afectan.
    if dias_max > 1:
        slv = rt.solver()
        visita_transit = [0] * rt.Size()
        for idx in range(rt.Size()):
            visita_transit[idx] = servicio[man.IndexToNode(idx)]
        for vi in range(len(pool)):
            noches = [slv.FixedDurationIntervalVar(dd * 1440 + 19 * 60, dd * 1440 + 22 * 60,
                                                   C.DESCANSO_NOCTURNO_MIN, False, f"noche{vi}_{dd}")
                      for dd in range(H)]
            tiempo.SetBreakIntervalsOfVehicle(noches, vi, visita_transit)

    # --- ventanas de las tiendas + compatibilidad de vehículo + visita opcional -------------
    penal = int(C.PENALIZACION_NO_SERVIR_EUR * 100)
    rango_max = {t: C.ORDEN_TIPOS.index(t) for t in C.ORDEN_TIPOS}
    for k, v in enumerate(visitas, start=1):
        idx = man.NodeToIndex(k)
        c = tiempo.CumulVar(idx)
        cierre = max(v.ini, v.fin - v.descarga - C.MARGEN_VENTANA_MIN)
        abiertos = [(dd * 1440 + v.ini, dd * 1440 + cierre) for dd in v.dias]
        c.SetRange(abiertos[0][0], abiertos[-1][1])
        for (a0, a1), (b0, _) in zip(abiertos, abiertos[1:]):
            c.RemoveInterval(a1 + 1, b0 - 1)
        permitidos = [vi for vi, veh in enumerate(pool) if veh.rango <= rango_max[v.vehiculo_max]]
        rt.VehicleVar(idx).SetValues([-1] + permitidos)
        rt.AddDisjunction([idx], penal + 100 * int(v.kg))

    # --- las entregas de una misma tienda van en rutas distintas ---------------------------------
    # (si no, el solver junta la entrega de L-M y la de X-V en una ruta y el conductor duerme
    #  junto a la tienda para descargar la segunda mitad al día siguiente)
    slv = rt.solver()
    por_tienda: dict[str, list[int]] = {}
    for k, v in enumerate(visitas, start=1):
        por_tienda.setdefault(v.tienda, []).append(man.NodeToIndex(k))
    for idxs in por_tienda.values():
        for i, a in enumerate(idxs):
            for b in idxs[i + 1:]:
                va, vb = rt.VehicleVar(a), rt.VehicleVar(b)
                # distinto vehículo, o al menos una de las dos sin servir (vehículo = -1)
                slv.Add(slv.IsDifferentVar(va, vb) + slv.IsEqualCstVar(va, -1) >= 1)

    for vi in range(len(pool)):
        rt.AddVariableMinimizedByFinalizer(tiempo.CumulVar(rt.Start(vi)))
        rt.AddVariableMinimizedByFinalizer(tiempo.CumulVar(rt.End(vi)))

    sp = pywrapcp.DefaultRoutingSearchParameters()
    sp.first_solution_strategy = getattr(routing_enums_pb2.FirstSolutionStrategy, estrategia)
    sp.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH

    def extraer(sol):
        """Copia lo que interesa de la solución (el objeto de OR-Tools se reutiliza entre búsquedas)."""
        rutas, servidas, por_vehiculo = [], set(), {}
        for vi, veh in enumerate(pool):
            if not rt.IsVehicleUsed(sol, vi):
                continue
            idx, paradas, nodos = rt.Start(vi), [], []
            salida = sol.Min(tiempo.CumulVar(idx))
            idx = sol.Value(rt.NextVar(idx))
            while not rt.IsEnd(idx):
                nodo = man.IndexToNode(idx)
                paradas.append({"visita": visitas[nodo - 1], "llegada_solver": sol.Min(tiempo.CumulVar(idx))})
                servidas.add(nodo)
                nodos.append(nodo)
                idx = sol.Value(rt.NextVar(idx))
            por_vehiculo[vi] = nodos
            rutas.append({"vehiculo": veh, "salida": salida, "paradas": paradas,
                          "vuelta_solver": sol.Min(tiempo.CumulVar(idx))})
        return {"obj": sol.ObjectiveValue(), "rutas": rutas, "servidas": servidas, "por_vehiculo": por_vehiculo}

    def leer_rutas(por_vehiculo):
        """Rutas {vehículo: [nodos]} -> asignación inicial de OR-Tools, o None si no es válida."""
        rutas = [[man.NodeToIndex(k) for k in por_vehiculo.get(vi, [])] for vi in range(len(pool))]
        try:
            return rt.ReadAssignmentFromRoutes(rutas, True)
        except Exception:  # noqa: BLE001 — una solución previa inservible nunca debe romper el cálculo
            return None

    sp.time_limit.seconds = max(1, int(segundos))
    rt.CloseModelWithParameters(sp)

    # --- arranque en caliente: rutas de un plan anterior ----------------------------------------------------
    # Una sola validación (ReadAssignmentFromRoutes es cara en el troncal por los descansos nocturnos):
    # si el plan previo ya no es factible con los pedidos de ahora, se arranca de cero.
    arranque = None
    if previo:
        nodo_de = {v.id: k for k, v in enumerate(visitas, start=1)}
        ids_pool = {veh.id: vi for vi, veh in enumerate(pool)}
        usados, usadas_visitas, inicial = set(), set(), {}
        for r in previo:
            nodos = [nodo_de[x] for x in r["visitas"] if x in nodo_de and nodo_de[x] not in usadas_visitas]
            if not nodos:
                continue
            vi = ids_pool.get(r["id"])
            if vi is None or vi in usados:   # el vehículo ya no existe en el pool: otro libre de la misma tarifa
                vi = next((k for k, veh in enumerate(pool) if veh.tarifa == r.get("tarifa") and k not in usados), None)
            if vi is None:
                continue
            inicial[vi] = nodos
            usados.add(vi)
            usadas_visitas.update(nodos)
        # solo si el plan previo cubre casi todas las visitas de ahora: si no, validarlo cuesta tiempo y no encaja
        cubiertas = len(usadas_visitas) / len(visitas) if visitas else 0
        arranque = leer_rutas(inicial) if inicial and cubiertas >= 0.8 else None
    arranque_caliente = arranque is not None

    # --- búsqueda por tandas con parada temprana ---------------------------------------------------------
    # La búsqueda local converge pronto y el resto del tiempo apenas mejora: se resuelve en tandas de
    # ~1/3 del tiempo, cada una partiendo de la mejor solución, y se para cuando una tanda no mejora.
    tanda = max(2, math.ceil(segundos / 3))
    restante = int(segundos)
    mejor = None
    while restante >= 1:
        sp.time_limit.seconds = min(tanda, restante)
        restante -= min(tanda, restante)
        sol = rt.SolveFromAssignmentWithParameters(arranque, sp) if arranque is not None else             rt.SolveWithParameters(sp)
        if sol is None and arranque is not None:     # el arranque en caliente no sirvió: empieza de cero
            arranque, arranque_caliente = None, False
            sol = rt.SolveWithParameters(sp)
        if sol is None:
            break
        nuevo = extraer(sol)
        mejora = (mejor["obj"] - nuevo["obj"]) if mejor else float("inf")
        if mejor is None or nuevo["obj"] < mejor["obj"]:
            mejor = nuevo
        if mejora <= max(1.0, 0.001 * abs(mejor["obj"])):   # < 0,1 %: converge
            break
        arranque = sol    # la siguiente tanda continúa desde esta solución
    if mejor is None:
        return {"planta": planta, "estado": "SIN_SOLUCION", "rutas": [], "no_servidas": [v.id for v in visitas],
                "objetivo_eur": float("inf"), "config": f"{estrategia} sin {sorted(excluir)}"}

    rutas, servidas = mejor["rutas"], mejor["servidas"]
    no_serv = [visitas[k - 1].id for k in range(1, n) if k not in servidas]
    return {"planta": planta, "estado": "OK", "objetivo_eur": mejor["obj"] / 100,
            "rutas": rutas, "no_servidas": no_serv, "pool": len(pool), "arranque_caliente": arranque_caliente,
            "segundos_usados": int(segundos) - restante,
            "config": f"{estrategia} sin {sorted(excluir)}" if excluir else estrategia}
