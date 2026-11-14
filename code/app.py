"""App web del prototipo.

  /tienda   -> el dueño de cada tienda indica cuántas cajas (o kg) necesita la semana siguiente
  /central  -> la central ve los pedidos, lanza la optimización y consulta las rutas (mapa + tablas)

    .venv/Scripts/python app.py      ->  http://127.0.0.1:5000
"""
import math
import re
import threading
import traceback

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from modare import config as C
from modare import pedidos as PED
from modare.datos import cargar_datos, demanda_estimada
from modare.planificador import cargar_plan, planificar

app = Flask(__name__)
app.secret_key = "hackathon-modare"
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


@app.route("/")
def inicio():
    return redirect(url_for("tienda"))


@app.route("/tienda", methods=["GET", "POST"])
def tienda():
    semana = _sem(request.values.get("semana"))
    tid = request.values.get("tienda") or ""
    if request.method == "POST":
        if request.form.get("accion") == "borrar":
            PED.borrar(semana, tid)
            flash("Pedido eliminado: se usará la estimación por ventas.")
        else:
            cajas = int(request.form.get("cajas") or 0)
            kg = request.form.get("kg") or None
            if cajas <= 0 and kg:
                cajas = math.ceil(float(kg) / C.KG_POR_CAJA)
            if cajas <= 0:
                flash("Indica al menos una caja.", "error")
            else:
                PED.guardar(semana, tid, cajas, kg, request.form.get("comentario", ""))
                flash(f"Pedido guardado: {cajas} cajas para la semana {semana}.")
        return redirect(url_for("tienda", tienda=tid, semana=semana))

    info = pedido = None
    if tid:
        t = DATOS.tiendas.set_index("id_tienda").loc[tid]
        est = demanda_estimada(DATOS, PED.num_semana(semana)).get(tid, 0)
        info = {"id": tid, "nombre": t.nombre, "municipio": t.municipio, "categoria": t.categoria_tamano,
                "ventana": f"{t.dias_recepcion} {t.ventana_inicio}-{t.ventana_fin}",
                "estim_kg": est, "estim_cajas": math.ceil(est / C.KG_POR_CAJA),
                "max_cajas": int(DATOS.P[f"max_palets_entrega_{t.categoria_tamano}"]
                                 * DATOS.P["kg_por_palet"] / C.KG_POR_CAJA)}
        pedido = PED.de_semana(semana).get(tid)
    return render_template("tienda.html", tiendas=_tiendas(), semana=semana, tid=tid, info=info,
                           pedido=pedido, kg_caja=C.KG_POR_CAJA)


@app.route("/central")
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
def estado():
    return jsonify(TRABAJOS.get(_sem(request.args.get("semana")), {}))


@app.get("/central/plan")
def plan():
    semana = _sem(request.args.get("semana"))
    p = cargar_plan(semana)
    if not p:
        abort(404)
    return render_template("plan.html", semana=semana, plan=p)


@app.get("/central/plan.json")
def plan_json():
    p = cargar_plan(_sem(request.args.get("semana")))
    return jsonify(p) if p else abort(404)


@app.get("/central/paradas.csv")
def paradas_csv():
    f = C.SALIDAS_DIR / _sem(request.args.get("semana")) / "paradas.csv"
    return send_file(f, as_attachment=True) if f.exists() else abort(404)


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
