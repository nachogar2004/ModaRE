"""Base de datos de la librería de rutas: la MISMA que la app de gestión (variable MODARE_DB).

La librería LEE de aquí todos sus datos (modare.datos.cargar_datos: plantas, almacenes, tiendas, vetos, parametros,
tipos_vehiculo, tarifas, distancias; tablas definidas en gestion/modelos.py) y GUARDA lo que antes iba a ficheros:
  pedidos_optimizador     pedido semanal de cada tienda (kg y cajas): la entrada del optimizador
  planes                  el plan de cada semana (lo mismo que salidas/<semana>/plan.json)
  trabajos_optimizacion   estado de cada optimización (calculando / ok / error), visible desde cualquier proceso

Usa SQLAlchemy Core (sin Flask) para que el optimizador funcione también desde la línea de comandos.

    MODARE_DB=sqlite:///ruta/gestion.db                       (por defecto: code/gestion.db)
    MODARE_DB=postgresql+psycopg://usuario:clave@host/base    (producción)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading

import sqlalchemy as sa

from . import config as C

metadata = sa.MetaData()

pedidos_optimizador = sa.Table(
    "pedidos_optimizador", metadata,
    sa.Column("semana", sa.String(8), primary_key=True),          # '2026-W47'
    sa.Column("id_tienda", sa.String(16), primary_key=True),
    sa.Column("cajas", sa.Integer, nullable=False),
    sa.Column("kg", sa.Float, nullable=False),
    sa.Column("comentario", sa.Text),
    sa.Column("actualizado", sa.DateTime, nullable=False),
)

planes = sa.Table(
    "planes", metadata,
    sa.Column("semana", sa.String(8), primary_key=True),
    sa.Column("plan", sa.Text, nullable=False),                   # JSON (texto: igual en SQLite y PostgreSQL)
    sa.Column("actualizado", sa.DateTime, nullable=False),
)

trabajos = sa.Table(
    "trabajos_optimizacion", metadata,
    sa.Column("semana", sa.String(8), primary_key=True),
    sa.Column("estado", sa.String(12), nullable=False),          # pendiente / calculando / ok / error
    sa.Column("mensaje", sa.Text),
    sa.Column("actualizado", sa.DateTime, nullable=False),
    sa.Column("parametros", sa.Text),                            # JSON con las opciones (cola del trabajador externo)
)
LATIDO = "latido"            # fila especial: el ordenador optimizador (trabajador.py) avisa de que está conectado
SEG_LATIDO = 20              # el trabajador escribe su latido cada 20 s
MAX_MIN_PENDIENTE = 10       # una petición en cola más antigua sin trabajador conectado se avisa en la web

MAX_MIN_CALCULANDO = 60     # una optimización «calculando» más antigua se da por interrumpida (en un servidor lento tarda ~20 min)

_engine = None
_lock = threading.Lock()


def url_bd() -> str:
    url = os.environ.get("MODARE_DB") or f"sqlite:///{C.CODE_DIR / 'gestion.db'}"
    # los proveedores suelen dar postgres://… : SQLAlchemy necesita el nombre del dialecto y del driver
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def engine() -> sa.Engine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = sa.create_engine(url_bd(), pool_pre_ping=True, future=True)
            # crea solo lo que falte (en la app las crean las migraciones; esto cubre el uso desde la CLI)
            metadata.create_all(_engine, checkfirst=True)
        return _engine


def leer(consulta: str, **params):
    """Consulta de solo lectura -> DataFrame. La usa modare.datos para leer los datos del optimizador
    (red, vetos, parámetros, vehículos, tarifas y distancias), que son tablas de la app (gestion/modelos.py)."""
    import pandas as pd
    with engine().connect() as con:
        return pd.read_sql_query(sa.text(consulta), con, params=params or None)


def ahora() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def _upsert(con, tabla: sa.Table, valores: dict, claves: list[str]):
    """INSERT o UPDATE portable (SQLite y PostgreSQL)."""
    cond = sa.and_(*(tabla.c[k] == valores[k] for k in claves))
    res = con.execute(sa.update(tabla).where(cond).values(**{k: v for k, v in valores.items() if k not in claves}))
    if res.rowcount == 0:
        con.execute(sa.insert(tabla).values(**valores))


# ----------------------------------------------------------------------------- planes
def guardar_plan(plan: dict):
    texto = json.dumps(plan, ensure_ascii=False, default=str)
    with engine().begin() as con:
        _upsert(con, planes, {"semana": plan["resumen"]["semana"], "plan": texto, "actualizado": ahora()}, ["semana"])


def cargar_plan(semana: str) -> dict | None:
    with engine().connect() as con:
        texto = con.execute(sa.select(planes.c.plan).where(planes.c.semana == semana)).scalar()
    return json.loads(texto) if texto else None


# ----------------------------------------------------------------------------- estado de las optimizaciones
def poner_trabajo(semana: str, estado: str, mensaje: str | None = None, parametros: dict | None = None):
    with engine().begin() as con:
        _upsert(con, trabajos, {"semana": semana, "estado": estado, "mensaje": mensaje, "actualizado": ahora(),
                                "parametros": json.dumps(parametros) if parametros is not None else None}, ["semana"])


def estado_trabajo(semana: str) -> dict:
    """{} si nunca se ha optimizado; {"estado", "msg"} si no.
    estado: pendiente (en cola para el ordenador optimizador) / calculando / ok / error"""
    with engine().connect() as con:
        r = con.execute(sa.select(trabajos).where(trabajos.c.semana == semana)).mappings().first()
    if not r:
        return {}
    edad = ahora() - r["actualizado"]
    if r["estado"] == "calculando" and edad > dt.timedelta(minutes=MAX_MIN_CALCULANDO):
        return {"estado": "error", "msg": "La optimización se interrumpió (¿se reinició el servidor o el ordenador "
                                          "optimizador?). Vuelve a lanzarla."}
    if r["estado"] == "pendiente" and edad > dt.timedelta(minutes=MAX_MIN_PENDIENTE) and not trabajador_conectado():
        return {"estado": "pendiente", "msg": "Sigue en cola: el ordenador optimizador no está conectado."}
    return {"estado": r["estado"], "msg": r["mensaje"]}


def borrar_lote(semana: str):
    """Al deshacer un lote: se olvidan su plan y el estado de su optimización."""
    with engine().begin() as con:
        con.execute(sa.delete(planes).where(planes.c.semana == semana))
        con.execute(sa.delete(trabajos).where(trabajos.c.semana == semana))


def _ocupado(semana: str) -> bool:
    return estado_trabajo(semana).get("estado") in ("calculando", "pendiente")


def reclamar_trabajo(semana: str) -> bool:
    """Optimizar AQUÍ: marca la semana como «calculando». False si ya hay una optimización en marcha o en cola."""
    if _ocupado(semana):
        return False
    poner_trabajo(semana, "calculando")
    return True


# ----------------------------------------------------------------------------- cola para el ordenador optimizador
def encolar_trabajo(semana: str, parametros: dict) -> bool:
    """Optimizar en el ORDENADOR OPTIMIZADOR (trabajador.py): deja la petición en cola. False si ya hay una."""
    if _ocupado(semana):
        return False
    poner_trabajo(semana, "pendiente", "En cola: esperando al ordenador optimizador.", parametros)
    return True


def tomar_pendiente() -> tuple[str, dict] | None:
    """El trabajador toma la petición más antigua de la cola. El UPDATE condicionado evita que dos trabajadores
    cojan la misma."""
    with engine().begin() as con:
        filas = con.execute(sa.select(trabajos.c.semana, trabajos.c.parametros)
                            .where(trabajos.c.estado == "pendiente").order_by(trabajos.c.actualizado)).all()
        for semana, params in filas:
            r = con.execute(sa.update(trabajos).where(trabajos.c.semana == semana, trabajos.c.estado == "pendiente")
                            .values(estado="calculando", mensaje="Calculando en el ordenador optimizador…",
                                    actualizado=ahora()))
            if r.rowcount == 1:
                return semana, json.loads(params or "{}")
    return None


def latido():
    poner_trabajo(LATIDO, "vivo")


def trabajador_conectado() -> bool:
    with engine().connect() as con:
        t = con.execute(sa.select(trabajos.c.actualizado).where(trabajos.c.semana == LATIDO)).scalar()
    return bool(t) and ahora() - t < dt.timedelta(seconds=3 * SEG_LATIDO)
