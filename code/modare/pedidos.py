"""Pedido semanal de cada tienda (kg y cajas): la entrada del optimizador.

Vive en la base de datos común (tabla pedidos_optimizador, ver modare.bd). La app de gestión lo rellena al
cerrar la semana sumando los pedidos automáticos y adicionales de cada tienda.
"""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from . import config as C
from .bd import _upsert, ahora, engine, pedidos_optimizador as T


def semana_actual() -> str:
    """Semana que se planifica: la siguiente a la actual (los pedidos se cierran el viernes)."""
    y, w, _ = (dt.date.today() + dt.timedelta(days=7)).isocalendar()
    return f"{y}-W{w:02d}"


def num_semana(semana: str) -> int:
    return int(semana.split("-W")[1])


def guardar(semana: str, id_tienda: str, cajas: int, kg: float | None = None, comentario: str = ""):
    kg = float(kg) if kg else cajas * C.KG_POR_CAJA
    with engine().begin() as con:
        _upsert(con, T, {"semana": semana, "id_tienda": id_tienda, "cajas": int(cajas), "kg": kg,
                         "comentario": comentario, "actualizado": ahora()}, ["semana", "id_tienda"])


def borrar(semana: str, id_tienda: str):
    with engine().begin() as con:
        con.execute(sa.delete(T).where(T.c.semana == semana, T.c.id_tienda == id_tienda))


def borrar_semana(semana: str):
    with engine().begin() as con:
        con.execute(sa.delete(T).where(T.c.semana == semana))


def de_semana(semana: str) -> dict[str, dict]:
    with engine().connect() as con:
        rows = con.execute(sa.select(T).where(T.c.semana == semana)).mappings().all()
    return {r["id_tienda"]: {**r, "actualizado": r["actualizado"].isoformat(timespec="seconds")} for r in rows}
