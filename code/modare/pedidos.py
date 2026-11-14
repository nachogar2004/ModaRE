"""Pedidos de las tiendas (lo que rellenan los dueños en la app). SQLite, un pedido por tienda y semana."""
from __future__ import annotations

import datetime as dt
import sqlite3

from . import config as C

ESQUEMA = """
CREATE TABLE IF NOT EXISTS pedidos (
    semana      TEXT NOT NULL,          -- '2026-W47'
    id_tienda   TEXT NOT NULL,
    cajas       INTEGER NOT NULL,
    kg          REAL NOT NULL,
    comentario  TEXT,
    actualizado TEXT NOT NULL,
    PRIMARY KEY (semana, id_tienda)
);
"""


def _con():
    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(ESQUEMA)
    return con


def semana_actual() -> str:
    """Semana que se planifica: la siguiente a la actual (los pedidos se cierran el viernes)."""
    y, w, _ = (dt.date.today() + dt.timedelta(days=7)).isocalendar()
    return f"{y}-W{w:02d}"


def num_semana(semana: str) -> int:
    return int(semana.split("-W")[1])


def guardar(semana: str, id_tienda: str, cajas: int, kg: float | None = None, comentario: str = ""):
    kg = float(kg) if kg else cajas * C.KG_POR_CAJA
    with _con() as con:
        con.execute(
            "INSERT INTO pedidos VALUES (?,?,?,?,?,?) ON CONFLICT(semana,id_tienda) DO UPDATE SET "
            "cajas=excluded.cajas, kg=excluded.kg, comentario=excluded.comentario, actualizado=excluded.actualizado",
            (semana, id_tienda, int(cajas), kg, comentario, dt.datetime.now().isoformat(timespec="seconds")),
        )


def borrar(semana: str, id_tienda: str):
    with _con() as con:
        con.execute("DELETE FROM pedidos WHERE semana=? AND id_tienda=?", (semana, id_tienda))


def de_semana(semana: str) -> dict[str, dict]:
    with _con() as con:
        rows = con.execute("SELECT * FROM pedidos WHERE semana=?", (semana,)).fetchall()
    return {r["id_tienda"]: dict(r) for r in rows}
