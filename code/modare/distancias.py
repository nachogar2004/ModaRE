"""Distancias por carretera entre los puntos de la red, guardadas en la base de datos (tabla distancias).

Puntos de la red: todas las plantas (activas y futuras), los almacenes activos y las tiendas activas con acceso por
carretera. Si a un par le falta dato, el optimizador usa línea recta × factor_rodeo (Datos.dist).

  calcular(todos=True)   recalcula todos los pares con OSRM (~30 s, 25 consultas)
  calcular()             solo los puntos sin distancias completas: nuevos o movidos en la app (unos segundos)
  olvidar(ids)           borra las distancias de esos puntos (la app lo hace al mover uno)
"""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from .bd import engine, leer
from .datos import Datos, cargar_datos, haversine_km
from .osrm import matriz, matriz_parcial

T = sa.table("distancias", sa.column("origen", sa.String), sa.column("destino", sa.String),
             sa.column("distancia_km", sa.Float), sa.column("duracion_min", sa.Float),
             sa.column("duracion_camion_min", sa.Float), sa.column("metodo", sa.String), sa.column("calculada", sa.Date))
RESPALDO = "HAVERSINE_X1.26"


def nodos(d: Datos) -> list[str]:
    return (list(d.plantas.id_planta) + list(d.almacenes.id_almacen)
            + list(d.tiendas[d.tiendas.acceso_carretera == 1].id_tienda))


def sin_distancias(d: Datos | None = None) -> list[str]:
    """Puntos que hay que (re)calcular para que no falte ningún par: los nuevos o movidos.

    Al mover una tienda se borran sus pares con TODOS los demás puntos, así que a todos les falta uno; no se
    trata de recalcularlos todos, sino de elegir pocos puntos que cubran los pares que faltan (cobertura
    voraz: primero el punto con más pares pendientes, que es el movido)."""
    d = d or cargar_datos(distancias=False)
    ns = nodos(d)
    conjunto = set(ns)
    pares = leer("SELECT origen, destino FROM distancias")
    hay = {(o, de) for o, de in zip(pares.origen, pares.destino) if o in conjunto and de in conjunto}
    pendientes = {(a, b) for a in ns for b in ns if a != b and (a, b) not in hay}
    if len(pendientes) > len(ns) * (len(ns) - 1) // 2:     # falta más de la mitad: se recalcula todo
        return ns
    elegidos: set[str] = set()
    while pendientes:
        cuenta: dict[str, int] = {}
        for a, b in pendientes:
            cuenta[a] = cuenta.get(a, 0) + 1
            cuenta[b] = cuenta.get(b, 0) + 1
        n = max(cuenta, key=cuenta.get)
        elegidos.add(n)
        pendientes = {p for p in pendientes if n not in p}
    return [n for n in ns if n in elegidos]


def olvidar(ids: list[str]):
    """Borra las distancias de esos puntos: hasta que se recalculen, el optimizador usa línea recta."""
    if ids:
        with engine().begin() as con:
            con.execute(sa.delete(T).where(sa.or_(T.c.origen.in_(ids), T.c.destino.in_(ids))))


def calcular(todos: bool = False, log=print) -> dict:
    """Calcula con OSRM las distancias que faltan (o todas) y las guarda en la base. Devuelve un resumen."""
    d = cargar_datos(distancias=False)
    P = d.P
    ns = nodos(d)
    puntos = [d.coords[n] for n in ns]
    faltan = [] if todos else sin_distancias(d)
    if not todos and len(faltan) > len(ns) // 2:     # más barato pedir la matriz entera
        todos = True
    if todos:
        objetivo = list(range(len(ns)))
        log(f"{len(ns)} puntos -> {len(ns) * (len(ns) - 1)} pares")
        km, mins = matriz(puntos, log=log)
        res = {(i, j): (km[i][j], mins[i][j]) for i in objetivo for j in objetivo if i != j}
    else:
        faltan = set(faltan)
        objetivo = [i for i, n in enumerate(ns) if n in faltan]
        if not objetivo:
            log("No falta ninguna distancia.")
            return {"puntos": 0, "pares": 0, "osrm": 0, "linea_recta": 0}
        log(f"{len(objetivo)} puntos sin distancias completas: {', '.join(ns[i] for i in objetivo)}")
        res = matriz_parcial(puntos, objetivo, log=log)

    hoy = dt.date.today()
    filas, respaldo = [], 0
    for (i, j), (k, m) in res.items():
        if k is not None and m is not None:
            metodo = "OSRM"
        else:
            (la, oa), (lb, ob) = puntos[i], puntos[j]
            k = haversine_km(la, oa, lb, ob) * P["factor_rodeo"]
            m = k / P["velocidad_respaldo_kmh"] * 60
            metodo = RESPALDO
            respaldo += 1
        filas.append({"origen": ns[i], "destino": ns[j], "distancia_km": round(k, 1), "duracion_min": round(m, 1),
                      "duracion_camion_min": round(m * P["factor_tiempo_camion"], 1), "metodo": metodo,
                      "calculada": hoy})
    with engine().begin() as con:
        if todos:
            con.execute(sa.delete(T))
        else:
            ids = [ns[i] for i in objetivo]
            con.execute(sa.delete(T).where(sa.or_(T.c.origen.in_(ids), T.c.destino.in_(ids))))
        for k in range(0, len(filas), 5000):
            con.execute(sa.insert(T), filas[k:k + 5000])
    log(f"{len(filas)} pares guardados en la base ({len(filas) - respaldo} por carretera, {respaldo} en línea recta)")
    return {"puntos": len(objetivo), "pares": len(filas), "osrm": len(filas) - respaldo, "linea_recta": respaldo}


def exportar_csv(ruta, log=print):
    """Escribe la tabla en un CSV con el formato de data/distancias.csv (para actualizar la semilla en git)."""
    import csv
    df = leer("SELECT origen, destino, distancia_km, duracion_min, duracion_camion_min, metodo, calculada "
              "FROM distancias ORDER BY origen, destino")
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["origen", "destino", "distancia_km", "duracion_min", "duracion_camion_min", "metodo",
                    "etiqueta", "fuente"])
        for r in df.itertuples():
            fuente = (f"OSRM (router.project-osrm.org), datos OpenStreetMap, perfil coche, {r.calculada}"
                      if r.metodo == "OSRM" else "Línea recta × factor_rodeo (OSRM sin ruta)")
            w.writerow([r.origen, r.destino, r.distancia_km, r.duracion_min, r.duracion_camion_min, r.metodo,
                        "ESTIMADO", fuente])
    log(f"{len(df)} pares -> {ruta}")
