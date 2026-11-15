"""Carga de los datos desde la base de datos y construcción de la demanda semanal (visitas)."""
from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

import pandas as pd

from . import config as C


# ----------------------------------------------------------------------------- utilidades
def hhmm_a_min(s: str) -> int:
    h, m = str(s).strip().split(":")
    return int(h) * 60 + int(m)


def min_a_hhmm(m: float) -> str:
    """Minuto absoluto de la semana -> 'X 10:35' (día + hora)."""
    m = int(round(m))
    dia, resto = divmod(m, 1440)
    return f"{C.DIAS[dia % 7]} {resto // 60:02d}:{resto % 60:02d}"


def dias_desde_rango(rango: str, horizonte: int) -> list[int]:
    """'L-V' -> [0,1,2,3,4]; 'M-V' -> [1,2,3,4]."""
    a, b = rango.split("-")
    ini, fin = C.DIAS.index(a), C.DIAS.index(b)
    return [d for d in range(ini, fin + 1) if d < horizonte]


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ----------------------------------------------------------------------------- carga
# Todo sale de la base de datos (MODARE_DB, la misma que la app de gestión). Los CSV de data/ solo son la
# semilla con la que se crea la base (gestion/semilla.py) y la entrada de generar_almacenes.py.
class BaseSinDatos(RuntimeError):
    pass


def valor_parametro(v):
    """Los parámetros se guardan como texto: número si se puede (12 -> 12.0); si no, texto (VEH-R12;VEH-R18)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def leer_csv(nombre: str) -> pd.DataFrame:
    """Un CSV de data/ (solo para herramientas que preparan la semilla, como generar_almacenes.py)."""
    return pd.read_csv(C.DATA_DIR / nombre, encoding="utf-8")


def parametros_csv() -> dict:
    df = leer_csv("parametros.csv")
    return {k: valor_parametro(v) for k, v in zip(df.clave, df.valor)}


def _sql(consulta: str) -> pd.DataFrame:
    from .bd import leer
    try:
        return leer(consulta)
    except Exception as e:  # noqa: BLE001 — tabla inexistente: base sin crear o sin migrar
        raise BaseSinDatos(
            f"La base de datos no tiene los datos del optimizador ({e.__class__.__name__}). Arranca la app "
            "(python app.py) o ejecuta  python -m gestion.semilla  para crearla desde data/*.csv.") from e


def cargar_parametros() -> dict:
    df = _sql("SELECT id, valor FROM parametros")
    if df.empty:
        raise BaseSinDatos("La tabla de parámetros está vacía: ejecuta  python -m gestion.semilla")
    return {k: valor_parametro(v) for k, v in zip(df.id, df.valor)}


def _vacios_a_nulo(df: pd.DataFrame) -> pd.DataFrame:
    """En la app un campo de texto vacío se guarda como NULL, pero por si acaso: '' -> nulo."""
    return df.replace({"": None})


@dataclass
class Datos:
    P: dict
    plantas: pd.DataFrame
    almacenes: pd.DataFrame        # cross-docks (escalón intermedio planta -> almacén -> tienda)
    tiendas: pd.DataFrame
    tipos: pd.DataFrame            # vehiculos + emisiones + consumo derivado
    tarifas: pd.DataFrame          # una fila por (operador, tipo)
    km: dict = field(repr=False)   # (origen, destino) -> km
    mins: dict = field(repr=False) # (origen, destino) -> min de camión (sin pausas)
    coords: dict = field(repr=False)
    # vetos (los decide la app): por defecto todo almacén recibe de cualquier planta y toda tienda de cualquier almacén
    plantas_vetadas: dict = field(default_factory=dict, repr=False)     # almacén -> {plantas que no quiere}
    almacenes_vetados: dict = field(default_factory=dict, repr=False)   # tienda  -> {almacenes que no quiere}

    def dist(self, a: str, b: str) -> tuple[float, float]:
        """km y minutos de conducción de camión. Respaldo: línea recta × factor_rodeo."""
        if a == b:
            return 0.0, 0.0
        if (a, b) in self.km:
            return self.km[(a, b)], self.mins[(a, b)]
        (la, oa), (lb, ob) = self.coords[a], self.coords[b]
        km = haversine_km(la, oa, lb, ob) * self.P["factor_rodeo"]
        mins = km / self.P["velocidad_respaldo_kmh"] * 60 * self.P["factor_tiempo_camion"]
        return km, mins

    def horario_deposito(self, dep: str) -> tuple[int, int]:
        """Horario de carga (min del día) de una planta o un almacén."""
        df, col = (self.almacenes, "id_almacen") if dep.startswith("AL-") else (self.plantas, "id_planta")
        row = df.set_index(col).loc[dep]
        return hhmm_a_min(row.hora_inicio_carga), hhmm_a_min(row.hora_fin_carga)

    def nombre_lugar(self, lugar: str) -> tuple[str, str, float, float]:
        """(nombre, municipio/provincia, lat, lon) de una tienda, almacén o planta."""
        for df, col, mun in ((self.tiendas, "id_tienda", "municipio"), (self.almacenes, "id_almacen", "provincia"),
                             (self.plantas, "id_planta", "municipio")):
            m = df[df[col] == lugar]
            if len(m):
                r = m.iloc[0]
                return r.nombre, r[mun], float(r.lat), float(r.lon)
        raise KeyError(lugar)


def cargar_datos(distancias: bool = True) -> Datos:
    """Foto de la base de datos para una optimización. distancias=False no carga la matriz (pantallas que solo
    necesitan la red: es lo más pesado, ~48.000 pares); entonces dist() usa línea recta × factor_rodeo."""
    P = cargar_parametros()
    plantas = _vacios_a_nulo(_sql(
        "SELECT id AS id_planta, nombre, direccion, municipio, provincia, lat, lon, activa, apertura_prevista, "
        "capacidad_salida_kg_semana, hora_inicio_carga, hora_fin_carga FROM plantas ORDER BY id"))
    plantas["activa"] = plantas.activa.astype(int)
    plantas["hora_inicio_carga"] = plantas.hora_inicio_carga.fillna("06:00")
    plantas["hora_fin_carga"] = plantas.hora_fin_carga.fillna("18:00")

    tiendas = _vacios_a_nulo(_sql(
        "SELECT id AS id_tienda, nombre, subtipo, direccion, municipio, provincia, ccaa, lat, lon, acceso_carretera, "
        "superficie_m2, categoria_tamano, ventas_anuales_eur AS ventas_periodo, kg_semana, dias_recepcion, ventana_inicio, "
        "ventana_fin, tiempo_descarga_min, vehiculo_max, en_muestra, planta_actual, activa FROM tiendas ORDER BY id"))
    for c in ("acceso_carretera", "en_muestra", "activa"):
        tiendas[c] = tiendas[c].astype(int)
    tiendas = tiendas[tiendas.activa == 1].copy()
    tiendas["ventas_periodo"] = tiendas.ventas_periodo.fillna(0).astype(float)
    tiendas["ventas_unidad"] = "EUR"            # la app guarda ventas anuales en € (la semilla convierte los índices)
    tiendas["categoria_tamano"] = tiendas.categoria_tamano.fillna("mediana")
    tiendas["dias_recepcion"] = tiendas.dias_recepcion.fillna("L-V")
    tiendas["tiempo_descarga_min"] = tiendas.tiempo_descarga_min.fillna(30).astype(int)

    almacenes = _vacios_a_nulo(_sql(
        "SELECT id AS id_almacen, nombre, provincia, poligono, municipio, lat, lon, capacidad_kg_semana, "
        "hora_inicio AS hora_inicio_carga, hora_fin AS hora_fin_carga, dias_recepcion, tiempo_descarga_min, activo, "
        "coste_fijo_eur_semana, coste_manipulacion_eur_kg FROM almacenes ORDER BY id"))
    almacenes["activo"] = almacenes.activo.astype(int)
    almacenes = almacenes[almacenes.activo == 1].copy()
    if almacenes.empty:
        raise BaseSinDatos("No hay ningún almacén activo en la base de datos")
    almacenes["hora_inicio_carga"] = almacenes.hora_inicio_carga.fillna("06:00")
    almacenes["hora_fin_carga"] = almacenes.hora_fin_carga.fillna("20:00")
    almacenes["dias_recepcion"] = almacenes.dias_recepcion.fillna("L-V")
    almacenes["tiempo_descarga_min"] = almacenes.tiempo_descarga_min.fillna(45).astype(int)
    for c in ("coste_fijo_eur_semana", "coste_manipulacion_eur_kg"):
        almacenes[c] = almacenes[c].astype(float)

    tipos = _sql("SELECT id AS id_tipo, descripcion, mma_t, carga_util_kg, volumen_m3, capacidad_palets, "
                 "kgco2e_km_vacio, kgco2e_km_lleno FROM tipos_vehiculo")
    desconocidos = set(tipos.id_tipo) - set(C.ORDEN_TIPOS)
    if desconocidos:
        raise BaseSinDatos(f"Tipos de vehículo desconocidos para el optimizador: {sorted(desconocidos)} "
                           f"(admitidos: {C.ORDEN_TIPOS})")
    # Consumo propio de cada camión (L/100 km) derivado de sus emisiones
    tipos["l100km_vacio"] = tipos.kgco2e_km_vacio / C.KG_CO2E_POR_LITRO_DIESEL * 100
    tipos["l100km_lleno"] = tipos.kgco2e_km_lleno / C.KG_CO2E_POR_LITRO_DIESEL * 100
    # Capacidad en cajas: limita lo primero entre palés y volumen
    cajas_por_palet = P["kg_por_palet"] / C.KG_POR_CAJA
    m3_por_caja = C.KG_POR_CAJA / P["densidad_kg_m3"]
    tipos["cap_cajas"] = [
        int(min(p * cajas_por_palet, v / m3_por_caja)) for p, v in zip(tipos.capacidad_palets, tipos.volumen_m3)
    ]
    tipos["rango"] = tipos.id_tipo.map(C.ORDEN_TIPOS.index)
    tipos = tipos.sort_values("rango").reset_index(drop=True)

    tarifas = _sql("SELECT id AS id_tarifa, operador, tipo_vehiculo AS id_tipo, coste_fijo_dia_eur, coste_km_eur, "
                   "coste_hora_eur, unidades_disponibles FROM tarifas")
    tarifas = (tarifas.assign(_r=tarifas.id_tipo.map(C.ORDEN_TIPOS.index))
               .sort_values(["operador", "_r"]).drop(columns="_r").reset_index(drop=True))
    mult = P.get("multiplicador_tarifas", 1.0)
    for c in ("coste_fijo_dia_eur", "coste_km_eur", "coste_hora_eur"):
        tarifas[c] = tarifas[c].astype(float).fillna(0) * mult
    tarifas["unidades_disponibles"] = tarifas.unidades_disponibles.astype(float)   # NaN = ilimitado

    km, mins = {}, {}
    if distancias:
        dist = _sql("SELECT origen, destino, distancia_km, duracion_camion_min FROM distancias")
        km = dict(zip(zip(dist.origen, dist.destino), dist.distancia_km))
        mins = dict(zip(zip(dist.origen, dist.destino), dist.duracion_camion_min))

    coords = {r.id_planta: (r.lat, r.lon) for r in plantas.itertuples()}
    coords.update({r.id_almacen: (r.lat, r.lon) for r in almacenes.itertuples()})
    coords.update({r.id_tienda: (r.lat, r.lon) for r in tiendas.itertuples()})
    return Datos(P, plantas, almacenes, tiendas, tipos, tarifas, km, mins, coords,
                 _cargar_vetos("SELECT almacen_id, planta_id FROM almacen_plantas_vetadas"),
                 _cargar_vetos("SELECT tienda_id, almacen_id FROM tienda_almacenes_vetados"))


def _cargar_vetos(consulta: str) -> dict[str, set[str]]:
    """Vetos de la app: cada fila = «quien» no quiere recibir de «vetado». Sin filas, no hay vetos."""
    out: dict[str, set[str]] = {}
    for q, v in _sql(consulta).itertuples(index=False):
        out.setdefault(q, set()).add(v)
    return out


# ----------------------------------------------------------------------------- demanda
def kg_semana_estimado(tienda: pd.Series, P: dict) -> float:
    """kg/semana que hay que reponer según ventas (fórmula de parametros.csv)."""
    ventas = tienda.ventas_periodo
    if tienda.ventas_unidad == "INDICE":
        ventas = ventas / 100 * P["ventas_media_eur_tienda"]
    return ventas / P["precio_medio_eur_kg"] / P["semanas_venta_anio"]


def demanda_red(d: Datos) -> dict[str, float]:
    """La demanda de la app: los kg/semana de cada tienda activa, todas a la vez (sin semanas ni alternancias).
    Una tienda sin kg/semana usa la estimación por ventas."""
    out = {}
    for t in d.tiendas.itertuples():
        kg = t.kg_semana if pd.notna(t.kg_semana) else kg_semana_estimado(pd.Series(t._asdict()), d.P)
        if kg > 0:
            out[t.id_tienda] = round(float(kg), 1)
    return out


def demanda_estimada(d: Datos, semana_iso: int) -> dict[str, float]:
    """Pedido 'por defecto' de cada tienda para una semana, aplicando la frecuencia de su segmento.

    Pequeñas (0,5 entregas/semana) reciben semanas alternas el doble de kg; la semana
    que les toca depende de la paridad de un hash de su id para repartir la carga.
    """
    out = {}
    for t in d.tiendas.itertuples():
        kg = kg_semana_estimado(pd.Series(t._asdict()), d.P)
        freq = d.P[f"entregas_semana_{t.categoria_tamano}"]
        if freq < 1:
            periodo = round(1 / freq)
            if (semana_iso + zlib.crc32(t.id_tienda.encode())) % periodo != 0:
                continue
            kg *= periodo
        out[t.id_tienda] = round(kg, 1)
    return out


@dataclass
class Visita:
    id: str                 # TD-001#1  (o AL-MADRID#1 en el escalón troncal)
    tienda: str             # lugar de entrega: id de tienda o de almacén
    kg: float
    cajas: int
    dias: list[int]         # días permitidos (0 = lunes)
    ini: int                # ventana en minutos del día
    fin: int
    descarga: int
    vehiculo_max: str
    planta: str | None = None      # planta de origen de la mercancía
    deposito: str | None = None    # desde dónde sale el vehículo que la entrega (almacén o planta)


def construir_visitas(d: Datos, pedidos_kg: dict[str, float]) -> tuple[list[Visita], list[dict]]:
    """Convierte el pedido (kg) de cada tienda en una o varias visitas.

    nº visitas = max(frecuencia del segmento, cajas / máx. cajas por entrega), y los días
    de la semana se reparten en bloques consecutivos para que no caigan el mismo día.
    Devuelve (visitas, avisos) — los avisos son pedidos que superan lo que la tienda puede recibir y tiendas
    sin acceso por carretera.
    """
    P, horizonte = d.P, int(d.P["horizonte_planificacion_dias"])
    cajas_por_palet = P["kg_por_palet"] / C.KG_POR_CAJA
    tiendas = d.tiendas.set_index("id_tienda")
    visitas, avisos = [], []
    for tid, kg in pedidos_kg.items():
        if kg <= 0 or tid not in tiendas.index:
            continue
        t = tiendas.loc[tid]
        if t.get("acceso_carretera", 1) != 1:   # islas dadas de alta en la app de gestión
            avisos.append({"tienda": tid, "nombre": t.nombre, "kg": kg,
                           "motivo": "Sin acceso por carretera (islas): requiere transporte marítimo, fuera del VRP"})
            continue
        cajas = math.ceil(kg / C.KG_POR_CAJA)
        cat = t.categoria_tamano
        max_cajas_entrega = int(P[f"max_palets_entrega_{cat}"] * cajas_por_palet)
        dias_ok = dias_desde_rango(t.dias_recepcion, horizonte)
        freq = max(1, round(P[f"entregas_semana_{cat}"]))
        n = max(freq, math.ceil(cajas / max_cajas_entrega))
        if n > len(dias_ok):
            avisos.append({"tienda": tid, "nombre": t.nombre, "kg": kg,
                           "motivo": f"Pedido supera {len(dias_ok)} entregas × {max_cajas_entrega} cajas; se recorta"})
            n = len(dias_ok)
            cajas = min(cajas, n * max_cajas_entrega)
            kg = min(kg, cajas * C.KG_POR_CAJA)
        vmax = t.vehiculo_max if isinstance(t.vehiculo_max, str) else C.VEHICULO_MAX_SIN_DATO
        ini = hhmm_a_min(t.ventana_inicio if isinstance(t.ventana_inicio, str) else P["ventana_inicio_defecto"])
        fin = hhmm_a_min(t.ventana_fin if isinstance(t.ventana_fin, str) else P["ventana_fin_defecto"])
        bloques = [dias_ok[i * len(dias_ok) // n:(i + 1) * len(dias_ok) // n] for i in range(n)]
        reparto = [cajas // n + (1 if i < cajas % n else 0) for i in range(n)]
        for i, (bl, cj) in enumerate(zip(bloques, reparto)):
            visitas.append(Visita(
                id=f"{tid}#{i + 1}", tienda=tid, kg=round(kg * cj / cajas, 1), cajas=cj, dias=bl,
                ini=ini, fin=fin, descarga=int(t.tiempo_descarga_min), vehiculo_max=vmax,
            ))
    return visitas, avisos


def construir_visitas_troncal(d: Datos, visitas_tienda: list[Visita],
                              flujos: dict[str, dict[str, float]]) -> list[Visita]:
    """Escalón troncal: una reposición por (almacén, planta) con flujo, con los kg que esa planta le envía.

    Un almacén puede recibir de varias plantas (flujos = {almacén: {planta: kg}}); cada pareja es una visita
    independiente en el VRP troncal de su planta. Si no cabe en el camión troncal más grande (en cajas o kg)
    o la frecuencia lo pide, se divide en varias entregas en bloques de días distintos (L-V del almacén).
    """
    P, horizonte = d.P, int(d.P["horizonte_planificacion_dias"])
    tipos_t = [t for t in str(P["tipos_troncal"]).split(";") if t]
    tt = d.tipos.set_index("id_tipo").loc[tipos_t]
    max_cajas = int(min(tt.cap_cajas.max(), (tt.carga_util_kg / C.KG_POR_CAJA).max()))
    vmax = max(tipos_t, key=C.ORDEN_TIPOS.index)
    cajas_alm = {}
    for v in visitas_tienda:
        cajas_alm[v.deposito] = cajas_alm.get(v.deposito, 0) + v.cajas
    alm = d.almacenes.set_index("id_almacen")
    out = []
    for a, por_planta in flujos.items():
        r = alm.loc[a]
        kg_a = sum(por_planta.values())
        dias_ok = dias_desde_rango(r.dias_recepcion, horizonte)
        varias = len(por_planta) > 1
        for p, kg in por_planta.items():
            cajas = max(1, round(cajas_alm[a] * kg / kg_a))
            n = min(len(dias_ok), max(round(P["entregas_semana_almacen"]), math.ceil(cajas / max_cajas)))
            bloques = [dias_ok[i * len(dias_ok) // n:(i + 1) * len(dias_ok) // n] for i in range(n)]
            reparto = [cajas // n + (1 if i < cajas % n else 0) for i in range(n)]
            pref = f"{a}<{p.replace('PL-', '')}" if varias else a   # id único aunque haya varias plantas
            for i, (bl, cj) in enumerate(zip(bloques, reparto)):
                out.append(Visita(
                    id=f"{pref}#{i + 1}", tienda=a, kg=round(kg * cj / cajas, 1), cajas=cj, dias=bl,
                    ini=hhmm_a_min(r.hora_inicio_carga), fin=hhmm_a_min(r.hora_fin_carga),
                    descarga=int(r.tiempo_descarga_min), vehiculo_max=vmax,
                    planta=p, deposito=p,
                ))
    return out
