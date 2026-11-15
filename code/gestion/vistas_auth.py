from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import inicio_de
from .modelos import Usuario, ahora, db

bp = Blueprint("auth", __name__)


@bp.route("/")
def raiz():
    if current_user.is_authenticated:
        return redirect(inicio_de(current_user))
    return redirect(url_for("auth.entrar"))


@bp.route("/entrar", methods=["GET", "POST"])
def entrar():
    if request.method == "POST":
        u = Usuario.query.filter_by(username=request.form.get("username", "").strip().lower()).first()
        if u and u.activo and u.check_password(request.form.get("password", "")):
            login_user(u)
            u.ultimo_acceso = ahora()
            db.session.commit()
            sig = request.args.get("next")
            return redirect(sig if sig and sig.startswith("/") and not sig.startswith("//") else inicio_de(u))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("gestion/entrar.html")


@bp.post("/salir")
@login_required
def salir():
    logout_user()
    return redirect(url_for("auth.entrar"))


@bp.route("/cuenta", methods=["GET", "POST"])
@login_required
def cuenta():
    if request.method == "POST":
        if request.form.get("accion") == "datos":
            current_user.nombre = (request.form.get("nombre") or "").strip()[:120] or None
            current_user.email = (request.form.get("email") or "").strip()[:120] or None
            db.session.commit()
            flash("Datos guardados.")
            return redirect(url_for("auth.cuenta"))
        actual, nueva = request.form.get("actual", ""), request.form.get("nueva", "")
        if not current_user.check_password(actual):
            flash("La contraseña actual no es correcta.", "error")
        elif len(nueva) < 8:
            flash("La nueva contraseña debe tener al menos 8 caracteres.", "error")
        elif nueva != request.form.get("repetir"):
            flash("Las contraseñas no coinciden.", "error")
        else:
            current_user.set_password(nueva)
            db.session.commit()
            flash("Contraseña cambiada.")
        return redirect(url_for("auth.cuenta"))
    return render_template("gestion/cuenta.html")
