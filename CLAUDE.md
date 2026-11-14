# ModaRE · Optimización del reparto semanal (hackathon IA4Good)

## El problema

Moda re- (ropa de segunda mano) tiene **plantas** que clasifican ropa y **tiendas** que la venden.
Cada semana hay que llevar ropa (medida en **kg**, movida en **cajas** sobre palés) de las plantas a
las tiendas con camiones de distintos tipos, cada uno con su **capacidad, tarifa y consumo**.

**Los camiones grandes no entran en las ciudades**, así que no existe el trayecto planta → tienda.
El reparto es en **dos escalones**:

- **troncal**: planta → **almacén** (cross-dock, uno por provincia) con camiones grandes;
- **reparto**: almacén → tiendas con vehículos que sí entran en la ciudad.

Hay que decidir:

1. qué almacén sirve a cada tienda y qué planta repone cada almacén (con capacidades de ambos),
2. **cuántos camiones** salen en cada escalón y de qué tipo/operador (no es fijo),
3. el orden de las paradas y el horario de cada ruta,

minimizando el coste (y opcionalmente el CO₂) y cumpliendo ventanas de recepción, capacidades y
las reglas de los conductores (**pausa de 20 min cada 200 km**, jornada y descanso nocturno).

Flujo de negocio que implementa el prototipo:
**los dueños de las tiendas piden en la app → al final de la semana la central optimiza → salen las rutas.**

## Datos (`data/`)

Todos los CSV llevan columnas `etiqueta` (REAL / ESTIMADO / SINTÉTICO) y `fuente`. Respetadlas al presentar.

| Fichero | Qué tiene | Notas |
|---|---|---|
| `plantas.csv` | 6 plantas (4 activas: MAD, VAL, BCN, BIO; AST y SCQ futuras) | `capacidad_salida_kg_semana`, horario de carga 06–18 h |
| `almacenes.csv` | 38 almacenes, uno por provincia peninsular con tiendas | **todo SINTÉTICO**: ubicación = **polígono industrial principal** de la provincia (elegido por el equipo en `code/modare/poligonos.py`, coordenadas de OpenStreetMap/Nominatim cacheadas en `poligonos_geocodificados.json`; Zamora fijado a mano, Palencia en el polígono San Blas de Villalobón, junto a la capital), capacidad = 1,5 × demanda media (mín. 1.500 kg/semana), horario 06–20 h L-V. Se genera con `code/generar_almacenes.py`; se puede editar a mano |
| `tiendas.csv` | 182 tiendas | ventas €/año, tamaño, ventana de recepción, `vehiculo_max`, `acceso_carretera` (7 de Baleares = 0), `en_muestra` (53 para pruebas) |
| `vehiculos.csv` | 5 tipos: FUR 3,5 t, R75, R12, R18, ART 40 t | carga útil kg, volumen m³, palés |
| `emisiones.csv` | kgCO₂e/km vacío y lleno por tipo (DEFRA 2025) | de aquí sale el **consumo propio** de cada camión |
| `tarifas.csv` | Operador A (€/km + €/h, ilimitado) y B (€/día + €/km, unidades limitadas) | |
| `distancias.csv` | matriz completa **por carretera (OSRM)** entre plantas, almacenes y tiendas: km, min de coche y min de camión (×`factor_tiempo_camion`) | 219 puntos, 47.742 pares, islas no incluidas. Se regenera con `code/calcular_distancias.py` (~30 s); hay que repetirlo si se mueven/añaden almacenes, plantas o tiendas. Si a un par le falta dato se usa línea recta × `factor_rodeo` |
| `geometrias_osrm.json` | caché de trazados por carretera de cada tramo (polyline) para el mapa | se rellena sola al guardar un plan |
| `parametros.csv` | todos los supuestos de negocio (kg/palé, frecuencias, jornada, días máx. de ruta, `tipos_troncal`…) | **cambiad aquí los supuestos, no en el código** |

Demanda por defecto (si la tienda no pide): `kg_semana = ventas / precio_medio_eur_kg / semanas_venta_anio`
(~34 t/semana en total frente a 46 t de capacidad de las 4 plantas activas).

## Modelo de optimización

Descomposición *cluster-first, route-second* en dos escalones, todo con OR-Tools:

### Etapa 1 · Asignación tienda → almacén → planta (`code/modare/asignacion.py`, CP-SAT)
```
min  Σ c_rep · km(a,s) · kg_s · x[s,a]  +  Σ c_tro · km(p,a) · f[a,p]
s.a. Σ_a x[s,a] = 1                        cada tienda la sirve un almacén (de sus 5 más cercanos)
     Σ_s kg_s · x[s,a] ≤ capacidad_a        capacidad semanal del almacén
     Σ_p y[a,p] ≤ 1                         cada almacén lo repone una sola planta
     Σ_p f[a,p] = Σ_s kg_s · x[s,a]         conservación del flujo;  f[a,p] ≤ cap_a · y[a,p]
     Σ_a f[a,p] ≤ capacidad_p               capacidad de salida semanal de la planta (solo activas)
```
`c_rep`, `c_tro` = €/(kg·km) del operador A con el vehículo típico de cada escalón (7,5 t / tráiler).
`--asignacion actual`: cada tienda a su almacén provincial y cada almacén a la `planta_actual` mayoritaria de sus tiendas.

### Etapa 2 · VRP heterogéneo con ventanas horarias, uno por depósito (`code/modare/vrp.py`, routing solver)
El mismo modelo se resuelve en los dos escalones:

| | depósito | visitas | vehículos |
|---|---|---|---|
| **troncal** | cada planta | 1 reposición por almacén y semana (`entregas_semana_almacen`; se parte si no cabe en un camión) | `tipos_troncal` = R12, R18, ART |
| **reparto** | cada almacén | entregas a tiendas | todos menos ART, limitados por `vehiculo_max` de la tienda |

- **Visitas a tienda**: `max(entregas_semana_<tamaño>, cajas / máx_cajas_por_entrega)`; las grandes reciben 2/semana
  (L-M y X-V), las pequeñas cada 2 semanas el doble (semana par/impar según hash del id).
- **Vehículos**: un *pool* de rutas candidatas por (operador, tipo). **El solver decide cuáles usa** →
  el nº de camiones es una decisión. Operador B limitado a `unidades_disponibles` × días por depósito.
- **Función objetivo** (céntimos): fijo/día (B) + km × (€/km + precio_CO₂ × kgCO₂/km) + horas de trabajo × €/h (A)
  + tiempo fuera del depósito × `coste_pernocta`/24 h.
- **Restricciones**:
  - capacidad en **kg** (carga útil) y en **cajas** (= min(palés × 20, volumen / 0,083 m³))
  - `vehiculo_max` de la tienda (centros urbanos ≤ 7,5 t; corners sin dato → hasta R18; ART nunca reparte)
  - ventana de recepción solo en los días permitidos (L-V / M-V), reloj semanal en minutos
  - salida del depósito en su horario de carga + 45 min de carga
  - **pausa 20 min / 200 km** (prorrateada en el tiempo de cada tramo)
  - **descanso nocturno obligatorio** de 11 h (break interval entre 19:00 y 22:00 cada noche): nadie conduce de noche
  - jornada ≤ 13 h y conducción ≤ 9 h por día × `dias_max_ruta` (3); rutas multidía con pernocta
  - cada visita se sirve o se paga una penalización de 100.000 € → si no cabe, se reporta, no rompe
- **Portafolio**: la búsqueda local se atasca según la solución inicial (si arranca con furgonetas no
  consolida en camiones). Cada depósito se resuelve con varias configuraciones (estrategia inicial × flota con/sin
  furgonetas) en paralelo y se queda la mejor: 3 en el troncal, 5 en almacenes con > 6 entregas, 2 en el resto.
  Los almacenes usan un tiempo de búsqueda proporcional a su tamaño (`segundos × entregas / 25`, mín. 3 s).
- **Sincronización** (supuesto): los almacenes trabajan con stock; la reposición troncal de la semana W cubre
  el reparto de la semana W+1, así que los dos escalones se planifican a la vez sin esperarse.

### Simulación exacta (`code/modare/simulacion.py`)
El solver usa aproximaciones lineales, así que cada ruta se re-simula con reglas exactas: pausas de 20 min
al cumplir cada 200 km, descanso de 11 h al agotar 9 h de conducción o 13 h de jornada, esperas a ventana.
De ahí salen los **KPIs reales**: coste facturable por tarifa, km, horas, pernoctas, **litros de gasóleo y
CO₂ interpolados según la carga de cada tramo** (L/km = kgCO₂e/km ÷ 2,51), y si cada entrega llega a tiempo.
Los **camiones físicos** = máximo de rutas solapadas por (depósito, tarifa): un camión hace varias rutas a la semana.

## Código (`code/`)

```
code/
  app.py              Flask: /tienda (pedido de cada tienda) y /central (optimizar + mapa)
  optimizar.py        CLI
  generar_almacenes.py  crea data/almacenes.csv (sintético) a partir de tiendas.csv
  modare/config.py    rutas y supuestos que NO están en parametros.csv (kg/caja, pausas, CO2/L…)
  modare/datos.py     carga de CSV, demanda estimada, pedidos → visitas, visitas troncales
  modare/asignacion.py, vrp.py, simulacion.py   (ver arriba)
  modare/pedidos.py   SQLite (code/pedidos.db), un pedido por (semana ISO, tienda)
  modare/planificador.py  orquesta todo y escribe salidas/<semana>/
  modare/visualizar.py    genera salidas/<semana>/mapa.html
  modare/osrm.py      cliente OSRM: matriz de distancias y trazados de rutas (1 petición/s, con caché)
  calcular_distancias.py  regenera data/distancias.csv por carretera
  templates/          HTML de la app (Leaflet para el mapa)
```

### Salidas (`code/salidas/<semana>/`, se sobrescriben en cada ejecución de esa semana)
- `mapa.html`: grafo sobre el mapa con el **trazado real por carretera** (troncal = línea gruesa del color de la planta, reparto = línea fina),
  lista de rutas filtrable por escalón y planta, y cronograma semanal. Se abre con doble clic (fondo Esri, sin API key).
  Pestañas de analítica (calculadas en el navegador desde el plan): **Coste por tienda** (troncal + reparto imputados,
  €/kg, CSV), **Rutas** (coste y desglose km/horas/fijo/pernoctas), **Almacenes** (€/kg troncal y reparto) y **Resumen**
  (por componente, vehículo, operador, planta y flota). Imputación: el coste de cada ruta se reparte entre sus paradas
  por kg (o por kg × distancia en línea recta al depósito, seleccionable); cada almacén tiene un €/kg troncal
  (coste troncal recibido ÷ kg recibidos) que pagan sus tiendas por sus kg. Es una convención, no una factura.
- `rutas.csv`: una fila por ruta. Nombres `T-MAD-01` (troncal desde la planta de Madrid), `R-SEVILLA-02`
  (reparto desde el almacén de Sevilla); el id interno del solver va en `id_solver`.
- `paradas.csv`: una fila por parada. `almacenes.csv`: uso de cada almacén. `plan.json`: todo lo anterior + KPIs.
- Regenerar el mapa sin optimizar: `.venv\Scripts\python -m modare.visualizar 2026-W47`.

### Cómo ejecutar (Windows, desde `code/`)
```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python generar_almacenes.py                                         # solo si falta data/almacenes.csv
.venv\Scripts\python calcular_distancias.py                                       # tras cambiar almacenes/plantas/tiendas
.venv\Scripts\python optimizar.py --muestra --fuente estimacion --segundos 10   # prueba rápida (~15 s)
.venv\Scripts\python optimizar.py --semana 2026-W47 --segundos 30               # red completa (~60 s)
.venv\Scripts\python app.py                                                      # http://127.0.0.1:5000
```
Opciones CLI: `--fuente mixto|pedidos|estimacion`, `--asignacion optimo|actual`, `--plantas-futuras`,
`--precio-co2 80` (€/t en el objetivo), `--secuencial` (sin portafolio, para depurar).
En consola de Windows usad `PYTHONIOENCODING=utf8` si salen caracteres raros.

### Resultados de referencia (semana 2026-W47, 30 s/planta, 20 CPU)
Dos escalones (modelo actual):

| Escenario | Rutas (troncal + reparto) | Camiones | km | Coste (troncal + reparto) | CO₂ | Servicio |
|---|---|---|---|---|---|---|
| Asignación óptima, almacenes en **polígonos**, distancias por carretera | 69 (10 + 59) | 52 | 12.401 | 16.037 € (7.702 + 8.335) | 5,7 t | 100 % |
| Ídem con almacenes en el centroide de la provincia | 64 (10 + 54) | 52 | 11.372 | 16.425 € (8.221 + 8.204) | 5,5 t | 100 % |
| Ídem con distancias en línea recta ×1,26 (antes de OSRM) | 69 (10 + 59) | 50 | 11.406 | 14.577 € (7.453 + 7.124) | 5,4 t | 100 % |
| Asignación actual, semana W48, línea recta ×1,26 | 70 (11 + 59) | 51 | 12.100 | 15.780 € (8.720 + 7.060) | 6,0 t | 100 % (troncal 97 %) |

El troncal sale casi todo en rígidos de 12 t (un tráiler para Barcelona) y el reparto en furgonetas; 0,52 €/kg.
Con distancias reales los km apenas cambian, pero los tiempos son mayores (sobre todo en tramos urbanos),
lo que encarece el coste por hora del operador A un ~13 %.
La primera ejecución tarda ~1 min más porque pide a OSRM el trazado de cada ruta (luego queda en caché).
**Ojo al comparar con el modelo directo anterior** (planta → tienda: 17.025 €, 19 camiones): el coste de dos
escalones **no incluye operar los almacenes** (manipulación, alquiler, personal), que hoy vale 0; y los camiones
físicos del reparto se cuentan por almacén (cada almacén necesita al menos uno).

## Supuestos y limitaciones (decidlos/validadlos con la ONG)
- **Almacenes inventados** (qué polígono, capacidad, horario): ver `almacenes.csv`, `modare/poligonos.py` y `generar_almacenes.py`.
  Tras cambiar un almacén: `generar_almacenes.py` → `calcular_distancias.py` → `optimizar.py`.
- Coste de operar los almacenes no modelado (añadir €/kg manipulado o coste fijo por almacén abierto).
- Todos los almacenes se consideran abiertos; no se decide cuáles abrir (sería un problema de localización).
- Sincronización troncal/reparto por stock (la reposición de W sirve el reparto de W+1).
- 1 caja = 12,5 kg (20 cajas/palé) — `config.KG_POR_CAJA`, SINTÉTICO.
- `unidades_disponibles` del operador B se interpreta **por depósito (planta o almacén) y día**.
- Las 7 tiendas de Baleares (`acceso_carretera = 0`) quedan fuera del VRP y se listan como aviso (transporte marítimo).
- El usuario mencionó 192 tiendas; `tiendas.csv` tiene 182.
- La tarifa €/km ya incluye combustible; los litros y € de gasóleo se reportan solo como información.
- Las rutas no se encadenan explícitamente por camión (multi-trip): la flota física se estima por solapamiento.
- Vuelta al depósito en vacío: la logística inversa (`kg_retorno_por_kg_entregado`) no está modelada aún.
- OSRM público usa el perfil de **coche**; el tiempo de camión = tiempo de coche × `factor_tiempo_camion` (1,2). No tiene en cuenta restricciones de camión (gálibo, peso, ZBE).

## Siguientes pasos posibles
- Coste de almacén y decisión de qué almacenes abrir (localización de cross-docks).
- Ubicaciones reales de almacenes y sus distancias por OSRM.
- Logística inversa (recogida de sacas/no vendido en la vuelta) como pickup-and-delivery.
- Escenarios: abrir Asturias/Santiago (`--plantas-futuras`), subida de tarifas (`multiplicador_tarifas`), precio del CO₂.
- Multi-trip explícito y turnos de conductores; más tiempo de búsqueda o warm-start con el plan de la semana anterior.
- Autenticación por tienda en la app y cierre de pedidos el viernes.
