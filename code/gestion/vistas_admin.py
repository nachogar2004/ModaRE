"""Administrador: gestiona usuarios y toda la red (alta/baja/edición), ve todos los datos y lanza la planificación."""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import secrets
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from flask import (Blueprint, abort, current_app, flash, g, jsonify, make_response, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import DataError, IntegrityError

from modare import config as C
from modare import bd as BD
from modare.osrm import contexto_ssl
from modare.planificador import cargar_plan

from . import logistica as L
from . import optimizador as OPT
from . import rol_requerido
from .modelos import (DIAS_SEMANA, ESTADOS_CAMION, ESTADOS_PEDIDO, FRECUENCIAS, ROLES, Almacen, CamionReparto,
                      CamionTroncal, Categoria, MovimientoStock, Pedido, Planta, Reposicion, StockPlanta, StockTienda,
                      Tienda, Usuario, Venta, Viaje, db)
from . import peninsula
from .semilla import _slug, capacidad_vehiculos

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@login_required
@rol_requerido()
def _solo_admin():
    pass


# ----------------------------------------------------------------------------- CRUD genérico
@dataclass
class Campo:
    nombre: str
    etiqueta: str
    tipo: str = "text"            # text, int, float, bool, select, multi, date, time, textarea, password
    opciones: Callable[[], list[tuple[str, str]]] | None = None
    requerido: bool = False
    ayuda: str = ""
    solo_alta: bool = False       # p. ej. el id no se cambia después
    modelo: Any = None            # tipo "multi": modelo de los elementos marcados
    validar: Callable[[Any], None] | None = None   # comprobación extra del valor (lanza ValueError)


@dataclass
class Entidad:
    modelo: Any
    titulo: str
    singular: str
    campos: list[Campo]
    columnas: list[tuple[str, str]]          # (atributo o expresión, cabecera)
    orden: Any = None
    activo: str | None = None                # columna de baja lógica
    al_crear: Callable | None = None
    id_auto: Callable | None = None
    enlace: Callable | None = None           # url al panel de la entidad
    filtros: list[str] = field(default_factory=list)
    patron_id: str | None = None             # formato obligatorio del código (regex)


def _op_plantas():
    return [(p.id, f"{p.id} · {p.nombre}") for p in Planta.query.order_by(Planta.id)]


def _op_almacenes():
    return [(a.id, f"{a.provincia} · {a.nombre}") for a in Almacen.query.order_by(Almacen.provincia)]


def _op_tiendas():
    return [(t.id, f"{t.id} · {t.nombre}") for t in Tienda.query.order_by(Tienda.id)]


def _op_tipos(tipos):
    cap = capacidad_vehiculos()
    return lambda: [(t, f"{t} · {cap[t]['descripcion']}") for t in tipos]


def _op(d: dict):
    return lambda: list(d.items())


_HORA = "HH:MM"


def _rango(minimo=None, maximo=None):
    def validar(v):
        if (minimo is not None and v < minimo) or (maximo is not None and v > maximo):
            raise ValueError(f"{v:g} está fuera de rango (" + (f"mín. {minimo}" if minimo is not None else "")
                             + (", " if minimo is not None and maximo is not None else "")
                             + (f"máx. {maximo}" if maximo is not None else "") + ")")
    return validar


def _rango_dias(v: str):
    """«L-V», «M-V»…: lo que entiende la librería de rutas (modare.datos.dias_desde_rango)."""
    a, _, b = v.partition("-")
    if not (a in C.DIAS and b in C.DIAS and C.DIAS.index(a) <= C.DIAS.index(b)):
        raise ValueError(f"«{v}» no es un rango de días válido (p. ej. L-V, M-V; días {''.join(C.DIAS)})")


TAMANOS = {"pequeña": "Pequeña", "mediana": "Mediana", "grande": "Grande"}
VEH_MAX = {"": "Sin límite (hasta R18)", "VEH-FUR": "Furgoneta 3,5 t", "VEH-R75": "Rígido 7,5 t",
           "VEH-R12": "Rígido 12 t", "VEH-R18": "Rígido 18 t"}
CAMPOS_CAMION = [
    Campo("matricula", "Matrícula", requerido=True),
    Campo("operador", "Operador / transportista", ayuda="Operador A (€/km + €/h), Operador B (€/día) o flota propia"),
    Campo("conductor", "Conductor habitual"), Campo("telefono_conductor", "Teléfono del conductor"),
    Campo("estado", "Estado", "select", _op(ESTADOS_CAMION), requerido=True),
    Campo("itv_vence", "ITV vence", "date"), Campo("km_acumulados", "Km acumulados", "float"),
    Campo("carga_util_kg", "Carga útil (kg)", "float", ayuda="Vacío = la del tipo de vehículo"),
    Campo("capacidad_cajas", "Capacidad (cajas)", "int", ayuda="Vacío = la del tipo de vehículo"),
    Campo("capacidad_palets", "Capacidad (palés)", "int"),
    Campo("notas", "Notas", "textarea"),
]


def _rellenar_camion(c, _form):
    cap = capacidad_vehiculos()[c.tipo_vehiculo]
    c.carga_util_kg = c.carga_util_kg or cap["carga_util_kg"]
    c.capacidad_cajas = c.capacidad_cajas or cap["capacidad_cajas"]
    c.capacidad_palets = c.capacidad_palets or cap["capacidad_palets"]
    c.estado = c.estado or "disponible"


def nueva_password() -> str:
    """Aleatoria; empieza por letra o número (un «-» inicial se confunde con una fórmula al abrirla en Excel)."""
    while True:
        pw = secrets.token_urlsafe(8)
        if pw[0].isalnum():
            return pw


def crear_usuario(rol, entidad, password_hash: str | None = None) -> tuple[Usuario, str | None]:
    """Usuario de acceso de una tienda/planta/almacén. Sin hash, genera una contraseña aleatoria y la devuelve."""
    u = Usuario(username=entidad.id.lower(), nombre=entidad.nombre, rol=rol, password_hash=password_hash,
                **{f"{rol}_id": entidad.id})
    pw = None
    if not password_hash:
        pw = nueva_password()
        u.set_password(pw)
    db.session.add(u)
    return u, pw


def _usuario_para(rol, entidad):
    """Al dar de alta una tienda/planta/almacén se le crea su usuario con contraseña aleatoria. La contraseña se
    enseña una vez en la respuesta del alta (no por flash: viajaría en la cookie de sesión, que no va cifrada)."""
    u, pw = crear_usuario(rol, entidad)
    g.alta_usuario = (u.username, pw)


def _alta_tienda(t, _form):
    t.stock = [StockTienda(categoria_id=c.id, unidades=0) for c in Categoria.query]
    _usuario_para("tienda", t)


def _alta_planta(p, _form):
    p.stock = [StockPlanta(categoria_id=c.id, unidades=0) for c in Categoria.query]
    _usuario_para("planta", p)


def _alta_almacen(a, _form):
    _usuario_para("almacen", a)


def num_tienda(i: str) -> int | None:
    return int(i[3:]) if re.fullmatch(r"TD-\d+", i or "") else None


def max_num_tienda() -> int:
    return max((n for (i,) in db.session.query(Tienda.id) if (n := num_tienda(i)) is not None), default=0)


def _id_tienda(_obj):
    """Siguiente TD-### (numérico: TD-1000 va después de TD-999, no antes)."""
    return f"TD-{max_num_tienda() + 1:03d}"


def _alta_usuario(u, form):
    if not form.get("password"):
        raise ValueError("La contraseña es obligatoria al crear un usuario.")


ENTIDADES: dict[str, Entidad] = {
    "tiendas": Entidad(
        Tienda, "Tiendas", "tienda",
        [Campo("id", "Código", ayuda="Vacío = siguiente TD-###", solo_alta=True),
         Campo("nombre", "Nombre", requerido=True), Campo("subtipo", "Subtipo", ayuda="tienda_calle, corner…"),
         Campo("direccion", "Dirección"), Campo("municipio", "Municipio", requerido=True),
         Campo("provincia", "Provincia", requerido=True), Campo("ccaa", "Comunidad autónoma"),
         Campo("lat", "Latitud", "float", requerido=True), Campo("lon", "Longitud", "float", requerido=True),
         Campo("acceso_carretera", "Acceso por carretera", "bool",
               ayuda="En Baleares se desmarca siempre: el optimizador no la incluye (envío marítimo)"),
         Campo("superficie_m2", "Superficie (m²)", "float", validar=_rango(0)),
         Campo("categoria_tamano", "Tamaño", "select", _op(TAMANOS), requerido=True),
         Campo("almacenes_vetados", "Almacenes de los que NO quiere recibir ropa", "multi", _op_almacenes, modelo=Almacen,
               ayuda="Por defecto ninguno: el optimizador elige cada semana el almacén que mejor la sirve"),
         Campo("ventas_anuales_eur", "Ventas anuales (€)", "float", ayuda="Lo usa la estimación de demanda del optimizador",
               validar=_rango(0)),
         Campo("dias_recepcion", "Días de recepción", ayuda="L-V, M-V…", validar=_rango_dias),
         Campo("ventana_inicio", "Recepción desde", "time"), Campo("ventana_fin", "Recepción hasta", "time"),
         Campo("tiempo_descarga_min", "Tiempo de descarga (min)", "int", validar=_rango(1, 600)),
         Campo("vehiculo_max", "Vehículo máximo que puede llegar", "select", _op(VEH_MAX)),
         Campo("frecuencia_pedido", "Frecuencia de pedido automático", "select", _op(FRECUENCIAS), requerido=True),
         Campo("dia_pedido", "Día de pedido", "int", ayuda="Semanal: 0=lunes … 6=domingo. Mensual: día del mes (1-28)"),
         Campo("dias_seguridad", "Días de stock de seguridad", "int", validar=_rango(0, 60)),
         Campo("pedido_auto_activo", "Pedidos automáticos activos", "bool"),
         Campo("capacidad_max_prendas", "Capacidad máx. (prendas)", "int", validar=_rango(0)),
         Campo("responsable", "Responsable"), Campo("telefono", "Teléfono"), Campo("email", "Email"),
         Campo("activa", "Activa", "bool"), Campo("fecha_alta", "Fecha de alta", "date")],
        [("id", "Código"), ("nombre", "Nombre"), ("provincia", "Provincia"), ("categoria_tamano", "Tamaño"),
         ("almacenes_vetados_txt", "Vetados"), ("frecuencia_pedido", "Pedido auto"), ("stock_prendas", "Stock (prendas)"),
         ("activa", "Activa")],
        orden=Tienda.id, activo="activa", al_crear=_alta_tienda, id_auto=_id_tienda,
        enlace=lambda o: url_for("tienda.panel", tid=o.id), filtros=["provincia", "nombre", "id"], patron_id=r"TD-\d{3,}"),
    "plantas": Entidad(
        Planta, "Plantas", "planta",
        [Campo("id", "Código", requerido=True, ayuda="PL-XXX", solo_alta=True), Campo("nombre", "Nombre", requerido=True),
         Campo("direccion", "Dirección"), Campo("municipio", "Municipio", requerido=True),
         Campo("provincia", "Provincia", requerido=True),
         Campo("lat", "Latitud", "float", requerido=True), Campo("lon", "Longitud", "float", requerido=True),
         Campo("capacidad_salida_kg_semana", "Capacidad de salida (kg/semana)", "float", requerido=True),
         Campo("stock_max_prendas", "Stock máximo (prendas)", "int", requerido=True),
         Campo("hora_inicio_carga", "Carga desde", "time"), Campo("hora_fin_carga", "Carga hasta", "time"),
         Campo("responsable", "Responsable"), Campo("telefono", "Teléfono"),
         Campo("activa", "Activa", "bool"), Campo("apertura_prevista", "Apertura prevista")],
        [("id", "Código"), ("nombre", "Nombre"), ("provincia", "Provincia"), ("stock_prendas", "Stock"),
         ("stock_max_prendas", "Máximo"), ("ocupacion", "Ocupación"), ("capacidad_salida_kg_semana", "Salida kg/sem"),
         ("activa", "Activa")],
        orden=Planta.id, activo="activa", al_crear=_alta_planta, enlace=lambda o: url_for("planta.panel", pid=o.id)),
    "almacenes": Entidad(
        Almacen, "Almacenes", "almacén",
        [Campo("id", "Código", ayuda="Vacío = AL-<PROVINCIA>", solo_alta=True), Campo("nombre", "Nombre", requerido=True),
         Campo("provincia", "Provincia", requerido=True), Campo("poligono", "Polígono"), Campo("municipio", "Municipio"),
         Campo("lat", "Latitud", "float", requerido=True), Campo("lon", "Longitud", "float", requerido=True),
         Campo("stock_max_cajas", "Stock máximo (cajas)", "int", requerido=True),
         Campo("stock_cajas", "Stock actual (cajas)", "int"),
         Campo("capacidad_kg_semana", "Capacidad de reparto (kg/semana)", "float", requerido=True),
         Campo("hora_inicio", "Abre", "time"), Campo("hora_fin", "Cierra", "time"),
         Campo("dias_recepcion", "Días de recepción", ayuda="L-V", validar=_rango_dias),
         Campo("plantas_vetadas", "Plantas de las que NO quiere recibir ropa", "multi", _op_plantas, modelo=Planta,
               ayuda="Por defecto ninguna: el optimizador elige cada semana la planta que mejor lo repone"),
         Campo("responsable", "Responsable"), Campo("telefono", "Teléfono"), Campo("activo", "Activo", "bool")],
        [("id", "Código"), ("provincia", "Provincia"), ("municipio", "Municipio"), ("stock_cajas", "Cajas"),
         ("stock_max_cajas", "Máximo"), ("ocupacion", "Ocupación"), ("plantas_vetadas_txt", "Plantas vetadas"),
         ("planta_id", "Último plan"), ("activo", "Activo")],
        orden=Almacen.provincia, activo="activo", al_crear=_alta_almacen,
        id_auto=lambda a: f"AL-{_slug(a.provincia)}", enlace=lambda o: url_for("almacen.panel", aid=o.id), filtros=["provincia"]),
    "camiones-troncal": Entidad(
        CamionTroncal, "Camiones troncales (planta → almacén)", "camión troncal",
        [Campo("planta_id", "Planta base", "select", _op_plantas, requerido=True),
         Campo("tipo_vehiculo", "Tipo", "select", _op_tipos(["VEH-R12", "VEH-R18", "VEH-ART"]), requerido=True),
         *CAMPOS_CAMION],
        [("matricula", "Matrícula"), ("planta_id", "Planta"), ("tipo_vehiculo", "Tipo"), ("capacidad_cajas", "Cajas"),
         ("carga_util_kg", "Kg"), ("operador", "Operador"), ("estado", "Estado"), ("itv_vence", "ITV")],
        orden=CamionTroncal.planta_id, al_crear=_rellenar_camion, filtros=["planta_id", "matricula"]),
    "camiones-reparto": Entidad(
        CamionReparto, "Camiones de reparto (almacén → tienda)", "camión de reparto",
        [Campo("almacen_id", "Almacén base", "select", _op_almacenes, requerido=True),
         Campo("tipo_vehiculo", "Tipo", "select", _op_tipos(["VEH-FUR", "VEH-R75", "VEH-R12", "VEH-R18"]), requerido=True),
         *CAMPOS_CAMION],
        [("matricula", "Matrícula"), ("almacen_id", "Almacén"), ("tipo_vehiculo", "Tipo"), ("capacidad_cajas", "Cajas"),
         ("carga_util_kg", "Kg"), ("operador", "Operador"), ("estado", "Estado"), ("itv_vence", "ITV")],
        orden=CamionReparto.almacen_id, al_crear=_rellenar_camion, filtros=["almacen_id", "matricula"]),
    "usuarios": Entidad(
        Usuario, "Usuarios", "usuario",
        [Campo("username", "Usuario", requerido=True), Campo("nombre", "Nombre"), Campo("email", "Email"),
         Campo("rol", "Rol", "select", _op(ROLES), requerido=True),
         Campo("tienda_id", "Tienda (si rol = tienda)", "select", _op_tiendas),
         Campo("planta_id", "Planta (si rol = planta)", "select", _op_plantas),
         Campo("almacen_id", "Almacén (si rol = almacén)", "select", _op_almacenes),
         Campo("password", "Contraseña", "password", ayuda="Vacío = no cambiarla"),
         Campo("activo", "Activo", "bool")],
        [("username", "Usuario"), ("nombre", "Nombre"), ("rol", "Rol"), ("entidad_id", "Entidad"),
         ("ultimo_acceso", "Último acceso"), ("activo", "Activo")],
        orden=Usuario.username, activo="activo", al_crear=_alta_usuario, filtros=["username", "nombre", "rol"]),
    "categorias": Entidad(
        Categoria, "Categorías de prenda", "categoría",
        [Campo("id", "Código", requerido=True, solo_alta=True), Campo("segmento", "Segmento", requerido=True),
         Campo("tipo", "Tipo de prenda", requerido=True),
         Campo("kg_unidad", "Peso medio por prenda (kg)", "float", requerido=True, ayuda="Convierte prendas en cajas"),
         Campo("precio_unidad", "Precio medio (€)", "float", requerido=True),
         Campo("cuota_ventas", "Cuota de ventas", "float", requerido=True), Campo("orden", "Orden", "int")],
        [("id", "Código"), ("segmento", "Segmento"), ("tipo", "Tipo"), ("kg_unidad", "kg/prenda"),
         ("precio_unidad", "€/prenda"), ("cuota_ventas", "Cuota")],
        orden=Categoria.orden),
}


def _obtener(e: Entidad, oid: str):
    """Las tablas de camiones y usuarios tienen id numérico; el resto, un código (TD-001, PL-MAD…)."""
    numerico = isinstance(e.modelo.__table__.c.id.type, db.Integer)
    if numerico and not oid.isdigit():
        abort(404)
    return db.get_or_404(e.modelo, int(oid) if numerico else oid)


# Secciones del formulario de cada entidad (los campos no listados van al final, en «Otros datos»)
SECCIONES = {
    "tiendas": [("Identificación", ["id", "nombre", "subtipo", "categoria_tamano", "superficie_m2", "ventas_anuales_eur",
                                    "capacidad_max_prendas"]),
                ("Ubicación", ["direccion", "municipio", "provincia", "ccaa", "lat", "lon", "acceso_carretera"]),
                ("Recepción de mercancía", ["dias_recepcion", "ventana_inicio", "ventana_fin", "tiempo_descarga_min",
                                            "vehiculo_max"]),
                ("Pedido automático", ["pedido_auto_activo", "frecuencia_pedido", "dia_pedido", "dias_seguridad"]),
                ("Contacto y estado", ["responsable", "telefono", "email", "fecha_alta", "activa"]),
                ("Almacenes vetados", ["almacenes_vetados"])],
    "plantas": [("Identificación", ["id", "nombre", "activa", "apertura_prevista"]),
                ("Ubicación", ["direccion", "municipio", "provincia", "lat", "lon"]),
                ("Capacidad y horario", ["capacidad_salida_kg_semana", "stock_max_prendas", "hora_inicio_carga", "hora_fin_carga"]),
                ("Contacto", ["responsable", "telefono"])],
    "almacenes": [("Identificación", ["id", "nombre", "provincia", "activo"]),
                  ("Ubicación", ["poligono", "municipio", "lat", "lon"]),
                  ("Stock y capacidad", ["stock_cajas", "stock_max_cajas", "capacidad_kg_semana"]),
                  ("Horario", ["hora_inicio", "hora_fin", "dias_recepcion"]),
                  ("Plantas vetadas", ["plantas_vetadas"]),
                  ("Contacto", ["responsable", "telefono"])],
    "camiones-troncal": [("Vehículo", ["planta_id", "tipo_vehiculo", "matricula", "operador", "estado"]),
                         ("Capacidad", ["carga_util_kg", "capacidad_cajas", "capacidad_palets"]),
                         ("Conductor y mantenimiento", ["conductor", "telefono_conductor", "itv_vence", "km_acumulados", "notas"])],
    "camiones-reparto": [("Vehículo", ["almacen_id", "tipo_vehiculo", "matricula", "operador", "estado"]),
                         ("Capacidad", ["carga_util_kg", "capacidad_cajas", "capacidad_palets"]),
                         ("Conductor y mantenimiento", ["conductor", "telefono_conductor", "itv_vence", "km_acumulados", "notas"])],
    "usuarios": [("Cuenta", ["username", "nombre", "email", "password", "activo"]),
                 ("Permisos", ["rol", "tienda_id", "planta_id", "almacen_id"])],
}


def secciones(ent: str, e: Entidad) -> list[tuple[str | None, list[Campo]]]:
    por_nombre = {c.nombre: c for c in e.campos}
    out, usados = [], set()
    for titulo, nombres in SECCIONES.get(ent, []):
        cs = [por_nombre[n] for n in nombres if n in por_nombre]
        usados.update(c.nombre for c in cs)
        if cs:
            out.append((titulo, cs))
    resto = [c for c in e.campos if c.nombre not in usados]
    if resto:
        out.append(("Otros datos" if out else None, resto))
    return out


def _ent(nombre) -> Entidad:
    e = ENTIDADES.get(nombre)
    if not e:
        abort(404)
    return e


def _valor(campo: Campo, raw: str | None):
    if campo.tipo == "bool":
        return raw is not None
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        if campo.tipo == "int":
            return int(raw)
        if campo.tipo == "float":
            v = float(raw.replace(",", "."))
            if not math.isfinite(v):
                raise ValueError
            return v
        if campo.tipo == "date":
            return dt.date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"«{campo.etiqueta}» no es válido: {raw}")
    if campo.tipo == "time" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", raw):
        raise ValueError(f"«{campo.etiqueta}» debe tener formato {_HORA}")
    return raw


def _aplicar(e: Entidad, obj, form, alta: bool):
    for c in e.campos:
        if c.solo_alta and not alta:
            continue
        if c.tipo == "multi":
            ids = form.getlist(c.nombre)
            setattr(obj, c.nombre, c.modelo.query.filter(c.modelo.id.in_(ids)).all() if ids else [])
            continue
        v = _valor(c, form.get(c.nombre))
        if c.tipo == "password":
            if v:
                obj.set_password(v)
            continue
        if c.requerido and v is None:
            raise ValueError(f"«{c.etiqueta}» es obligatorio.")
        if v is not None and c.tipo == "select" and c.opciones:
            validos = [str(k) for k, _ in c.opciones() if k != ""]
            if str(v) not in validos:
                raise ValueError(f"«{c.etiqueta}» no admite «{v}»"
                                 + (f" (valores: {', '.join(validos)})." if len(validos) <= 8 else "."))
        if v is not None and c.validar:
            try:
                c.validar(v)
            except ValueError as ex:
                raise ValueError(f"«{c.etiqueta}»: {ex}")
        col = e.modelo.__table__.c.get(c.nombre)
        largo = getattr(getattr(col, "type", None), "length", None)
        if isinstance(v, str) and largo and len(v) > largo:   # PostgreSQL lo rechazaría (SQLite no avisa)
            raise ValueError(f"«{c.etiqueta}» admite como máximo {largo} caracteres (tiene {len(v)}).")
        setattr(obj, c.nombre, v)


# ----------------------------------------------------------------------------- ubicación en el mapa
ENT_GEO = ("tiendas", "plantas", "almacenes")


def _coordenadas_validas(lat, lon) -> bool:
    return math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180


def validar_id(e: Entidad, obj):
    if e.patron_id and obj.id and not re.fullmatch(e.patron_id, obj.id):
        raise ValueError(f"El código «{obj.id}» no tiene el formato esperado ({e.patron_id.replace(chr(92) + 'd{3,}', '###')}).")


def _validar_ubicacion(obj):
    """Plantas y almacenes solo en la España peninsular; las tiendas también en Baleares (gestion/peninsula.py)."""
    if obj.lat is None or obj.lon is None:
        return
    msg = peninsula.motivo_fuera(obj.lat, obj.lon, islas=isinstance(obj, Tienda))
    if msg:
        raise ValueError(msg)


AVISO_INSULAR = ("Tienda insular: se gestiona en la plataforma, pero el optimizador de rutas no la incluye "
                 "(transporte marítimo).")


def canon_provincia(v: str, conocidos: dict | None = None) -> tuple[str, bool]:
    """Grafía de la BD para una provincia escrita a mano (sin tildes, alias y nombres bilingües, como el geocodificador).
    Devuelve (valor, conocida). La asignación del optimizador empareja provincias por nombre exacto."""
    if conocidos is None:
        conocidos = _valores_bd(Tienda.provincia, Almacen.provincia, Planta.provincia)
    for n in [_sin_tildes(v)] + [_sin_tildes(x) for x in (v or "").split("/")]:
        n = _ALIAS_PROV.get(n, n)
        if n in conocidos:
            return conocidos[n], True
    return v, False


def normalizar(obj, provincias: dict | None = None) -> list[str]:
    """Tras `_aplicar`: provincia con la grafía de la BD, reglas entre campos de la tienda y Baleares.
    Devuelve avisos (no bloquean); ValueError si algo no vale."""
    avisos = []
    if isinstance(obj, (Tienda, Planta, Almacen)) and obj.provincia:
        obj.provincia, conocida = canon_provincia(obj.provincia, provincias)
        if not conocida:
            avisos.append(f"Provincia «{obj.provincia}» desconocida en la red: revisa la grafía "
                          "(el optimizador empareja por nombre exacto con el almacén provincial).")
    if isinstance(obj, Tienda):
        d = obj.dia_pedido
        if d is not None and obj.frecuencia_pedido == "semanal" and not 0 <= d <= 6:
            raise ValueError("«Día de pedido»: con pedido semanal va de 0 (lunes) a 6 (domingo).")
        if d is not None and obj.frecuencia_pedido == "mensual" and not 1 <= d <= 28:
            raise ValueError("«Día de pedido»: con pedido mensual va del 1 al 28.")
        if forzar_insular(obj):
            avisos.append(AVISO_INSULAR)
        elif obj.acceso_carretera is False:
            avisos.append("Tienda peninsular sin «Acceso por carretera»: el optimizador de rutas no la incluirá.")
    return avisos


def forzar_insular(obj) -> bool:
    """Una tienda en Baleares nunca tiene acceso por carretera (así la excluye el optimizador). True si es insular."""
    if isinstance(obj, Tienda) and obj.lat is not None and obj.lon is not None \
            and peninsula.zona(obj.lat, obj.lon) == "baleares":
        obj.acceso_carretera = False
        return True
    return False


def _prellenar(obj):
    """/nuevo?lat=..&lon=..[&municipio=..&provincia=..] rellena la ficha (clic en el mapa de la lista)."""
    try:
        lat, lon = (float(request.args.get(k, "").replace(",", ".")) for k in ("lat", "lon"))
    except ValueError:
        lat = lon = math.nan
    if _coordenadas_validas(lat, lon):
        obj.lat, obj.lon = round(lat, 6), round(lon, 6)
    for k in ("municipio", "provincia"):
        if request.args.get(k):
            setattr(obj, k, request.args[k].strip()[:120])


@bp.get("/geo/instalaciones")
def geo_instalaciones():
    """Todas las instalaciones con coordenadas, para dibujarlas como contexto en el mapa."""
    out = {}
    for clave, m, act in (("tiendas", Tienda, "activa"), ("plantas", Planta, "activa"), ("almacenes", Almacen, "activo")):
        out[clave] = [{"id": o.id, "nombre": o.nombre, "lat": o.lat, "lon": o.lon, "activo": bool(getattr(o, act))}
                      for o in m.query.order_by(m.id) if o.lat is not None and o.lon is not None]
    return jsonify(out)


NOMINATIM = "https://nominatim.openstreetmap.org"
_geo_lock = threading.Lock()
_geo_ultima = 0.0
_geo_cache: dict[str, Any] = {}


def _nominatim(ruta: str, **params):
    """Petición a Nominatim: 1/s como máximo (política de uso), User-Agent genérico y caché en memoria."""
    global _geo_ultima
    url = f"{NOMINATIM}/{ruta}?" + urllib.parse.urlencode({**params, "format": "jsonv2", "addressdetails": 1,
                                                          "accept-language": "es"})
    with _geo_lock:
        if url in _geo_cache:
            return _geo_cache[url]
        time.sleep(max(0.0, _geo_ultima + 1.05 - time.monotonic()))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ModaRE-gestion/1.0"})
            with urllib.request.urlopen(req, timeout=8, context=contexto_ssl()) as r:
                datos = json.load(r)
        finally:
            _geo_ultima = time.monotonic()
        if len(_geo_cache) > 500:
            _geo_cache.clear()
        _geo_cache[url] = datos
        return datos


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


# alias (sin tildes, minúsculas) -> grafía buscada en la BD (también sin tildes). Incluye las comunidades
# uniprovinciales, donde Nominatim no da provincia y se usa la comunidad (state)
_ALIAS_PROV = {"alacant": "alicante", "castello": "castellon", "vizcaya": "bizkaia", "guipuzcoa": "gipuzkoa",
               "araba": "araba/alava", "alava": "araba/alava", "la coruna": "a coruna", "orense": "ourense",
               "gerona": "girona", "lerida": "lleida", "baleares": "illes balears", "comunidad de madrid": "madrid",
               "principado de asturias": "asturias", "region de murcia": "murcia",
               "comunidad foral de navarra": "navarra", "nafarroa": "navarra"}
_ALIAS_CCAA = {"catalunya": "cataluna", "euskadi": "pais vasco", "comunidad valenciana": "comunitat valenciana",
               "comunidad foral de navarra": "navarra", "nafarroa": "navarra", "galiza": "galicia"}


def _valores_bd(*columnas) -> dict[str, str]:
    """Valores distintos de esas columnas: {sin tildes: grafía que ya usa la BD}."""
    out = {}
    for col in columnas:
        for (v,) in db.session.query(col).distinct():
            if v:
                out.setdefault(_sin_tildes(v), v)
    return out


def _canonico(address: dict, claves: tuple, alias: dict, conocidos: dict) -> str:
    """Primer valor de `claves` que coincide con la BD (sin tildes, con alias y nombres bilingües «Valencia/València»);
    si ninguno coincide, el alias conocido (p. ej. Navarra, que no está en la BD) o el primero tal cual."""
    primero = de_alias = ""
    for k in claves:
        for parte in (address.get(k) or "").split("/"):
            n = _sin_tildes(parte)
            if not n:
                continue
            primero = primero or parte.strip()
            if n in alias:
                n = de_alias = alias[n]
            if n in conocidos:
                return conocidos[n]
    return de_alias.title() or primero


def _normalizar(r: dict, lat=None, lon=None) -> dict:
    a = r.get("address") or {}
    provs = _valores_bd(Tienda.provincia, Almacen.provincia, Planta.provincia)
    # «province» no existe en las comunidades uniprovinciales: se recurre a «state»
    provincia = _canonico(a, ("province", "state"), _ALIAS_PROV, provs)
    calle = a.get("road") or a.get("pedestrian") or a.get("path") or ""
    return {
        "lat": round(float(lat if lat is not None else r["lat"]), 6),
        "lon": round(float(lon if lon is not None else r["lon"]), 6),
        "direccion": ", ".join(x for x in (calle, a.get("house_number")) if x),
        "municipio": next((a[k] for k in ("city", "town", "village", "municipality", "hamlet") if a.get(k)), ""),
        "provincia": provincia,
        "ccaa": _canonico(a, ("state",), _ALIAS_CCAA, _valores_bd(Tienda.ccaa)),
        "poligono": a.get("industrial", ""),
        "etiqueta": r.get("display_name", ""),
    }


def _geo_respuesta(f):
    try:
        return f()
    except (OSError, ValueError, KeyError) as ex:        # URLError y timeouts son OSError
        current_app.logger.warning("Nominatim: %s", ex)
        return jsonify(error="No se pudo consultar el servicio de direcciones (OpenStreetMap). "
                             "Puedes escribir los datos a mano."), 502


@bp.get("/geo/inversa")
def geo_inversa():
    try:
        lat, lon = float(request.args["lat"]), float(request.args["lon"])
    except (KeyError, ValueError):
        return jsonify(error="Faltan lat y lon."), 400
    if not _coordenadas_validas(lat, lon):
        return jsonify(error="Coordenadas fuera de rango."), 400

    def consulta():
        r = _nominatim("reverse", lat=round(lat, 5), lon=round(lon, 5), zoom=18)
        if not isinstance(r, dict) or "address" not in r:
            return jsonify(error="No hay ninguna dirección conocida en ese punto."), 404
        return jsonify(_normalizar(r, lat, lon))
    return _geo_respuesta(consulta)


@bp.get("/geo/buscar")
def geo_buscar():
    q = request.args.get("q", "").strip()[:200]
    if len(q) < 3:
        return jsonify(error="Escribe al menos 3 caracteres."), 400
    return _geo_respuesta(lambda: jsonify([_normalizar(r) for r in _nominatim("search", q=q, limit=5, countrycodes="es")]))


@bp.route("/<ent>")
def lista(ent):
    e = _ent(ent)
    q = e.modelo.query
    filtro = request.args.get("q", "").strip()
    if filtro and e.filtros:
        q = q.filter(db.or_(*[getattr(e.modelo, f).ilike(f"%{filtro}%") for f in e.filtros]))
    if e.activo and not request.args.get("todos"):
        q = q.filter(getattr(e.modelo, e.activo).is_(True))
    filas = q.order_by(e.orden).all() if e.orden is not None else q.all()
    return render_template("gestion/admin_lista.html", e=e, ent=ent, filas=filas, filtro=filtro)


@bp.route("/<ent>/nuevo", methods=["GET", "POST"])
@bp.route("/<ent>/<oid>/editar", methods=["GET", "POST"])
def editar(ent, oid=None):
    e = _ent(ent)
    alta = oid is None
    obj = e.modelo() if alta else _obtener(e, oid)
    geo = ent in ENT_GEO
    antes = None if alta or not geo else (obj.lat, obj.lon)
    if alta and geo and request.method == "GET":
        _prellenar(obj)
    if request.method == "POST":
        try:
            _aplicar(e, obj, request.form, alta)
            if geo and (alta or antes != (obj.lat, obj.lon)):      # no bloquea editar lo que ya estaba
                _validar_ubicacion(obj)
            avisos = normalizar(obj)
            if alta:
                if e.id_auto and not obj.id:
                    obj.id = e.id_auto(obj)
                validar_id(e, obj)
                for c in ("activa", "activo"):
                    if hasattr(obj, c) and getattr(obj, c) is None:
                        setattr(obj, c, True)
                db.session.add(obj)
                if e.al_crear:
                    e.al_crear(obj, request.form)
            if ent == "usuarios":
                _validar_usuario(obj)
            if ent.startswith("camiones"):
                _rellenar_camion(obj, request.form)
            db.session.commit()
            flash(f"Guardado: {e.singular} {obj.id}.")
            for av in avisos:
                flash(av)
            if geo and (alta or antes != (obj.lat, obj.lon)):
                flash("Las distancias de esta ubicación usan línea recta × factor_rodeo hasta regenerar "
                      "data/distancias.csv con  python calcular_distancias.py.")
            if g.get("alta_usuario"):           # la contraseña se enseña una vez, sin flash ni caché
                usuario, pw = g.alta_usuario
                resp = make_response(render_template("gestion/admin_creado.html", e=e, ent=ent, obj=obj,
                                                     usuario=usuario, password=pw))
                resp.headers["Cache-Control"] = "no-store"
                return resp
            return redirect(url_for("admin.lista", ent=ent))
        except (ValueError, IntegrityError, DataError) as ex:
            db.session.rollback()
            msg = ("Ya existe un registro con ese código o matrícula." if isinstance(ex, IntegrityError)
                   else "Algún dato no es válido para la base de datos (demasiado largo o con formato incorrecto)."
                   if isinstance(ex, DataError) else str(ex))
            flash(msg, "error")
            if not alta:
                obj = _obtener(e, oid)
    return render_template("gestion/admin_form.html", e=e, ent=ent, obj=obj, alta=alta, form=request.form,
                           secciones=secciones(ent, e), oid=oid, peninsula=peninsula.para_js(ent == "tiendas") if geo else None)


def _validar_usuario(u: Usuario):
    if u.id == current_user.id and (u.rol != "admin" or not u.activo):
        raise ValueError("No puedes quitarte el rol de administrador ni desactivar tu propio usuario.")
    if u.rol != "admin" or not u.activo:
        otros = Usuario.query.filter(Usuario.rol == "admin", Usuario.activo.is_(True), Usuario.id != u.id).count()
        with db.session.no_autoflush:      # el rol que tenía en la BD, antes de este cambio
            era_admin = u.id is not None and db.session.query(Usuario.rol).filter_by(id=u.id).scalar() == "admin"
        if era_admin and otros == 0:
            raise ValueError("Tiene que quedar al menos un administrador activo.")
    necesita = {"tienda": "tienda_id", "planta": "planta_id", "almacen": "almacen_id"}
    for rol, col in necesita.items():
        if u.rol == rol and not getattr(u, col):
            raise ValueError(f"Un usuario con rol {ROLES[rol]} necesita su {rol} asociada.")
        if u.rol != rol:
            setattr(u, col, None)
    u.username = u.username.lower()


@bp.post("/<ent>/<oid>/borrar")
def borrar(ent, oid):
    """Borrado definitivo si no tiene historial; si lo tiene, se da de baja (se conserva el histórico)."""
    e = _ent(ent)
    obj = _obtener(e, oid)
    if ent == "usuarios" and obj.id == current_user.id:
        flash("No puedes borrar tu propio usuario.", "error")
        return redirect(url_for("admin.lista", ent=ent))
    col_usuario = {"tiendas": Usuario.tienda_id, "plantas": Usuario.planta_id, "almacenes": Usuario.almacen_id}.get(ent)
    try:
        if _tiene_historial(ent, obj):
            raise IntegrityError("historial", None, Exception())
        if col_usuario is not None:          # su usuario se va con ella
            Usuario.query.filter(col_usuario == obj.id).delete()
        db.session.delete(obj)
        db.session.commit()
        flash(f"Eliminado: {e.singular} {oid}.")
    except IntegrityError:
        db.session.rollback()
        obj = _obtener(e, oid)
        if e.activo:
            setattr(obj, e.activo, False)
            if col_usuario is not None:
                Usuario.query.filter(col_usuario == obj.id).update({"activo": False})
            db.session.commit()
            flash("Tiene historial (pedidos, viajes, movimientos…): se ha dado de baja en lugar de borrarlo.")
        elif ent.startswith("camiones"):
            obj.estado = "baja"
            db.session.commit()
            flash("El camión tiene viajes registrados: se ha marcado como «de baja».")
        else:
            flash("No se puede borrar: está en uso.", "error")
    return redirect(url_for("admin.lista", ent=ent))


def _tiene_historial(ent: str, obj) -> bool:
    """Datos que no deben desaparecer al borrar: ventas (se borrarían en cascada), pedidos, movimientos…"""
    if ent == "tiendas":
        return any(q.first() for q in (Venta.query.filter_by(tienda_id=obj.id), Pedido.query.filter_by(tienda_id=obj.id),
                                        MovimientoStock.query.filter_by(entidad="tienda", entidad_id=obj.id)))
    if ent == "plantas":
        return any(q.first() for q in (Reposicion.query.filter_by(planta_id=obj.id),
                                        MovimientoStock.query.filter_by(entidad="planta", entidad_id=obj.id)))
    if ent == "almacenes":
        return any(q.first() for q in (Reposicion.query.filter_by(almacen_id=obj.id), Pedido.query.filter_by(almacen_id=obj.id),
                                        MovimientoStock.query.filter_by(entidad="almacen", entidad_id=obj.id)))
    if ent == "usuarios":
        return bool(MovimientoStock.query.filter_by(usuario_id=obj.id).first()
                    or Pedido.query.filter_by(creado_por_id=obj.id).first())
    return False


# ----------------------------------------------------------------------------- panel y consultas
@bp.route("/")
def panel():
    hoy = dt.date.today()
    sem = L.semana_entrega_para(hoy)
    kpi = {
        "tiendas": Tienda.query.filter_by(activa=True).count(),
        "plantas": Planta.query.filter_by(activa=True).count(),
        "almacenes": Almacen.query.filter_by(activo=True).count(),
        "camiones_t": CamionTroncal.query.filter(CamionTroncal.estado != "baja").count(),
        "camiones_r": CamionReparto.query.filter(CamionReparto.estado != "baja").count(),
        "pedidos_sem": Pedido.query.filter(Pedido.semana_entrega == sem, Pedido.estado != "cancelado").count(),
        "kg_sem": sum(p.kg for p in Pedido.query.filter(Pedido.semana_entrega == sem, Pedido.estado != "cancelado")),
        "en_reparto": Pedido.query.filter_by(estado="en_reparto").count(),
        "stock_almacenes": db.session.query(func.sum(Almacen.stock_cajas)).filter(Almacen.activo.is_(True)).scalar() or 0,
        "max_almacenes": db.session.query(func.sum(Almacen.stock_max_cajas)).filter(Almacen.activo.is_(True)).scalar() or 0,
    }
    plantas = Planta.query.filter_by(activa=True).order_by(Planta.id).all()
    almacenes = Almacen.query.filter_by(activo=True).order_by(Almacen.stock_cajas.desc()).all()
    adicionales = (Pedido.query.filter(Pedido.tipo == "adicional", Pedido.estado != "cancelado")
                   .order_by(Pedido.urgente.desc(), Pedido.creado.desc()).limit(6).all())
    return render_template("gestion/admin_panel.html", kpi=kpi, sem=sem, plantas=plantas, almacenes=almacenes,
                           alertas=L.alertas("admin"), cerrada=L.semana_cerrada(sem), adicionales=adicionales,
                           hay_plan=Viaje.query.filter_by(semana=sem).first() is not None)


@bp.route("/pedidos")
def pedidos():
    q = Pedido.query
    f = {k: request.args.get(k, "") for k in ("semana", "estado", "tipo", "tienda")}
    if f["semana"]:
        q = q.filter_by(semana_entrega=f["semana"])
    if f["estado"]:
        q = q.filter_by(estado=f["estado"])
    if f["tipo"]:
        q = q.filter_by(tipo=f["tipo"])
    if f["tienda"]:
        q = q.filter(Pedido.tienda_id.ilike(f"%{f['tienda']}%"))
    filas = q.order_by(Pedido.creado.desc(), Pedido.id.desc()).limit(500).all()
    semanas = [s for (s,) in db.session.query(Pedido.semana_entrega).distinct().order_by(Pedido.semana_entrega.desc())]
    return render_template("gestion/admin_pedidos.html", filas=filas, f=f, semanas=semanas)


@bp.route("/movimientos")
def movimientos():
    q = MovimientoStock.query
    ent = request.args.get("entidad_id", "").strip()
    if ent:
        q = q.filter(MovimientoStock.entidad_id.ilike(f"%{ent}%"))
    filas = q.order_by(MovimientoStock.id.desc()).limit(500).all()
    return render_template("gestion/admin_movimientos.html", filas=filas, ent=ent)


@bp.post("/automaticos")
def automaticos():
    n = L.generar_automaticos()
    flash(f"{n} pedidos automáticos nuevos." if n else "No tocaba generar ningún pedido automático nuevo.")
    return redirect(request.referrer or url_for("admin.panel"))


# ----------------------------------------------------------------------------- planificación semanal
def _sem(v):
    v = v or L.semana_entrega_para(dt.date.today())
    if not re.fullmatch(r"\d{4}-W\d{2}", v):
        abort(400, "Semana con formato AAAA-Www")
    return v


@bp.route("/planificacion")
def planificacion():
    semana = _sem(request.args.get("semana"))
    peds = Pedido.query.filter(Pedido.semana_entrega == semana, Pedido.estado != "cancelado").all()
    resumen = {e: 0 for e in ESTADOS_PEDIDO}
    for p in peds:
        resumen[p.estado] += 1
    viajes = Viaje.query.filter_by(semana=semana).order_by(Viaje.escalon.desc(), Viaje.origen, Viaje.t_salida).all()
    reps = Reposicion.query.filter_by(semana=semana).order_by(Reposicion.planta_id, Reposicion.almacen_id).all()
    flota_t = {}
    for c in CamionTroncal.query.filter(CamionTroncal.estado != "baja"):
        flota_t.setdefault(c.planta_id, []).append(c)
    flota_r = {}
    for c in CamionReparto.query.filter(CamionReparto.estado != "baja"):
        flota_r.setdefault(c.almacen_id, []).append(c)
    lunes = L.lunes_de(semana)
    return render_template(
        "gestion/admin_planificacion.html", semana=semana, peds=peds, resumen=resumen,
        sem_ant=L.semana_iso(lunes - dt.timedelta(days=7)), sem_sig=L.semana_iso(lunes + dt.timedelta(days=7)),
        sem_actual=L.semana_entrega_para(dt.date.today()),
        kg=round(sum(p.kg for p in peds), 1), cajas=sum(p.cajas for p in peds),
        tiendas=len({p.tienda_id for p in peds}), cerrada=L.semana_cerrada(semana), viajes=viajes, reps=reps,
        plan=cargar_plan(semana), trabajo=BD.estado_trabajo(semana), flota_t=flota_t, flota_r=flota_r)


@bp.post("/planificacion/cerrar")
def cerrar():
    semana = _sem(request.form.get("semana"))
    r = L.cerrar_semana(semana)
    flash(f"Semana {semana} cerrada: {r['pedidos']} pedidos de {r['tiendas']} tiendas ({r['kg']:,.0f} kg) "
          f"enviados al optimizador.")
    return redirect(url_for("admin.planificacion", semana=semana))


@bp.post("/planificacion/reabrir")
def reabrir():
    semana = _sem(request.form.get("semana"))
    try:
        L.reabrir_semana(semana)
        flash(f"Semana {semana} reabierta: los pedidos vuelven a estar pendientes.")
    except L.ErrorNegocio as e:
        flash(str(e), "error")
    return redirect(url_for("admin.planificacion", semana=semana))


@bp.post("/planificacion/optimizar")
def optimizar():
    semana = _sem(request.form.get("semana"))
    if not L.semana_cerrada(semana):
        flash("Cierra primero la semana para fijar los pedidos.", "error")
    elif not OPT.lanzar(current_app._get_current_object(), semana, segundos=int(request.form.get("segundos", 30)),
                        asignacion=request.form.get("asignacion", "optimo"),
                        precio_co2=float(request.form.get("precio_co2") or 0),
                        plantas_futuras=bool(request.form.get("plantas_futuras"))):
        flash("Ya hay una optimización en marcha para esta semana.", "error")
    return redirect(url_for("admin.planificacion", semana=semana))


@bp.get("/planificacion/estado")
def estado():
    return jsonify(BD.estado_trabajo(_sem(request.args.get("semana"))))


@bp.post("/planificacion/importar")
def importar():
    semana = _sem(request.form.get("semana"))
    try:
        flash(OPT.importar_plan(semana))
    except (FileNotFoundError, RuntimeError) as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("admin.planificacion", semana=semana))


@bp.post("/viaje/<int:vid>/camion")
def camion_viaje(vid):
    v = db.get_or_404(Viaje, vid)
    cid = request.form.get("camion_id")
    OPT.reasignar_camion(v, int(cid) if cid else None)
    flash(f"Camión de la ruta {v.nombre} actualizado.")
    return redirect(url_for("admin.planificacion", semana=v.semana) + "#viajes")


@bp.post("/exportar")
def exportar():
    OPT.exportar_maestros()
    flash("Tiendas, plantas y almacenes exportados a data/*.csv para la librería de rutas. "
          "Si has añadido o movido puntos, regenera las distancias con  python calcular_distancias.py")
    return redirect(request.referrer or url_for("admin.panel"))
