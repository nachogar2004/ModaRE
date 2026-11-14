"""Tablas de la aplicación de gestión (SQLite vía SQLAlchemy).

Flujo físico:   planta ──(camión troncal)──▶ almacén ──(camión de reparto)──▶ tienda
Unidades:       tiendas y plantas guardan stock en PRENDAS por categoría (segmento × tipo de prenda);
                los almacenes son cross-docks y guardan CAJAS MIXTAS (sin desglose por categoría).
                1 caja ≈ modare.config.KG_POR_CAJA kg; cada categoría tiene un peso medio por prenda.
"""
from __future__ import annotations

import datetime as dt
import math

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.security import check_password_hash, generate_password_hash

from modare import config as C

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _fk_sqlite(con, _):
    """SQLite no comprueba claves foráneas si no se le pide: así no se borra una tienda con historial."""
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def ahora() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def semana_iso(fecha: dt.date) -> str:
    y, w, _ = fecha.isocalendar()
    return f"{y}-W{w:02d}"


# ----------------------------------------------------------------------------- catálogo
SEGMENTOS = ["Mujer", "Hombre", "Niños"]
TIPOS_PRENDA = ["Partes de arriba", "Partes de abajo", "Abrigos", "Zapatos", "Bolsos"]

ROLES = {"admin": "Administrador", "tienda": "Tienda", "planta": "Planta", "almacen": "Almacén"}
FRECUENCIAS = {"diaria": "Diaria (L-V)", "semanal": "Semanal", "mensual": "Mensual"}
DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

ESTADOS_PEDIDO = {
    "pendiente": "Pendiente",          # creado, aún se puede editar/cancelar
    "confirmado": "Confirmado",        # semana cerrada: enviado al optimizador
    "planificado": "Planificado",      # tiene ruta y camión de reparto
    "en_reparto": "En reparto",        # el almacén lo ha cargado en el camión
    "entregado": "Entregado",          # la tienda ha confirmado la recepción
    "cancelado": "Cancelado",
}
ESTADOS_REPOSICION = {"planificada": "Planificada", "expedida": "Expedida", "recibida": "Recibida"}
ESTADOS_VIAJE = {"planificado": "Planificado", "en_curso": "En curso", "completado": "Completado"}
ESTADOS_CAMION = {"disponible": "Disponible", "en_ruta": "En ruta", "mantenimiento": "Mantenimiento", "baja": "De baja"}


class Categoria(db.Model):
    """Una categoría de prenda = segmento × tipo (15 en total). Es el 'SKU' de stock y ventas."""
    __tablename__ = "categorias"
    id = db.Column(db.String(16), primary_key=True)          # p. ej. MUJ-ARR
    segmento = db.Column(db.String(20), nullable=False)
    tipo = db.Column(db.String(30), nullable=False)
    kg_unidad = db.Column(db.Float, nullable=False)          # peso medio de una prenda (para pasar a cajas)
    precio_unidad = db.Column(db.Float, nullable=False)      # precio medio de venta (€)
    cuota_ventas = db.Column(db.Float, nullable=False)       # fracción de las ventas (para datos sintéticos)
    orden = db.Column(db.Integer, default=0)

    @property
    def nombre(self):
        return f"{self.segmento} · {self.tipo}"


# ----------------------------------------------------------------------------- usuarios
class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    nombre = db.Column(db.String(120))
    email = db.Column(db.String(120))
    rol = db.Column(db.String(10), nullable=False)
    tienda_id = db.Column(db.String(16), db.ForeignKey("tiendas.id"))
    planta_id = db.Column(db.String(16), db.ForeignKey("plantas.id"))
    almacen_id = db.Column(db.String(24), db.ForeignKey("almacenes.id"))
    activo = db.Column(db.Boolean, default=True, nullable=False)
    creado = db.Column(db.DateTime, default=ahora)
    ultimo_acceso = db.Column(db.DateTime)

    tienda = db.relationship("Tienda")
    planta = db.relationship("Planta")
    almacen = db.relationship("Almacen")

    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)

    @property
    def is_active(self):  # Flask-Login: un usuario desactivado no puede entrar
        return self.activo

    @property
    def entidad_id(self):
        return {"tienda": self.tienda_id, "planta": self.planta_id, "almacen": self.almacen_id}.get(self.rol)


# ----------------------------------------------------------------------------- red logística
class Planta(db.Model):
    __tablename__ = "plantas"
    id = db.Column(db.String(16), primary_key=True)          # PL-MAD
    nombre = db.Column(db.String(120), nullable=False)
    direccion = db.Column(db.String(200))
    municipio = db.Column(db.String(80))
    provincia = db.Column(db.String(60))
    lat = db.Column(db.Float, nullable=False)
    lon = db.Column(db.Float, nullable=False)
    capacidad_salida_kg_semana = db.Column(db.Float, nullable=False, default=8000)
    stock_max_prendas = db.Column(db.Integer, nullable=False, default=100000)
    hora_inicio_carga = db.Column(db.String(5), default="06:00")
    hora_fin_carga = db.Column(db.String(5), default="18:00")
    responsable = db.Column(db.String(120))
    telefono = db.Column(db.String(30))
    activa = db.Column(db.Boolean, default=True, nullable=False)
    apertura_prevista = db.Column(db.String(20))

    stock = db.relationship("StockPlanta", cascade="all, delete-orphan", lazy="selectin")
    camiones = db.relationship("CamionTroncal", back_populates="planta")

    @property
    def stock_prendas(self):
        return sum(s.unidades for s in self.stock)

    @property
    def ocupacion(self):
        return self.stock_prendas / self.stock_max_prendas if self.stock_max_prendas else 0


class Almacen(db.Model):
    """Cross-dock provincial: recibe cajas mixtas de una planta y las reparte a las tiendas."""
    __tablename__ = "almacenes"
    id = db.Column(db.String(24), primary_key=True)          # AL-MADRID
    nombre = db.Column(db.String(120), nullable=False)
    provincia = db.Column(db.String(60), nullable=False)
    poligono = db.Column(db.String(120))
    municipio = db.Column(db.String(80))
    lat = db.Column(db.Float, nullable=False)
    lon = db.Column(db.Float, nullable=False)
    stock_max_cajas = db.Column(db.Integer, nullable=False, default=300)
    stock_cajas = db.Column(db.Integer, nullable=False, default=0)
    capacidad_kg_semana = db.Column(db.Float, nullable=False, default=1500)
    hora_inicio = db.Column(db.String(5), default="06:00")
    hora_fin = db.Column(db.String(5), default="20:00")
    dias_recepcion = db.Column(db.String(5), default="L-V")
    planta_id = db.Column(db.String(16), db.ForeignKey("plantas.id"))   # planta que lo repone (último plan)
    responsable = db.Column(db.String(120))
    telefono = db.Column(db.String(30))
    activo = db.Column(db.Boolean, default=True, nullable=False)

    planta = db.relationship("Planta")
    camiones = db.relationship("CamionReparto", back_populates="almacen")

    @property
    def ocupacion(self):
        return self.stock_cajas / self.stock_max_cajas if self.stock_max_cajas else 0


class Tienda(db.Model):
    __tablename__ = "tiendas"
    id = db.Column(db.String(16), primary_key=True)          # TD-001
    nombre = db.Column(db.String(160), nullable=False)
    subtipo = db.Column(db.String(30))
    direccion = db.Column(db.String(200))
    municipio = db.Column(db.String(80))
    provincia = db.Column(db.String(60))
    ccaa = db.Column(db.String(60))
    lat = db.Column(db.Float, nullable=False)
    lon = db.Column(db.Float, nullable=False)
    acceso_carretera = db.Column(db.Boolean, default=True, nullable=False)
    superficie_m2 = db.Column(db.Float)
    categoria_tamano = db.Column(db.String(10), default="mediana")       # pequeña / mediana / grande
    almacen_id = db.Column(db.String(24), db.ForeignKey("almacenes.id"))  # almacén habitual (provincial)
    ventas_anuales_eur = db.Column(db.Float)
    # recepción de mercancía
    dias_recepcion = db.Column(db.String(5), default="L-V")
    ventana_inicio = db.Column(db.String(5), default="10:00")
    ventana_fin = db.Column(db.String(5), default="14:00")
    tiempo_descarga_min = db.Column(db.Integer, default=30)
    vehiculo_max = db.Column(db.String(10))                   # tipo de vehículo más grande que puede llegar
    # pedidos automáticos
    frecuencia_pedido = db.Column(db.String(10), default="semanal", nullable=False)
    dia_pedido = db.Column(db.Integer, default=3)             # semanal: 0=lunes..6; mensual: día del mes (1-28)
    dias_seguridad = db.Column(db.Integer, default=3)         # colchón de stock de seguridad (días de venta)
    pedido_auto_activo = db.Column(db.Boolean, default=True, nullable=False)
    capacidad_max_prendas = db.Column(db.Integer)             # lo que cabe en tienda + trastienda
    # contacto
    responsable = db.Column(db.String(120))
    telefono = db.Column(db.String(30))
    email = db.Column(db.String(120))
    activa = db.Column(db.Boolean, default=True, nullable=False)
    fecha_alta = db.Column(db.Date, default=dt.date.today)

    almacen = db.relationship("Almacen")
    stock = db.relationship("StockTienda", cascade="all, delete-orphan", lazy="selectin")

    @property
    def stock_prendas(self):
        return sum(s.unidades for s in self.stock)


class StockPlanta(db.Model):
    __tablename__ = "stock_planta"
    planta_id = db.Column(db.String(16), db.ForeignKey("plantas.id", ondelete="CASCADE"), primary_key=True)
    categoria_id = db.Column(db.String(16), db.ForeignKey("categorias.id"), primary_key=True)
    unidades = db.Column(db.Integer, default=0, nullable=False)
    categoria = db.relationship("Categoria", lazy="joined")


class StockTienda(db.Model):
    __tablename__ = "stock_tienda"
    tienda_id = db.Column(db.String(16), db.ForeignKey("tiendas.id", ondelete="CASCADE"), primary_key=True)
    categoria_id = db.Column(db.String(16), db.ForeignKey("categorias.id"), primary_key=True)
    unidades = db.Column(db.Integer, default=0, nullable=False)
    categoria = db.relationship("Categoria", lazy="joined")


class Venta(db.Model):
    """Ventas diarias por categoría: son la base de los pedidos automáticos."""
    __tablename__ = "ventas"
    id = db.Column(db.Integer, primary_key=True)
    tienda_id = db.Column(db.String(16), db.ForeignKey("tiendas.id", ondelete="CASCADE"), nullable=False, index=True)
    categoria_id = db.Column(db.String(16), db.ForeignKey("categorias.id"), nullable=False)
    fecha = db.Column(db.Date, nullable=False, index=True)
    unidades = db.Column(db.Integer, nullable=False)
    sintetica = db.Column(db.Boolean, default=False)


class MovimientoStock(db.Model):
    """Histórico de todo cambio de stock (auditoría para el administrador)."""
    __tablename__ = "movimientos"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, default=ahora, index=True)
    entidad = db.Column(db.String(10), nullable=False)        # tienda / planta / almacen
    entidad_id = db.Column(db.String(24), nullable=False, index=True)
    categoria_id = db.Column(db.String(16))                   # None para cajas de almacén
    delta = db.Column(db.Integer, nullable=False)             # prendas (tienda/planta) o cajas (almacén)
    motivo = db.Column(db.String(40), nullable=False)         # venta, ajuste, recepcion, expedicion...
    referencia = db.Column(db.String(40))                     # pedido / reposición relacionados
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    usuario = db.relationship("Usuario")


# ----------------------------------------------------------------------------- pedidos
class Pedido(db.Model):
    """Pedido de una tienda: automático (por ventas) o adicional (lo pide la tienda)."""
    __tablename__ = "pedidos"
    id = db.Column(db.Integer, primary_key=True)
    tienda_id = db.Column(db.String(16), db.ForeignKey("tiendas.id"), nullable=False, index=True)
    tipo = db.Column(db.String(12), nullable=False)           # automatico / adicional
    estado = db.Column(db.String(12), default="pendiente", nullable=False, index=True)
    creado = db.Column(db.DateTime, default=ahora)
    fecha_generacion = db.Column(db.Date)                     # día al que corresponde (automáticos)
    semana_entrega = db.Column(db.String(8), nullable=False, index=True)  # 2026-W47
    urgente = db.Column(db.Boolean, default=False)
    comentario = db.Column(db.Text)
    almacen_id = db.Column(db.String(24), db.ForeignKey("almacenes.id"))  # almacén que lo sirve (del plan)
    viaje_id = db.Column(db.Integer, db.ForeignKey("viajes.id"))
    entregado = db.Column(db.DateTime)
    creado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))

    tienda = db.relationship("Tienda")
    almacen = db.relationship("Almacen")
    viaje = db.relationship("Viaje")
    lineas = db.relationship("LineaPedido", cascade="all, delete-orphan", lazy="selectin")
    creado_por = db.relationship("Usuario")

    @property
    def unidades(self):
        return sum(l.unidades for l in self.lineas)

    @property
    def kg(self):
        return round(sum(l.unidades * l.categoria.kg_unidad for l in self.lineas), 1)

    @property
    def cajas(self):
        return math.ceil(self.kg / C.KG_POR_CAJA) if self.kg else 0

    @property
    def codigo(self):
        return f"P-{self.id:05d}"


class LineaPedido(db.Model):
    __tablename__ = "lineas_pedido"
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedidos.id", ondelete="CASCADE"), primary_key=True)
    categoria_id = db.Column(db.String(16), db.ForeignKey("categorias.id"), primary_key=True)
    unidades = db.Column(db.Integer, nullable=False)
    categoria = db.relationship("Categoria", lazy="joined")


# ----------------------------------------------------------------------------- camiones
class _CamionBase:
    matricula = db.Column(db.String(12), unique=True, nullable=False)
    tipo_vehiculo = db.Column(db.String(10), nullable=False)   # VEH-FUR, VEH-R75, VEH-R12, VEH-R18, VEH-ART
    operador = db.Column(db.String(20), default="Operador A")   # transportista / propio
    carga_util_kg = db.Column(db.Float, nullable=False)
    capacidad_cajas = db.Column(db.Integer, nullable=False)
    capacidad_palets = db.Column(db.Integer)
    conductor = db.Column(db.String(120))
    telefono_conductor = db.Column(db.String(30))
    estado = db.Column(db.String(14), default="disponible", nullable=False)
    itv_vence = db.Column(db.Date)
    km_acumulados = db.Column(db.Float, default=0)
    notas = db.Column(db.Text)


class CamionTroncal(_CamionBase, db.Model):
    """Camiones grandes que hacen planta → almacén (no entran en las ciudades)."""
    __tablename__ = "camiones_troncal"
    id = db.Column(db.Integer, primary_key=True)
    planta_id = db.Column(db.String(16), db.ForeignKey("plantas.id"), nullable=False)
    planta = db.relationship("Planta", back_populates="camiones")

    @property
    def base_id(self):
        return self.planta_id


class CamionReparto(_CamionBase, db.Model):
    """Vehículos que hacen almacén → tienda (furgonetas y rígidos que sí entran en ciudad)."""
    __tablename__ = "camiones_reparto"
    id = db.Column(db.Integer, primary_key=True)
    almacen_id = db.Column(db.String(24), db.ForeignKey("almacenes.id"), nullable=False)
    almacen = db.relationship("Almacen", back_populates="camiones")

    @property
    def base_id(self):
        return self.almacen_id


# ----------------------------------------------------------------------------- planificación
class Viaje(db.Model):
    """Una ruta del plan del optimizador (code/salidas/<semana>/plan.json) con su camión asignado."""
    __tablename__ = "viajes"
    id = db.Column(db.Integer, primary_key=True)
    semana = db.Column(db.String(8), nullable=False, index=True)
    nombre = db.Column(db.String(30), nullable=False)          # T-MAD-01 / R-SEVILLA-02
    escalon = db.Column(db.String(8), nullable=False)          # troncal / reparto
    origen = db.Column(db.String(24), nullable=False)          # planta o almacén
    tipo_vehiculo = db.Column(db.String(10))
    operador = db.Column(db.String(20))
    salida = db.Column(db.String(12))                          # 'L 07:30'
    vuelta = db.Column(db.String(12))
    t_salida = db.Column(db.Integer)                           # minuto de la semana
    t_vuelta = db.Column(db.Integer)
    km = db.Column(db.Float)
    kg = db.Column(db.Float)
    cajas = db.Column(db.Integer)
    coste_eur = db.Column(db.Float)
    co2_kg = db.Column(db.Float)
    recorrido = db.Column(db.Text)                             # 'PL-MAD > AL-TOLEDO > PL-MAD'
    camion_troncal_id = db.Column(db.Integer, db.ForeignKey("camiones_troncal.id"))
    camion_reparto_id = db.Column(db.Integer, db.ForeignKey("camiones_reparto.id"))
    estado = db.Column(db.String(12), default="planificado")   # planificado / en_curso / completado

    camion_troncal = db.relationship("CamionTroncal")
    camion_reparto = db.relationship("CamionReparto")
    __table_args__ = (db.UniqueConstraint("semana", "nombre"),)

    @property
    def camion(self):
        return self.camion_troncal if self.escalon == "troncal" else self.camion_reparto


class Reposicion(db.Model):
    """Envío troncal planta → almacén para una semana (cajas mixtas con la mezcla de los pedidos)."""
    __tablename__ = "reposiciones"
    id = db.Column(db.Integer, primary_key=True)
    semana = db.Column(db.String(8), nullable=False, index=True)
    planta_id = db.Column(db.String(16), db.ForeignKey("plantas.id"), nullable=False)
    almacen_id = db.Column(db.String(24), db.ForeignKey("almacenes.id"), nullable=False)
    viaje_id = db.Column(db.Integer, db.ForeignKey("viajes.id"))
    cajas = db.Column(db.Integer, nullable=False)
    kg = db.Column(db.Float, nullable=False)
    estado = db.Column(db.String(12), default="planificada", nullable=False)
    expedida = db.Column(db.DateTime)
    recibida = db.Column(db.DateTime)

    planta = db.relationship("Planta")
    almacen = db.relationship("Almacen")
    viaje = db.relationship("Viaje")
    lineas = db.relationship("LineaReposicion", cascade="all, delete-orphan", lazy="selectin")
    __table_args__ = (db.UniqueConstraint("semana", "almacen_id"),)

    @property
    def codigo(self):
        return f"R-{self.id:05d}"


class LineaReposicion(db.Model):
    __tablename__ = "lineas_reposicion"
    reposicion_id = db.Column(db.Integer, db.ForeignKey("reposiciones.id", ondelete="CASCADE"), primary_key=True)
    categoria_id = db.Column(db.String(16), db.ForeignKey("categorias.id"), primary_key=True)
    unidades = db.Column(db.Integer, nullable=False)
    categoria = db.relationship("Categoria", lazy="joined")


class Ajuste(db.Model):
    """Pares clave-valor de estado de la app (p. ej. último día de pedidos automáticos generados)."""
    __tablename__ = "ajustes"
    clave = db.Column(db.String(40), primary_key=True)
    valor = db.Column(db.String(200))

    @staticmethod
    def get(clave, defecto=None):
        a = db.session.get(Ajuste, clave)
        return a.valor if a else defecto

    @staticmethod
    def set(clave, valor):
        a = db.session.get(Ajuste, clave) or Ajuste(clave=clave)
        a.valor = str(valor)
        db.session.add(a)
