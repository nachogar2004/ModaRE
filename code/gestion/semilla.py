"""Carga inicial de la base de datos de gestión a partir de los CSV de data/.

    .venv/bin/python -m gestion.semilla            # crea gestion.db si está vacía
    .venv/bin/python -m gestion.semilla --reset    # la borra y la vuelve a crear

Lo que viene de los CSV (plantas, almacenes, tiendas, tipos de vehículo) es el dato del reto.
Lo que se inventa aquí está marcado como SINTÉTICO y se puede cambiar desde la app:
catálogo de categorías (peso y precio por prenda), stock inicial, historial de ventas de 8 semanas,
flota de camiones (matrículas ficticias) y usuarios con contraseña inicial.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import random
import unicodedata

import pandas as pd
from sqlalchemy import insert

from modare import config as C
from modare.datos import cargar_parametros, kg_semana_estimado

from .modelos import (SEGMENTOS, TIPOS_PRENDA, Almacen, CamionReparto, CamionTroncal, Categoria, Planta,
                      StockPlanta, StockTienda, Tienda, Usuario, Venta, db)

PASSWORD_INICIAL = os.environ.get("MODARE_PASSWORD_INICIAL", "modare2026")
PASSWORD_ADMIN = os.environ.get("MODARE_PASSWORD_ADMIN", "admin")

# --- catálogo SINTÉTICO -----------------------------------------------------------
_TIPO = {  # código, kg/prenda, €/prenda, cuota dentro del segmento
    "Partes de arriba": ("ARR", 0.25, 6.0, 0.38),
    "Partes de abajo": ("ABA", 0.45, 9.0, 0.27),
    "Abrigos": ("ABR", 1.10, 18.0, 0.12),
    "Zapatos": ("ZAP", 0.80, 12.0, 0.13),
    "Bolsos": ("BOL", 0.50, 10.0, 0.10),
}
_SEG = {"Mujer": ("MUJ", 1.0, 1.0, 0.50), "Hombre": ("HOM", 1.1, 1.0, 0.30), "Niños": ("NIN", 0.5, 0.6, 0.20)}
DIAS_HISTORIAL = 56
TIPOS_TRONCAL = ["VEH-R12", "VEH-R18", "VEH-ART"]
TIPOS_REPARTO = ["VEH-FUR", "VEH-R75", "VEH-R12", "VEH-R18"]


def _csv(nombre):
    return pd.read_csv(C.DATA_DIR / nombre, encoding="utf-8")


def _txt(v, defecto=None):
    return defecto if pd.isna(v) else str(v)


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.upper() if ch.isalnum())


def capacidad_vehiculos() -> dict[str, dict]:
    """carga útil, palés y cajas de cada tipo de vehículo (misma regla que modare.datos)."""
    P = cargar_parametros()
    m3_caja = C.KG_POR_CAJA / P["densidad_kg_m3"]
    out = {}
    for r in _csv("vehiculos.csv").itertuples():
        out[r.id_tipo] = {"descripcion": r.descripcion, "carga_util_kg": float(r.carga_util_kg),
                          "capacidad_palets": int(r.capacidad_palets),
                          "capacidad_cajas": int(min(r.capacidad_palets * P["kg_por_palet"] / C.KG_POR_CAJA,
                                                     r.volumen_m3 / m3_caja))}
    return out


def crear_categorias():
    orden = 0
    for seg in SEGMENTOS:
        cs, fkg, fprecio, cuota_seg = _SEG[seg]
        for tipo in TIPOS_PRENDA:
            ct, kg, precio, cuota_t = _TIPO[tipo]
            orden += 1
            db.session.add(Categoria(id=f"{cs}-{ct}", segmento=seg, tipo=tipo, kg_unidad=round(kg * fkg, 3),
                                     precio_unidad=round(precio * fprecio, 2), cuota_ventas=cuota_seg * cuota_t,
                                     orden=orden))
    db.session.flush()


def _reparto_por_categoria(total_prendas: float, cats) -> dict[str, int]:
    return {c.id: int(round(total_prendas * c.cuota_ventas)) for c in cats}


def crear_red(rng: random.Random):
    P = cargar_parametros()
    cats = Categoria.query.order_by(Categoria.orden).all()
    kg_medio = sum(c.cuota_ventas * c.kg_unidad for c in cats)   # kg por prenda vendida, en media

    for r in _csv("plantas.csv").itertuples():
        stock_max = int(r.capacidad_salida_kg_semana * 3 / kg_medio)      # SINTÉTICO: 3 semanas de salida
        p = Planta(id=r.id_planta, nombre=r.nombre, direccion=_txt(r.direccion), municipio=r.municipio,
                   provincia=r.provincia, lat=r.lat, lon=r.lon, capacidad_salida_kg_semana=r.capacidad_salida_kg_semana,
                   stock_max_prendas=stock_max, hora_inicio_carga=r.hora_inicio_carga, hora_fin_carga=r.hora_fin_carga,
                   activa=bool(r.activa), apertura_prevista=_txt(r.apertura_prevista))
        if p.activa:
            p.stock = [StockPlanta(categoria_id=k, unidades=v)
                       for k, v in _reparto_por_categoria(stock_max * 0.6, cats).items()]
        db.session.add(p)

    for r in _csv("almacenes.csv").itertuples():
        maxc = int(math.ceil(2 * r.capacidad_kg_semana / C.KG_POR_CAJA / 10) * 10)   # SINTÉTICO: 2 semanas
        db.session.add(Almacen(id=r.id_almacen, nombre=r.nombre, provincia=r.provincia, poligono=_txt(r.poligono),
                               municipio=_txt(r.municipio), lat=r.lat, lon=r.lon, stock_max_cajas=maxc,
                               stock_cajas=int(maxc * 0.25), capacidad_kg_semana=r.capacidad_kg_semana,
                               hora_inicio=r.hora_inicio_carga, hora_fin=r.hora_fin_carga,
                               dias_recepcion=r.dias_recepcion, activo=bool(r.activo)))
    db.session.flush()
    alm_por_prov = {a.provincia: a.id for a in Almacen.query}

    hoy = dt.date.today()
    filas_venta = []
    for r in _csv("tiendas.csv").itertuples():
        t_ser = pd.Series(r._asdict())
        kg_sem = kg_semana_estimado(t_ser, P)
        ventas_eur = r.ventas_periodo if r.ventas_unidad == "EUR" else r.ventas_periodo / 100 * P["ventas_media_eur_tienda"]
        prendas_dia = kg_sem / kg_medio / 6                     # se vende de lunes a sábado
        cat_tam = r.categoria_tamano
        t = Tienda(id=r.id_tienda, nombre=r.nombre, subtipo=_txt(r.subtipo), direccion=_txt(r.direccion),
                   municipio=r.municipio, provincia=r.provincia, ccaa=_txt(r.ccaa), lat=r.lat, lon=r.lon,
                   acceso_carretera=bool(r.acceso_carretera), superficie_m2=None if pd.isna(r.superficie_m2) else r.superficie_m2,
                   categoria_tamano=cat_tam, almacen_id=alm_por_prov.get(r.provincia), ventas_anuales_eur=float(ventas_eur),
                   dias_recepcion=_txt(r.dias_recepcion, "L-V"), ventana_inicio=_txt(r.ventana_inicio, "10:00"),
                   ventana_fin=_txt(r.ventana_fin, "14:00"), tiempo_descarga_min=int(r.tiempo_descarga_min),
                   vehiculo_max=_txt(r.vehiculo_max),
                   # SINTÉTICO: las pequeñas piden al mes, el resto cada jueves (se cierran pedidos el viernes)
                   frecuencia_pedido="mensual" if cat_tam == "pequeña" else "semanal",
                   dia_pedido=1 if cat_tam == "pequeña" else 3, dias_seguridad=3,
                   capacidad_max_prendas=int(prendas_dia * 6 * 5),
                   activa=bool(r.activa), fecha_alta=pd.to_datetime(r.fecha_alta).date() if not pd.isna(r.fecha_alta) else hoy)
        t.stock = [StockTienda(categoria_id=k, unidades=v)
                   for k, v in _reparto_por_categoria(prendas_dia * 4, cats).items()]   # ~4 días de venta
        db.session.add(t)
        for d in range(DIAS_HISTORIAL, 0, -1):
            f = hoy - dt.timedelta(days=d)
            if f.weekday() == 6:
                continue
            for c in cats:
                u = _poisson(rng, prendas_dia * c.cuota_ventas)
                if u:
                    filas_venta.append({"tienda_id": r.id_tienda, "categoria_id": c.id, "fecha": f,
                                        "unidades": u, "sintetica": True})
    db.session.flush()
    db.session.execute(insert(Venta), filas_venta)


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:                                    # aproximación normal
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def crear_flota(rng: random.Random):
    """Flota SINTÉTICA: 3 camiones troncales por planta activa y 1-2 vehículos de reparto por almacén."""
    cap = capacidad_vehiculos()
    letras = "BCDFGHJKLMNPRSTVWXYZ"
    n = [0]

    def matricula():
        n[0] += 1
        return f"{(n[0] * 7919) % 10000:04d} {''.join(rng.choice(letras) for _ in range(3))}"

    for p in Planta.query.filter_by(activa=True):
        tipos = ["VEH-R12", "VEH-R12", "VEH-R18"] + (["VEH-ART"] if p.id == "PL-BCN" else [])
        for tipo in tipos:
            db.session.add(CamionTroncal(planta_id=p.id, matricula=matricula(), tipo_vehiculo=tipo,
                                         operador="Operador A", notas="SINTÉTICO", **_cap(cap, tipo)))
    for a in Almacen.query.filter_by(activo=True):
        tipos = ["VEH-FUR"] + (["VEH-R75"] if a.capacidad_kg_semana >= 3000 else [])
        for tipo in tipos:
            db.session.add(CamionReparto(almacen_id=a.id, matricula=matricula(), tipo_vehiculo=tipo,
                                         operador="Operador A", notas="SINTÉTICO", **_cap(cap, tipo)))


def _cap(cap, tipo):
    c = cap[tipo]
    return {"carga_util_kg": c["carga_util_kg"], "capacidad_cajas": c["capacidad_cajas"],
            "capacidad_palets": c["capacidad_palets"]}


def crear_usuarios():
    admin = Usuario(username="admin", nombre="Administrador", rol="admin")
    admin.set_password(PASSWORD_ADMIN)
    db.session.add(admin)
    # Un hash por usuario es lento (scrypt): se calcula una vez y se reutiliza para la contraseña inicial
    tmp = Usuario()
    tmp.set_password(PASSWORD_INICIAL)
    h = tmp.password_hash
    for t in Tienda.query:
        db.session.add(Usuario(username=t.id.lower(), nombre=t.nombre, rol="tienda", tienda_id=t.id, password_hash=h))
    for p in Planta.query:
        db.session.add(Usuario(username=p.id.lower(), nombre=p.nombre, rol="planta", planta_id=p.id, password_hash=h))
    for a in Almacen.query:
        db.session.add(Usuario(username=a.id.lower(), nombre=a.nombre, rol="almacen", almacen_id=a.id, password_hash=h))


def sembrar(log=print):
    rng = random.Random(2026)
    crear_categorias()
    crear_red(rng)
    crear_flota(rng)
    crear_usuarios()
    db.session.commit()
    log(f"Base de datos creada: {Tienda.query.count()} tiendas, {Planta.query.count()} plantas, "
        f"{Almacen.query.count()} almacenes, {CamionTroncal.query.count()} camiones troncales, "
        f"{CamionReparto.query.count()} de reparto, {Venta.query.count()} registros de ventas, "
        f"{Usuario.query.count()} usuarios.")
    log(f"Acceso: admin / {PASSWORD_ADMIN}  ·  tiendas td-001…, plantas pl-mad…, almacenes al-madrid… "
        f"con contraseña inicial '{PASSWORD_INICIAL}' (cámbiala en 'Mi cuenta').")


def sembrar_si_vacia(log=print):
    db.create_all()
    if not db.session.query(Categoria.id).first():
        sembrar(log)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="borra gestion.db y la vuelve a crear")
    a = ap.parse_args()
    from flask import Flask

    from . import DB_PATH
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("MODARE_DB", f"sqlite:///{DB_PATH}")
    db.init_app(app)
    with app.app_context():
        if a.reset:
            db.drop_all()
        sembrar_si_vacia()
