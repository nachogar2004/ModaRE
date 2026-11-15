"""Genera data/almacenes.csv: un almacén (cross-dock) SINTÉTICO por provincia con tiendas.

  * Ubicación: el polígono industrial principal de la provincia (lista en modare/poligonos.py, coordenadas de
    OpenStreetMap vía Nominatim, guardadas en data/poligonos_geocodificados.json). Si no se encuentra, el
    centroide de las tiendas de la provincia.
  * Capacidad (kg/semana que puede recibir y expedir): holgura × demanda media semanal estimada de las
    tiendas de la provincia, redondeada al alza a 500 kg y con un mínimo. Valores en parametros.csv.
  * Costes (SINTÉTICOS, coherentes con el tamaño y la provincia):
      superficie  = m2_base_almacen + (capacidad / kg_por_palet) × m2_por_palet_almacen   (1 semana de stock)
      fijo €/sem  = superficie × (alquiler €/m²/mes de la provincia + gastos_m2_mes_almacen) × 12/52
                    + coste_gestion_almacen_eur_semana
      manipulación €/kg = coste_hora_operario_eur × índice salarial de la zona / productividad_kg_hora_almacen
    El alquiler por provincia (ALQUILER_M2_MES) es un orden de magnitud de naves logísticas pequeñas, sin fuente
    verificada: más caro en Barcelona y Madrid, intermedio en País Vasco, Valencia y corredores logísticos, más
    barato en provincias de interior poco pobladas.

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


# €/m²/mes, nave logística pequeña (SINTÉTICO: orden de magnitud, sin fuente verificada)
ALQUILER_M2_MES = {
    "AL-BARCELONA": 8.0, "AL-MADRID": 6.5, "AL-GIPUZKOA": 6.0, "AL-BIZKAIA": 5.5, "AL-GUADALAJARA": 5.0,
    "AL-VALENCIA": 5.0, "AL-TARRAGONA": 5.0, "AL-GIRONA": 5.0, "AL-ARABA": 4.5, "AL-ALICANTE": 4.5,
    "AL-ZARAGOZA": 4.5, "AL-SEVILLA": 4.5, "AL-MURCIA": 4.0, "AL-CADIZ": 4.0, "AL-ACORUNA": 4.0,
    "AL-PONTEVEDRA": 4.0, "AL-ASTURIAS": 4.0, "AL-CANTABRIA": 4.0, "AL-TOLEDO": 4.0, "AL-CASTELLON": 4.0,
    "AL-LLEIDA": 4.0, "AL-ALMERIA": 3.5, "AL-CORDOBA": 3.5, "AL-LARIOJA": 3.5, "AL-VALLADOLID": 3.5,
    "AL-BURGOS": 3.5, "AL-SEGOVIA": 3.5, "AL-JAEN": 3.0, "AL-OURENSE": 3.0, "AL-CIUDADREAL": 3.0,
    "AL-ALBACETE": 3.0, "AL-LEON": 3.0, "AL-PALENCIA": 3.0, "AL-HUESCA": 3.0, "AL-CACERES": 3.0,
    "AL-ZAMORA": 2.5, "AL-SORIA": 2.5, "AL-TERUEL": 2.5,
}
ALQUILER_DEFECTO = 3.5


def indice_salarial(alquiler: float) -> float:
    """Coste laboral relativo de la zona (SINTÉTICO): sigue al nivel de alquileres."""
    return 1.10 if alquiler >= 6 else 1.0 if alquiler >= 4.5 else 0.92


def slug(provincia: str) -> str:
    base = provincia.split("/")[0]
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z]", "", base).upper()


def costes_almacen(aid: str, cap: float, P: dict) -> dict:
    alquiler = ALQUILER_M2_MES.get(aid, ALQUILER_DEFECTO)
    m2 = P["m2_base_almacen"] + cap / P["kg_por_palet"] * P["m2_por_palet_almacen"]
    fijo = m2 * (alquiler + P["gastos_m2_mes_almacen"]) * 12 / 52 + P["coste_gestion_almacen_eur_semana"]
    manip = P["coste_hora_operario_eur"] * indice_salarial(alquiler) / P["productividad_kg_hora_almacen"]
    return {
        "superficie_m2": round(m2), "alquiler_eur_m2_mes": alquiler,
        "coste_fijo_eur_semana": round(fijo, 2), "coste_manipulacion_eur_kg": round(manip, 4),
        "coste_etiqueta": "SINTÉTICO",
        "coste_fuente": "generar_almacenes.py: superficie por capacidad, alquiler por provincia (orden de magnitud), "
                        "gestión fija y manipulación = €/h operario ÷ kg/h (parametros.csv)",
    }


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
            **costes_almacen(aid, cap, P),
        })
    out = C.DATA_DIR / "almacenes.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    print(f"{len(filas)} almacenes -> {out}  (capacidad total {sum(x['capacidad_kg_semana'] for x in filas):,} kg/semana, "
          f"coste fijo si están todos abiertos {sum(x['coste_fijo_eur_semana'] for x in filas):,.0f} €/semana)")


if __name__ == "__main__":
    main()
