"""Genera data/almacenes.csv: un almacén (cross-dock) SINTÉTICO por provincia con tiendas accesibles por carretera.

  * Ubicación: el polígono industrial principal de la provincia (lista en modare/poligonos.py, coordenadas de
    OpenStreetMap vía Nominatim, guardadas en data/poligonos_geocodificados.json). Si no se encuentra, el
    centroide de las tiendas de la provincia.
  * Capacidad (kg/semana que puede recibir y expedir): holgura × demanda media semanal estimada de las
    tiendas de la provincia, redondeada al alza a 500 kg y con un mínimo. Valores en parametros.csv.

Uso (desde code/):  .venv\\Scripts\\python generar_almacenes.py
Después se puede editar el CSV a mano (mover un almacén, cambiar su capacidad u horario).
"""
import csv
import math
import re
import unicodedata

from modare import config as C
from modare.datos import cargar_datos, kg_semana_estimado
from modare.poligonos import geocodificar


def slug(provincia: str) -> str:
    base = provincia.split("/")[0]
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z]", "", base).upper()


def main():
    d = cargar_datos()
    P = d.P
    t = d.tiendas[d.tiendas.acceso_carretera == 1].copy()
    t["kg"] = [kg_semana_estimado(r, P) for _, r in t.iterrows()]
    grupos = list(t.groupby("provincia"))
    centroides = {f"AL-{slug(prov)}": (g.lat.mean(), g.lon.mean()) for prov, g in grupos}
    print("Ubicando almacenes en el polígono industrial principal de cada provincia (Nominatim, con caché):")
    geo = geocodificar(centroides)
    filas = []
    for prov, g in grupos:
        aid = f"AL-{slug(prov)}"
        cap = max(P["capacidad_min_almacen_kg"],
                  math.ceil(g.kg.sum() * P["holgura_capacidad_almacen"] / 500) * 500)
        if aid in geo:
            x = geo[aid]
            lat, lon, poligono, muni = x["lat"], x["lon"], x["poligono"], x["municipio"]
            fuente = f"Polígono elegido por el equipo; coordenadas de OpenStreetMap (Nominatim): {x['osm']}"
        else:
            lat, lon = centroides[aid]
            poligono, muni = "", ""
            fuente = f"Centroide de las {len(g)} tiendas activas de la provincia (polígono no encontrado)"
            print(f"  aviso: {aid} sin polígono, queda en el centroide")
        filas.append({
            "id_almacen": aid, "nombre": f"Almacén {prov}", "provincia": prov,
            "poligono": poligono, "municipio": muni,
            "lat": round(lat, 6), "lon": round(lon, 6),
            "ubicacion_etiqueta": "SINTÉTICO", "ubicacion_fuente": fuente,
            "capacidad_kg_semana": int(cap), "capacidad_etiqueta": "SINTÉTICO",
            "capacidad_fuente": f"{P['holgura_capacidad_almacen']} × demanda media estimada ({g.kg.sum():.0f} kg/semana), "
                                f"redondeado a 500 kg, mínimo {P['capacidad_min_almacen_kg']:.0f}",
            "hora_inicio_carga": "06:00", "hora_fin_carga": "20:00", "dias_recepcion": "L-V",
            "tiempo_descarga_min": int(P["tiempo_descarga_almacen_min"]),
            "horario_etiqueta": "SINTÉTICO", "activo": 1,
        })
    out = C.DATA_DIR / "almacenes.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    print(f"{len(filas)} almacenes -> {out}  (capacidad total {sum(x['capacidad_kg_semana'] for x in filas):,} kg/semana)")


if __name__ == "__main__":
    main()
