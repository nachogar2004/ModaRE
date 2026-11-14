"""Polígono industrial principal de cada provincia, donde se coloca su almacén (SINTÉTICO: elección del equipo).

Criterio: el gran polígono logístico/industrial del área metropolitana de la capital (o el principal nudo
logístico de la provincia). Cada entrada: id del almacén -> (nombre del polígono, municipio, consultas a Nominatim).
Las coordenadas se obtienen con geocodificar() y se guardan en data/poligonos_geocodificados.json; si alguna
sale mal, se puede corregir a mano en ese JSON (o en almacenes.csv) y volver a ejecutar generar_almacenes.py.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from . import config as C
from .datos import haversine_km

POLIGONOS = {
    "AL-ACORUNA": ("Polígono de Sabón", "Arteixo", ["Polígono de Sabón", "Sabón Arteixo"]),
    "AL-ALBACETE": ("Polígono Industrial Campollano", "Albacete", ["Campollano Albacete", "Polígono Campollano"]),
    "AL-ALICANTE": ("Polígono Industrial Las Atalayas", "Alicante", ["Las Atalayas Alicante", "Polígono Las Atalayas"]),
    "AL-ALMERIA": ("Polígono Industrial La Juaida", "Viator", ["La Juaida", "Polígono La Juaida"]),
    "AL-ARABA": ("Polígono Industrial de Júndiz", "Vitoria-Gasteiz", ["Júndiz", "Jundiz Vitoria"]),
    "AL-ASTURIAS": ("Polígono Industrial de Asipo", "Llanera", ["Asipo Llanera", "Polígono de Asipo"]),
    "AL-BARCELONA": ("Zona Franca de Barcelona", "Barcelona", ["Zona Franca de Barcelona", "Polígon Zona Franca"]),
    "AL-BIZKAIA": ("Polígono Industrial de Asua", "Erandio", ["Asua Erandio", "Polígono Industrial Asua"]),
    "AL-BURGOS": ("Polígono Industrial de Villalonquéjar", "Burgos", ["Villalonquéjar", "Polígono Villalonquéjar Burgos"]),
    "AL-CANTABRIA": ("Polígono Industrial de Guarnizo", "El Astillero", ["Polígono Industrial de Guarnizo", "Guarnizo"]),
    "AL-CASTELLON": ("Ciudad del Transporte", "Castellón de la Plana", ["Ciudad del Transporte Castellón", "Ciudad del Transporte"]),
    "AL-CIUDADREAL": ("Polígono Industrial Larache", "Ciudad Real", ["Larache Ciudad Real", "Polígono Larache"]),
    "AL-CACERES": ("Polígono Industrial Las Capellanías", "Cáceres", ["Las Capellanías Cáceres", "Capellanías"]),
    "AL-CADIZ": ("Parque Empresarial / Polígono El Portal", "Jerez de la Frontera", ["El Portal Jerez", "Polígono El Portal"]),
    "AL-CORDOBA": ("Polígono Industrial Las Quemadas", "Córdoba", ["Las Quemadas Córdoba", "Polígono Las Quemadas"]),
    "AL-GIPUZKOA": ("Polígono Industrial Belartza", "Donostia / San Sebastián", ["Belartza", "Belartza Donostia"]),
    "AL-GIRONA": ("Polígon Industrial Mas Xirgu", "Girona", ["Mas Xirgu", "Mas Xirgu Girona"]),
    "AL-GUADALAJARA": ("Polígono Industrial El Henares", "Guadalajara", ["Polígono Industrial El Henares, Guadalajara"]),
    "AL-HUESCA": ("Plataforma Logística de Huesca (PLHUS)", "Huesca", ["PLHUS", "Plataforma Logística Huesca"]),
    "AL-JAEN": ("Polígono Industrial Los Olivares", "Jaén", ["polígono industrial Los Olivares Jaén"]),
    "AL-LARIOJA": ("Polígono Industrial Cantabria", "Logroño", ["Polígono Cantabria Logroño", "Polígono Industrial Cantabria"]),
    "AL-LEON": ("Polígono Industrial de Onzonilla", "Onzonilla", ["Polígono Industrial de Onzonilla", "Onzonilla"]),
    "AL-LLEIDA": ("Polígon Industrial El Segre", "Lleida", ["Polígon Industrial El Segre, Lleida"]),
    "AL-MADRID": ("Centro de Transportes de Coslada", "Coslada", ["Centro de Transportes de Coslada", "Polígono Industrial Coslada"]),
    "AL-MURCIA": ("Polígono Industrial Oeste", "Murcia (San Ginés)", ["Polígono Industrial Oeste Murcia", "Polígono Oeste San Ginés"]),
    "AL-OURENSE": ("Polígono Industrial de San Cibrao das Viñas", "San Cibrao das Viñas", ["polígono industrial San Cibrao das Viñas"]),
    "AL-PALENCIA": ("Polígono Industrial San Blas (junto a Palencia capital)", "Villalobón", ["Polígono de Villalobón"]),
    "AL-PONTEVEDRA": ("Polígono Industrial A Granxa", "O Porriño", ["A Granxa Porriño", "Polígono A Granxa"]),
    "AL-SEGOVIA": ("Polígono Industrial de Hontoria", "Segovia", ["Hontoria Segovia", "Polígono de Hontoria"]),
    "AL-SEVILLA": ("Polígono Industrial Carretera Amarilla", "Sevilla", ["Carretera Amarilla Sevilla", "Polígono Carretera Amarilla"]),
    "AL-SORIA": ("Polígono Industrial Las Casas", "Soria", ["Polígono Las Casas Soria", "Las Casas Soria"]),
    "AL-TARRAGONA": ("Polígon Industrial Riu Clar", "Tarragona", ["Riu Clar Tarragona", "Polígon Riu Clar"]),
    "AL-TERUEL": ("Plataforma Logístico-Industrial de Teruel (PLATEA)", "Teruel", ["PLATEA Teruel", "Plataforma Logística Teruel"]),
    "AL-TOLEDO": ("Polígono Industrial de Toledo (Santa María de Benquerencia)", "Toledo", ["Santa María de Benquerencia Toledo", "Polígono Industrial Toledo"]),
    "AL-VALENCIA": ("Polígono Industrial Fuente del Jarro", "Paterna", ["Fuente del Jarro Paterna", "Font del Gerro"]),
    "AL-VALLADOLID": ("Polígono Industrial de San Cristóbal", "Valladolid", ["Polígono San Cristóbal Valladolid", "San Cristóbal Valladolid polígono"]),
    "AL-ZAMORA": ("Polígono Industrial Los Llanos", "Zamora", ["polígono industrial Zamora"]),
    "AL-ZARAGOZA": ("Plataforma Logística de Zaragoza (PLAZA)", "Zaragoza", ["Plataforma Logística de Zaragoza", "PLAZA Zaragoza"]),
}

CACHE = C.DATA_DIR / "poligonos_geocodificados.json"
URL = "https://nominatim.openstreetmap.org/search"
MAX_KM = 80  # el resultado debe estar a menos de esto del centroide de las tiendas de la provincia


def _buscar(q: str) -> list[dict]:
    url = URL + "?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 10, "countrycodes": "es"})
    req = urllib.request.Request(url, headers={"User-Agent": "modare-prototipo/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read().decode())
    time.sleep(1.1)  # política de uso de Nominatim: 1 petición/s
    return out


def _puntua(res: dict) -> int:
    """Prefiere zonas industriales de OSM sobre edificios, calles o paradas."""
    cat, tip = res.get("category"), res.get("type")
    if cat == "landuse" and tip in ("industrial", "commercial", "retail"):
        return 0
    if cat in ("place", "boundary") or tip in ("industrial", "neighbourhood", "quarter", "suburb", "hamlet"):
        return 1
    if cat == "highway":
        return 2
    return 3


def geocodificar(centroides: dict[str, tuple[float, float]], log=print) -> dict:
    """Devuelve {id_almacen: {"lat", "lon", "poligono", "municipio", "osm", "consulta"}} (con caché)."""
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    for aid, (nombre, muni, consultas) in POLIGONOS.items():
        if aid in cache or aid not in centroides:
            continue
        clat, clon = centroides[aid]
        mejor = None
        for q in consultas:
            for r in _buscar(q):
                km = haversine_km(clat, clon, float(r["lat"]), float(r["lon"]))
                if km > MAX_KM:
                    continue
                clave = (_puntua(r), km)
                if mejor is None or clave < mejor[0]:
                    mejor = (clave, r, q)
            if mejor and mejor[0][0] == 0:
                break
        if mejor:
            _, r, q = mejor
            cache[aid] = {"lat": round(float(r["lat"]), 6), "lon": round(float(r["lon"]), 6), "poligono": nombre,
                          "municipio": muni, "osm": f"{r['category']}/{r['type']}: {r['display_name'][:160]}",
                          "consulta": q}
            log(f"  {aid:15s} {r['category']}/{r['type']:12s} {r['display_name'][:90]}")
        else:
            log(f"  {aid:15s} NO ENCONTRADO -> se queda en el centroide")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache
