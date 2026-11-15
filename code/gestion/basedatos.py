"""Preparación de la base de datos: migraciones (Alembic vía Flask-Migrate), carga inicial y adopción de bases antiguas.

Una sola base para todo (variable MODARE_DB; por defecto code/gestion.db en SQLite):
  - tablas de la app de gestión (gestion/modelos.py), incluidos los datos que lee el optimizador
    (red, vetos, parametros, tipos_vehiculo, tarifas, distancias)
  - tablas de la librería de rutas (modare/bd.py): pedidos_optimizador, planes, trabajos_optimizacion
Los CSV de data/ solo se leen para crear la base (semilla) o completar tablas vacías.

Al arrancar la app se llama a preparar_bd():
  - base vacía               -> crea el esquema con las migraciones y la rellena con los datos de ejemplo
  - base con migraciones     -> aplica las migraciones pendientes (flask db upgrade)
  - base anterior a Alembic  -> la pone al día a mano y la marca con la versión inicial (flask db stamp)
Con MODARE_AUTO_MIGRAR=0 no se migra al arrancar: hay que ejecutar «flask --app app db upgrade» en el despliegue
(recomendado si el servidor arranca varios procesos a la vez).

Cambios futuros de los modelos:  flask --app app db migrate -m "qué cambia"   ->  revisar el fichero  ->  flask --app app db upgrade
"""
from __future__ import annotations

import os
import sqlite3

import sqlalchemy as sa
from flask import Flask
from flask_migrate import stamp, upgrade

from modare import bd as BD
from modare import config as C

from .modelos import Ajuste, Almacen, Categoria, Planta, db

REVISION_INICIAL = "0001_inicial"


def app_bd() -> Flask:
    """App mínima (sin pantallas) para tareas de base de datos desde la línea de comandos."""
    from . import DIR_MIGRACIONES, migrate
    app = Flask(__name__)
    url = BD.url_bd()
    app.config["SQLALCHEMY_DATABASE_URI"] = url
    db.init_app(app)
    migrate.init_app(app, db, directory=str(DIR_MIGRACIONES), render_as_batch=url.startswith("sqlite"))
    return app


def _tablas() -> set[str]:
    return set(sa.inspect(db.engine).get_table_names())


def preparar_bd(log=print):
    from . import DIR_MIGRACIONES
    auto = os.environ.get("MODARE_AUTO_MIGRAR", "1") != "0"
    tablas = _tablas()
    if "alembic_version" not in tablas and "categorias" in tablas:
        _adoptar_base_antigua(log)
    elif auto:
        upgrade(directory=str(DIR_MIGRACIONES))
    elif "alembic_version" not in tablas:
        raise RuntimeError("La base de datos está vacía y MODARE_AUTO_MIGRAR=0: ejecuta «flask --app app db upgrade».")
    from .semilla import completar_kg_semana, completar_red_desde_csv, crear_datos_optimizador, sembrar
    if not db.session.query(Categoria.id).first():
        sembrar(log)
    # bases creadas antes de que el optimizador leyera de la base: se completan una vez desde data/*.csv
    crear_datos_optimizador(log)
    completar_red_desde_csv(log)
    completar_kg_semana(log)
    _importar_pedidos_db_antiguo(log)


def preparar_desde_cli(log=print):
    """Para los scripts de línea de comandos (optimizar.py, calcular_distancias.py): migra y, si la base está
    vacía, la crea desde data/*.csv, igual que al arrancar la app."""
    app = app_bd()
    with app.app_context():
        preparar_bd(log)


def borrar_todo():
    """Elimina TODAS las tablas (app, librería y control de migraciones). Solo para --reset."""
    db.drop_all()
    BD.metadata.drop_all(db.engine)
    with db.engine.begin() as con:
        for t in ("alembic_version", "almacen_plantas"):
            con.execute(sa.text(f"DROP TABLE IF EXISTS {t}"))


# ----------------------------------------------------------------------------- bases anteriores a las migraciones
def _adoptar_base_antigua(log):
    """gestion.db creada con create_all() antes de usar Alembic: se completa, se aplican los cambios que antes
    se hacían a mano y se marca con la versión inicial para que, a partir de aquí, mande Alembic."""
    from . import DIR_MIGRACIONES
    db.create_all()                                  # tablas que falten (p. ej. las de vetos)
    BD.metadata.create_all(db.engine)                # tablas de la librería
    insp = sa.inspect(db.engine)
    if Ajuste.get("migracion_vetos") != "1":
        # antes: plantas PERMITIDAS por almacén y almacén habitual por tienda; ahora: todo vale salvo lo vetado
        if "almacen_plantas" in insp.get_table_names():
            permitidas: dict[str, set] = {}
            for a_id, p_id in db.session.execute(sa.text("SELECT almacen_id, planta_id FROM almacen_plantas")):
                permitidas.setdefault(a_id, set()).add(p_id)
            todas = Planta.query.all()
            for a in Almacen.query:
                if a.id in permitidas:
                    a.plantas_vetadas = [p for p in todas if p.id not in permitidas[a.id]]
            db.session.execute(sa.text("DROP TABLE almacen_plantas"))
        Ajuste.set("migracion_vetos", "1")
        db.session.commit()
    if "almacen_id" in {c["name"] for c in insp.get_columns("tiendas")}:
        _quitar_columna("tiendas", "almacen_id")     # el antiguo «almacén habitual»
    stamp(directory=str(DIR_MIGRACIONES), revision=REVISION_INICIAL)
    upgrade(directory=str(DIR_MIGRACIONES))          # por si hay migraciones posteriores a la inicial
    log("Base de datos antigua adoptada: a partir de ahora los cambios de esquema van con migraciones (Alembic).")


def _quitar_columna(tabla: str, columna: str):
    """Elimina una columna. En SQLite hay que recrear la tabla: se desactivan las claves foráneas en ESA conexión,
    porque al borrar la tabla vieja se borrarían en cascada las filas que la referencian (stock, ventas…)."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    db.session.close()
    with db.engine.connect() as con:
        sqlite_ = con.dialect.name == "sqlite"
        if sqlite_:
            con.exec_driver_sql("PRAGMA foreign_keys=OFF")
        op = Operations(MigrationContext.configure(con))
        with op.batch_alter_table(tabla, recreate="always" if sqlite_ else "auto") as b:
            b.drop_column(columna)
        con.commit()
        if sqlite_:
            con.exec_driver_sql("PRAGMA foreign_keys=ON")


def _importar_pedidos_db_antiguo(log):
    """Antes el optimizador guardaba los pedidos en code/pedidos.db (otra base). Se copian a la base común."""
    f = C.DB_PATH_ANTIGUO
    if not f.exists():
        return
    con = sqlite3.connect(f)
    try:
        filas = con.execute("SELECT semana, id_tienda, cajas, kg, comentario FROM pedidos").fetchall()
    except sqlite3.Error:
        filas = []
    finally:
        con.close()
    from modare import pedidos as PED
    ya = {(s, t) for s in {r[0] for r in filas} for t in PED.de_semana(s)}
    nuevas = [r for r in filas if (r[0], r[1]) not in ya]
    for semana, tienda, cajas, kg, coment in nuevas:
        PED.guardar(semana, tienda, cajas, kg, coment or "")
    f.rename(f.with_name("pedidos.db.migrado"))
    log(f"Pedidos del optimizador copiados desde {f.name} a la base común ({len(nuevas)} filas); "
        f"el fichero antiguo queda como {f.name}.migrado.")
