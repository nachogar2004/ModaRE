"""Base de datos de la librería de rutas: la MISMA que la app de gestión (variable MODARE_DB).

La librería guarda aquí lo que antes iba a ficheros sueltos:
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
    sa.Column("estado", sa.String(12), nullable=False),          # calculando / ok / error
    sa.Column("mensaje", sa.Text),
    sa.Column("actualizado", sa.DateTime, nullable=False),
)

MAX_MIN_CALCULANDO = 30     # una optimización «calculando» más antigua se da por interrumpida (servidor reiniciado)

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
def poner_trabajo(semana: str, estado: str, mensaje: str | None = None):
    with engine().begin() as con:
        _upsert(con, trabajos, {"semana": semana, "estado": estado, "mensaje": mensaje, "actualizado": ahora()}, ["semana"])


def estado_trabajo(semana: str) -> dict:
    """{} si nunca se ha optimizado; {"estado", "msg"} si no."""
    with engine().connect() as con:
        r = con.execute(sa.select(trabajos).where(trabajos.c.semana == semana)).mappings().first()
    if not r:
        return {}
    if r["estado"] == "calculando" and ahora() - r["actualizado"] > dt.timedelta(minutes=MAX_MIN_CALCULANDO):
        return {"estado": "error", "msg": "La optimización se interrumpió (¿se reinició el servidor?). Vuelve a lanzarla."}
    return {"estado": r["estado"], "msg": r["mensaje"]}


def reclamar_trabajo(semana: str) -> bool:
    """Marca la semana como «calculando» si no lo estaba ya. False si otra optimización sigue en marcha."""
    if estado_trabajo(semana).get("estado") == "calculando":
        return False
    poner_trabajo(semana, "calculando")
    return True
