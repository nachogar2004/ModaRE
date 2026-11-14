"""Orquestador del reparto en dos escalones:

    pedidos -> visitas a tiendas -> asignación tienda→almacén→planta (CP-SAT)
            -> VRP troncal por planta (planta → almacenes, camiones grandes)
            -> VRP de reparto por almacén (almacén → tiendas, vehículos que entran en ciudad)
            -> simulación exacta de cada ruta -> KPIs

Supuesto de sincronización: los almacenes trabajan con stock. La reposición troncal de la semana W
cubre el reparto de la semana W+1, así que los dos escalones se planifican a la vez sin esperar uno al otro.
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from . import config as C
from . import pedidos as PED
from .asignacion import asignar, plantas_activas
from .datos import cargar_datos, construir_visitas, construir_visitas_troncal, demanda_estimada
from .osrm import añadir_geometrias
from .simulacion import flota_necesaria, simular_ruta
from .vrp import PORTAFOLIO, resolver_planta

_D = None  # datos cargados una vez por proceso trabajador


def _init_trabajador():
    global _D
    _D = cargar_datos()


def _resolver_y_simular(args):
    escalon, dep, visitas, segundos, precio_co2, estrategia, excluir, tipos = args
    d = _D if _D is not None else cargar_datos()
    res = resolver_planta(d, dep, visitas, segundos, precio_co2, estrategia, excluir, tipos)
    res["rutas"] = [simular_ruta(d, dep, r) for r in res["rutas"]]
    res["escalon"] = escalon
    return res


def _mejor(resultados: list[dict]) -> dict:
    """Menos visitas sin servir y, a igualdad, menor coste real simulado."""
    return min(resultados, key=lambda r: (len(r["no_servidas"]), sum(x["coste_eur"] for x in r["rutas"])))


def _segundos_reparto(segundos: int, n_visitas: int) -> int:
    """Los almacenes tienen pocas tiendas: tiempo de búsqueda proporcional al tamaño."""
    return max(3, min(segundos, math.ceil(segundos * n_visitas / 25)))


def _kpis(rutas: list[dict]) -> dict:
    flota = flota_necesaria(rutas)
    return {
        "rutas": len(rutas), "camiones": sum(f["camiones"] for f in flota),
        "km": round(sum(r["km"] for r in rutas), 1),
        "coste_eur": round(sum(r["coste_eur"] for r in rutas), 2),
        "litros": round(sum(r["litros"] for r in rutas), 1),
        "co2_kg": round(sum(r["co2_kg"] for r in rutas), 1),
        "pernoctas": sum(r["noches"] for r in rutas),
        "ocupacion_media_cajas": round(sum(r["ocupacion_cajas"] for r in rutas) / len(rutas), 3) if rutas else 0,
    }


def planificar(semana: str | None = None, *, fuente: str = "mixto", asignacion: str = "optimo",
               plantas_futuras: bool = False, segundos: int = 30, precio_co2: float = C.PRECIO_CO2_EUR_T,
               solo_muestra: bool = False, paralelo: bool = True, log=print) -> dict:
    """
    fuente: 'pedidos'    -> solo lo que han pedido las tiendas en la app
            'estimacion' -> demanda estimada por ventas (sin app)
            'mixto'      -> pedido si existe; si no, estimación (por defecto)
    segundos: tiempo de búsqueda por planta en el troncal (los almacenes usan menos, según su tamaño)
    """
    t0 = time.time()
    semana = semana or PED.semana_actual()
    d = cargar_datos()
    estim = demanda_estimada(d, PED.num_semana(semana))
    peds = PED.de_semana(semana)
    if fuente == "pedidos":
        demanda = {k: v["kg"] for k, v in peds.items()}
    elif fuente == "estimacion":
        demanda = estim
    else:
        demanda = {**estim, **{k: v["kg"] for k, v in peds.items()}}
    if solo_muestra:
        muestra = set(d.tiendas[d.tiendas.en_muestra == 1].id_tienda)
        demanda = {k: v for k, v in demanda.items() if k in muestra}

    visitas, avisos = construir_visitas(d, demanda)
    plantas = plantas_activas(d, plantas_futuras)
    uso = asignar(d, visitas, plantas, asignacion)
    troncal = construir_visitas_troncal(d, visitas, uso["almacen_planta"])
    log(f"[{semana}] {len(demanda)} tiendas con demanda, {len(visitas)} entregas a tienda desde "
        f"{len(uso['almacenes'])} almacenes, {len(troncal)} reposiciones troncales desde {plantas}")

    tipos_troncal = set(str(d.P["tipos_troncal"]).split(";"))
    trabajos = []
    por_planta = defaultdict(list)
    for v in troncal:
        por_planta[v.deposito].append(v)
    for p, vs in por_planta.items():
        for e, x in (PORTAFOLIO[:3] if paralelo else PORTAFOLIO[:1]):
            trabajos.append(("troncal", p, vs, segundos, precio_co2, e, x, tipos_troncal))
    por_almacen = defaultdict(list)
    for v in visitas:
        por_almacen[v.deposito].append(v)
    for a, vs in por_almacen.items():
        port = PORTAFOLIO if len(vs) > 6 else PORTAFOLIO[:2]
        for e, x in (port if paralelo else port[:1]):
            trabajos.append(("reparto", a, vs, _segundos_reparto(segundos, len(vs)), precio_co2, e, x, None))
    trabajos.sort(key=lambda t: -t[3])   # los largos primero: reparte mejor la carga entre procesos
    if paralelo and len(trabajos) > 1:
        with ProcessPoolExecutor(max_workers=min(len(trabajos), os.cpu_count() or 4),
                                 initializer=_init_trabajador) as ex:
            todos = list(ex.map(_resolver_y_simular, trabajos))
    else:
        todos = [_resolver_y_simular(t) for t in trabajos]
    deps = list(por_planta) + list(por_almacen)
    resultados = [_mejor([r for r in todos if r["planta"] == dep]) for dep in deps]

    alm_planta = uso["almacen_planta"]
    rutas, no_servidas = [], []
    for r in resultados:
        if r["escalon"] == "troncal" or r["no_servidas"]:
            log(f"  {r['escalon']:7s} {r['planta']}: {len(r['rutas'])} rutas, "
                f"{len(r['no_servidas'])} sin servir  [{r['config']}]")
        for x in r["rutas"]:
            x["escalon"] = r["escalon"]
            x["origen"] = r["planta"]
            x["planta"] = r["planta"] if r["escalon"] == "troncal" else alm_planta[r["planta"]]
        rutas += r["rutas"]
        no_servidas += [(r["escalon"], i) for i in r["no_servidas"]]
    log(f"  reparto: {sum(1 for r in rutas if r['escalon'] == 'reparto')} rutas desde {len(por_almacen)} almacenes")

    vis_by_id = {v.id: v for v in visitas + troncal}
    for r in rutas:
        for p in r["paradas"]:
            nombre, mun, lat, lon = d.nombre_lugar(p["tienda"])
            p.update(nombre=nombre, municipio=mun, lat=lat, lon=lon)
    rep = [r for r in rutas if r["escalon"] == "reparto"]
    tro = [r for r in rutas if r["escalon"] == "troncal"]
    kg_dem = sum(v.kg for v in visitas)
    kg_ok = sum(r["kg_a_tiempo"] for r in rep)
    kg_tro = sum(v.kg for v in troncal)
    kg_tro_ok = sum(r["kg_a_tiempo"] for r in tro)
    pl = d.plantas.set_index("id_planta")
    al = d.almacenes.set_index("id_almacen")
    resumen = {
        "semana": semana, "fuente": fuente, "asignacion": asignacion, "segundos_por_planta": segundos,
        "precio_co2_eur_t": precio_co2,
        "tiendas_con_demanda": len(demanda), "pedidos_app": len(peds), "visitas": len(visitas),
        "almacenes_usados": len(uso["almacenes"]), "reposiciones_troncal": len(troncal),
        "kg_demandados": round(kg_dem, 1), "kg_a_tiempo": round(kg_ok, 1),
        "nivel_servicio": round(kg_ok / kg_dem, 4) if kg_dem else None,
        "nivel_servicio_troncal": round(kg_tro_ok / kg_tro, 4) if kg_tro else None,
        "nivel_servicio_objetivo": d.P["nivel_servicio_objetivo"],
        **_kpis(rutas),
        "troncal": _kpis(tro), "reparto": _kpis(rep),
        "segundos_calculo": round(time.time() - t0, 1),
    }
    resumen["eur_por_kg"] = round(resumen["coste_eur"] / kg_ok, 3) if kg_ok else None
    plan = {
        "resumen": resumen, "flota": flota_necesaria(rutas),
        "plantas": [{"id": p, "nombre": pl.loc[p].nombre, "lat": pl.loc[p].lat, "lon": pl.loc[p].lon, **u}
                    for p, u in uso["plantas"].items()],
        "almacenes": [{"id": a, "nombre": al.loc[a].nombre, "lat": al.loc[a].lat, "lon": al.loc[a].lon,
                       "poligono": str(al.loc[a].get("poligono", "") or ""),
                       "municipio": str(al.loc[a].get("municipio", "") or ""), **u}
                      for a, u in sorted(uso["almacenes"].items())],
        "rutas": rutas,
        "no_servidas": [{"escalon": esc, "visita": i, "tienda": vis_by_id[i].tienda, "kg": vis_by_id[i].kg,
                         "nombre": d.nombre_lugar(vis_by_id[i].tienda)[0]} for esc, i in no_servidas],
        "avisos": avisos,
    }
    añadir_geometrias(plan, log)   # trazado por carretera de cada tramo (para el mapa)
    guardar(plan)
    return plan


def nombrar_rutas(plan: dict):
    """Ordena las rutas (troncal primero, luego por depósito y hora de salida) y les da un nombre legible:
    T-MAD-01 = troncal desde la planta de Madrid; R-SEVILLA-02 = reparto desde el almacén de Sevilla.
    El id interno del solver (depósito/tarifa/índice del pool) se conserva en r["id"]."""
    orden = {"troncal": 0, "reparto": 1}
    plan["rutas"].sort(key=lambda r: (orden[r.get("escalon", "reparto")], r.get("origen", r["planta"]),
                                      r["t_salida"]))
    cuenta = defaultdict(int)
    for r in plan["rutas"]:
        origen = r.get("origen", r["planta"])
        cuenta[origen] += 1
        pref = "T" if r.get("escalon") == "troncal" else "R"
        r["nombre"] = f"{pref}-{origen.split('-', 1)[1]}-{cuenta[origen]:02d}"


def guardar(plan: dict):
    from .visualizar import escribir_mapa
    nombrar_rutas(plan)
    out = C.SALIDAS_DIR / plan["resumen"]["semana"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    # utf-8-sig para que Excel muestre bien las tildes
    with open(out / "paradas.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ruta", "escalon", "origen", "planta", "tipo", "operador", "salida", "orden", "visita",
                    "destino", "nombre", "municipio", "kg", "cajas", "llegada", "inicio_descarga", "ventana",
                    "a_tiempo", "id_solver"])
        for r in plan["rutas"]:
            for i, p in enumerate(r["paradas"], 1):
                w.writerow([r["nombre"], r.get("escalon"), r.get("origen"), r["planta"], r["tipo"], r["operador"],
                            r["salida"], i, p["visita"], p["tienda"], p["nombre"], p["municipio"], p["kg"],
                            p["cajas"], p["llegada"], p["inicio_descarga"], p["ventana"], p["a_tiempo"], r["id"]])
    with open(out / "rutas.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        cols = ["tipo", "operador", "salida", "vuelta", "dias_ruta", "noches", "n_paradas", "kg", "cajas",
                "ocupacion_kg", "ocupacion_cajas", "km", "horas_trabajo", "pausas_20min", "coste_eur",
                "litros", "co2_kg", "kg_tarde"]
        w.writerow(["ruta", "escalon", "origen", "planta", *cols, "recorrido", "id_solver"])
        for r in plan["rutas"]:
            o = r.get("origen", r["planta"])
            recorrido = " > ".join([o, *(p["tienda"] for p in r["paradas"]), o])
            w.writerow([r["nombre"], r.get("escalon"), o, r["planta"], *(r[c] for c in cols), recorrido, r["id"]])
    with open(out / "almacenes.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["almacen", "nombre", "planta", "tiendas", "kg_semana", "capacidad_kg_semana", "uso"])
        for a in plan.get("almacenes", []):
            w.writerow([a["id"], a["nombre"], a["planta"], a["tiendas"], a["kg"], a["capacidad"],
                        round(a["kg"] / a["capacidad"], 3)])
    escribir_mapa(plan, out)


def cargar_plan(semana: str) -> dict | None:
    f = C.SALIDAS_DIR / semana / "plan.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
