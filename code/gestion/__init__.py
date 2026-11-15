"""ReMode Studio: app de gestión del transporte de Moda re- (tiendas, plantas, almacenes, camiones y administrador).

Se monta sobre la app Flask de code/app.py con `init_app(app)`.
"""
from __future__ import annotations

import os
import secrets
from functools import wraps

from flask import abort, flash, redirect, request, session, url_for
from flask_login import LoginManager, current_user
from flask_migrate import Migrate

from modare import config as C

from .modelos import (DIAS_SEMANA, ESTADOS_CAMION, ESTADOS_PEDIDO, ESTADOS_REPOSICION, ESTADOS_VIAJE, FRECUENCIAS, ROLES,
                      Usuario, db)

DIR_MIGRACIONES = C.CODE_DIR / "migrations"
login = LoginManager()
migrate = Migrate()
login.login_view = "auth.entrar"
login.login_message = "Inicia sesión para continuar."


@login.user_loader
def _cargar(uid):
    return db.session.get(Usuario, int(uid))


# ----------------------------------------------------------------------------- permisos
def rol_requerido(*roles):
    """Decorador: solo deja pasar a esos roles (el admin siempre pasa)."""
    def deco(f):
        @wraps(f)
        def w(*a, **kw):
            if not current_user.is_authenticated:
                return login.unauthorized()
            if current_user.rol != "admin" and current_user.rol not in roles:
                abort(403)
            return f(*a, **kw)
        return w
    return deco


def comprobar_acceso(rol: str, entidad_id: str):
    """Una tienda solo ve su tienda, una planta su planta… el admin lo ve todo."""
    if current_user.rol == "admin":
        return
    if current_user.rol != rol or current_user.entidad_id != entidad_id:
        abort(403)


def inicio_de(u: Usuario) -> str:
    if u.rol == "admin":
        return url_for("admin.resumen")
    if u.rol == "tienda":
        return url_for("tienda.panel", tid=u.tienda_id)
    if u.rol == "planta":
        return url_for("planta.panel", pid=u.planta_id)
    return url_for("almacen.panel", aid=u.almacen_id)


# ----------------------------------------------------------------------------- CSRF mínimo
def _csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(24)
    return session["_csrf"]


def _comprobar_csrf():
    if request.method == "POST" and request.endpoint != "auth.entrar":
        token = session.get("_csrf")
        if not token or request.form.get("_csrf") != token:
            abort(400, "Formulario caducado: recarga la página e inténtalo de nuevo.")


# ----------------------------------------------------------------------------- montaje
def init_app(app):
    from modare.bd import url_bd
    url = url_bd()                       # la MISMA base que usa la librería de rutas (variable MODARE_DB)
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", url)
    app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {"pool_pre_ping": True})
    # (asignación directa: Flask ya trae estas claves definidas y setdefault no las cambiaría)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"      # el navegador no manda la sesión desde otras webs
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    db.init_app(app)
    # render_as_batch: SQLite no sabe alterar columnas; Alembic recrea la tabla. En PostgreSQL no hace falta.
    migrate.init_app(app, db, directory=str(DIR_MIGRACIONES), render_as_batch=url.startswith("sqlite"))
    login.init_app(app)

    from .vistas_admin import bp as bp_admin
    from .vistas_auth import bp as bp_auth
    from .vistas_red import bp_almacen, bp_planta, bp_tienda
    for bp in (bp_auth, bp_admin, bp_tienda, bp_planta, bp_almacen):
        app.register_blueprint(bp)

    app.before_request(_comprobar_csrf)
    # (los pedidos automáticos diarios ya no se generan: la app trabaja con los kg/semana de cada tienda, sin semanas)

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(500)
    def _error(e):
        from flask import render_template
        from werkzeug.exceptions import HTTPException
        codigo = e.code if isinstance(e, HTTPException) else 500
        textos = {400: "Petición no válida", 403: "No tienes permiso para ver esta página",
                  404: "No encontrado", 500: "Error interno"}
        detalle = e.description if isinstance(e, HTTPException) and codigo == 400 else ""
        return render_template("gestion/error.html", codigo=codigo, titulo=textos.get(codigo, "Error"),
                               detalle=detalle), codigo

    @app.context_processor
    def _globales():
        return {"csrf_token": _csrf_token, "ROLES": ROLES, "ESTADOS_PEDIDO": ESTADOS_PEDIDO,
                "ESTADOS_REPOSICION": ESTADOS_REPOSICION, "ESTADOS_CAMION": ESTADOS_CAMION, "ESTADOS_VIAJE": ESTADOS_VIAJE,
                "FRECUENCIAS": FRECUENCIAS, "DIAS_SEMANA": DIAS_SEMANA, "KG_POR_CAJA": C.KG_POR_CAJA,
                "inicio_de": inicio_de}

    @app.template_filter("miles")
    def _miles(v, dec=0):
        if v is None or v == "":
            return "—"
        return f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @app.template_filter("pct")
    def _pct(v):
        return "—" if v is None else f"{v * 100:.0f} %"

    with app.app_context():
        from .basedatos import preparar_bd
        preparar_bd()
