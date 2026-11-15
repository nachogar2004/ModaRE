"""App web de Moda re-: gestión del transporte planta → almacén → tienda.

  /entrar                 inicio de sesión (roles: admin, tienda, planta, almacén)
  /tienda/<id>            stock por prenda, ventas, pedidos automáticos y adicionales, recepción
  /planta/<id>            stock y expedición de reposiciones a los almacenes
  /almacen/<id>           stock de cajas (máximo), recepción de reposiciones y salida de pedidos
  /admin                  usuarios, red, camiones, pedidos y planificación semanal (gestion/)
  /central                (admin) herramienta avanzada del optimizador: escenarios, mapa y analítica

    .venv/bin/python app.py      ->  http://127.0.0.1:5000      (Windows: .venv/Scripts/python app.py)

Producción (ver .env.example y docs/guia_app.md): MODARE_ENV=produccion, MODARE_DB=postgresql://…, MODARE_SECRET_KEY,
y un servidor WSGI:  gunicorn -w 2 -b 0.0.0.0:8000 app:app   (Windows: waitress-serve --port 8000 app:app)
"""
import csv
import io
import os
import re
import threading
import traceback

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

import gestion
from gestion import rol_requerido

from modare import bd as BD
from modare import config as C
from modare import pedidos as PED
from modare.datos import cargar_datos, demanda_estimada
from modare.planificador import cargar_plan, filas_paradas, planificar

app = Flask(__name__)
PRODUCCION = os.environ.get("MODARE_ENV", "desarrollo").lower() in ("produccion", "producción", "production")
_clave = os.environ.get("MODARE_SECRET_KEY", "")
if PRODUCCION:
    # configuración obligatoria para uso real (ver .env.example)
    if len(_clave) < 32:
        raise RuntimeError("MODARE_ENV=produccion exige MODARE_SECRET_KEY de al menos 32 caracteres "
                           "(genera una con: python -c \"import secrets; print(secrets.token_urlsafe(48))\")")
    if not os.environ.get("MODARE_DB"):
        raise RuntimeError("MODARE_ENV=produccion exige MODARE_DB (p. ej. postgresql://usuario:clave@host/base)")
    app.config["SESSION_COOKIE_SECURE"] = True        # la cookie de sesión solo viaja por HTTPS
    app.config["PREFERRED_URL_SCHEME"] = "https"
    if os.environ.get("MODARE_PROXY", "1") != "0":   # detrás de un proxy (nginx, Render, Azure…): IP y https reales
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = _clave or "solo-para-desarrollo-local"
gestion.init_app(app)
DATOS = cargar_datos()


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
                           plan=cargar_plan(semana), trabajo=BD.estado_trabajo(semana))


def _lanzar(semana, kw):
    try:
        planificar(semana, log=lambda *a: None, **kw)
        BD.poner_trabajo(semana, "ok")
    except Exception as e:  # noqa: BLE001 — se muestra en la web
        traceback.print_exc()
        BD.poner_trabajo(semana, "error", str(e))


@app.post("/central/optimizar")
@login_required
@rol_requerido()
def optimizar():
    semana = _sem(request.form.get("semana"))
    if not BD.reclamar_trabajo(semana):
        flash("Ya hay una optimización en marcha para esta semana.")
        return redirect(url_for("central", semana=semana))
    kw = {"fuente": request.form.get("fuente", "mixto"), "asignacion": request.form.get("asignacion", "optimo"),
          "segundos": int(request.form.get("segundos", 30)), "precio_co2": float(request.form.get("precio_co2", 0)),
          "plantas_futuras": bool(request.form.get("plantas_futuras"))}
    threading.Thread(target=_lanzar, args=(semana, kw), daemon=True).start()
    return redirect(url_for("central", semana=semana))


@app.get("/central/estado")
@login_required
@rol_requerido()
def estado():
    return jsonify(BD.estado_trabajo(_sem(request.args.get("semana"))))


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
    """Se genera desde el plan guardado en la base de datos (no depende de que exista el fichero en disco)."""
    semana = _sem(request.args.get("semana"))
    plan = cargar_plan(semana)
    if not plan:
        abort(404)
    buf = io.StringIO()
    buf.write("\ufeff")                    # BOM: Excel muestra bien las tildes
    csv.writer(buf).writerows(filas_paradas(plan))
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=paradas_{semana}.csv"})


if __name__ == "__main__":
    # en macOS el 5000 lo ocupa AirPlay: PORT=5050 python app.py
    app.run(debug=True, use_reloader=False, port=int(os.environ.get("PORT", 5000)))
