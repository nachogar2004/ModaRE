"""Administrador: gestiona usuarios y toda la red (alta/baja/edición), ve todos los datos y lanza la planificación."""
from __future__ import annotations

import datetime as dt
import re
import secrets
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import DataError, IntegrityError

from modare import config as C
from modare import bd as BD
from modare import distancias as DIST
from modare.datos import valor_parametro
from modare.planificador import cargar_plan

from . import logistica as L
from . import optimizador as OPT
from . import rol_requerido
from .modelos import (DIAS_SEMANA, ESTADOS_CAMION, ESTADOS_PEDIDO, FRECUENCIAS, ROLES, Ajuste, Almacen, CamionReparto,
                      CamionTroncal, Categoria, MovimientoStock, Parametro, Pedido, Planta, Reposicion, StockPlanta,
                      StockTienda, Tarifa, Tienda, TipoVehiculo, Usuario, Venta, Viaje, db)
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


def _usuario_para(rol, entidad):
    """Al dar de alta una tienda/planta/almacén se le crea su usuario con contraseña aleatoria."""
    pw = secrets.token_urlsafe(8)
    u = Usuario(username=entidad.id.lower(), nombre=entidad.nombre, rol=rol, **{f"{rol}_id": entidad.id})
    u.set_password(pw)
    db.session.add(u)
    flash(f"Usuario creado: «{u.username}» con contraseña «{pw}». Apúntala: no se volverá a mostrar.")


def _alta_tienda(t, _form):
    t.stock = [StockTienda(categoria_id=c.id, unidades=0) for c in Categoria.query]
    _usuario_para("tienda", t)


def _alta_planta(p, _form):
    p.stock = [StockPlanta(categoria_id=c.id, unidades=0) for c in Categoria.query]
    _usuario_para("planta", p)


def _alta_almacen(a, _form):
    _usuario_para("almacen", a)


def _id_tienda(_obj):
    """Siguiente TD-### (numérico: TD-1000 va después de TD-999, no antes)."""
    nums = [int(i.split("-")[1]) for (i,) in db.session.query(Tienda.id) if re.fullmatch(r"TD-\d+", i)]
    return f"TD-{max(nums, default=0) + 1:03d}"


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
         Campo("acceso_carretera", "Acceso por carretera", "bool", ayuda="Desmarcar en islas (envío marítimo)"),
         Campo("superficie_m2", "Superficie (m²)", "float"),
         Campo("categoria_tamano", "Tamaño", "select", _op(TAMANOS), requerido=True),
         Campo("almacenes_vetados", "Almacenes de los que NO quiere recibir ropa", "multi", _op_almacenes, modelo=Almacen,
               ayuda="Por defecto ninguno: el optimizador elige cada semana el almacén que mejor la sirve"),
         Campo("ventas_anuales_eur", "Ventas anuales (€)", "float", ayuda="Lo usa la estimación de demanda del optimizador"),
         Campo("dias_recepcion", "Días de recepción", ayuda="L-V, M-V…"),
         Campo("ventana_inicio", "Recepción desde", "time"), Campo("ventana_fin", "Recepción hasta", "time"),
         Campo("tiempo_descarga_min", "Tiempo de descarga (min)", "int"),
         Campo("vehiculo_max", "Vehículo máximo que puede llegar", "select", _op(VEH_MAX)),
         Campo("frecuencia_pedido", "Frecuencia de pedido automático", "select", _op(FRECUENCIAS), requerido=True),
         Campo("dia_pedido", "Día de pedido", "int", ayuda="Semanal: 0=lunes … 6=domingo. Mensual: día del mes (1-28)"),
         Campo("dias_seguridad", "Días de stock de seguridad", "int"),
         Campo("pedido_auto_activo", "Pedidos automáticos activos", "bool"),
         Campo("capacidad_max_prendas", "Capacidad máx. (prendas)", "int"),
         Campo("en_muestra", "En la muestra de pruebas", "bool", ayuda="optimizar.py --muestra usa solo estas tiendas"),
         Campo("planta_actual", "Planta que la sirve hoy", "select", _op_plantas,
               ayuda="Solo para el escenario «asignación actual» (comparar con lo que se hace hoy)"),
         Campo("responsable", "Responsable"), Campo("telefono", "Teléfono"), Campo("email", "Email"),
         Campo("activa", "Activa", "bool"), Campo("fecha_alta", "Fecha de alta", "date")],
        [("id", "Código"), ("nombre", "Nombre"), ("provincia", "Provincia"), ("categoria_tamano", "Tamaño"),
         ("almacenes_vetados_txt", "Vetados"), ("frecuencia_pedido", "Pedido auto"), ("stock_prendas", "Stock (prendas)"),
         ("activa", "Activa")],
        orden=Tienda.id, activo="activa", al_crear=_alta_tienda, id_auto=_id_tienda,
        enlace=lambda o: url_for("tienda.panel", tid=o.id), filtros=["provincia", "nombre", "id"]),
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
         Campo("dias_recepcion", "Días de recepción", ayuda="L-V"),
         Campo("tiempo_descarga_min", "Tiempo de descarga del camión troncal (min)", "int"),
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
            return float(raw.replace(",", "."))
        if campo.tipo == "date":
            return dt.date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"«{campo.etiqueta}» no es válido: {raw}")
    if campo.tipo == "time" and not re.fullmatch(r"\d{2}:\d{2}", raw):
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
        col = e.modelo.__table__.c.get(c.nombre)
        largo = getattr(getattr(col, "type", None), "length", None)
        if isinstance(v, str) and largo and len(v) > largo:   # PostgreSQL lo rechazaría (SQLite no avisa)
            raise ValueError(f"«{c.etiqueta}» admite como máximo {largo} caracteres (tiene {len(v)}).")
        setattr(obj, c.nombre, v)


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
    if request.method == "POST":
        punto_antes = _punto(ent, obj) if not alta else None
        try:
            _aplicar(e, obj, request.form, alta)
            if e.validar:
                e.validar(obj)
            if alta:
                if e.id_auto and not obj.id:
                    obj.id = e.id_auto(obj)
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
                           secciones=secciones(ent, e), oid=oid)


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
    # semanas que tienen pedidos activos (para saltar a ellas: la página abre por defecto la próxima entrega)
    con_pedidos = (db.session.query(Pedido.semana_entrega, Pedido.estado, func.count())
                   .filter(Pedido.estado.in_(L.ACTIVOS)).group_by(Pedido.semana_entrega, Pedido.estado).all())
    semanas_pedidos: dict[str, dict] = {}
    for sem_, est_, n_ in con_pedidos:
        semanas_pedidos.setdefault(sem_, {})[est_] = n_
    return render_template(
        "gestion/admin_planificacion.html", semana=semana, peds=peds, resumen=resumen,
        sem_ant=L.semana_iso(lunes - dt.timedelta(days=7)), sem_sig=L.semana_iso(lunes + dt.timedelta(days=7)),
        sem_actual=L.semana_entrega_para(dt.date.today()), semanas_pedidos=dict(sorted(semanas_pedidos.items())),
        kg=round(sum(p.kg for p in peds), 1), cajas=sum(p.cajas for p in peds),
        tiendas=len({p.tienda_id for p in peds}), cerrada=L.semana_cerrada(semana), viajes=viajes, reps=reps,
        plan=cargar_plan(semana), trabajo=BD.estado_trabajo(semana), flota_t=flota_t, flota_r=flota_r,
        dist=_estado_distancias(),
        externo=OPT.EXTERNO, trabajador=BD.trabajador_conectado() if OPT.EXTERNO else None)


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
    elif not OPT.lanzar(current_app._get_current_object(), semana,
                        asignacion=request.form.get("asignacion", "optimo"),
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
