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

Flujo que implementa el prototipo (sin semanas ni pedidos):
**el admin describe la red actual en la app (tiendas con sus kg/semana, plantas, almacenes, parámetros) → pulsa
«Calcular rutas» → sale un único plan (semana tipo) con todas las tiendas activas.**

## Datos (`data/` = semilla; la fuente de verdad es la base de datos)

**El optimizador lee TODO de la base de datos** (`MODARE_DB`, la misma que la app; `modare/datos.py:cargar_datos`):
red, vetos, parámetros, tipos de vehículo, tarifas y distancias. Los CSV de `data/` son solo la **semilla**: se
cargan al crear la base (`gestion/semilla.py`) o para rellenar una tabla vacía; después mandan la base y la app
(Admin → Tiendas/Plantas/Almacenes, y Admin → Optimizador: Parámetros, Tipos de vehículo, Tarifas). Editar un CSV
no cambia nada en una base ya creada: hay que rehacerla con `python -m gestion.semilla --reset` (borra lo hecho en
la app). `gestion.db` no va a git (cada uno tiene la suya; para compartir estado, una base PostgreSQL común).

Todos los CSV llevan columnas `etiqueta` (REAL / ESTIMADO / SINTÉTICO) y `fuente` (en la base se conservan en
parámetros, vehículos y tarifas). Respetadlas al presentar.

| Fichero | Qué tiene | Notas |
|---|---|---|
| `plantas.csv` | 6 plantas (4 activas: MAD, VAL, BCN, BIO; AST y SCQ futuras) | `capacidad_salida_kg_semana`, horario de carga 06–18 h |
| `almacenes.csv` | 38 almacenes, uno por provincia peninsular con tiendas | **todo SINTÉTICO**: ubicación = **polígono industrial principal** de la provincia (elegido por el equipo en `code/modare/poligonos.py`, coordenadas de OpenStreetMap/Nominatim cacheadas en `poligonos_geocodificados.json`; Zamora fijado a mano, Palencia en el polígono San Blas de Villalobón, junto a la capital), capacidad = 1,5 × demanda media (mín. 1.500 kg/semana), horario 06–20 h L-V, **costes**: `coste_fijo_eur_semana` (superficie según capacidad × alquiler €/m² de la provincia + gastos + gestión: 231–578 €/semana) y `coste_manipulacion_eur_kg` (€/h operario ÷ kg/h: 0,055–0,066 €/kg). Se genera con `code/generar_almacenes.py` (herramienta de la semilla). En la base: Admin → Almacenes |
| `tiendas.csv` | 175 tiendas peninsulares (las 7 de Baleares se quitaron del CSV del reto; en la base de la app están con `acceso_carretera=0` y el optimizador las excluye) | ventas €/año, tamaño, ventana de recepción, `vehiculo_max`, `en_muestra` (53 para pruebas) |
| `vehiculos.csv` | 5 tipos: FUR 3,5 t, R75, R12, R18, ART 40 t | carga útil kg, volumen m³, palés |
| `emisiones.csv` | kgCO₂e/km vacío y lleno por tipo (DEFRA 2025) | de aquí sale el **consumo propio** de cada camión |
| `tarifas.csv` | Operador A (€/km + €/h, ilimitado) y B (€/día + €/km, unidades limitadas) | |
| `distancias.csv` | matriz completa **por carretera (OSRM)** entre plantas, almacenes y tiendas: km, min de coche y min de camión (×`factor_tiempo_camion`) | 219 puntos, 47.742 pares, en la tabla `distancias`. Al mover un punto en la app se borran sus pares; los que faltan (puntos nuevos o movidos) se calculan con el botón de Admin → Planificación o `calcular_distancias.py` (segundos; `--todos` rehace todo en ~30 s, `--csv` actualiza la semilla). Si a un par le falta dato se usa línea recta × `factor_rodeo` |
| `geometrias_osrm.json` | caché de trazados por carretera de cada tramo (polyline) para el mapa | se rellena sola al guardar un plan |
| `parametros.csv` | todos los supuestos de negocio (kg/palé, frecuencias, jornada, días máx. de ruta, `tipos_troncal`…) | semilla de la tabla `parametros`: **cambiad los supuestos en Admin → Parámetros**, no en el código |

Demanda: cada tienda tiene en la base un **`kg_semana` editable** (lo que reparte el optimizador, fuente `red`); se
inicializa con `kg_semana = ventas / precio_medio_eur_kg / semanas_venta_anio`
(~34 t/semana en total frente a 46 t de capacidad de las 4 plantas activas).

## Modelo de optimización

Descomposición *cluster-first, route-second* en dos escalones, todo con OR-Tools:

### Etapa 1 · Qué almacenes abrir y asignación tienda → almacén → planta (`code/modare/asignacion.py`, CP-SAT)
```
min  Σ fijo_a·z[a] + Σ manip_a·kg_s·x[s,a] + Σ R(s,a)·x[s,a] + Σ T(p,a)·f[a,p]
s.a. Σ_a x[s,a] = 1                        cada tienda la sirve un almacén abierto (x ≤ z) a ≤ 3 h (`max_min_almacen_tienda`)
     z[a] = 1 ∀a  y  Σ_s x[s,a] ≥ 1         (por defecto) los 38 abiertos y cada uno sirve ≥1 tienda si su provincia tiene demanda
     Σ_s kg_s · x[s,a] ≤ capacidad_a        capacidad semanal del almacén
     Σ_p f[a,p] = Σ_s kg_s · x[s,a]         conservación del flujo (f[a,p] = kg que la planta p envía al almacén a)
     f[a,p] solo existe si el almacén no ha vetado la planta (tabla almacen_plantas_vetadas)
     x[s,a] = 0 si la tienda veta el almacén (tabla tienda_almacenes_vetados); tiendas que vetan todos -> aviso, sin servir
     Σ_a f[a,p] ≤ capacidad_p               capacidad de salida semanal de la planta (solo activas)
```
**Cualquier almacén puede recibir de cualquier planta, y de varias a la vez** (no hay abastecimiento único): el
modelo solo divide si le sale más barato, p.ej. si la planta más cercana se queda sin capacidad. Con las capacidades
actuales no divide ninguno; con plantas al 75 % divide Cádiz (MAD + 1 kg de VAL) y Zaragoza (BCN + 83 kg de BIO).
Cada pareja (almacén, planta) con flujo es una reposición troncal propia (`AL-ZARAGOZA<BIO#1`). En la interfaz cada
almacén tiene una `planta` principal (la que más kg envía, para filtros y colores) y el reparto completo en `plantas`.
**Requisito: exactamente un almacén por provincia peninsular con tiendas (38), todos abiertos** (`--almacenes todos`,
por defecto): se paga el fijo de los 38; una tienda puede ir al almacén de otra provincia si sale más barato, pero cada
almacén reparte al menos a una tienda cuando su provincia tiene demanda esa semana (si sus tiendas pequeñas no reciben
esa semana, queda abierto sin tiendas, p.ej. Cantabria y Palencia en W47). **Escenario `--almacenes optimo`**: el modelo
decide cuáles abrir (en W47 abre 25 y cierra 13). Para comparar transporte con almacén, el transporte se estima en
€ calibrados con las rutas reales: `R(s,a)` = `factor_coste_reparto` (0,95) × entregas × ida y vuelta en 7,5 t;
`T(p,a)` = `factor_coste_troncal` (3,2) × ida y vuelta en 12 t ÷ carga, por kg. (La estimación kg×km anterior
daba ~4 % del coste real: con costes de almacén habría cerrado casi todos.) Recalibrar si cambian mucho las rutas.
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
  - jornada ≤ 13 h y conducción ≤ 9 h por día × días de ruta: troncal hasta `dias_max_ruta` (3, con pernocta);
    **reparto de 1 día** (`dias_max_ruta_reparto`): el conductor vuelve cada día al almacén
  - **las entregas de una misma tienda van en rutas distintas** (si no, el solver juntaba la de L-M y la de X-V
    en una ruta y el conductor dormía junto a la tienda)
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
  app.py              Flask: monta la app de gestión (gestion/) y /central (optimizador avanzado + mapa, solo admin)
  gestion/            app de gestión del transporte con usuarios y roles (ver «App de gestión» más abajo)
  gestion/basedatos.py  prepara la base al arrancar: migraciones, carga inicial, adopción de bases antiguas
  migrations/         migraciones Alembic (Flask-Migrate) de TODAS las tablas (app + librería)
  .env.example        variables de entorno (producción: MODARE_ENV, MODARE_DB, MODARE_SECRET_KEY…)
  optimizar.py        CLI (usa la base; si está vacía la crea desde data/*.csv)
  generar_almacenes.py  crea data/almacenes.csv (semilla, sintético) a partir de tiendas.csv
  modare/config.py    rutas y supuestos que NO están en la tabla parametros (kg/caja, pausas, CO2/L…)
  modare/datos.py     carga desde la base (foto de una optimización), demanda estimada, pedidos → visitas
  modare/asignacion.py, vrp.py, simulacion.py   (ver arriba)
  modare/bd.py        base de datos común (MODARE_DB): lee las tablas de la app; escribe pedidos_optimizador, planes, trabajos_optimizacion
  modare/pedidos.py   pedido semanal por (semana ISO, tienda) -> tabla pedidos_optimizador
  modare/planificador.py  orquesta todo y escribe salidas/<semana>/
  modare/visualizar.py    genera salidas/<semana>/mapa.html
  modare/osrm.py      cliente OSRM: matriz de distancias y trazados de rutas (1 petición/s, con caché)
  modare/distancias.py    distancias por carretera en la tabla distancias: qué puntos faltan, cálculo parcial o total
  calcular_distancias.py  CLI de lo anterior (por defecto solo los puntos nuevos o movidos)
  templates/          HTML de la app (Leaflet para el mapa); templates/gestion/ = pantallas de la app de gestión
  static/             remode.css (diseño vaquero del logo: tokens claro/oscuro, costuras, componentes) y remode.js (pestañas, filtros, totales en vivo, avisos)
  static/marca/       logo RMS sin fondo (webp) e icono camiseta-R (png). Original y script que los genera en docs/marca/
```

### Salidas (`code/salidas/<semana>/`, se sobrescriben en cada ejecución de esa semana)
- `mapa.html`: grafo sobre el mapa con el **trazado real por carretera** (troncal = línea gruesa del color de la planta, reparto = línea fina),
  lista de rutas filtrable por escalón y planta, y cronograma semanal (tramos de trabajo, descansos nocturnos rayados
  y una marca por entrega; los descansos salen de la simulación, campo `descansos` de cada ruta). Se abre con doble clic (fondo Esri, sin API key).
  Pestañas de analítica (calculadas en el navegador desde el plan): **Coste por tienda** (troncal + reparto imputados,
  €/kg, CSV), **Rutas** (coste y desglose km/horas/fijo/pernoctas), **Almacenes** (€/kg troncal y reparto) y **Resumen**
  (por componente, vehículo, operador, planta y flota). Imputación: el coste de cada ruta se reparte entre sus paradas
  por kg (o por kg × distancia en línea recta al depósito, seleccionable); cada almacén tiene un €/kg troncal
  (coste troncal recibido ÷ kg recibidos) que pagan sus tiendas por sus kg. Es una convención, no una factura.
- `rutas.csv`: una fila por ruta. Nombres `T-MAD-01` (troncal desde la planta de Madrid), `R-SEVILLA-02`
  (reparto desde el almacén de Sevilla); el id interno del solver va en `id_solver`.
- `paradas.csv`: una fila por parada. `almacenes.csv`: uso de cada almacén. `plan.json`: todo lo anterior + KPIs.
- Regenerar el mapa sin optimizar: `.venv\Scripts\python -m modare.visualizar 2026-W47`. También reintenta los tramos que
  no se pudieron dibujar. Los trazados vienen de la caché (`data/geometrias_osrm.json`, en git) o del servidor público
  de OSRM; si no hay conexión se dibujan en línea recta (**el plan y los costes no cambian**: usan la tabla `distancias`).
  Como cada ordenador encuentra rutas algo distintas, los tramos nuevos se piden a OSRM. Si falla por certificados
  (`CERTIFICATE_VERIFY_FAILED`, típico del Python de python.org en Mac): instalar `requirements.txt` (incluye certifi)
  o ejecutar `Install Certificates.command`; en redes con inspección HTTPS, probar otra red o sin VPN.

### Cómo ejecutar (Windows, desde `code/`)
```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python generar_almacenes.py                                         # solo para rehacer la semilla data/almacenes.csv
.venv\Scripts\python calcular_distancias.py                                       # distancias de puntos nuevos/movidos (o botón en la app)
.venv\Scripts\python optimizar.py --muestra --segundos 10                      # prueba rápida (~25 s)
.venv\Scripts\python optimizar.py                                                 # plan de la red actual (= botón de la app, ~75 s)
.venv\Scripts\python app.py                                                      # http://127.0.0.1:5000
.venv\Scripts\python -m gestion.semilla --reset                                 # rehace gestion.db desde data/*.csv
```
En macOS: `.venv/bin/python …` y `PORT=5050 .venv/bin/python app.py` (AirPlay ocupa el 5000).
Opciones CLI: `--fuente mixto|pedidos|estimacion`, `--asignacion optimo|actual`, `--plantas-futuras`,
`--precio-co2 80` (€/t en el objetivo), `--secuencial` (sin portafolio, para depurar).
En consola de Windows usad `PYTHONIOENCODING=utf8` si salen caracteres raros.

### Resultados de referencia (semana 2026-W47, 30 s/planta, 20 CPU)
Dos escalones (modelo actual):

| Escenario (W47, con coste de almacenes) | Almacenes | Rutas | Camiones | km | Transporte | Almacenes | **Total** | €/kg |
|---|---|---|---|---|---|---|---|---|
| **Requisito: 38 abiertos** (`--almacenes todos`) | 38 (36 con tiendas) | 67 (10 + 57) | 50 | 11.938 | 15.541 € | 13.088 € | **28.630 €** | 0,90 |
| Escenario: el modelo elige (`--almacenes optimo`) | 25 | 60 (10 + 50) | 41 | 12.279 | 14.974 € | 9.543 € | 24.517 € | 0,77 |

Exigir los 38 almacenes cuesta ≈ 4.100 €/semana más que dejar que el modelo cierre 13 (sobre todo por sus costes fijos).

Histórico (solo transporte, antes de costear almacenes):

| Escenario | Rutas (troncal + reparto) | Camiones | km | Coste (troncal + reparto) | CO₂ | Servicio |
|---|---|---|---|---|---|---|
| **Actual**: polígonos, carretera, reparto de 1 día y entregas de una tienda en rutas distintas | 67 (10 + 57) | 52 | 12.616 | 15.648 € (7.702 + 7.946) | 6,0 t | 100 % |
| Almacenes en polígonos, sin esas dos restricciones | 69 (10 + 59) | 52 | 12.401 | 16.037 € (7.702 + 8.335) | 5,7 t | 100 % |
| Ídem con almacenes en el centroide de la provincia | 64 (10 + 54) | 52 | 11.372 | 16.425 € (8.221 + 8.204) | 5,5 t | 100 % |
| Ídem con distancias en línea recta ×1,26 (antes de OSRM) | 69 (10 + 59) | 50 | 11.406 | 14.577 € (7.453 + 7.124) | 5,4 t | 100 % |
| Asignación actual, semana W48, línea recta ×1,26 | 70 (11 + 59) | 51 | 12.100 | 15.780 € (8.720 + 7.060) | 6,0 t | 100 % (troncal 97 %) |

El troncal sale casi todo en rígidos de 12 t (un tráiler para Barcelona) y el reparto en furgonetas; 0,52 €/kg.
Con distancias reales los km apenas cambian, pero los tiempos son mayores (sobre todo en tramos urbanos),
lo que encarece el coste por hora del operador A un ~13 %.
La primera ejecución tarda ~1 min más porque pide a OSRM el trazado de cada ruta (luego queda en caché).
**Ojo al comparar con el modelo directo antiguo** (planta → tienda: 17.025 €, 19 camiones): se calculó con distancias
en línea recta y sin almacenes; para una comparación justa habría que recalcularlo con los datos actuales. Los camiones
físicos del reparto se cuentan por almacén (cada almacén necesita al menos uno).

## App «ReMode Studio» (`code/gestion/`, BD única en `MODARE_DB`; en desarrollo `code/gestion.db`)

Aplicación web **solo para el administrador** (las cuentas de tienda/planta/almacén no pueden entrar) sobre la misma
app Flask. **No trabaja por semanas**: edita la red y pulsa **Calcular rutas** (página de inicio, `admin.panel`) →
`gestion/optimizador.py:calcular_rutas()` → `planificar(C.PLAN_ACTUAL, fuente="red")` en un hilo → plan único guardado
en `planes` con la clave **`ACTUAL`** (cada cálculo sustituye al anterior; 30 s/planta y CO₂ sin precio, fijos en
`config.py`). La página avisa si la red cambió después de que empezara el último cálculo (`ajustes.red_cambiada` frente a
`resumen.inicio_calculo`) y si hay puntos nuevos/movidos sin distancias por carretera. Mapa y analítica en `/central/plan`.

Menú: **Rutas** (Panel = inicio con gráficas del estado actual, `admin.resumen` + `templates/gestion/admin_resumen.html`,
en HTML/CSS sin librerías y con la paleta categórica de referencia; calcular; mapa) · **Red** (tiendas con `kg_semana`, plantas, almacenes con costes; vetos en sus fichas) ·
**Optimizador** (parámetros, tipos de vehículo, tarifas: solo edición) · **Usuarios**. La BD se crea y se rellena sola
desde `data/*.csv` la primera vez que se arranca `app.py` (`gestion/semilla.py`); desde entonces es la única fuente.

**Datos**: una sola base de datos (SQLAlchemy; SQLite en desarrollo, PostgreSQL en producción) para la app y la
librería. Esquema con migraciones (`flask --app app db migrate/upgrade`); no tocar el esquema a mano. Despliegue y
variables: §10 de la guía. Guía de uso: [docs/guia_app.md](docs/guia_app.md).
No hay asociaciones fijas tienda→almacén ni almacén→planta; solo **vetos** (`almacen_plantas_vetadas`,
`tienda_almacenes_vetados`), que el optimizador respeta.

**Tablas en uso**: `tiendas` (con `kg_semana`), `plantas`, `almacenes`, vetos, `parametros`, `tipos_vehiculo`,
`tarifas`, `distancias`, `planes`, `trabajos_optimizacion`, `usuarios`, `ajustes`. La librería las lee por nombre en
`modare/datos.py`: si se renombra una columna, adaptarla allí. Borrar una tienda la elimina (sus ventas sintéticas
de la semilla se van con ella); si tiene historial operativo se da de baja.

**Parte operativa antigua, fuera de la interfaz** (código y tablas conservados por si se recupera): ciclo semanal
de pedidos automáticos/adicionales (`gestion/logistica.py`), stock en prendas y cajas, ventas, categorías de prenda,
flota de camiones, viajes, reposiciones y movimientos; paneles de tienda/planta/almacén (`gestion/vistas_red.py`),
`/admin/planificacion` y `/central` (siguen accesibles por URL para el admin, sin enlaces). La CLI conserva las
fuentes por semanas (`--fuente pedidos|estimacion|mixto --semana …`).

**Ubicación en el mapa** (altas/ediciones de tiendas, plantas y almacenes en `admin_form.html`; mapa plegable con «Nueva
aquí» en `admin_lista.html`): Leaflet + `static/mapa_ubicacion.js`; `gestion/vistas_admin.py` expone
`/admin/geo/{instalaciones,inversa,buscar}` (proxy de Nominatim, 1 req/s, caché) y valida que el punto esté en la España
peninsular (`gestion/peninsula.py`, polígono simplificado); las **tiendas** también pueden estar en Baleares (`zona()`): se
fuerzan a `acceso_carretera=0` y el optimizador las excluye. Al guardar se normaliza la provincia a la grafía de la base
(`vistas_admin.normalizar`) y se comprueban rangos. Mover/añadir un punto: «Calcular distancias» (ver tabla `distancias`).

**Importar tiendas desde CSV** (Admin → Tiendas → Importar CSV, `gestion/importar.py` + `admin_importar.html`): mismo
formato que `data/tiendas.csv` (más `kg_semana` opcional; si falta se estima por ventas como en la semilla), vista previa
sin escribir (nueva / ya existe / repetida / error, con la validación del formulario), confirmación revalidada en servidor
y en una transacción (aborta si alguna fila elegida cambió de estado/coincidencias desde la vista previa: evita duplicados
por reenvío o por dos admins); crea cada tienda con `semilla.completar_tienda`. Sin usuario de acceso (la app es solo admin).

## Supuestos y limitaciones (decidlos/validadlos con la ONG)
- **Almacenes inventados** (qué polígono, capacidad, horario): ver `almacenes.csv`, `modare/poligonos.py` y `generar_almacenes.py`.
  Para cambiar un almacén: editarlo en la app → «Calcular distancias» en Planificación → optimizar.
- **Costes de almacén inventados** (alquiler por provincia sin fuente verificada, gestión 100 €/semana, 21 €/h
  operario, 350 kg/h). Un almacén cerrado cuesta 0 (se supone que se puede dejar de alquilar).
- La capacidad de cada almacén es fija (1,5 × demanda de su provincia): al cerrar almacenes, los vecinos solo absorben
  hasta su capacidad. Una variante sería elegir también el tamaño (más m² → más capacidad y más coste fijo).
- Si una ruta llega tarde con las reglas exactas, se prueba a adelantar la salida (`simular_con_reajuste`).
- Sincronización troncal/reparto por stock (la reposición de W sirve el reparto de W+1).
- 1 caja = 12,5 kg (20 cajas/palé) — `config.KG_POR_CAJA`, SINTÉTICO.
- `unidades_disponibles` del operador B se interpreta **por depósito (planta o almacén) y día**.
- **Sin islas en las rutas**: las 7 tiendas de Baleares se gestionan en la app con `acceso_carretera=0` (reparto marítimo, por otra vía) y el
  optimizador las excluye (`modare/datos.py`); no hay plantas ni almacenes en islas (el servidor los rechaza).
- El usuario mencionó 192 tiendas; `tiendas.csv` tenía 182 (175 tras quitar Baleares).
- La tarifa €/km ya incluye combustible; los litros y € de gasóleo se reportan solo como información.
- Las rutas no se encadenan explícitamente por camión (multi-trip): la flota física se estima por solapamiento.
- Vuelta al depósito en vacío: la logística inversa (`kg_retorno_por_kg_entregado`) no está modelada aún.
- OSRM público usa el perfil de **coche**; el tiempo de camión = tiempo de coche × `factor_tiempo_camion` (1,2). No tiene en cuenta restricciones de camión (gálibo, peso, ZBE).

## Siguientes pasos posibles
- Validar con la ONG los costes de almacén (o usar locales cedidos por entidades socias, mucho más baratos).
- Elegir también el tamaño de cada almacén; recalcular el reparto directo con los datos actuales para comparar.
- Ubicaciones reales de almacenes y sus distancias por OSRM.
- Logística inversa (recogida de sacas/no vendido en la vuelta) como pickup-and-delivery.
- Escenarios: abrir Asturias/Santiago (`--plantas-futuras`), subida de tarifas (`multiplicador_tarifas`), precio del CO₂.
- Multi-trip explícito y turnos de conductores; más tiempo de búsqueda o warm-start con el plan de la semana anterior.
- Autenticación por tienda en la app y cierre de pedidos el viernes.
