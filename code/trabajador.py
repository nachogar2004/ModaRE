"""Ordenador optimizador de ReMode Studio.

La web publicada (con MODARE_OPTIMIZADOR=externo) no calcula las rutas: deja la petición en cola en la base de
datos. Este programa, ejecutado en un ordenador potente (p. ej. tu Mac), vigila la cola, optimiza con toda su
potencia y guarda el resultado en la MISMA base de datos: las rutas aparecen al momento en la web.

    cd code
    export MODARE_DB='postgresql://…'        # la misma base que usa la web
    .venv/bin/python trabajador.py           # Windows: .venv\\Scripts\\python trabajador.py

Déjalo abierto mientras quieras poder optimizar desde la web. Ctrl + C para pararlo.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import threading
import time
import traceback

ESPERA_SEG = 5     # cada cuánto mira si hay peticiones nuevas


def _hora() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def main():
    if not os.environ.get("MODARE_DB"):
        sys.exit("Falta MODARE_DB: define la misma base de datos que usa la web, p. ej.\n"
                 "  export MODARE_DB='postgresql://…'")
    from gestion import optimizador as OPT
    from gestion.basedatos import app_bd
    from modare import bd as BD

    app = app_bd()
    with app.app_context():                 # deja la base al día (por si el servidor aún no ha desplegado)
        from flask_migrate import upgrade
        from gestion import DIR_MIGRACIONES
        upgrade(directory=str(DIR_MIGRACIONES))

    def latidos():                     # avisa a la web de que este ordenador está conectado (también mientras calcula)
        while True:
            try:
                BD.latido()
            except Exception as e:  # noqa: BLE001 — sin red un momento: se reintenta
                print(f"[{_hora()}] sin conexión con la base de datos ({e.__class__.__name__}); reintentando…")
            time.sleep(BD.SEG_LATIDO)

    threading.Thread(target=latidos, daemon=True).start()
    print(f"[{_hora()}] Ordenador optimizador conectado. Esperando optimizaciones pedidas desde la web… "
          f"(Ctrl + C para parar)")
    while True:
        try:
            tarea = BD.tomar_pendiente()
        except Exception as e:  # noqa: BLE001
            print(f"[{_hora()}] no se pudo consultar la cola ({e.__class__.__name__}); reintentando…")
            time.sleep(ESPERA_SEG * 3)
            continue
        if not tarea:
            time.sleep(ESPERA_SEG)
            continue
        semana, params = tarea
        print(f"[{_hora()}] Optimizando {semana} {params} …")
        t0 = time.time()
        try:
            with app.app_context():
                msg = OPT.ejecutar(semana, log=lambda *a: print("   ", *a), **params)
            BD.poner_trabajo(semana, "ok", msg)
            print(f"[{_hora()}] {semana} lista en {time.time() - t0:.0f} s: {msg}")
        except Exception as e:  # noqa: BLE001 — el error se enseña en la web
            traceback.print_exc()
            BD.poner_trabajo(semana, "error", f"Error en el ordenador optimizador: {e}")
            print(f"[{_hora()}] {semana} falló: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOrdenador optimizador desconectado.")
