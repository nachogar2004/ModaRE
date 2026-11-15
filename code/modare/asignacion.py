"""Etapa 1 — Qué almacenes abrir y asignación tienda → almacén → planta (CP-SAT).

Los camiones grandes no entran en las ciudades, así que no hay trayecto planta → tienda:
la planta repone almacenes (escalón troncal) y cada almacén reparte a sus tiendas (escalón de reparto).
Los almacenes tienen coste (data/almacenes.csv): un fijo semanal si están abiertos y un €/kg de manipulación.

    min  Σ_a  fijo_a · z[a]                                   (almacenes abiertos)
       + Σ_a  manip_a · Σ_s kg_s · x[s,a]                     (manipulación en el almacén)
       + Σ_s Σ_a  R(s,a) · x[s,a]                              (reparto almacén → tienda)
       + Σ_a Σ_p  T(p,a) · f[a,p]                              (troncal planta → almacén, por kg)
    s.a. Σ_a x[s,a] = 1                         ∀ tienda s   (cada tienda la sirve un almacén…)
         x[s,a] ≤ z[a]                                       (…abierto…)
         x[s,a] = 0 si min(a,s) > max_min_almacen_tienda      (…a menos de 3 h: el reparto es de 1 día)
         Σ_s kg_s · x[s,a] ≤ capacidad_a        ∀ almacén a
         Σ_p f[a,p] = Σ_s kg_s · x[s,a]         ∀ almacén a  (conservación del flujo)
         Σ_a f[a,p] ≤ capacidad_salida_p        ∀ planta p   (solo plantas activas)
         f[a,p] solo existe si el almacén a no ha vetado la planta p   (tabla almacen_plantas_vetadas)
         x[s,a] = 0 si la tienda s ha vetado el almacén a              (tabla tienda_almacenes_vetados)

Estimación del transporte en € (calibrada con el coste real de las rutas, ver parametros.csv):
    R(s,a) = factor_coste_reparto × nº entregas_s × (2·km(a,s)·€/km + 2·h(a,s)·€/h)   camión de 7,5 t, operador A
    T(p,a) = factor_coste_troncal × (2·km(p,a)·€/km + 2·h(p,a)·€/h) / carga útil         camión de 12 t, operador A
Sin calibrar, la estimación kg×km se quedaba en ~4 % del coste real y el modelo habría cerrado casi todos
los almacenes por creer que el transporte es gratis.

Cualquier almacén puede recibir de cualquier planta que no haya vetado, y de varias a la vez (no se impone
abastecimiento único).

Después el VRP se descompone por depósito (cluster-first, route-second): un VRP troncal por planta y un
VRP de reparto por almacén abierto.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from ortools.sat.python import cp_model

from . import config as C
from .datos import Datos, Visita

TIPO_REPARTO = "VEH-R75"
TIPO_TRONCAL = "VEH-R12"


def plantas_activas(d: Datos, incluir_futuras: bool = False) -> list[str]:
    df = d.plantas if incluir_futuras else d.plantas[d.plantas.activa == 1]
    return list(df.id_planta)


def permitidas(d: Datos, almacen: str, plantas: list[str]) -> list[str]:
    """Plantas (de las activas en este escenario) que el almacén no ha vetado."""
    vetadas = d.plantas_vetadas.get(almacen, set())
    return [p for p in plantas if p not in vetadas]


def almacenes_utiles(d: Datos, plantas: list[str]) -> list[str]:
    """Almacenes que pueden recibir de alguna planta (no las han vetado todas)."""
    return [a for a in d.almacenes.id_almacen if permitidas(d, a, plantas)]


def almacenes_para(d: Datos, tienda: str, utiles: list[str]) -> list[str]:
    """Almacenes que pueden servir a la tienda (útiles y no vetados por ella)."""
    vetados = d.almacenes_vetados.get(tienda, set())
    return [a for a in utiles if a not in vetados]


def tiendas_sin_almacen(d: Datos, tiendas, plantas: list[str]) -> set[str]:
    """Tiendas que han vetado todos los almacenes que pueden recibir mercancía: no se pueden servir."""
    utiles = almacenes_utiles(d, plantas)
    return {s for s in tiendas if not almacenes_para(d, s, utiles)}


def _tarifa_a(d: Datos, tipo: str) -> tuple[float, float]:
    tar = d.tarifas[(d.tarifas.id_tipo == tipo) & (d.tarifas.operador == "Operador A")].iloc[0]
    return float(tar.coste_km_eur), float(tar.coste_hora_eur)


def coste_reparto_visita(d: Datos, a: str, s: str) -> float:
    """€ estimados de una entrega a la tienda s desde el almacén a (ida y vuelta, calibrado)."""
    eur_km, eur_h = _tarifa_a(d, TIPO_REPARTO)
    km, mins = d.dist(a, s)
    return d.P["factor_coste_reparto"] * (2 * km * eur_km + 2 * mins / 60 * eur_h)


def coste_troncal_kg(d: Datos, p: str, a: str) -> float:
    """€ estimados por kg llevado de la planta p al almacén a (ida y vuelta de un camión lleno, calibrado)."""
    eur_km, eur_h = _tarifa_a(d, TIPO_TRONCAL)
    t = d.tipos.set_index("id_tipo").loc[TIPO_TRONCAL]
    carga = min(t.carga_util_kg, t.cap_cajas * C.KG_POR_CAJA)
    km, mins = d.dist(p, a)
    return d.P["factor_coste_troncal"] * (2 * km * eur_km + 2 * mins / 60 * eur_h) / carga


def asignar(d: Datos, visitas: list[Visita], plantas: list[str], modo: str = "optimo",
            almacenes_modo: str = "todos") -> dict:
    """Rellena visita.deposito (almacén) y visita.planta (planta principal del almacén = la que más kg le envía).
    Devuelve los flujos planta→almacén en kg y el coste de cada almacén.

    almacenes_modo='todos' (por defecto, requisito del reto): hay exactamente un almacén por provincia peninsular
        con tiendas y están TODOS abiertos (se paga su fijo). Cada almacén sirve al menos una tienda si su provincia
        tiene demanda esa semana; las tiendas pueden ir al almacén de otra provincia si sale más barato.
    almacenes_modo='optimo' (escenario): el modelo decide qué almacenes abrir según su coste.
    modo='actual': cada tienda va al almacén de su provincia; cada almacén lo repone la planta_actual mayoritaria
        (en kg) de sus tiendas. Todos los almacenes abiertos.
    """
    kg_tienda = defaultdict(float)
    n_vis = Counter()
    for v in visitas:
        kg_tienda[v.tienda] += v.kg
        n_vis[v.tienda] += 1
    tiendas = list(kg_tienda)
    almacenes = list(d.almacenes.id_almacen)
    alm = d.almacenes.set_index("id_almacen")
    cap_p = dict(zip(d.plantas.id_planta, d.plantas.capacidad_salida_kg_semana))
    cap_a = alm.capacidad_kg_semana.to_dict()
    # un almacén dado de alta en la app de gestión aún no tiene costes en almacenes.csv: se le supone la mediana
    fijo = alm.coste_fijo_eur_semana.fillna(alm.coste_fijo_eur_semana.median()).fillna(0).to_dict()
    manip = alm.coste_manipulacion_eur_kg.fillna(alm.coste_manipulacion_eur_kg.median()).fillna(0).to_dict()
    prov_alm = dict(zip(d.almacenes.provincia, d.almacenes.id_almacen))
    prov_tienda = d.tiendas.set_index("id_tienda").provincia.to_dict()
    propio = {s: prov_alm.get(prov_tienda.get(s)) for s in tiendas}   # almacén de la provincia de la tienda
    todos = almacenes_modo == "todos" or modo == "actual"

    perm = {a: permitidas(d, a, plantas) for a in almacenes}
    con_planta = [a for a in almacenes if perm[a]]
    if not con_planta:
        raise RuntimeError("Todos los almacenes han vetado todas las plantas activas: revisa los vetos")
    opciones = {s: almacenes_para(d, s, con_planta) for s in tiendas}
    sin = [s for s in tiendas if not opciones[s]]
    if sin:   # el planificador ya las quita antes; por si se llama a asignar() directamente
        raise RuntimeError(f"Tiendas que han vetado todos los almacenes disponibles: {', '.join(sin)}")

    if modo == "actual":
        t = d.tiendas.set_index("id_tienda")
        # su almacén provincial; si no existe o está vetado, el más cercano que pueda servirla
        asig_t = {s: propio[s] if propio[s] in opciones[s]
                  else min(opciones[s], key=lambda q: d.dist(q, s)[0]) for s in tiendas}
        votos = defaultdict(Counter)
        for s, a in asig_t.items():
            votos[a][t.loc[s].planta_actual] += kg_tienda[s]
        flujo = {}
        for a, c in votos.items():
            p = next((p for p, _ in c.most_common() if p in perm[a]), None) or min(perm[a], key=lambda q: d.dist(q, a)[0])
            flujo[a] = {p: sum(kg_tienda[s] for s, q in asig_t.items() if q == a)}
    else:
        max_min = d.P["max_min_almacen_tienda"]
        cand = {}
        for s in tiendas:   # solo almacenes que la tienda no ha vetado y que alguna planta puede reponer
            cerca = [a for a in opciones[s] if d.dist(a, s)[1] <= max_min]
            cand[s] = cerca or [min(opciones[s], key=lambda a: d.dist(a, s)[1])]
            if propio[s] in opciones[s] and propio[s] not in cand[s]:
                cand[s].append(propio[s])          # su almacén provincial siempre es una opción
        cand_a = sorted({a for cs in cand.values() for a in cs})
        pares = [(a, p) for a in cand_a for p in perm[a]]   # un almacén no recibe de las plantas que ha vetado

        m = cp_model.CpModel()
        z = {a: m.NewBoolVar(f"z_{a}") for a in cand_a}
        x = {(s, a): m.NewBoolVar(f"x_{s}_{a}") for s in tiendas for a in cand[s]}
        f = {(a, p): m.NewIntVar(0, int(cap_a[a]), f"f_{a}_{p}") for a, p in pares}
        kg_i = {s: int(round(kg_tienda[s])) for s in tiendas}
        entra = defaultdict(list)
        for (s, a), var in x.items():
            entra[a].append(kg_i[s] * var)
            m.AddImplication(var, z[a])
        for s in tiendas:
            m.AddExactlyOne(x[s, a] for a in cand[s])
        for a in cand_a:
            carga = sum(entra[a])
            m.Add(carga <= int(cap_a[a]) * z[a])
            m.Add(sum(f[a, p] for p in perm[a]) == carga)
            if todos:
                m.Add(z[a] == 1)
                # un almacén abierto tiene que repartir algo si en su provincia hay demanda esta semana
                # (de alguna tienda que no lo haya vetado)
                if any(propio[s] == a and a in cand[s] for s in tiendas):
                    m.Add(sum(var for (s, b), var in x.items() if b == a) >= 1)
        for p in plantas:
            m.Add(sum(f[a, q] for a, q in pares if q == p) <= int(cap_p[p]))
        # objetivo en diezmilésimas de euro (el troncal por kg son pocos céntimos: hace falta resolución)
        u = lambda v: int(round(10000 * v))
        m.Minimize(
            sum(u(fijo[a]) * z[a] for a in cand_a)
            + sum(u(manip[a] * kg_tienda[s] + n_vis[s] * coste_reparto_visita(d, a, s)) * var
                  for (s, a), var in x.items())
            + sum(u(coste_troncal_kg(d, p, a)) * f[a, p] for a, p in pares))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 30
        solver.parameters.num_workers = 8
        st = solver.Solve(m)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError("Asignación infactible: la demanda supera la capacidad de los almacenes o de las "
                               "plantas que pueden reponerlos (revisa capacidades y vetos)")
        asig_t = {s: a for (s, a), var in x.items() if solver.Value(var)}
        flujo = defaultdict(dict)
        for (a, p), var in f.items():
            if solver.Value(var) > 0:
                flujo[a][p] = float(solver.Value(var))

    uso_a = defaultdict(float)
    for s, a in asig_t.items():
        uso_a[a] += kg_tienda[s]
    # flujos en kg reales (el modelo trabaja con kg enteros): se reescalan a lo que reparte el almacén
    flujos = {}
    for a, kg in uso_a.items():
        tot = sum(flujo[a].values())
        flujos[a] = {p: kg * v / tot for p, v in sorted(flujo[a].items(), key=lambda x: -x[1])}
    principal = {a: next(iter(fl)) for a, fl in flujos.items()}

    for v in visitas:
        v.deposito = asig_t[v.tienda]
        v.planta = principal[v.deposito]

    uso_p = defaultdict(float)
    for a, fl in flujos.items():
        for p, kg in fl.items():
            uso_p[p] += kg
    abiertos = almacenes if todos else sorted(uso_a)
    inactivos = sorted(set(abiertos) - set(uso_a))   # abiertos sin tiendas esta semana (pagan el fijo igual)
    return {
        "inactivos": {a: {"kg": 0, "capacidad": cap_a[a], "planta": "", "plantas": {}, "tiendas": 0,
                          "coste_fijo_eur": round(fijo[a], 2), "coste_manipulacion_eur": 0} for a in inactivos},
        "plantas": {p: {"kg": round(uso_p[p], 1), "capacidad": cap_p[p],
                        "almacenes": sum(1 for fl in flujos.values() if p in fl),
                        "tiendas": sum(1 for a in asig_t.values() if principal[a] == p)} for p in plantas},
        "almacenes": {a: {"kg": round(uso_a[a], 1), "capacidad": cap_a[a], "planta": principal[a],
                          "plantas": {p: round(kg, 1) for p, kg in flujos[a].items()},
                          "tiendas": sum(1 for q in asig_t.values() if q == a),
                          "coste_fijo_eur": round(fijo[a], 2),
                          "coste_manipulacion_eur": round(manip[a] * uso_a[a], 2)} for a in uso_a},
        "cerrados": sorted(set(almacenes) - set(abiertos)),
        "almacen_planta": principal,
        "flujos": flujos,
    }
