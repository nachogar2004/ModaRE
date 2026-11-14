"""Recalcula data/distancias.csv con distancias y tiempos POR CARRETERA (OSRM) entre todos los puntos de la red:
plantas (activas y futuras), almacenes y tiendas con acceso por carretera.

    .venv\\Scripts\\python calcular_distancias.py

Tarda unos 30 s (25 consultas al servidor público de OSRM, 1 por segundo). Hay que volver a ejecutarlo si se
añaden o mueven almacenes, plantas o tiendas. Si OSRM no encuentra ruta para un par, ese par se queda con la
estimación en línea recta × factor_rodeo (metodo = HAVERSINE_X1.26) y se avisa.
"""
import csv
import datetime as dt

from modare import config as C
from modare.datos import cargar_datos, haversine_km
from modare.osrm import matriz


def main():
    d = cargar_datos()
    P = d.P
    nodos = list(d.plantas.id_planta) + list(d.almacenes.id_almacen) + \
        list(d.tiendas[d.tiendas.acceso_carretera == 1].id_tienda)
    puntos = [d.coords[n] for n in nodos]
    print(f"{len(nodos)} puntos -> {len(nodos) ** 2 - len(nodos)} pares")
    km, mins = matriz(puntos)

    hoy = dt.date.today().isoformat()
    fuente_osrm = f"OSRM (router.project-osrm.org), datos OpenStreetMap, perfil coche, {hoy}"
    filas, respaldo = [], 0
    for i, a in enumerate(nodos):
        for j, b in enumerate(nodos):
            if i == j:
                continue
            if km[i][j] is not None and mins[i][j] is not None:
                k, m, metodo, fuente = km[i][j], mins[i][j], "OSRM", fuente_osrm
            else:
                (la, oa), (lb, ob) = puntos[i], puntos[j]
                k = haversine_km(la, oa, lb, ob) * P["factor_rodeo"]
                m = k / P["velocidad_respaldo_kmh"] * 60
                metodo, fuente = "HAVERSINE_X1.26", "Línea recta × factor_rodeo (OSRM sin ruta)"
                respaldo += 1
            filas.append([a, b, round(k, 1), round(m, 1), round(m * P["factor_tiempo_camion"], 1),
                          metodo, "ESTIMADO", fuente])
    out = C.DATA_DIR / "distancias.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["origen", "destino", "distancia_km", "duracion_min", "duracion_camion_min", "metodo",
                    "etiqueta", "fuente"])
        w.writerows(filas)
    print(f"{len(filas)} pares -> {out}  ({len(filas) - respaldo} por carretera, {respaldo} en línea recta)")


if __name__ == "__main__":
    main()
