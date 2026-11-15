"""Calcula las distancias y tiempos POR CARRETERA (OSRM) entre los puntos de la red y los guarda en la base de datos
(tabla distancias, la que lee el optimizador): plantas (activas y futuras), almacenes activos y tiendas activas con
acceso por carretera.

    .venv\\Scripts\\python calcular_distancias.py            # solo los puntos nuevos o movidos (unos segundos)
    .venv\\Scripts\\python calcular_distancias.py --todos    # todos los pares (~30 s, 25 consultas a OSRM)
    .venv\\Scripts\\python calcular_distancias.py --csv      # además escribe data/distancias.csv (semilla en git)

Lo mismo que el botón «Calcular distancias por carretera» de Admin → Planificación. Si OSRM no encuentra ruta
para un par, se guarda la estimación en línea recta × factor_rodeo (metodo = HAVERSINE_X1.26).
"""
import argparse

from gestion.basedatos import preparar_desde_cli
from modare import config as C
from modare import distancias as DIST


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--todos", action="store_true", help="Recalcula todos los pares, no solo los que faltan")
    ap.add_argument("--csv", action="store_true", help="Escribe también data/distancias.csv (semilla)")
    a = ap.parse_args()
    preparar_desde_cli()
    DIST.calcular(todos=a.todos)
    if a.csv:
        DIST.exportar_csv(C.DATA_DIR / "distancias.csv")


if __name__ == "__main__":
    main()
