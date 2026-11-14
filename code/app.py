"""App web de Moda re-: gestión del transporte planta → almacén → tienda.

  /entrar                 inicio de sesión (roles: admin, tienda, planta, almacén)
  /tienda/<id>            stock por prenda, ventas, pedidos automáticos y adicionales, recepción
  /planta/<id>            stock y expedición de reposiciones a los almacenes
  /almacen/<id>           stock de cajas (máximo), recepción de reposiciones y salida de pedidos
  /admin                  usuarios, red, camiones, pedidos y planificación semanal (gestion/)
  /central                (admin) herramienta avanzada del optimizador: escenarios, mapa y analítica

    .venv/bin/python app.py      ->  http://127.0.0.1:5000      (Windows: .venv/Scripts/python app.py)
"""
import os
import re
import threading
import traceback

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import login_required

import gestion
from gestion import rol_requerido

from modare import config as C
from modare import pedidos as PED
from modare.datos import cargar_datos, demanda_estimada
from modare.planificador import cargar_plan, planificar

app = Flask(__name__)
app.secret_key = os.environ.get("MODARE_SECRET_KEY", "hackathon-modare")   # cambiadla en producción
gestion.init_app(app)
DATOS = cargar_datos()
TRABAJOS: dict[str, dict] = {}   # semana -> {"estado": "calculando"|"ok"|"error", "msg": str}


def _sem(v: str | None) -> str:
    """La semana se usa en rutas de fichero: solo se acepta el formato ISO AAAA-Www."""
    v = v or PED.semana_actual()
    if not re.fullmatch(r"\d{4}-W\d{2}", v):
        abort(400, "Semana con formato AAAA-Www, p.ej. 2026-W47")
    return v


def _tiendas():
    return DATOS.tiendas.sort_values(["provincia", "nombre"])


@app.route("/central")
@login_required
@rol_requerido()
def central():
    semana = _sem(request.args.get("semana"))
    peds = PED.de_semana(semana)
    est = demanda_estimada(DATOS, PED.num_semana(semana))
    filas = []
    for t in _tiendas().itertuples():
        p = peds.get(t.id_tienda)
        filas.append({"id": t.id_tienda, "nombre": t.nombre, "provincia": t.provincia, "cat": t.categoria_tamano,
                      "pedido_cajas": p["cajas"] if p else None, "pedido_kg": p["kg"] if p else None,
                      "estim_kg": est.get(t.id_tienda), "comentario": p["comentario"] if p else ""})
    return render_template("central.html", semana=semana, filas=filas, n_pedidos=len(peds),
                           plan=cargar_plan(semana), trabajo=TRABAJOS.get(semana))


def _lanzar(semana, kw):
    try:
        planificar(semana, log=lambda *a: None, **kw)
        TRABAJOS[semana] = {"estado": "ok"}
    except Exception as e:  # noqa: BLE001 — se muestra en la web
        traceback.print_exc()
        TRABAJOS[semana] = {"estado": "error", "msg": str(e)}


@app.post("/central/optimizar")
@login_required
@rol_requerido()
def optimizar():
    semana = _sem(request.form.get("semana"))
    if TRABAJOS.get(semana, {}).get("estado") == "calculando":
        flash("Ya hay una optimización en marcha para esta semana.")
        return redirect(url_for("central", semana=semana))
    kw = {"fuente": request.form.get("fuente", "mixto"), "asignacion": request.form.get("asignacion", "optimo"),
          "segundos": int(request.form.get("segundos", 30)), "precio_co2": float(request.form.get("precio_co2", 0)),
          "plantas_futuras": bool(request.form.get("plantas_futuras"))}
    TRABAJOS[semana] = {"estado": "calculando"}
    threading.Thread(target=_lanzar, args=(semana, kw), daemon=True).start()
    return redirect(url_for("central", semana=semana))


@app.get("/central/estado")
@login_required
@rol_requerido()
def estado():
    return jsonify(TRABAJOS.get(_sem(request.args.get("semana")), {}))


@app.get("/central/plan")
@login_required
@rol_requerido()
def plan():
    semana = _sem(request.args.get("semana"))
    p = cargar_plan(semana)
    if not p:
        abort(404)
    return render_template("plan.html", semana=semana, plan=p)


@app.get("/central/plan.json")
@login_required
@rol_requerido()
def plan_json():
    p = cargar_plan(_sem(request.args.get("semana")))
    return jsonify(p) if p else abort(404)


@app.get("/central/paradas.csv")
@login_required
@rol_requerido()
def paradas_csv():
    f = C.SALIDAS_DIR / _sem(request.args.get("semana")) / "paradas.csv"
    return send_file(f, as_attachment=True) if f.exists() else abort(404)


if __name__ == "__main__":
    # en macOS el 5000 lo ocupa AirPlay: PORT=5050 python app.py
    app.run(debug=True, use_reloader=False, port=int(os.environ.get("PORT", 5000)))
