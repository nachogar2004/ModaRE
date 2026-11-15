"""Reparto del coste de transporte de un plan entre las tiendas (lo que se le imputa o cobra a cada una).

Criterio (decidido por Moda re-):
  * Troncal (planta → almacén): el coste de cada camión se reparte entre las tiendas que llevan ropa en él, en
    proporción a su peso. El camión deja kg en uno o varios almacenes; lo que deja en un almacén es la ropa de las
    tiendas que ese almacén reparte, así que la parte de cada tienda es
        coste_ruta × (kg que deja en su almacén / kg de toda la ruta) × (kg de la tienda / kg que reparte su almacén)
  * Reparto (almacén → tiendas): el coste de cada ruta se reparte entre sus tiendas en proporción a la distancia por
    carretera del almacén a cada tienda (tabla distancias; si falta, línea recta × factor_rodeo).

Los costes de los almacenes (alquiler y manipulación) no entran: es solo transporte. La suma de todas las tiendas
cuadra con el coste de transporte del plan, salvo lo que no se puede imputar (p. ej. un camión troncal a un almacén
sin tiendas en el plan), que se informa aparte.
"""
from __future__ import annotations

from collections import defaultdict

from .datos import haversine_km


def _distancias_desde_almacenes(almacenes: set[str]) -> dict[tuple[str, str], float]:
    """km por carretera (origen almacén) de la base de datos."""
    from .bd import leer
    if not almacenes:
        return {}
    d = leer("SELECT origen, destino, distancia_km FROM distancias WHERE origen LIKE 'AL-%'")
    d = d[d.origen.isin(almacenes)]
    return dict(zip(zip(d.origen, d.destino), d.distancia_km))


def _centimos(costes: dict[str, float]) -> dict[str, int]:
    """Redondea a céntimos de forma que la suma cuadre exactamente con el total (método del mayor resto): para
    facturar, lo que se cobra a las tiendas tiene que sumar lo que se paga a los transportistas."""
    exactos = {k: v * 100 for k, v in costes.items()}
    base = {k: int(v // 1) for k, v in exactos.items()}
    faltan = round(sum(exactos.values())) - sum(base.values())
    for k in sorted(exactos, key=lambda k: exactos[k] - base[k], reverse=True)[:max(0, faltan)]:
        base[k] += 1
    return base


def coste_por_tienda(plan: dict, km: dict[tuple[str, str], float] | None = None,
                     factor_rodeo: float | None = None) -> dict:
    """Devuelve {"tiendas": [fila por tienda], "troncal_eur", "reparto_eur", "imputado_eur", "sin_imputar_eur",
    "sin_imputar": [motivos]}. `km` y `factor_rodeo` se leen de la base si no se pasan (para pruebas)."""
    rutas = plan.get("rutas", [])
    reparto = [r for r in rutas if r.get("escalon") == "reparto"]
    troncal = [r for r in rutas if r.get("escalon") == "troncal"]

    # quién es cada tienda, qué almacén la sirve y cuántos kg recibe en el plan
    info: dict[str, dict] = {}
    kg_tienda: dict[str, float] = defaultdict(float)
    tiendas_de: dict[str, set] = defaultdict(set)
    for r in reparto:
        a = r["origen"]
        for p in r["paradas"]:
            s = p["tienda"]
            kg_tienda[s] += p["kg"]
            tiendas_de[a].add(s)
            t = info.setdefault(s, {"id": s, "nombre": p.get("nombre", s), "municipio": p.get("municipio", ""),
                                    "almacen": a, "lat": p.get("lat"), "lon": p.get("lon"), "entregas": 0})
            t["entregas"] += 1
    kg_almacen = {a: sum(kg_tienda[s] for s in ts) for a, ts in tiendas_de.items()}
    coste_t: dict[str, float] = defaultdict(float)
    coste_r: dict[str, float] = defaultdict(float)
    sin_imputar, motivos = 0.0, []

    # --- troncal: por peso
    for r in troncal:
        kg_ruta = sum(p["kg"] for p in r["paradas"])
        if not kg_ruta:
            sin_imputar += r["coste_eur"]
            motivos.append(f"{r.get('nombre', r['id'])}: ruta troncal sin carga")
            continue
        for p in r["paradas"]:
            a = p["tienda"]                       # en el troncal, la «tienda» de la parada es el almacén
            parte = r["coste_eur"] * p["kg"] / kg_ruta
            if not kg_almacen.get(a):
                sin_imputar += parte
                motivos.append(f"{r.get('nombre', r['id'])}: el almacén {a} no reparte a ninguna tienda en este plan")
                continue
            for s in tiendas_de[a]:
                coste_t[s] += parte * kg_tienda[s] / kg_almacen[a]

    # --- reparto: por distancia almacén → tienda
    if km is None:
        km = _distancias_desde_almacenes({r["origen"] for r in reparto})
    if factor_rodeo is None:
        try:
            from .datos import cargar_parametros
            factor_rodeo = float(cargar_parametros().get("factor_rodeo", 1.26))
        except Exception:  # noqa: BLE001 — sin base de datos (pruebas): el valor por defecto
            factor_rodeo = 1.26
    coords_alm = {a["id"]: (a.get("lat"), a.get("lon")) for a in plan.get("almacenes", [])}

    def distancia(a: str, s: str) -> float:
        if (a, s) in km:
            return float(km[(a, s)])
        (la, oa), t = coords_alm.get(a, (None, None)), info[s]
        if None in (la, oa, t["lat"], t["lon"]):
            return 0.0
        return haversine_km(la, oa, t["lat"], t["lon"]) * factor_rodeo

    for r in reparto:
        a = r["origen"]
        ds = [(p["tienda"], distancia(a, p["tienda"])) for p in r["paradas"]]
        total_d = sum(d for _, d in ds)
        for s, d in ds:
            # si todas las distancias son 0 (tiendas pegadas al almacén), a partes iguales
            coste_r[s] += r["coste_eur"] * (d / total_d if total_d else 1 / len(ds))

    tro_c, rep_c = _centimos(coste_t), _centimos(coste_r)
    filas = []
    for s, t in info.items():
        a = t["almacen"]
        total = (tro_c.get(s, 0) + rep_c.get(s, 0)) / 100
        filas.append({**t, "kg": round(kg_tienda[s], 1), "km_almacen": round(distancia(a, s), 1),
                      "troncal_eur": tro_c.get(s, 0) / 100, "reparto_eur": rep_c.get(s, 0) / 100,
                      "total_eur": round(total, 2), "eur_kg": round(total / kg_tienda[s], 3) if kg_tienda[s] else None})
    filas.sort(key=lambda f: f["id"])
    troncal_eur = sum(r["coste_eur"] for r in troncal)
    reparto_eur = sum(r["coste_eur"] for r in reparto)
    return {"tiendas": filas, "troncal_eur": round(troncal_eur, 2), "reparto_eur": round(reparto_eur, 2),
            "imputado_eur": round(sum(coste_t.values()) + sum(coste_r.values()), 2),
            "sin_imputar_eur": round(sin_imputar, 2), "sin_imputar": motivos}
