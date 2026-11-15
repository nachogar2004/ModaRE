"""Zonas donde puede estar una instalación: España peninsular y, solo para tiendas, las Islas Baleares.

El trabajo de rutas excluye las islas, Ceuta y Melilla: plantas y almacenes (depósitos del VRP) solo en la península.
Las tiendas de Baleares sí se gestionan en la plataforma (el reparto va por vía marítima): se guardan con
acceso_carretera = 0 y el optimizador las descarta (modare/datos.py). PENINSULA es un polígono grueso (vértices (lat, lon),
en sentido horario): la costa va unos 3-8 km mar adentro para no dejar fuera ninguna instalación costera, y la frontera
con Portugal y Francia es aproximada (±5 km). Excluye Gibraltar y Andorra. BALEARES es un rectángulo amplio con Mallorca,
Menorca, Ibiza, Formentera y Cabrera, lejos de la costa peninsular (Dénia/Jávea lon ≤ 0,25; Delta del Ebro lon ≤ 0,95).
Las mismas zonas se pasan al JS del formulario (`para_js`), no se duplican.
"""
from __future__ import annotations

import math

PENINSULA = [
    # costa norte, de oeste a este
    (43.10, -9.35), (43.20, -9.30), (43.45, -8.90), (43.45, -8.35), (43.60, -8.30), (43.82, -7.68), (43.62, -7.02),
    (43.68, -5.85), (43.65, -5.60), (43.50, -4.60), (43.56, -3.75), (43.43, -3.00), (43.42, -2.50), (43.40, -1.95),
    (43.42, -1.78),
    # frontera con Francia (Pirineos), de oeste a este; muesca de Andorra
    (43.37, -1.775), (43.33, -1.74), (43.30, -1.62), (43.27, -1.50), (43.15, -1.36), (43.03, -1.22), (43.00, -1.00),
    (42.96, -0.73), (42.80, -0.52), (42.80, -0.30), (42.72, 0.10), (42.72, 0.40), (42.88, 0.70), (42.78, 1.00),
    (42.66, 1.35), (42.61, 1.43), (42.43, 1.43), (42.43, 1.74), (42.60, 1.74), (42.47, 1.90), (42.49, 2.02),
    (42.40, 2.20), (42.40, 2.40), (42.48, 2.67), (42.46, 2.90), (42.43, 3.18), (42.38, 3.38),
    # costa mediterránea, de norte a sur
    (42.25, 3.35), (42.10, 3.30), (41.88, 3.22), (41.60, 2.90), (41.45, 2.40), (41.30, 2.25), (41.20, 1.90),
    (41.05, 1.35), (40.80, 1.10), (40.70, 1.00), (40.60, 0.80), (40.50, 0.55), (40.35, 0.50), (40.10, 0.25),
    (39.98, 0.10), (39.65, -0.15), (39.45, -0.25), (39.15, -0.15), (38.97, -0.10), (38.88, 0.18), (38.72, 0.30),
    (38.54, -0.05), (38.30, -0.40), (38.17, -0.50), (37.95, -0.62), (37.62, -0.65), (37.55, -0.95), (37.52, -1.25),
    (37.38, -1.55), (36.99, -1.85), (36.68, -2.12), (36.75, -2.45), (36.70, -2.65), (36.69, -3.03), (36.67, -3.52),
    (36.70, -3.88), (36.68, -4.42), (36.58, -4.46), (36.52, -4.58), (36.47, -4.72), (36.46, -4.88), (36.38, -5.15), (36.24, -5.25),
    # estrecho: muesca de Gibraltar (frontera en lat ~36,152)
    (36.152, -5.30), (36.152, -5.40), (36.07, -5.47), (35.97, -5.60), (36.00, -5.75),
    # costa atlántica andaluza, de este a oeste
    (36.12, -6.05), (36.52, -6.36), (36.74, -6.50), (36.95, -6.65), (37.08, -6.92), (37.10, -7.45),
    # frontera con Portugal, de sur a norte
    (37.25, -7.46), (37.45, -7.50), (37.75, -7.45), (37.98, -7.30), (38.13, -6.97), (38.50, -7.22), (38.68, -7.18),
    (38.90, -7.10), (39.10, -7.06), (39.25, -7.10), (39.45, -7.34), (39.70, -7.65), (39.90, -7.10), (40.05, -6.93),
    (40.28, -6.90), (40.60, -6.92), (41.04, -6.95), (41.20, -6.80), (41.45, -6.35), (41.80, -6.55), (41.98, -6.90),
    (41.88, -7.05), (41.88, -7.50), (41.87, -8.20), (42.11, -8.20), (42.095, -8.45), (42.035, -8.66), (42.00, -8.69),
    (41.958, -8.71), (41.946, -8.745), (41.92, -8.78), (41.89, -8.83), (41.865, -8.87), (41.865, -8.93), (41.85, -8.95),
    # costa gallega atlántica, de sur a norte
    (42.10, -9.00), (42.25, -9.00), (42.45, -9.00), (42.60, -9.20), (42.90, -9.35),
]


def dentro(lat: float, lon: float) -> bool:
    """Punto en polígono (trazado de rayos)."""
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    n, dentro_ = len(PENINSULA), False
    for i in range(n):
        la1, lo1 = PENINSULA[i]
        la2, lo2 = PENINSULA[(i + 1) % n]
        if (la1 > lat) != (la2 > lat) and lon < lo1 + (lat - la1) * (lo2 - lo1) / (la2 - la1):
            dentro_ = not dentro_
    return dentro_


BALEARES = (38.55, 40.15, 1.10, 4.45)       # lat mín, lat máx, lon mín, lon máx


def zona(lat: float, lon: float) -> str | None:
    """"peninsula", "baleares" o None (Canarias, Ceuta, Melilla, otros países…)."""
    if dentro(lat, lon):
        return "peninsula"
    la1, la2, lo1, lo2 = BALEARES
    if math.isfinite(lat) and math.isfinite(lon) and la1 <= lat <= la2 and lo1 <= lon <= lo2:
        return "baleares"
    return None


def motivo_fuera(lat: float, lon: float, islas: bool = False) -> str:
    """Texto de por qué no vale la ubicación; vacío si vale (con `islas`, también Baleares)."""
    z = zona(lat, lon)
    if z == "peninsula" or (z == "baleares" and islas):
        return ""
    if z == "baleares":
        return "La ubicación está en las Islas Baleares: las plantas y los almacenes solo pueden estar en la España peninsular."
    return ("La ubicación está fuera de la zona admitida (España peninsular" + (" y Baleares" if islas else "")
            + "; no se admiten Canarias, Ceuta, Melilla ni otros países).")


def para_js(islas: bool) -> dict:
    """Lo que necesita static/mapa_ubicacion.js para avisar con la misma regla que el servidor."""
    return {"poligono": PENINSULA, "baleares": BALEARES, "islas": islas}
