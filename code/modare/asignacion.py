"""Etapa 1 — Asignación en dos escalones: tienda → almacén → planta (CP-SAT).

Los camiones grandes no entran en las ciudades, así que no hay trayecto planta → tienda:
la planta repone almacenes (escalón troncal) y cada almacén reparte a sus tiendas (escalón de reparto).

    min  Σ_s Σ_a  c_rep · km(a,s) · kg_s · x[s,a]          (reparto almacén → tienda)
       + Σ_a Σ_p  c_tro · km(p,a) · f[a,p]                  (troncal planta → almacén)
    s.a. Σ_a x[s,a] = 1                         ∀ tienda s   (cada tienda la sirve un almacén)
         Σ_s kg_s · x[s,a] ≤ capacidad_a        ∀ almacén a
         Σ_p y[a,p] ≤ 1                         ∀ almacén a  (cada almacén lo repone una sola planta)
         Σ_p f[a,p] = Σ_s kg_s · x[s,a]         ∀ almacén a  (conservación del flujo)
         f[a,p] ≤ capacidad_a · y[a,p]
         Σ_a f[a,p] ≤ capacidad_salida_p        ∀ planta p
         y[a,p] = 0 si p no está activa

c_rep y c_tro son €/(kg·km) de referencia: €/km de la tarifa A ÷ carga útil efectiva del vehículo típico
de cada escalón (camión de 7,5 t en reparto, el troncal más grande en el troncal). Así el modelo sabe que
mover un kg un km en tráiler es más barato que en camión pequeño.

Después el VRP se descompone por depósito (cluster-first, route-second): un VRP troncal por planta y un
VRP de reparto por almacén.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from ortools.sat.python import cp_model

from . import config as C
from .datos import Datos, Visita

CANDIDATOS_ALMACEN = 5   # cada tienda solo puede ir a sus 5 almacenes más cercanos (modelo más pequeño)


def plantas_activas(d: Datos, incluir_futuras: bool = False) -> list[str]:
    df = d.plantas if incluir_futuras else d.plantas[d.plantas.activa == 1]
    return list(df.id_planta)


def coste_kg_km(d: Datos, tipo: str) -> float:
    """€/(kg·km) del operador A para un tipo de vehículo lleno (limitado por kg o por cajas)."""
    t = d.tipos.set_index("id_tipo").loc[tipo]
    tar = d.tarifas[(d.tarifas.id_tipo == tipo) & (d.tarifas.operador == "Operador A")].iloc[0]
    carga = min(t.carga_util_kg, t.cap_cajas * C.KG_POR_CAJA)
    return float(tar.coste_km_eur) / carga


def asignar(d: Datos, visitas: list[Visita], plantas: list[str], modo: str = "optimo") -> dict:
    """Rellena visita.deposito (almacén) y visita.planta. modo='actual': cada tienda a su almacén provincial
    y cada almacén a la planta_actual mayoritaria (en kg) de sus tiendas."""
    kg_tienda = defaultdict(float)
    for v in visitas:
        kg_tienda[v.tienda] += v.kg
    tiendas = list(kg_tienda)
    almacenes = list(d.almacenes.id_almacen)
    cap_p = dict(zip(d.plantas.id_planta, d.plantas.capacidad_salida_kg_semana))
    cap_a = dict(zip(d.almacenes.id_almacen, d.almacenes.capacidad_kg_semana))

    if modo == "actual":
        prov_alm = dict(zip(d.almacenes.provincia, d.almacenes.id_almacen))
        t = d.tiendas.set_index("id_tienda")
        asig_t = {s: prov_alm.get(t.loc[s].provincia) or min(almacenes, key=lambda a: d.dist(a, s)[0])
                  for s in tiendas}
        votos = defaultdict(Counter)
        for s, a in asig_t.items():
            votos[a][t.loc[s].planta_actual] += kg_tienda[s]
        asig_a = {}
        for a, c in votos.items():
            p = next((p for p, _ in c.most_common() if p in plantas), None)
            asig_a[a] = p or min(plantas, key=lambda q: d.dist(q, a)[0])
    else:
        c_rep = coste_kg_km(d, d.P["vehiculo_max_centro_urbano"])
        c_tro = coste_kg_km(d, max(str(d.P["tipos_troncal"]).split(";"), key=C.ORDEN_TIPOS.index))
        cand = {s: sorted(almacenes, key=lambda a: d.dist(a, s)[0])[:CANDIDATOS_ALMACEN] for s in tiendas}
        m = cp_model.CpModel()
        x = {(s, a): m.NewBoolVar(f"x_{s}_{a}") for s in tiendas for a in cand[s]}
        y = {(a, p): m.NewBoolVar(f"y_{a}_{p}") for a in almacenes for p in plantas}
        f = {(a, p): m.NewIntVar(0, int(cap_a[a]), f"f_{a}_{p}") for a in almacenes for p in plantas}
        kg_i = {s: int(round(kg_tienda[s])) for s in tiendas}
        entra = defaultdict(list)
        for (s, a), var in x.items():
            entra[a].append(kg_i[s] * var)
        for s in tiendas:
            m.AddExactlyOne(x[s, a] for a in cand[s])
        for a in almacenes:
            carga = sum(entra[a])
            m.Add(carga <= int(cap_a[a]))
            m.Add(sum(y[a, p] for p in plantas) <= 1)
            m.Add(sum(f[a, p] for p in plantas) == carga)
            for p in plantas:
                m.Add(f[a, p] <= int(cap_a[a]) * y[a, p])
        for p in plantas:
            m.Add(sum(f[a, p] for a in almacenes) <= int(cap_p[p]))
        # objetivo en milésimas de euro
        m.Minimize(
            sum(int(1000 * c_rep * d.dist(a, s)[0] * kg_tienda[s]) * var for (s, a), var in x.items())
            + sum(int(round(1000 * c_tro * d.dist(p, a)[0])) * f[a, p] for a in almacenes for p in plantas))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 30
        solver.parameters.num_workers = 8
        st = solver.Solve(m)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError("Asignación infactible: la demanda supera la capacidad de almacenes o plantas")
        asig_t = {s: a for (s, a), var in x.items() if solver.Value(var)}
        asig_a = {a: p for (a, p), var in y.items() if solver.Value(var) and solver.Value(f[a, p]) > 0}

    for v in visitas:
        v.deposito = asig_t[v.tienda]
        v.planta = asig_a[v.deposito]

    uso_a = defaultdict(float)
    for s, a in asig_t.items():
        uso_a[a] += kg_tienda[s]
    uso_p = defaultdict(float)
    for a, kg in uso_a.items():
        uso_p[asig_a[a]] += kg
    return {
        "plantas": {p: {"kg": round(uso_p[p], 1), "capacidad": cap_p[p],
                        "almacenes": sum(1 for a in uso_a if asig_a[a] == p),
                        "tiendas": sum(1 for a in asig_t.values() if asig_a[a] == p)} for p in plantas},
        "almacenes": {a: {"kg": round(uso_a[a], 1), "capacidad": cap_a[a], "planta": asig_a[a],
                          "tiendas": sum(1 for q in asig_t.values() if q == a)} for a in uso_a},
        "almacen_planta": {a: asig_a[a] for a in uso_a},
    }
