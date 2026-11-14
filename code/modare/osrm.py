"""Distancias y trazados por carretera con OSRM (router.project-osrm.org, datos OpenStreetMap, perfil coche).

  * matriz(...)            -> km y minutos por carretera entre todos los pares (para distancias.csv)
  * añadir_geometrias(...) -> trazado por carretera de cada tramo de cada ruta del plan (para el mapa)

El servidor público pide como máximo ~1 petición por segundo; aquí se respeta con PAUSA_S. Los trazados se
guardan en data/geometrias_osrm.json (clave "lat,lon|lat,lon" de cada tramo) para no volver a pedirlos.
Si el servidor no responde, el plan sigue funcionando: los tramos sin trazado se dibujan en línea recta.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request

from . import config as C


URL = "https://router.project-osrm.org"
PAUSA_S = 1.1
CACHE_GEOM = C.DATA_DIR / "geometrias_osrm.json"
_ultima = 0.0


def contexto_ssl() -> ssl.SSLContext:
    """Certificados del sistema + los de certifi.
    * Del sistema: en redes con inspección HTTPS (antivirus, proxy de empresa/universidad) el certificado
      raíz de la red solo está en el almacén del sistema operativo.
    * De certifi: el Python de python.org en macOS no trae certificados del sistema y sin certifi todas las
      peticiones HTTPS fallan con CERTIFICATE_VERIFY_FAILED."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError):
        pass
    return ctx


_SSL = contexto_ssl()


class SinConexion(RuntimeError):
    """Fallo de red o de certificados: no tiene sentido seguir intentándolo con más rutas."""


def _explicar(e: Exception) -> str:
    txt = str(e)
    if "CERTIFICATE_VERIFY_FAILED" in txt or isinstance(getattr(e, "reason", None), ssl.SSLError):
        return (f"{txt} -> problema de certificados SSL. Solución: '.venv/bin/python -m pip install certifi' "
                "(o en Mac ejecutar 'Install Certificates.command' de la carpeta de Python), o probar otra red/sin VPN")
    if isinstance(e, urllib.error.HTTPError) and e.code == 429:
        return f"{txt} -> el servidor público de OSRM limita las peticiones; vuelve a intentarlo en unos minutos"
    return txt


def _get(path: str, intentos: int = 4) -> dict:
    global _ultima
    for k in range(intentos):
        espera = PAUSA_S - (time.time() - _ultima)
        if espera > 0:
            time.sleep(espera)
        _ultima = time.time()
        try:
            req = urllib.request.Request(URL + path, headers={"User-Agent": "modare-prototipo/1.0"})
            with urllib.request.urlopen(req, timeout=60, context=_SSL) as r:
                data = json.loads(r.read().decode())
            if data.get("code") == "Ok":
                return data
            raise RuntimeError(data.get("message") or data.get("code"))
        except urllib.error.HTTPError as e:          # 429 (límite de peticiones), 5xx...
            if k == intentos - 1:
                raise RuntimeError(_explicar(e)) from e
            time.sleep(10 if e.code == 429 else 2 ** k * 2)
        except urllib.error.URLError as e:           # sin red, DNS, SSL: reintentar no suele servir
            if k == intentos - 1 or isinstance(e.reason, ssl.SSLError):
                raise SinConexion(_explicar(e)) from e
            time.sleep(2 ** k * 2)
        except (TimeoutError, RuntimeError, json.JSONDecodeError) as e:
            if k == intentos - 1:
                raise RuntimeError(_explicar(e)) from e
            time.sleep(2 ** k * 2)
    raise RuntimeError("inalcanzable")


def _coords(puntos: list[tuple[float, float]]) -> str:
    """[(lat, lon), ...] -> 'lon,lat;lon,lat' (OSRM usa lon,lat)."""
    return ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in puntos)


# ----------------------------------------------------------------------------- matriz de distancias
def matriz(puntos: list[tuple[float, float]], bloque: int = 50, log=print):
    """Matrices km[i][j] y min[i][j] (None si OSRM no encuentra ruta). Se pide por bloques de
    bloque × bloque porque el servidor público limita el nº de coordenadas por consulta."""
    n = len(puntos)
    km = [[None] * n for _ in range(n)]
    mins = [[None] * n for _ in range(n)]
    trozos = [list(range(i, min(i + bloque, n))) for i in range(0, n, bloque)]
    total, hecho = len(trozos) ** 2, 0
    for I in trozos:
        for J in trozos:
            idx = I if I == J else I + J
            src = list(range(len(I)))
            dst = src if I == J else list(range(len(I), len(I) + len(J)))
            q = (f"/table/v1/driving/{_coords([puntos[k] for k in idx])}?annotations=distance,duration"
                 f"&sources={';'.join(map(str, src))}&destinations={';'.join(map(str, dst))}")
            data = _get(q)
            for a, i in enumerate(I):
                for b, j in enumerate(J):
                    dist, dur = data["distances"][a][b], data["durations"][a][b]
                    km[i][j] = None if dist is None else dist / 1000
                    mins[i][j] = None if dur is None else dur / 60
            hecho += 1
            log(f"  OSRM tabla {hecho}/{total}")
    return km, mins


# ----------------------------------------------------------------------------- trazados de rutas
def _decodificar(s: str) -> list[list[float]]:
    """Polyline de Google (precisión 5) -> [[lat, lon], ...]."""
    pts, i, lat, lon = [], 0, 0, 0
    while i < len(s):
        for eje in range(2):
            res = shift = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            v = ~(res >> 1) if res & 1 else res >> 1
            if eje == 0:
                lat += v
            else:
                lon += v
        pts.append([lat / 1e5, lon / 1e5])
    return pts


def _codificar(pts: list[list[float]]) -> str:
    out, plat, plon = [], 0, 0
    for lat, lon in pts:
        for v, prev in ((round(lat * 1e5), plat), (round(lon * 1e5), plon)):
            d = v - prev
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plon = round(lat * 1e5), round(lon * 1e5)
    return "".join(out)


def _cargar_cache() -> dict:
    try:
        return json.loads(CACHE_GEOM.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def añadir_geometrias(plan: dict, log=print) -> None:
    """Añade a cada ruta r["geometria"]: una polyline codificada por tramo (salida→parada1, …, última→vuelta).
    Una petición OSRM por ruta con tramos nuevos (steps=true da la geometría de cada tramo por separado)."""
    lugares = {p["id"]: (p["lat"], p["lon"]) for p in plan["plantas"] + plan.get("almacenes", [])}
    cache = _cargar_cache()
    nuevas = fallos = 0
    sin_conexion = None
    for r in plan["rutas"]:
        origen = r.get("origen", r["planta"])
        ids = [origen] + [p["tienda"] for p in r["paradas"]] + [origen]
        pts = [lugares[origen]] + [(p["lat"], p["lon"]) for p in r["paradas"]] + [lugares[origen]]
        # clave por coordenadas (no por id): si se mueve un almacén, su trazado deja de valer
        claves = [f"{a[0]:.5f},{a[1]:.5f}|{b[0]:.5f},{b[1]:.5f}" for a, b in zip(pts, pts[1:])]
        faltan = [k for k, (a, b) in zip(claves, zip(ids, ids[1:])) if a != b and k not in cache]
        if faltan and sin_conexion:
            fallos += 1
        elif faltan:
            try:
                data = _get(f"/route/v1/driving/{_coords(pts)}?overview=false&steps=true&geometries=polyline")
                for k, leg in zip(claves, data["routes"][0]["legs"]):
                    tramo = []
                    for st in leg["steps"]:
                        g = _decodificar(st["geometry"])
                        tramo += g[1:] if tramo else g
                    cache[k] = _codificar(tramo)
                nuevas += 1
            except SinConexion as e:  # red/SSL: se avisa una vez y no se insiste con el resto de rutas
                fallos += 1
                sin_conexion = e
                log(f"  aviso: no se puede conectar con OSRM: {e}")
            except Exception as e:  # servidor caído, límite de peticiones...: se dibuja en línea recta
                fallos += 1
                log(f"  aviso: sin trazado OSRM para {r.get('nombre', r['id'])}: {e}")
        r["geometria"] = [cache.get(k, "") for k in claves]
    if nuevas:
        CACHE_GEOM.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    log(f"  trazados por carretera: {nuevas} rutas pedidas a OSRM, {len(plan['rutas']) - nuevas - fallos} desde caché"
        + (f", {fallos} sin trazado (línea recta; para reintentar solo el dibujo: "
           f"python -m modare.visualizar {plan['resumen']['semana']})" if fallos else ""))
