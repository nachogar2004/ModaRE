"""Genera data/almacenes.csv: un almacén (cross-dock) SINTÉTICO por provincia con tiendas accesibles por carretera.

  * Ubicación: centroide de las tiendas activas de la provincia (no hay datos reales todavía).
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


def slug(provincia: str) -> str:
    base = provincia.split("/")[0]
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z]", "", base).upper()


def main():
    d = cargar_datos()
    P = d.P
    t = d.tiendas[d.tiendas.acceso_carretera == 1].copy()
    t["kg"] = [kg_semana_estimado(r, P) for _, r in t.iterrows()]
    filas = []
    for prov, g in t.groupby("provincia"):
        cap = max(P["capacidad_min_almacen_kg"],
                  math.ceil(g.kg.sum() * P["holgura_capacidad_almacen"] / 500) * 500)
        filas.append({
            "id_almacen": f"AL-{slug(prov)}", "nombre": f"Almacén {prov}", "provincia": prov,
            "lat": round(g.lat.mean(), 6), "lon": round(g.lon.mean(), 6),
            "ubicacion_etiqueta": "SINTÉTICO", "ubicacion_fuente": f"Centroide de las {len(g)} tiendas activas de la provincia",
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
