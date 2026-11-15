"""Administrador: gestiona usuarios y toda la red (alta/baja/edición), ve todos los datos y lanza la planificación."""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import secrets
import threading
import time
import traceback
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
from modare import distancias as DIST
from modare.datos import valor_parametro
from modare.osrm import contexto_ssl
from modare.planificador import cargar_plan

from . import logistica as L
from . import optimizador as OPT
from . import rol_requerido
from .modelos import (DIAS_SEMANA, ESTADOS_CAMION, ESTADOS_PEDIDO, FRECUENCIAS, ROLES, Ajuste, Almacen, CamionReparto,
                      CamionTroncal, Categoria, Lote, MovimientoStock, Parametro, Pedido, Planta, Reposicion, StockPlanta,
                      StockTienda, Tarifa, Tienda, TipoVehiculo, Usuario, Venta, Viaje, db)
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
    solo_edicion: bool = False               # sin altas ni bajas (datos que el optimizador necesita completos)
    validar: Callable | None = None          # comprobaciones extra antes de guardar (lanza ValueError)
    patron_id: str | None = None             # formato obligatorio del código (regex)


def _op_plantas():
    return [(p.id, f"{p.id} · {p.nombre}") for p in Planta.query.order_by(Planta.id)]


def _op_almacenes():
    return [(a.id, f"{a.provincia} · {a.nombre}") for a in Almacen.query.order_by(Almacen.provincia)]


def _op_tiendas():
    return [(t.id, f"{t.id} · {t.nombre}") for t in Tienda.query.order_by(Tienda.id)]


def _op_tipos(tipos):
    def opciones():       # al pintar el formulario (los tipos de vehículo están en la base y se pueden editar)
        cap = capacidad_vehiculos()
        return [(t, f"{t} · {cap[t]['descripcion']}") for t in tipos if t in cap]
    return opciones


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
    # sin autoflush: leer los tipos de la base guardaría antes el camión a medio rellenar (sin capacidad) y fallaría
    with db.session.no_autoflush:
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


def _op_etiquetas():
    return [(x, x) for x in ("REAL", "ESTIMADO", "SINTÉTICO")]


def _validar_parametro(p: Parametro):
    """Un parámetro numérico tiene que seguir siéndolo (el optimizador hace cuentas con él)."""
    p.valor = p.valor.strip()
    with db.session.no_autoflush:
        antes = db.session.query(Parametro.valor).filter_by(id=p.id).scalar()
    if isinstance(valor_parametro(antes), float):
        v = valor_parametro(p.valor.replace(",", "."))
        if not isinstance(v, float):
            raise ValueError(f"«{p.id}» tiene que ser un número (antes valía {antes}).")
        p.valor = p.valor.replace(",", ".")


def _validar_positivos(obj):
    for c in obj.__table__.columns:
        v = getattr(obj, c.name)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
            raise ValueError(f"«{c.name}» no puede ser negativo.")


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
         Campo("en_muestra", "En la muestra de pruebas", "bool", ayuda="optimizar.py --muestra usa solo estas tiendas"),
         Campo("planta_actual", "Planta que la sirve hoy", "select", _op_plantas,
               ayuda="Solo para el escenario «asignación actual» (comparar con lo que se hace hoy)"),
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
         Campo("tiempo_descarga_min", "Tiempo de descarga del camión troncal (min)", "int", validar=_rango(1, 600)),
         Campo("coste_fijo_eur_semana", "Coste fijo (€/semana)", "float",
               ayuda="Alquiler + gastos + gestión. El optimizador lo paga si el almacén está abierto. Vacío = mediana del resto"),
         Campo("coste_manipulacion_eur_kg", "Coste de manipulación (€/kg)", "float",
               ayuda="€/h de operario ÷ kg/h. Vacío = mediana del resto"),
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
    "parametros": Entidad(
        Parametro, "Parámetros del optimizador", "parámetro",
        [Campo("id", "Clave", solo_alta=True), Campo("valor", "Valor", requerido=True,
                                                     ayuda="Número (con punto o coma decimal) o texto, según el parámetro"),
         Campo("unidad", "Unidad"), Campo("descripcion", "Descripción", "textarea"),
         Campo("etiqueta", "Etiqueta", "select", _op_etiquetas), Campo("fuente", "Fuente", "textarea")],
        [("id", "Clave"), ("valor", "Valor"), ("unidad", "Unidad"), ("etiqueta", "Etiqueta"), ("descripcion", "Descripción")],
        orden=Parametro.id, filtros=["id", "descripcion"], solo_edicion=True, validar=_validar_parametro),
    "vehiculos": Entidad(
        TipoVehiculo, "Tipos de vehículo", "tipo de vehículo",
        [Campo("id", "Código", solo_alta=True), Campo("descripcion", "Descripción", requerido=True),
         Campo("mma_t", "MMA (t)", "float"), Campo("carga_util_kg", "Carga útil (kg)", "float", requerido=True),
         Campo("volumen_m3", "Volumen (m³)", "float", requerido=True),
         Campo("capacidad_palets", "Capacidad (palés)", "int", requerido=True),
         Campo("combustible", "Combustible"),
         Campo("kgco2e_km_vacio", "Emisiones en vacío (kg CO₂e/km)", "float", requerido=True,
               ayuda="De aquí sale el consumo: L/km = kg CO₂e/km ÷ 2,51"),
         Campo("kgco2e_km_lleno", "Emisiones con carga completa (kg CO₂e/km)", "float", requerido=True),
         Campo("etiqueta", "Etiqueta", "select", _op_etiquetas), Campo("fuente", "Fuente (capacidad)", "textarea"),
         Campo("fuente_emisiones", "Fuente (emisiones)", "textarea")],
        [("id", "Código"), ("descripcion", "Descripción"), ("carga_util_kg", "Carga útil kg"), ("volumen_m3", "m³"),
         ("capacidad_palets", "Palés"), ("kgco2e_km_vacio", "kg CO₂e/km vacío"), ("kgco2e_km_lleno", "lleno")],
        orden=TipoVehiculo.carga_util_kg, solo_edicion=True, validar=_validar_positivos),
    "tarifas": Entidad(
        Tarifa, "Tarifas de transporte", "tarifa",
        [Campo("id", "Código", solo_alta=True),
         Campo("coste_fijo_dia_eur", "Coste fijo (€/día)", "float", ayuda="Operador B"),
         Campo("coste_km_eur", "Coste por km (€/km)", "float"),
         Campo("coste_hora_eur", "Coste por hora (€/h)", "float", ayuda="Operador A"),
         Campo("unidades_disponibles", "Unidades disponibles", "int",
               ayuda="Vacío = ilimitado. Si no, camiones por depósito y día"),
         Campo("etiqueta", "Etiqueta", "select", _op_etiquetas), Campo("fuente", "Fuente", "textarea")],
        [("id", "Código"), ("operador", "Operador"), ("tipo_vehiculo", "Tipo"), ("coste_fijo_dia_eur", "€/día"),
         ("coste_km_eur", "€/km"), ("coste_hora_eur", "€/h"), ("unidades_disponibles", "Unidades")],
        orden=Tarifa.id, solo_edicion=True, validar=_validar_positivos),
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
                ("Optimizador", ["en_muestra", "planta_actual"]),
                ("Contacto y estado", ["responsable", "telefono", "email", "fecha_alta", "activa"]),
                ("Almacenes vetados", ["almacenes_vetados"])],
    "plantas": [("Identificación", ["id", "nombre", "activa", "apertura_prevista"]),
                ("Ubicación", ["direccion", "municipio", "provincia", "lat", "lon"]),
                ("Capacidad y horario", ["capacidad_salida_kg_semana", "stock_max_prendas", "hora_inicio_carga", "hora_fin_carga"]),
                ("Contacto", ["responsable", "telefono"])],
    "almacenes": [("Identificación", ["id", "nombre", "provincia", "activo"]),
                  ("Ubicación", ["poligono", "municipio", "lat", "lon"]),
                  ("Stock y capacidad", ["stock_cajas", "stock_max_cajas", "capacidad_kg_semana"]),
                  ("Horario", ["hora_inicio", "hora_fin", "dias_recepcion", "tiempo_descarga_min"]),
                  ("Costes (optimizador)", ["coste_fijo_eur_semana", "coste_manipulacion_eur_kg"]),
                  ("Plantas vetadas", ["plantas_vetadas"]),
                  ("Contacto", ["responsable", "telefono"])],
    "camiones-troncal": [("Vehículo", ["planta_id", "tipo_vehiculo", "matricula", "operador", "estado"]),
                         ("Capacidad", ["carga_util_kg", "capacidad_cajas", "capacidad_palets"]),
                         ("Conductor y mantenimiento", ["conductor", "telefono_conductor", "itv_vence", "km_acumulados", "notas"])],
    "camiones-reparto": [("Vehículo", ["almacen_id", "tipo_vehiculo", "matricula", "operador", "estado"]),
                         ("Capacidad", ["carga_util_kg", "capacidad_cajas", "capacidad_palets"]),
                         ("Conductor y mantenimiento", ["conductor", "telefono_conductor", "itv_vence", "km_acumulados", "notas"])],
    "vehiculos": [("Capacidad", ["descripcion", "mma_t", "carga_util_kg", "volumen_m3", "capacidad_palets"]),
                  ("Emisiones y consumo", ["combustible", "kgco2e_km_vacio", "kgco2e_km_lleno"]),
                  ("Origen del dato", ["etiqueta", "fuente", "fuente_emisiones"])],
    "tarifas": [("Costes", ["coste_fijo_dia_eur", "coste_km_eur", "coste_hora_eur", "unidades_disponibles"]),
                ("Origen del dato", ["etiqueta", "fuente"])],
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
    if alta and e.solo_edicion:
        abort(404)
    obj = e.modelo() if alta else _obtener(e, oid)
    geo = ent in ENT_GEO
    antes = None if alta or not geo else (obj.lat, obj.lon)
    if alta and geo and request.method == "GET":
        _prellenar(obj)
    if request.method == "POST":
        punto_antes = _punto(ent, obj) if not alta else None
        try:
            _aplicar(e, obj, request.form, alta)
            if e.validar:
                e.validar(obj)
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
            _avisar_distancias(ent, obj, alta, punto_antes)
            for av in avisos:
                flash(av)
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


def _punto(ent: str, obj):
    """Lo que afecta a las distancias de un punto de la red: dónde está y si cuenta para el optimizador."""
    if ent not in ("tiendas", "plantas", "almacenes"):
        return None
    return (obj.lat, obj.lon, getattr(obj, "activa", getattr(obj, "activo", None)), getattr(obj, "acceso_carretera", True))


def _avisar_distancias(ent: str, obj, alta: bool, antes):
    """Si un punto se mueve, sus distancias por carretera ya no valen: se borran (el optimizador usará línea recta
    hasta recalcularlas). Si es nuevo o vuelve a estar activo, aún no tiene. En ambos casos se avisa."""
    despues = _punto(ent, obj)
    if despues is None:
        return
    movido = not alta and antes[:2] != despues[:2]
    if movido:
        DIST.olvidar([obj.id])
    if alta or movido or antes[2:] != despues[2:]:
        flash("Cambia la red: calcula las distancias por carretera de los puntos nuevos o movidos en "
              "Planificación → «Calcular distancias». Mientras tanto el optimizador usa línea recta × 1,26.")


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
    if e.solo_edicion:
        abort(404)
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
    pendientes = L.pendientes_de_planificar()
    ultimo = Lote.query.order_by(Lote.creado.desc(), Lote.codigo.desc()).first()
    kpi = {
        "tiendas": Tienda.query.filter_by(activa=True).count(),
        "plantas": Planta.query.filter_by(activa=True).count(),
        "almacenes": Almacen.query.filter_by(activo=True).count(),
        "camiones_t": CamionTroncal.query.filter(CamionTroncal.estado != "baja").count(),
        "camiones_r": CamionReparto.query.filter(CamionReparto.estado != "baja").count(),
        "pendientes": len(pendientes), "kg_pendientes": sum(p.kg for p in pendientes),
        "en_reparto": Pedido.query.filter_by(estado="en_reparto").count(),
        "stock_almacenes": db.session.query(func.sum(Almacen.stock_cajas)).filter(Almacen.activo.is_(True)).scalar() or 0,
        "max_almacenes": db.session.query(func.sum(Almacen.stock_max_cajas)).filter(Almacen.activo.is_(True)).scalar() or 0,
    }
    plantas = Planta.query.filter_by(activa=True).order_by(Planta.id).all()
    almacenes = Almacen.query.filter_by(activo=True).order_by(Almacen.stock_cajas.desc()).all()
    adicionales = (Pedido.query.filter(Pedido.tipo == "adicional", Pedido.estado != "cancelado")
                   .order_by(Pedido.urgente.desc(), Pedido.creado.desc()).limit(6).all())
    return render_template("gestion/admin_panel.html", kpi=kpi, plantas=plantas, almacenes=almacenes,
                           alertas=L.alertas("admin"), adicionales=adicionales, ultimo=ultimo,
                           ultimo_trabajo=BD.estado_trabajo(ultimo.codigo) if ultimo else {},
                           ultimo_rutas=Viaje.query.filter_by(semana=ultimo.codigo).count() if ultimo else 0)


@bp.route("/pedidos")
def pedidos():
    q = Pedido.query
    f = {k: request.args.get(k, "") for k in ("lote", "estado", "tipo", "tienda")}
    f["lote"] = f["lote"] or request.args.get("semana", "")
    if f["lote"] == "sin":
        q = q.filter(Pedido.semana_entrega.is_(None))          # pendientes de planificar
    elif f["lote"]:
        q = q.filter_by(semana_entrega=f["lote"])
    if f["estado"]:
        q = q.filter_by(estado=f["estado"])
    if f["tipo"]:
        q = q.filter_by(tipo=f["tipo"])
    if f["tienda"]:
        q = q.filter(Pedido.tienda_id.ilike(f"%{f['tienda']}%"))
    filas = q.order_by(Pedido.creado.desc(), Pedido.id.desc()).limit(500).all()
    lotes = [c for (c,) in db.session.query(Lote.codigo).order_by(Lote.creado.desc(), Lote.codigo.desc())]
    return render_template("gestion/admin_pedidos.html", filas=filas, f=f, lotes=lotes)


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
RE_LOTE = re.compile(r"L-\d{4,6}|\d{4}-W\d{2}")   # lotes (L-0001) y, de datos anteriores, semanas (2026-W48)


def _lote(v: str | None, obligatorio: bool = True) -> str | None:
    v = (v or "").strip()
    if not v:
        if obligatorio:
            abort(400, "Falta el lote")
        return None
    if not RE_LOTE.fullmatch(v):
        abort(400, "Lote no válido (formato L-0001)")
    return v


def _resumen_pedidos(peds) -> dict:
    return {"n": len(peds), "kg": round(sum(p.kg for p in peds), 1), "cajas": sum(p.cajas for p in peds),
            "tiendas": len({p.tienda_id for p in peds}), "urgentes": sum(1 for p in peds if p.urgente)}


@bp.route("/planificacion")
def planificacion():
    """Pedidos acumulados (sin lote) + lotes. Se cierra un lote cuando se quiere, sin esperar a ninguna semana."""
    pendientes = L.pendientes_de_planificar()
    lotes = Lote.query.order_by(Lote.creado.desc(), Lote.codigo.desc()).limit(30).all()
    codigo = _lote(request.args.get("lote") or request.args.get("semana"), obligatorio=False)
    if codigo is None and lotes and not pendientes:
        codigo = lotes[0].codigo                     # sin pendientes: se muestra el último lote
    lote = db.session.get(Lote, codigo) if codigo else None
    ctx = {}
    if codigo:
        peds = Pedido.query.filter(Pedido.semana_entrega == codigo, Pedido.estado != "cancelado").all()
        resumen = {e: 0 for e in ESTADOS_PEDIDO}
        for p in peds:
            resumen[p.estado] += 1
        flota_t, flota_r = {}, {}
        for c in CamionTroncal.query.filter(CamionTroncal.estado != "baja"):
            flota_t.setdefault(c.planta_id, []).append(c)
        for c in CamionReparto.query.filter(CamionReparto.estado != "baja"):
            flota_r.setdefault(c.almacen_id, []).append(c)
        ctx = dict(peds=peds, resumen=resumen, tot=_resumen_pedidos(peds),
                   viajes=Viaje.query.filter_by(semana=codigo).order_by(Viaje.escalon.desc(), Viaje.origen,
                                                                           Viaje.t_salida).all(),
                   reps=Reposicion.query.filter_by(semana=codigo).order_by(Reposicion.planta_id,
                                                                           Reposicion.almacen_id).all(),
                   plan=cargar_plan(codigo), trabajo=BD.estado_trabajo(codigo), flota_t=flota_t, flota_r=flota_r)
    # resumen de cada lote para la lista
    info_lotes = []
    for lt in lotes:
        ps = Pedido.query.filter(Pedido.semana_entrega == lt.codigo, Pedido.estado != "cancelado").all()
        info_lotes.append({"lote": lt, **_resumen_pedidos(ps), "trabajo": BD.estado_trabajo(lt.codigo),
                           "rutas": Viaje.query.filter_by(semana=lt.codigo).count(),
                           "entregados": sum(1 for p in ps if p.estado == "entregado")})
    return render_template(
        "gestion/admin_planificacion.html", codigo=codigo, lote=lote, pendientes=pendientes,
        pend=_resumen_pedidos(pendientes), lotes=info_lotes, inicio_defecto=L.siguiente_dia_laborable(),
        hoy=dt.date.today(), dist=_estado_distancias(),
        externo=OPT.EXTERNO, trabajador=BD.trabajador_conectado() if OPT.EXTERNO else None, **ctx)


@bp.post("/planificacion/cerrar")
def cerrar():
    """Cierra un lote con todos los pendientes y lanza el cálculo de sus rutas."""
    try:
        inicio = dt.date.fromisoformat(request.form.get("inicio") or L.siguiente_dia_laborable().isoformat())
    except ValueError:
        flash("Fecha de inicio no válida.", "error")
        return redirect(url_for("admin.planificacion"))
    try:
        lote = L.cerrar_lote(inicio, current_user)
    except L.ErrorNegocio as e:
        flash(str(e), "error")
        return redirect(url_for("admin.planificacion"))
    r = _resumen_pedidos(lote.pedidos)
    lanzado = OPT.lanzar(current_app._get_current_object(), lote.codigo,
                         asignacion=request.form.get("asignacion", "optimo"),
                         plantas_futuras=bool(request.form.get("plantas_futuras")))
    flash(f"Lote {lote.codigo} cerrado: {r['n']} pedidos de {r['tiendas']} tiendas ({r['kg']:,.0f} kg), reparto desde "
          f"el {lote.inicio:%d/%m}. " + ("Calculando sus rutas…" if lanzado else "No se pudo lanzar el cálculo."))
    return redirect(url_for("admin.planificacion", lote=lote.codigo))


@bp.post("/planificacion/reabrir")
def reabrir():
    codigo = _lote(request.form.get("lote"))
    try:
        L.reabrir_lote(codigo)
        flash(f"Lote {codigo} deshecho: sus pedidos vuelven a «pendientes de planificar».")
        return redirect(url_for("admin.planificacion"))
    except L.ErrorNegocio as e:
        flash(str(e), "error")
        return redirect(url_for("admin.planificacion", lote=codigo))


@bp.post("/planificacion/optimizar")
def optimizar():
    """Vuelve a calcular las rutas de un lote ya cerrado."""
    codigo = _lote(request.form.get("lote"))
    if not db.session.get(Lote, codigo):
        flash("Ese lote no existe.", "error")
    elif not OPT.lanzar(current_app._get_current_object(), codigo,
                        asignacion=request.form.get("asignacion", "optimo"),
                        plantas_futuras=bool(request.form.get("plantas_futuras"))):
        flash("Ya hay un cálculo en marcha para este lote.", "error")
    return redirect(url_for("admin.planificacion", lote=codigo))


@bp.get("/planificacion/estado")
def estado():
    return jsonify(BD.estado_trabajo(_lote(request.args.get("lote") or request.args.get("semana"))))


@bp.post("/planificacion/importar")
def importar():
    codigo = _lote(request.form.get("lote"))
    try:
        flash(OPT.importar_plan(codigo))
    except (FileNotFoundError, RuntimeError) as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("admin.planificacion", lote=codigo))


@bp.post("/viaje/<int:vid>/camion")
def camion_viaje(vid):
    v = db.get_or_404(Viaje, vid)
    cid = request.form.get("camion_id")
    OPT.reasignar_camion(v, int(cid) if cid else None)
    flash(f"Camión de la ruta {v.nombre} actualizado.")
    return redirect(url_for("admin.planificacion", lote=v.semana) + "#viajes")


# ----------------------------------------------------------------------------- distancias por carretera
_calculando_dist = threading.Lock()


def _estado_distancias() -> dict:
    estado, _, msg = (Ajuste.get("distancias_estado") or "").partition("|")
    if estado == "calculando" and not _calculando_dist.locked():
        estado, msg = "error", "El cálculo se interrumpió (¿se reinició el servidor?). Vuelve a lanzarlo."
    try:
        faltan = DIST.sin_distancias()
    except Exception as ex:  # noqa: BLE001 — la página de planificación no debe caer por esto
        faltan, msg = [], f"{msg} (no se pudo comprobar: {ex})"
    return {"faltan": faltan, "estado": estado, "msg": msg}


def _poner_estado_distancias(app, estado: str, msg: str):
    with app.app_context():
        Ajuste.set("distancias_estado", f"{estado}|{msg}"[:200])
        db.session.commit()


@bp.post("/distancias")
def distancias():
    """Calcula con OSRM (servidor público, 1 consulta/s) las distancias que faltan, en segundo plano."""
    app = current_app._get_current_object()
    todos = bool(request.form.get("todos"))
    if not _calculando_dist.acquire(blocking=False):
        flash("Ya se están calculando las distancias.", "error")
        return redirect(request.referrer or url_for("admin.planificacion"))
    _poner_estado_distancias(app, "calculando", "todos los pares" if todos else "puntos nuevos o movidos")

    def _run():
        try:
            r = DIST.calcular(todos=todos, log=lambda *a: None)
            estado, msg = "ok", (f"{dt.datetime.now():%d/%m %H:%M} · {r['pares']} pares de {r['puntos']} puntos: "
                                 f"{r['osrm']} por carretera, {r['linea_recta']} en línea recta (OSRM sin ruta)")
        except Exception as ex:  # noqa: BLE001 — se enseña en la web
            traceback.print_exc()
            estado, msg = "error", str(ex)
        finally:
            _calculando_dist.release()
        _poner_estado_distancias(app, estado, msg)

    threading.Thread(target=_run, daemon=True).start()
    flash("Calculando distancias por carretera en segundo plano (unos segundos por punto). Recarga para ver el resultado.")
    return redirect(request.referrer or url_for("admin.planificacion"))
