"""Paneles de tienda, planta y almacén. Cada usuario solo ve el suyo; el administrador puede ver todos."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import comprobar_acceso, rol_requerido
from . import logistica as L
from .modelos import (FRECUENCIAS, SEGMENTOS, TIPOS_PRENDA, Almacen, Categoria, Pedido, Planta, Reposicion, Tienda,
                      Viaje, db)

bp_tienda = Blueprint("tienda", __name__, url_prefix="/tienda")
bp_planta = Blueprint("planta", __name__, url_prefix="/planta")
bp_almacen = Blueprint("almacen", __name__, url_prefix="/almacen")


# ----------------------------------------------------------------------------- utilidades
def categorias():
    return Categoria.query.order_by(Categoria.orden).all()


def matriz(cats):
    """{segmento: [categoría de cada tipo]} para pintar la tabla segmento × tipo de prenda."""
    m = {s: [None] * len(TIPOS_PRENDA) for s in SEGMENTOS}
    for c in cats:
        m[c.segmento][TIPOS_PRENDA.index(c.tipo)] = c
    return m


def leer_lineas(prefijo: str) -> dict[str, int]:
    out = {}
    for c in categorias():
        v = (request.form.get(f"{prefijo}_{c.id}") or "").strip()
        if v:
            try:
                out[c.id] = int(v)
            except ValueError:
                abort(400, f"Cantidad no válida en {c.nombre}")
    return out


def _avisar(avisos):
    for a in avisos:
        flash(a, "error")


def _accion(fn, ok_msg, destino):
    """Ejecuta una operación de negocio y muestra su error al usuario en vez de romper."""
    try:
        avisos = fn()
        flash(ok_msg)
        _avisar(avisos or [])
    except L.ErrorNegocio as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(destino)


def proxima_fecha_auto(t: Tienda, desde: dt.date) -> dt.date | None:
    if not t.pedido_auto_activo:
        return None
    for i in range(1, 62):
        f = desde + dt.timedelta(days=i)
        if L.toca_pedido(t, f):
            return f
    return None


def semanas_abiertas(n=6) -> list[str]:
    primera = L.lunes_de(L.semana_entrega_para(dt.date.today()))
    out = []
    for i in range(n + 4):
        s = (primera + dt.timedelta(weeks=i)).isocalendar()
        sem = f"{s[0]}-W{s[1]:02d}"
        if not L.semana_cerrada(sem):
            out.append(sem)
        if len(out) == n:
            break
    return out


# ============================================================================= TIENDA
@bp_tienda.route("/", endpoint="mia")
@login_required
@rol_requerido("tienda")
def tienda_mia():
    return redirect(url_for("tienda.panel", tid=current_user.tienda_id))


@bp_tienda.route("/<tid>", endpoint="panel")
@login_required
@rol_requerido("tienda")
def tienda_panel(tid):
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    hoy = dt.date.today()
    cats = categorias()
    stock = {s.categoria_id: s.unidades for s in t.stock}
    media = L.venta_media_diaria(t.id, hoy + dt.timedelta(days=1))
    semana_pasada = L.venta_media_diaria(t.id, hoy + dt.timedelta(days=1), dias=7)
    pedidos = Pedido.query.filter_by(tienda_id=t.id).order_by(Pedido.creado.desc(), Pedido.id.desc()).limit(40).all()
    cobertura = {c: f"≈ {stock.get(c, 0) / v:.0f} días" for c, v in media.items() if v > 0}
    return render_template(
        "gestion/tienda.html", t=t, cats=cats, m=matriz(cats), stock=stock, media=media, cobertura=cobertura,
        ventas_7d={k: round(v * 7) for k, v in semana_pasada.items()},
        en_camino=L.en_camino(t.id), propuesta=L.propuesta_automatica(t, hoy), pedidos=pedidos,
        proxima_auto=proxima_fecha_auto(t, hoy), semanas=semanas_abiertas(),
        alertas=L.alertas("tienda", t.id), hoy=hoy)


@bp_tienda.post("/<tid>/ventas", endpoint="ventas")
@login_required
@rol_requerido("tienda")
def tienda_ventas(tid):
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    fecha = dt.date.fromisoformat(request.form.get("fecha") or dt.date.today().isoformat())
    if fecha > dt.date.today():
        flash("No se pueden registrar ventas futuras.", "error")
        return redirect(url_for("tienda.panel", tid=tid))
    lineas = {k: v for k, v in leer_lineas("v").items() if v > 0}
    if not lineas:
        flash("No has indicado ninguna prenda vendida.", "error")
        return redirect(url_for("tienda.panel", tid=tid))
    return _accion(lambda: L.registrar_ventas(t, lineas, fecha, current_user),
                   f"Ventas del {fecha:%d/%m} registradas ({sum(lineas.values())} prendas).",
                   url_for("tienda.panel", tid=tid))


@bp_tienda.post("/<tid>/ajuste", endpoint="ajuste")
@login_required
@rol_requerido("tienda")
def tienda_ajuste(tid):
    """Poner/quitar prendas a mano: inventario, mermas, donaciones recibidas, prendas retiradas…"""
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    lineas = leer_lineas("a")
    return _accion(lambda: L.ajustar_stock_prendas(t, lineas, request.form.get("motivo") or "ajuste", current_user),
                   "Stock actualizado.", url_for("tienda.panel", tid=tid))


@bp_tienda.post("/<tid>/pedido", endpoint="pedido")
@login_required
@rol_requerido("tienda")
def tienda_pedido(tid):
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    lineas = leer_lineas("p")
    semana = request.form.get("semana", "")
    return _accion(lambda: [] if L.crear_pedido_adicional(t, lineas, semana, request.form.get("comentario", ""),
                                                          bool(request.form.get("urgente")), current_user) else [],
                   f"Pedido adicional registrado para la semana {semana}.", url_for("tienda.panel", tid=tid))


@bp_tienda.post("/<tid>/config", endpoint="config")
@login_required
@rol_requerido("tienda")
def tienda_config(tid):
    comprobar_acceso("tienda", tid)
    t = db.get_or_404(Tienda, tid)
    f = request.form.get("frecuencia_pedido")
    if f not in FRECUENCIAS:
        abort(400)
    t.frecuencia_pedido = f
    t.dia_pedido = int(request.form.get("dia_pedido") or 0)
    t.dias_seguridad = max(0, int(request.form.get("dias_seguridad") or 0))
    t.pedido_auto_activo = bool(request.form.get("pedido_auto_activo"))
    db.session.commit()
    flash("Configuración de pedidos automáticos guardada.")
    return redirect(url_for("tienda.panel", tid=tid))


def _pedido_de(tid, pid) -> Pedido:
    comprobar_acceso("tienda", tid)
    p = db.get_or_404(Pedido, pid)
    if p.tienda_id != tid:
        abort(404)
    return p


@bp_tienda.post("/<tid>/pedido/<int:pid>/cancelar", endpoint="cancelar")
@login_required
@rol_requerido("tienda")
def tienda_cancelar(tid, pid):
    p = _pedido_de(tid, pid)
    return _accion(lambda: L.cancelar_pedido(p), f"Pedido {p.codigo} cancelado.", url_for("tienda.panel", tid=tid))


@bp_tienda.post("/<tid>/pedido/<int:pid>/recibir", endpoint="recibir")
@login_required
@rol_requerido("tienda")
def tienda_recibir(tid, pid):
    p = _pedido_de(tid, pid)
    return _accion(lambda: L.recibir_pedido(p, current_user), f"Pedido {p.codigo} recibido: stock actualizado.",
                   url_for("tienda.panel", tid=tid))


# ============================================================================= PLANTA
@bp_planta.route("/<pid>", endpoint="panel")
@login_required
@rol_requerido("planta")
def planta_panel(pid):
    comprobar_acceso("planta", pid)
    p = db.get_or_404(Planta, pid)
    cats = categorias()
    reps = Reposicion.query.filter_by(planta_id=pid).order_by(Reposicion.semana.desc(), Reposicion.almacen_id).limit(80).all()
    viajes = Viaje.query.filter_by(escalon="troncal", origen=pid).order_by(Viaje.semana.desc(), Viaje.t_salida).limit(40).all()
    return render_template("gestion/planta.html", p=p, cats=cats, m=matriz(cats),
                           stock={s.categoria_id: s.unidades for s in p.stock}, reps=reps, viajes=viajes,
                           alertas=L.alertas("planta", pid))


@bp_planta.post("/<pid>/ajuste", endpoint="ajuste")
@login_required
@rol_requerido("planta")
def planta_ajuste(pid):
    """Entrada de prendas clasificadas (+) o salidas/mermas (−)."""
    comprobar_acceso("planta", pid)
    p = db.get_or_404(Planta, pid)
    return _accion(lambda: L.ajustar_stock_prendas(p, leer_lineas("a"), request.form.get("motivo") or "ajuste",
                                                   current_user),
                   "Stock de planta actualizado.", url_for("planta.panel", pid=pid))


@bp_planta.post("/<pid>/reposicion/<int:rid>/expedir", endpoint="expedir")
@login_required
@rol_requerido("planta")
def planta_expedir(pid, rid):
    comprobar_acceso("planta", pid)
    r = db.get_or_404(Reposicion, rid)
    if r.planta_id != pid:
        abort(404)
    return _accion(lambda: L.expedir_reposicion(r, current_user),
                   f"Reposición {r.codigo} expedida a {r.almacen.nombre}.", url_for("planta.panel", pid=pid))


# ============================================================================= ALMACÉN
@bp_almacen.route("/<aid>", endpoint="panel")
@login_required
@rol_requerido("almacen")
def almacen_panel(aid):
    comprobar_acceso("almacen", aid)
    a = db.get_or_404(Almacen, aid)
    entrantes = Reposicion.query.filter_by(almacen_id=aid).order_by(Reposicion.semana.desc()).limit(30).all()
    salientes = (Pedido.query.filter(Pedido.estado.in_(("confirmado", "planificado", "en_reparto", "entregado")))
                 .filter((Pedido.almacen_id == aid) | ((Pedido.almacen_id.is_(None)) &
                                                      Pedido.tienda.has(almacen_id=aid)))
                 .order_by(Pedido.semana_entrega.desc(), Pedido.tienda_id).limit(150).all())
    viajes = Viaje.query.filter_by(escalon="reparto", origen=aid).order_by(Viaje.semana.desc(), Viaje.t_salida).limit(40).all()
    previsto_in = sum(r.cajas for r in entrantes if r.estado != "recibida")
    previsto_out = sum(p.cajas for p in salientes if p.estado in ("confirmado", "planificado"))
    return render_template("gestion/almacen.html", a=a, entrantes=entrantes, salientes=salientes, viajes=viajes,
                           previsto_in=previsto_in, previsto_out=previsto_out, alertas=L.alertas("almacen", aid),
                           tiendas=Tienda.query.filter_by(almacen_id=aid, activa=True).order_by(Tienda.nombre).all())


@bp_almacen.post("/<aid>/ajuste", endpoint="ajuste")
@login_required
@rol_requerido("almacen")
def almacen_ajuste(aid):
    comprobar_acceso("almacen", aid)
    a = db.get_or_404(Almacen, aid)
    try:
        delta = int(request.form.get("delta") or 0)
    except ValueError:
        abort(400)
    return _accion(lambda: L.ajustar_cajas_almacen(a, delta, request.form.get("motivo") or "ajuste", current_user),
                   "Stock del almacén actualizado.", url_for("almacen.panel", aid=aid))


@bp_almacen.post("/<aid>/reposicion/<int:rid>/recibir", endpoint="recibir")
@login_required
@rol_requerido("almacen")
def almacen_recibir(aid, rid):
    comprobar_acceso("almacen", aid)
    r = db.get_or_404(Reposicion, rid)
    if r.almacen_id != aid:
        abort(404)
    return _accion(lambda: L.recibir_reposicion(r, current_user), f"Reposición {r.codigo} recibida (+{r.cajas} cajas).",
                   url_for("almacen.panel", aid=aid))


@bp_almacen.post("/<aid>/pedido/<int:pid>/expedir", endpoint="expedir")
@login_required
@rol_requerido("almacen")
def almacen_expedir(aid, pid):
    comprobar_acceso("almacen", aid)
    p = db.get_or_404(Pedido, pid)
    if (p.almacen_id or p.tienda.almacen_id) != aid:
        abort(404)
    return _accion(lambda: L.expedir_pedido(p, current_user), f"Pedido {p.codigo} cargado en el camión.",
                   url_for("almacen.panel", aid=aid))
