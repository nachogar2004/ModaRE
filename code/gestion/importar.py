"""Importación masiva desde CSV (Admin → Tiendas → Importar CSV): subir → vista previa → confirmar.

La vista previa no escribe nada. Entre pasos viaja el texto del CSV en un campo oculto, firmado con la clave de la
sesión (itsdangerous, comprimido) y con un nonce de un solo uso guardado en la sesión: al confirmar se vuelve a
analizar TODO en el servidor (lo que mandó el navegador solo dice qué filas se eligieron) y se inserta en una
transacción (todo o nada). Las tiendas no reciben usuario: la app es solo para el administrador.

La validación es la del formulario de alta (`vistas_admin._aplicar`, `validar_id`, `_validar_ubicacion`). Para
importar otra entidad basta con otra subclase de `Importador` en IMPORTADORES (columnas, fila CSV → formulario,
claves de duplicado y creación).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
import secrets
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from flask import Response, abort, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.exc import DataError, IntegrityError
from werkzeug.datastructures import MultiDict

from modare.datos import haversine_km

from . import vistas_admin as VA
from .modelos import Almacen, Categoria, Planta, Tienda, db
from .semilla import completar_tienda, defectos_tienda, kg_semana_por_ventas, parametros, ventas_eur

bp = VA.bp
LIMITE_BYTES = 1_000_000           # 1 MB
MAX_FILAS = 500                    # cada fila marcada es un campo del formulario (Werkzeug admite 1.000)
VALIDEZ_S = 3600                   # la vista previa caduca en 1 h
CERCA_M = 50                       # dos tiendas a menos de esto son probablemente la misma
MAX_COLUMNAS = 100


def _clave(s) -> str:
    """Para comparar nombres y direcciones: sin tildes, minúsculas, sin signos ni espacios dobles."""
    return " ".join(re.sub(r"[^0-9a-zñ]+", " ", VA._sin_tildes(s or "")).split())


# ----------------------------------------------------------------------------- lectura del fichero
def decodificar(datos: bytes) -> tuple[str, list[str]]:
    try:
        return datos.decode("utf-8-sig"), []
    except UnicodeDecodeError:
        return datos.decode("cp1252", errors="replace"), [
            "El fichero no está en UTF-8: se ha leído como Windows-1252 (Excel). Revisa las tildes en la vista previa."]


def leer_filas(texto: str) -> tuple[list[str], list[tuple[int, dict]]]:
    """Cabeceras normalizadas y filas (nº de línea, {cabecera: valor}). Separador «,» o «;» (Excel en español)."""
    primera = texto.lstrip("﻿").split("\n", 1)[0]
    sep = ";" if primera.count(";") > primera.count(",") else ","
    r = csv.reader(io.StringIO(texto.lstrip("\ufeff")), delimiter=sep)
    filas = []
    try:
        try:
            cab = [c.strip().lower() for c in next(r)]
        except StopIteration:
            raise ValueError("El fichero está vacío.")
        if len(cab) > MAX_COLUMNAS:
            raise ValueError(f"La cabecera tiene {len(cab)} columnas (máximo {MAX_COLUMNAS}): ¿es el fichero correcto?")
        repes = sorted(c for c, k in Counter(cab).items() if c and k > 1)
        if repes:
            raise ValueError(f"Columnas repetidas en la cabecera: {', '.join(repes)}.")
        for valores in r:
            if not any(v.strip() for v in valores):
                continue                                   # filas vacías (Excel deja ;;;;;)
            if len(filas) >= MAX_FILAS:
                raise ValueError(f"El fichero tiene más de {MAX_FILAS} filas: pártelo en varios.")
            d = {c: (valores[i].strip() if i < len(valores) else "") for i, c in enumerate(cab) if c}
            if len(valores) > len(cab) and any(v.strip() for v in valores[len(cab):]):
                d["__sobran__"] = "1"
            filas.append((r.line_num, d))
    except csv.Error as ex:                                # p. ej. un campo enorme o un fichero binario
        raise ValueError(f"No es un CSV válido (cerca de la línea {r.line_num}): {ex}")
    return cab, filas


# ----------------------------------------------------------------------------- análisis
ESTADOS = {"nueva": "Nueva", "existe": "Ya existe", "probable": "Ya existe (probable)", "repetida": "Repetida",
           "error": "Error"}


@dataclass
class Fila:
    n: int                                   # línea del fichero (la cabecera es la 1)
    datos: dict                              # valores del CSV
    obj: Any = None                          # objeto validado, sin añadir a la sesión
    estado: str = "nueva"
    motivo: str = ""
    avisos: list[str] = field(default_factory=list)
    coincide: list[tuple[str, str, str]] = field(default_factory=list)   # (id, nombre, por qué)
    id_repetido: bool = False                # repite el código de otra fila: no se puede importar

    @property
    def seleccionable(self) -> bool:
        return self.estado in ("nueva", "probable") or (self.estado == "repetida" and not self.id_repetido)

    @property
    def marcada(self) -> bool:
        return self.estado == "nueva"


@dataclass
class Analisis:
    filas: list[Fila]
    ignoradas: list[str]                     # columnas desconocidas
    no_guardadas: list[str]                  # conocidas pero sin columna en la BD (etiquetas, fuentes…)
    avisos: list[str]

    def huella(self) -> dict[str, list]:
        """Estado y coincidencias de cada fila: viaja firmado con la vista previa y se compara al confirmar."""
        return {str(f.n): [f.estado, sorted(c[0] for c in f.coincide)] for f in self.filas}

    @property
    def resumen(self) -> dict[str, int]:
        r = {"nueva": 0, "existe": 0, "repetida": 0, "error": 0}
        for f in self.filas:
            r["existe" if f.estado == "probable" else f.estado] += 1
        return r


class Importador:
    ent: str
    columnas: list[str]                      # cabecera de la plantilla (misma estructura que data/<ent>.csv)
    extra: list[str] = []                    # columnas de la app que también se aceptan
    no_guardadas: set[str] = set()
    obligatorias: list[str]
    ejemplo: dict

    def a_formulario(self, d: dict) -> tuple[MultiDict, list[str]]:
        """Fila del CSV → campos del formulario de alta (+ avisos). ValueError si algo no se puede convertir."""
        raise NotImplementedError

    def claves(self, obj) -> list[tuple[str, tuple]]:
        """(motivo, clave) para detectar duplicados probables."""
        return []

    def existentes(self) -> list:
        return self.e.modelo.query.all()

    def cerca(self, a, b) -> float | None:
        return None

    def avisos(self, obj) -> list[str]:
        """Avisos de una fila válida (no impiden importarla)."""
        return []

    def crear(self, filas: list[Fila], ventas: bool, analisis: Analisis) -> list[dict]:
        raise NotImplementedError

    @property
    def e(self) -> VA.Entidad:
        return VA.ENTIDADES[self.ent]


def _estado(imp, obj, ids_bd, pool_bd, vistas, validas):
    """Contra la BD (mismo código → existe; nombre/dirección/distancia → probable) y luego contra las filas
    anteriores del fichero (repetida). Devuelve (estado, coincidencias en la BD, (fila, motivo, mismo código))."""
    if obj.id in ids_bd:
        o = ids_bd[obj.id]
        return "existe", [(o.id, o.nombre, "mismo código")], None
    if obj.id and ("id", obj.id) in vistas:            # aunque además se parezca a otra de la BD
        previa = vistas[("id", obj.id)]
        return "repetida", [], (previa, f"El código {obj.id} ya está en la línea {previa.n}.", True)
    coinc = {}
    for motivo, k in imp.claves(obj):
        for o, _m in pool_bd.get(k, []):
            coinc.setdefault(o.id, (o, motivo))
    for o in ids_bd.values():
        dist = imp.cerca(obj, o)
        if dist is not None and o.id not in coinc:
            coinc[o.id] = (o, f"a {dist:.0f} m")
    if coinc:
        return "probable", [(o.id, o.nombre, m) for o, m in coinc.values()], None
    for motivo, k in imp.claves(obj):
        if k in vistas:
            return "repetida", [], (vistas[k], f"{motivo.capitalize()} que la línea {vistas[k].n}.", False)
    for g in validas:
        dist = imp.cerca(obj, g.obj)
        if dist is not None:
            return "repetida", [], (g, f"A {dist:.0f} m de la línea {g.n}.", False)
    return "nueva", [], None


def analizar(imp: Importador, texto: str, avisos: list[str] | None = None) -> Analisis:
    cab, crudas = leer_filas(texto)
    faltan = [c for c in imp.obligatorias if c not in cab]
    conocidas = set(imp.columnas) | set(imp.extra)
    if not conocidas & set(cab):
        raise ValueError("La primera línea debe ser la cabecera con los nombres de las columnas (nombre, municipio, "
                         "provincia, lat, lon, categoria_tamano…) y no se ha reconocido ninguna. Descarga la plantilla.")
    if faltan:
        raise ValueError(f"Faltan columnas obligatorias: {', '.join(faltan)}. Descarga la plantilla para ver el formato.")
    a = Analisis([], [c for c in cab if c and c not in conocidas], [c for c in cab if c in imp.no_guardadas],
                 list(avisos or []))
    if not crudas:
        raise ValueError("El fichero no tiene filas de datos.")
    e = imp.e
    ids_bd = {o.id: o for o in imp.existentes()}
    pool_bd: dict[tuple, list] = {}
    for o in ids_bd.values():
        for motivo, k in imp.claves(o):
            pool_bd.setdefault(k, []).append((o, motivo))
    provincias = VA._valores_bd(Tienda.provincia, Almacen.provincia, Planta.provincia)
    vistas: dict[tuple, Fila] = {}               # claves de las filas válidas anteriores del fichero
    validas: list[Fila] = []
    for n, d in crudas:
        f = Fila(n, d)
        a.filas.append(f)
        try:
            if d.get("__sobran__"):
                raise ValueError("Tiene más valores que columnas la cabecera (¿separador o comillas?).")
            form, f.avisos = imp.a_formulario(d)
            obj = e.modelo()
            with db.session.no_autoflush:
                VA._aplicar(e, obj, form, alta=True)
            VA.validar_id(e, obj)
            VA._validar_ubicacion(obj)
            f.avisos += [av + (" Se guarda con acceso_carretera = 0." if av == VA.AVISO_INSULAR else "")
                         for av in VA.normalizar(obj, provincias)] + imp.avisos(obj)
            f.obj = obj
        except ValueError as ex:
            f.estado, f.motivo, f.avisos = "error", str(ex), []
            continue
        f.estado, f.coincide, previa = _estado(imp, obj, ids_bd, pool_bd, vistas, validas)
        if previa:
            f.motivo, f.id_repetido = previa[1], previa[2]
        validas.append(f)
        if obj.id:
            vistas.setdefault(("id", obj.id), f)
        for _m, k in imp.claves(obj):
            vistas.setdefault(k, f)
    return a


# ----------------------------------------------------------------------------- tiendas
def _bool(v: str, defecto: bool, nombre: str) -> bool:
    s = VA._sin_tildes(v)
    if s == "":
        return defecto
    if s in ("1", "si", "s", "true", "verdadero", "x", "yes"):
        return True
    if s in ("0", "no", "n", "false", "falso"):
        return False
    raise ValueError(f"«{nombre}» debe ser 1/0 o sí/no: {v}")


def _hora(v: str) -> str:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::00)?", v)
    return f"{int(m[1]):02d}:{m[2]}" if m else v


def _entero(v: str) -> str:
    m = re.fullmatch(r"(-?\d+)[.,]0*", v)                 # Excel guarda 30 como 30,0
    return m[1] if m else v


def _fecha(v: str) -> str:
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)   # dd/mm/aaaa (Excel en español)
    return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}" if m else v


def _numero(v: str, nombre: str) -> float:
    """Admite coma o punto decimal y punto de miles (140.000 · 1.234,5). Solo para cantidades, no coordenadas."""
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", v):
        v = v.replace(".", "")
    try:
        x = float(v.replace(",", "."))
        if not math.isfinite(x):
            raise ValueError
        return x
    except ValueError:
        raise ValueError(f"«{nombre}» no es un número: {v}")


class ImportarTiendas(Importador):
    ent = "tiendas"
    columnas = ["id_tienda", "id_fuente", "nombre", "subtipo", "empresa_insercion", "direccion", "municipio", "provincia",
                "ccaa", "lat", "lon", "ubicacion_etiqueta", "ubicacion_fuente", "acceso_carretera", "superficie_m2",
                "categoria_tamano", "superficie_etiqueta", "ventas_periodo", "ventas_unidad", "periodo_ventas",
                "ventas_etiqueta", "planta_actual", "asignacion_etiqueta", "asignacion_fuente", "dias_recepcion",
                "ventana_inicio", "ventana_fin", "horario_etiqueta", "horario_fuente", "horario_publicado",
                "tiempo_descarga_min", "vehiculo_max", "recepcion_etiqueta", "en_muestra", "activa", "fecha_alta",
                "fecha_alta_etiqueta"]
    extra = ["kg_semana", "ventas_anuales_eur", "responsable", "telefono", "email"]
    no_guardadas = {"id_fuente", "empresa_insercion", "ubicacion_etiqueta", "ubicacion_fuente", "superficie_etiqueta",
                    "periodo_ventas", "ventas_etiqueta", "asignacion_etiqueta", "asignacion_fuente",
                    "horario_etiqueta", "horario_fuente", "horario_publicado", "recepcion_etiqueta",
                    "fecha_alta_etiqueta"}
    obligatorias = ["nombre", "municipio", "provincia", "lat", "lon", "categoria_tamano"]
    ejemplo = {"nombre": "Tienda de ejemplo (borra esta fila)", "subtipo": "tienda_calle",
               "direccion": "Calle Mayor, 1", "municipio": "Madrid", "provincia": "Madrid", "ccaa": "Comunidad de Madrid",
               "lat": "40.4155", "lon": "-3.7074", "acceso_carretera": "1", "superficie_m2": "120",
               "categoria_tamano": "mediana", "ventas_periodo": "140000", "ventas_unidad": "EUR",
               "dias_recepcion": "L-V", "ventana_inicio": "10:00", "ventana_fin": "14:00", "tiempo_descarga_min": "30",
               "vehiculo_max": "VEH-R75", "activa": "1"}
    _DIRECTAS = {"id_tienda": "id", "nombre": "nombre", "subtipo": "subtipo", "direccion": "direccion",
                 "municipio": "municipio", "provincia": "provincia", "ccaa": "ccaa", "lat": "lat", "lon": "lon",
                 "superficie_m2": "superficie_m2", "responsable": "responsable", "telefono": "telefono", "email": "email"}

    def a_formulario(self, d):
        avisos = []
        form = MultiDict({campo: d.get(col, "") for col, campo in self._DIRECTAS.items()})
        form["id"] = form["id"].upper()
        tam = VA._sin_tildes(d.get("categoria_tamano", ""))
        form["categoria_tamano"] = {"pequena": "pequeña"}.get(tam, tam)
        form["vehiculo_max"] = d.get("vehiculo_max", "").upper()
        form["dias_recepcion"] = d.get("dias_recepcion", "").upper().replace(" ", "")
        for k in ("ventana_inicio", "ventana_fin"):
            form[k] = _hora(d.get(k, ""))
        form["tiempo_descarga_min"] = _entero(d.get("tiempo_descarga_min", ""))
        for k in ("superficie_m2", "kg_semana"):                        # cantidades: admiten punto de miles
            if d.get(k):
                form[k] = str(_numero(d[k], k))
        form["planta_actual"] = d.get("planta_actual", "").upper()
        form["fecha_alta"] = _fecha(d.get("fecha_alta", ""))
        for k, defecto in (("acceso_carretera", True), ("activa", True), ("en_muestra", False)):  # casilla = marcada
            form.pop(k, None)
            if _bool(d.get(k, ""), defecto, k):
                form[k] = "on"
        # ventas: €/año (EUR) o índice 100 = tienda media (INDICE), como en data/tiendas.csv
        P = parametros()
        if d.get("ventas_periodo"):
            unidad = (d.get("ventas_unidad") or "EUR").upper()
            if unidad not in ("EUR", "INDICE"):
                raise ValueError(f"«ventas_unidad» debe ser EUR o INDICE: {unidad}")
            ventas = ventas_eur(_numero(d["ventas_periodo"], "ventas_periodo"), unidad, P)
        elif d.get("ventas_anuales_eur"):
            ventas = _numero(d["ventas_anuales_eur"], "ventas_anuales_eur")
        else:
            ventas = P["ventas_media_eur_tienda"]
            media = f"{ventas:,.0f}".replace(",", ".")
            avisos.append(f"Sin ventas: se usa la media de una tienda ({media} €/año, SINTÉTICO).")
        if 0 <= ventas < 1000:
            avisos.append(f"Ventas de solo {ventas:g} €/año: ¿falta el separador de miles?")
        form["ventas_anuales_eur"] = str(ventas)
        if not form.get("kg_semana"):              # la demanda del optimizador: como la semilla, por ventas
            form["kg_semana"] = str(kg_semana_por_ventas(ventas, P))
            avisos.append(f"kg/semana estimados por ventas: {form['kg_semana']} (columna kg_semana para fijarlos).")
        for k, v in defectos_tienda(form["categoria_tamano"]).items():
            if not form.get(k):
                form[k] = str(v)
        return form, avisos

    def claves(self, t):
        mun = _clave(t.municipio)
        out = [("mismo nombre y municipio", ("nm", _clave(t.nombre), mun))]
        if _clave(t.direccion):
            out.append(("misma dirección y municipio", ("dir", _clave(t.direccion), mun)))
        return out

    def cerca(self, a, b):
        if None in (a.lat, a.lon, b.lat, b.lon) or abs(a.lat - b.lat) > 0.01 or abs(a.lon - b.lon) > 0.01:
            return None
        m = haversine_km(a.lat, a.lon, b.lat, b.lon) * 1000
        return m if m < CERCA_M else None

    def avisos(self, t):
        if not t.vehiculo_max:          # convención de la librería: sin dato = hasta R18 (corners)
            return ["Sin vehículo máximo: podrá llegar hasta un rígido de 18 t (VEH-R18). Indícalo si es un centro urbano."]
        return []

    def crear(self, filas, analisis):
        P = parametros()
        cats = Categoria.query.order_by(Categoria.orden).all()
        explicitos = [VA.num_tienda(f.obj.id) for f in analisis.filas if f.obj is not None and f.obj.id]
        siguiente = max([VA.max_num_tienda(), *[x for x in explicitos if x is not None]]) + 1
        out = []
        for f in filas:
            t = f.obj
            if not t.id:                              # correlativos dentro del lote
                t.id = f"TD-{siguiente:03d}"
                siguiente += 1
            t.fecha_alta = t.fecha_alta or dt.date.today()
            completar_tienda(t, cats, P)              # defectos y stock inicial, como en la semilla (sin ventas)
            db.session.add(t)
            out.append({"id": t.id, "nombre": t.nombre, "municipio": t.municipio, "kg_semana": t.kg_semana})
        db.session.flush()
        return out


IMPORTADORES: dict[str, Importador] = {"tiendas": ImportarTiendas()}


# ----------------------------------------------------------------------------- vistas
def _imp(ent) -> Importador:
    imp = IMPORTADORES.get(ent)
    if not imp:
        abort(404)
    return imp


def _firmador():
    return URLSafeTimedSerializer(current_app.secret_key, salt="importar-csv")


def _clave_sesion(ent):
    return f"importar_{ent}"


def _vista_previa(imp: Importador, texto: str, avisos: list[str], aviso_extra: str = ""):
    try:
        a = analizar(imp, texto, avisos)
    except ValueError as ex:
        flash(str(ex), "error")
        return _subir(imp)
    nonce = secrets.token_urlsafe(16)
    session[_clave_sesion(imp.ent)] = nonce
    datos = _firmador().dumps({"ent": imp.ent, "texto": texto, "avisos": avisos, "nonce": nonce, "huella": a.huella()})
    return render_template("gestion/admin_importar.html", imp=imp, e=imp.e, ent=imp.ent, paso="previa", a=a,
                           datos=datos, ESTADOS=ESTADOS, aviso_extra=aviso_extra)


def _subir(imp: Importador):
    return render_template("gestion/admin_importar.html", imp=imp, e=imp.e, ent=imp.ent, paso="subir",
                           limite=LIMITE_BYTES, max_filas=MAX_FILAS)


@bp.app_context_processor
def _importables():
    return {"importables": tuple(IMPORTADORES)}


@bp.route("/<ent>/importar", methods=["GET", "POST"])
def importar_csv(ent):
    imp = _imp(ent)
    if request.method == "POST":
        f = request.files.get("fichero")
        datos = f.stream.read(LIMITE_BYTES + 1) if f else b""
        if not datos:
            flash("Elige un fichero CSV.", "error")
        elif len(datos) > LIMITE_BYTES:
            flash(f"El fichero pasa de {LIMITE_BYTES // 1_000_000} MB: pártelo en varios.", "error")
        else:
            texto, avisos = decodificar(datos)
            return _vista_previa(imp, texto, avisos)
    return _subir(imp)


@bp.post("/<ent>/importar/confirmar")
def importar_confirmar(ent):
    imp = _imp(ent)
    try:
        p = _firmador().loads(request.form.get("datos", ""), max_age=VALIDEZ_S)
    except BadSignature:
        flash("La vista previa ha caducado o no es válida: vuelve a subir el fichero.", "error")
        return redirect(url_for("admin.importar_csv", ent=ent))
    if p.get("ent") != ent or session.get(_clave_sesion(ent)) != p.get("nonce"):
        flash("Esta importación ya se confirmó o hay otra vista previa más reciente: vuelve a subir el fichero.", "error")
        return redirect(url_for("admin.importar_csv", ent=ent))
    # se vuelve a analizar todo: puede haber cambiado la BD (otro admin, o este mismo lote ya importado)
    try:
        a = analizar(imp, p["texto"], p.get("avisos"))
    except ValueError as ex:
        flash(str(ex), "error")
        return redirect(url_for("admin.importar_csv", ent=ent))
    texto, avisos = p["texto"], p.get("avisos") or []
    marcadas = set(request.form.getlist("fila"))
    elegidas = [f for f in a.filas if str(f.n) in marcadas]
    if not elegidas:
        return _vista_previa(imp, texto, avisos, "No has marcado ninguna fila.")
    # lo que el admin vio y eligió tiene que seguir igual: mismo estado y mismas coincidencias en cada fila elegida.
    # Así, reenviar el formulario después de importar (o con la cookie anterior) choca con las tiendas ya creadas.
    antes, ahora = p.get("huella") or {}, a.huella()
    cambiadas = [f.n for f in elegidas if antes.get(str(f.n)) != ahora[str(f.n)]]
    if cambiadas:
        return _vista_previa(imp, texto, avisos,
                             f"La base ha cambiado desde la vista previa (línea{'s' if len(cambiadas) > 1 else ''} "
                             f"{', '.join(map(str, cambiadas[:10]))}"
                             f"{'…' if len(cambiadas) > 10 else ''}): no se ha importado nada. ¿Ya se importó este "
                             "fichero? Revisa la selección y confirma de nuevo.")
    no_valen = [f.n for f in elegidas if not f.seleccionable]
    if no_valen:
        return _vista_previa(imp, texto, avisos,
                             (f"La línea {no_valen[0]} no se puede" if len(no_valen) == 1 else
                              f"Las líneas {', '.join(map(str, no_valen))} no se pueden")
                             + " importar (error, mismo código que una existente o código repetido): desmárcala"
                             + ("s" if len(no_valen) > 1 else "") + ". No se ha importado nada.")
    session.pop(_clave_sesion(ent), None)            # un solo uso
    try:
        creadas = imp.crear(elegidas, a)
        db.session.commit()
    except (ValueError, IntegrityError, DataError) as ex:
        db.session.rollback()
        current_app.logger.warning("Importación de %s fallida: %s", ent, ex.__class__.__name__)
        flash("No se ha importado nada: la base de datos rechazó algún dato "
              f"({'código repetido' if isinstance(ex, IntegrityError) else ex.__class__.__name__}).", "error")
        return redirect(url_for("admin.importar_csv", ent=ent))
    VA._marcar_red_cambiada(ent)
    flash(f"Importadas {len(creadas)} {imp.e.titulo.lower()}.")
    flash("Cambia la red: calcula las distancias por carretera de los puntos nuevos en Rutas → «Calcular distancias». "
          "Mientras tanto el optimizador usa línea recta × 1,26.")
    return render_template("gestion/admin_importar.html", imp=imp, e=imp.e, ent=ent, paso="hecho", creadas=creadas, a=a)


@bp.get("/<ent>/importar/plantilla.csv")
def importar_plantilla(ent):
    """Cabeceras + una fila de ejemplo. ?excel=1: separador «;», coma decimal y BOM (Excel en español)."""
    imp = _imp(ent)
    excel = bool(request.args.get("excel"))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";" if excel else ",", lineterminator="\r\n")
    w.writerow(imp.columnas)
    fila = [imp.ejemplo.get(c, "") for c in imp.columnas]
    if excel:
        fila = [v.replace(".", ",") if re.fullmatch(r"-?\d+\.\d+", v) else v for v in fila]
    w.writerow(fila)
    nombre = f"plantilla_{ent}{'_excel' if excel else ''}.csv"
    return Response(("﻿" if excel else "") + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={nombre}"})
