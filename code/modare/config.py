"""Rutas de ficheros y parámetros que NO están en data/parametros.csv.

Todo lo que sí está en parametros.csv se lee de allí (ver datos.cargar_parametros).
Lo de aquí son supuestos del prototipo: cámbialos o muévelos a parametros.csv.
"""
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = CODE_DIR.parent / "data"
SALIDAS_DIR = CODE_DIR / "salidas"
DB_PATH_ANTIGUO = CODE_DIR / "pedidos.db"   # antes los pedidos iban aquí; ahora a la BD común (modare.bd)

# --- Unidades de carga -------------------------------------------------------
# La ropa se mide en kg y se mueve en cajas. kg_por_palet (250) viene de parametros.csv.
KG_POR_CAJA = 12.5            # SINTÉTICO: caja estándar de ropa clasificada -> 20 cajas/palé

# --- Conductores --------------------------------------------------------------
PAUSA_CADA_KM = 200           # Regla del reto: el conductor para 20 min cada 200 km
PAUSA_MIN = 20
DESCANSO_NOCTURNO_MIN = 11 * 60   # Reglamento (CE) 561/2006: descanso diario normal

# --- Combustible / emisiones ---------------------------------------------------
# Consumo propio de cada tipo de camión: se deriva de emisiones.csv
#   litros/km = kgCO2e/km / KG_CO2E_POR_LITRO_DIESEL   (interpolado según carga)
KG_CO2E_POR_LITRO_DIESEL = 2.51   # ESTIMADO: DEFRA 2025, gasóleo con mezcla media de biocombustible
PRECIO_GASOLEO_EUR_L = 1.45        # ESTIMADO: solo informativo (la tarifa €/km ya incluye combustible)
PRECIO_CO2_EUR_T = 0.0             # Precio sombra del CO2 en la función objetivo (0 = solo coste)

# --- Solver --------------------------------------------------------------------
MARGEN_VENTANA_MIN = 15               # El solver planifica llegar 15 min antes del límite real (colchón
                                      # frente a la aproximación lineal de las pausas)
PENALIZACION_NO_SERVIR_EUR = 100_000   # Coste de dejar una visita sin servir (debe ser >> coste de una ruta)
TIPOS_EXCLUIDOS_REPARTO = {"VEH-ART"}  # El tráiler de 40 t no entra en ninguna tienda
VEHICULO_MAX_SIN_DATO = "VEH-R18"      # Corners en centros comerciales (vehiculo_max vacío)
ORDEN_TIPOS = ["VEH-FUR", "VEH-R75", "VEH-R12", "VEH-R18", "VEH-ART"]
DIAS = ["L", "M", "X", "J", "V", "S", "D"]
