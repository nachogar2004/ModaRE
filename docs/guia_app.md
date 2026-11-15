# ReMode Studio · guía de la aplicación de rutas

**ReMode Studio** es la aplicación web de Moda re- para planificar el transporte de ropa **planta → almacén → tienda**.
No trabaja por semanas ni con pedidos: describe la red **tal como está ahora** (tiendas, plantas, almacenes y cuánta
ropa necesita cada tienda a la semana) y, al pulsar **Calcular rutas**, el optimizador del repo (`code/modare`)
genera **un único plan de rutas** para toda la red: una semana tipo de lunes a viernes.

- [1. Puesta en marcha](#1-puesta-en-marcha)
- [2. Conceptos básicos](#2-conceptos-básicos)
- [3. La interfaz](#3-la-interfaz)
- [4. Calcular las rutas paso a paso](#4-calcular-las-rutas-paso-a-paso)
- [5. Vetos: de dónde no quiere recibir ropa cada uno](#5-vetos-de-dónde-no-quiere-recibir-ropa-cada-uno)
- [6. Cómo se conecta con el optimizador de rutas](#6-cómo-se-conecta-con-el-optimizador-de-rutas)
- [7. Datos y tablas](#7-datos-y-tablas)
- [8. Datos inventados (sintéticos)](#8-datos-inventados-sintéticos)
- [9. Problemas frecuentes](#9-problemas-frecuentes)
- [10. Uso real: base de datos y despliegue](#10-uso-real-base-de-datos-y-despliegue)

---

## 1. Puesta en marcha

Desde la carpeta `code/`:

| | Windows | macOS / Linux |
|---|---|---|
| Crear entorno | `python -m venv .venv` | `python3 -m venv .venv` |
| Instalar | `.venv\Scripts\python -m pip install -r requirements.txt` | `.venv/bin/python -m pip install -r requirements.txt` |
| Arrancar | `.venv\Scripts\python app.py` | `PORT=5050 .venv/bin/python app.py` |

Abrir `http://127.0.0.1:5000` (o el puerto indicado; en macOS el 5000 lo ocupa AirPlay) y entrar como
**`admin` / `admin`** (cámbiala en *Mi cuenta*). La aplicación solo la usa el administrador.

En desarrollo, la primera vez la app crea **`code/gestion.db`** (SQLite) con las migraciones y la rellena a partir
de los CSV de `data/`: 175 tiendas (con sus kg/semana estimados por ventas), 6 plantas (4 activas), 38 almacenes y
los datos del optimizador (parámetros, tipos de vehículo, tarifas y distancias por carretera).
A partir de ahí **la base de datos es la única fuente**: los CSV solo sirven para crearla. Para empezar de cero
(por ejemplo, si cambian los CSV): `python -m gestion.semilla --reset` (¡borra todo lo hecho en la app!).
Para uso real (PostgreSQL, servidor, copias) ver [§10](#10-uso-real-base-de-datos-y-despliegue).
Todas las variables de entorno están explicadas en [`code/.env.example`](../code/.env.example).

---

## 2. Conceptos básicos

```
 PLANTA  ──camión troncal──▶  ALMACÉN  ──camión de reparto──▶  TIENDA
            (R12, R18, tráiler)  (cross-dock,    (furgoneta, 7,5 t…)   (kg/semana)
                                 uno por provincia)
```

- Los camiones grandes no entran en las ciudades: la ropa va de la planta a un **almacén** y de ahí a las tiendas.
- Cada tienda tiene un **kg/semana**: la ropa que necesita cada semana. Es lo que se reparte. De partida es una
  estimación por sus ventas (ventas anuales ÷ 12 €/kg ÷ 50 semanas), y se cambia en su ficha.
- El plan es **una semana tipo** con **todas las tiendas activas**: las grandes reciben 2 entregas por semana y el
  resto 1, siempre dentro de su ventana de recepción, y respetando las capacidades de plantas, almacenes y
  camiones y las normas de los conductores (pausas, jornada, descanso nocturno).
- La ropa viaja en cajas de ≈ 12,5 kg sobre palés.
- **No hay asociaciones fijas**: el optimizador decide qué almacén sirve a cada tienda y qué planta repone cada
  almacén, buscando el menor coste. Solo se respetan los **vetos** (§5).

---

## 3. La interfaz

| Menú | Qué hace |
|---|---|
| **Rutas → Panel** (inicio) | Estado actual de un vistazo, con gráficas: coste semanal y en qué se va (troncal, reparto, almacenes), carga de cada planta frente a su capacidad, vehículos que usa el plan, los almacenes que más cuesta servir por kg y dónde está la demanda por provincia. Pasa el ratón por una barra para ver el detalle; cada gráfica tiene «Ver datos» con la tabla. Sin plan calculado solo muestra la red. |
| **Rutas → Calcular rutas** | Resumen de la red (tiendas, kg/semana, capacidad de las plantas), botón **Calcular rutas** y resultado del último cálculo: coste total con almacenes, transporte, almacenes, rutas, km y entregas a tiempo. |
| **Rutas → Mapa y analítica** | El plan sobre el mapa, la lista de rutas con sus paradas y horarios, y la descarga de paradas en CSV. |
| **Red → Tiendas, Plantas, Almacenes** | Alta, edición y baja. En tiendas: kg/semana, ubicación, ventana de recepción, vehículo máximo, vetos. En plantas: capacidad de salida y horario. En almacenes: capacidad, horario, costes y vetos. |
| **Optimizador → Parámetros, Tipos de vehículo, Tarifas** | Los supuestos con los que calcula el optimizador (kg por palé, jornada, frecuencias, capacidades y emisiones de cada vehículo, €/km, €/h, €/día…). Se editan, no se añaden ni se borran. |
| **Sistema → Usuarios** | Cuentas de administrador. |

**Eliminar**: una tienda, planta o almacén se borra de verdad salvo que tenga historial operativo antiguo
(pedidos, movimientos); en ese caso se **da de baja** (desactivada), que para las rutas es lo mismo: no entra en el
cálculo. Los dados de baja se ven con *Mostrar dados de baja* y se pueden reactivar.

---

## 4. Calcular las rutas paso a paso

1. **Ajusta la red**: cambia los kg/semana de las tiendas, da de alta o de baja tiendas, plantas o almacenes, o
   cambia parámetros y tarifas. Cada cambio queda guardado en la base de datos.
2. Si has **añadido o movido** una tienda, planta o almacén, en *Calcular rutas* aparece un aviso con el botón
   **Calcular distancias (OSRM)**: calcula solo las distancias por carretera de esos puntos (unos segundos por punto).
   Si no lo haces, el optimizador usa para ellos la línea recta × 1,26.
3. Pulsa **Calcular rutas**. Tarda 1–2 minutos; la página se actualiza sola al terminar.
4. Revisa el resultado y abre **Mapa y analítica** para ver cada ruta.
5. Si después cambias algo, la página avisa de que **el plan ya no refleja la red actual**: vuelve a pulsar
   *Calcular rutas*. Cada cálculo sustituye al anterior (solo hay un plan).

Si las tiendas piden más ropa de la que pueden enviar las plantas activas, la página lo avisa antes de calcular:
sube la capacidad de alguna planta o activa otra.

---

## 5. Vetos: de dónde no quiere recibir ropa cada uno

Por defecto **cualquier planta puede reponer cualquier almacén y cualquier almacén puede servir a cualquier tienda**.
Lo único que se configura son **vetos**, en la ficha de cada uno:

| Ficha | Qué veta |
|---|---|
| Tienda → *Almacenes vetados* | almacenes de los que no quiere recibir ropa |
| Almacén → *Plantas vetadas* | plantas de las que no quiere recibir ropa |

- Si una tienda veta los almacenes más cercanos, la sirve uno más lejano (más caro).
- Un almacén que veta **todas** las plantas activas no recibe mercancía: el optimizador no lo usa.
- Una tienda que veta **todos** los almacenes que pueden recibir mercancía no se puede servir: sale en los avisos
  del plan.

---

## 6. Cómo se conecta con el optimizador de rutas

El optimizador es el paquete `code/modare`. La app lo lanza **solo desde `code/gestion/optimizador.py`**
(`calcular_rutas()`), y el optimizador lee **todos sus datos de la base de datos** en el momento de empezar
(`modare/datos.py:cargar_datos`): lo que se cambia en la app entra en el siguiente cálculo, sin exportar nada.

| Dirección | Qué | Dónde |
|---|---|---|
| App → librería | tiendas (con sus kg/semana), plantas, almacenes y vetos | sus tablas (`tiendas`, `plantas`, `almacenes`, `*_vetad*`) |
| App → librería | parámetros, tipos de vehículo, tarifas | tablas `parametros`, `tipos_vehiculo`, `tarifas` |
| App → librería | distancias por carretera | tabla `distancias` |
| Librería → app | el plan: rutas, paradas, almacén y planta asignados, costes y KPIs | tabla `planes`, clave `ACTUAL` (y copia en `code/salidas/ACTUAL/`) |
| Ambas | estado del cálculo (calculando / ok / error) | tabla `trabajos_optimizacion` |

- El tiempo de búsqueda es fijo (30 s por planta) y el CO₂ no se penaliza: el plan minimiza el coste.
- Las tiendas sin acceso por carretera (islas) quedan fuera de las rutas y salen en los avisos.
- Desde la consola se obtiene el mismo plan con `python optimizar.py` (y escenarios con `--muestra`,
  `--asignacion actual`, `--plantas-futuras`, `--almacenes optimo`…).

---

## 7. Datos y tablas

**Una sola base de datos** para la app y la librería de rutas (variable `MODARE_DB`; en desarrollo `code/gestion.db`,
que no se sube a git). Modelos en `code/gestion/modelos.py` y `code/modare/bd.py`; el esquema lo crean y actualizan
las migraciones de `code/migrations/`.

| Tabla | Contenido |
|---|---|
| `tiendas` | ubicación, tamaño, **kg/semana**, ventas anuales (informativo), ventana de recepción, vehículo máximo, contacto, activa |
| `plantas` | ubicación, capacidad de salida kg/semana, horario de carga, activa |
| `almacenes` | ubicación, capacidad kg/semana, horario, tiempo de descarga, coste fijo y de manipulación, activo |
| `almacen_plantas_vetadas`, `tienda_almacenes_vetados` | vetos |
| `parametros`, `tipos_vehiculo`, `tarifas`, `distancias` | datos del optimizador |
| `planes`, `trabajos_optimizacion` | el plan y el estado del cálculo |
| `usuarios`, `ajustes` | cuentas y estado interno (p. ej. cuándo cambió la red por última vez) |
| `alembic_version` | versión del esquema (migraciones) |

Quedan en la base, **sin uso en la interfaz**, las tablas de la antigua parte operativa (pedidos, stock, ventas,
categorías de prenda, camiones, viajes, reposiciones, movimientos) y su código (`gestion/logistica.py`,
`gestion/vistas_red.py`), por si se recupera.

---

## 8. Datos inventados (sintéticos)

La carga inicial **inventa** lo que el reto no da; todo se puede cambiar en la app:

- los **kg/semana** de cada tienda (estimados por sus ventas, también sintéticas);
- los **almacenes**: uno por provincia, en su polígono industrial principal, con capacidad y costes estimados;
- muchos **parámetros** (frecuencias, kg por palé, ventanas por defecto…): ver su etiqueta en *Parámetros*.

---

## 9. Problemas frecuentes

| Problema | Solución |
|---|---|
| «Formulario caducado» / error 400 | La sesión ha cambiado (p. ej. se reinició el servidor). Recarga la página. |
| «Asignación infactible» | La demanda supera la capacidad de los almacenes o de las plantas que no están vetadas. Revisa capacidades y vetos. |
| Las rutas del mapa salen en línea recta | No se pudo pedir el trazado a OSRM (sin red o certificados). En macOS se resuelve con `certifi`, ya incluido en `requirements.txt`. Vuelve a calcular. |
| «La optimización se interrumpió» | Se reinició el servidor mientras calculaba. Vuelve a pulsar *Calcular rutas*. |
| Una tienda no aparece en las rutas | Revisa que esté activa, con kg/semana > 0 y con acceso por carretera, y que no haya vetado todos los almacenes. |
| Puerto 5000 ocupado (macOS) | `PORT=5050 .venv/bin/python app.py` |

### Límites conocidos

- No hay límite de intentos de inicio de sesión: conviene ponerlo en el proxy (nginx, Cloudflare…).
- El cálculo corre en un hilo del proceso web: si el servidor se reinicia mientras calcula, queda marcado como
  interrumpido y hay que relanzarlo.
- `code/salidas/` se escribe en el disco del servidor; la app no depende de ello (el plan está en la base).

---

## 10. Uso real: base de datos y despliegue

### Dónde están los datos

| Qué | Dónde |
|---|---|
| Todo lo de la app y del optimizador (red, kg/semana, usuarios, plan de rutas, estado del cálculo) | **la base de datos** de `MODARE_DB` |
| Red, vetos y datos del modelo (parámetros, vehículos, tarifas, distancias) | **la base de datos** (los edita la app) |
| Semilla con la que se crea una base nueva | `data/*.csv` en git (solo se leen al crear la base) |
| Copias legibles de cada plan (mapa, CSV) | `code/salidas/<semana>/` (prescindibles) |

La fuente de verdad es la base de datos. Nunca se sube a git.

### Base de datos recomendada: PostgreSQL

Un PostgreSQL gestionado **alojado en la UE** (hay datos personales: nombres y emails), con copias de seguridad
automáticas: Supabase, Neon, Azure Database for PostgreSQL, AWS RDS… Basta con crear una base vacía y poner su
cadena de conexión en `MODARE_DB` (`postgresql://usuario:clave@host:5432/base`; también vale `postgres://`).
La app crea las tablas sola en el primer arranque.

### Variables de entorno (ver `code/.env.example`)

| Variable | Producción | Para qué |
|---|---|---|
| `MODARE_ENV=produccion` | sí | activa las comprobaciones y las cookies seguras (solo HTTPS) |
| `MODARE_DB` | sí | cadena de conexión de la base de datos |
| `MODARE_SECRET_KEY` | sí, ≥ 32 caracteres | firma las sesiones. `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `MODARE_PASSWORD_ADMIN`, `MODARE_PASSWORD_INICIAL` | sí, solo en la primera carga | contraseña del admin y contraseña inicial del resto de usuarios |
| `MODARE_AUTO_MIGRAR` | `0` si hay varios procesos | `1` = migra al arrancar |
| `MODARE_PROXY` | `1` (defecto) | la app está detrás de un proxy HTTPS |

Sin `MODARE_SECRET_KEY` o sin `MODARE_DB`, en producción la app **se niega a arrancar**.

### Arrancar en un servidor

```bash
cd code
pip install -r requirements.txt
export MODARE_ENV=produccion MODARE_DB=postgresql://… MODARE_SECRET_KEY=… MODARE_AUTO_MIGRAR=0
flask --app app db upgrade                      # aplica las migraciones (en cada despliegue)
gunicorn -w 2 -b 0.0.0.0:8000 --timeout 120 app:app     # Windows: waitress-serve --port 8000 app:app
```

Delante, un proxy con HTTPS (nginx, Caddy o el que dé el proveedor). La optimización tarda 1–2 min y corre en
segundo plano, así que el `--timeout` solo afecta a las peticiones normales.

### Cambiar el esquema (añadir un campo, una tabla…)

1. Cambia el modelo en `gestion/modelos.py` (o `modare/bd.py`).
2. `flask --app app db migrate -m "qué cambia"` → genera un fichero en `code/migrations/versions/`. **Revísalo**.
3. `flask --app app db upgrade` en tu base; súbelo a git; en el servidor se aplica al desplegar.

Nunca se cambia el esquema a mano en producción. En SQLite las migraciones desactivan las claves foráneas mientras
recrean tablas (si no, se borrarían en cascada filas relacionadas).

### Copias de seguridad

- PostgreSQL gestionado: activa las copias automáticas y la restauración a un punto en el tiempo.
- Copia manual: `pg_dump "$MODARE_DB" > copia_$(date +%F).sql` · SQLite: copia el fichero `gestion.db` con la app parada.

### Bases creadas antes de las migraciones

Una `gestion.db` antigua se adopta sola en el primer arranque: se completa, se quita la columna del antiguo
«almacén habitual», se marca con la versión inicial y a partir de ahí mandan las migraciones. Si existe el
antiguo `code/pedidos.db`, sus pedidos se copian a la base común y el fichero queda como `pedidos.db.migrado`.
