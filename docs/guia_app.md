# ReMode Studio · guía de la aplicación de gestión del transporte

**ReMode Studio** es la aplicación web de Moda re- para gestionar el transporte de ropa **planta → almacén → tienda**: el stock de cada punto,
los pedidos de las tiendas (automáticos y adicionales), los camiones y la planificación semanal de rutas
con el optimizador del repo (`code/modare`).

- [1. Puesta en marcha](#1-puesta-en-marcha)
- [2. Conceptos básicos](#2-conceptos-básicos)
- [3. Usuarios y lo que puede hacer cada uno](#3-usuarios-y-lo-que-puede-hacer-cada-uno)
- [4. La semana paso a paso](#4-la-semana-paso-a-paso)
- [5. Pedidos automáticos](#5-pedidos-automáticos)
- [6. Vetos: de dónde no quiero recibir ropa](#6-vetos-de-dónde-no-quiero-recibir-ropa)
- [7. Alertas](#7-alertas)
- [8. Cómo se conecta con el optimizador de rutas](#8-cómo-se-conecta-con-el-optimizador-de-rutas)
- [9. Datos y tablas](#9-datos-y-tablas)
- [10. Datos inventados (sintéticos)](#10-datos-inventados-sintéticos)
- [11. Problemas frecuentes](#11-problemas-frecuentes)
- [12. Uso real: base de datos y despliegue](#12-uso-real-base-de-datos-y-despliegue)

---

## 1. Puesta en marcha

Desde la carpeta `code/`:

| | Windows | macOS / Linux |
|---|---|---|
| Crear entorno | `python -m venv .venv` | `python3 -m venv .venv` |
| Instalar | `.venv\Scripts\python -m pip install -r requirements.txt` | `.venv/bin/python -m pip install -r requirements.txt` |
| Arrancar | `.venv\Scripts\python app.py` | `PORT=5050 .venv/bin/python app.py` |

Abrir `http://127.0.0.1:5000` (o el puerto indicado; en macOS el 5000 lo ocupa AirPlay).

En desarrollo, la primera vez la app crea **`code/gestion.db`** (SQLite) con las migraciones y la rellena a partir
de los CSV de `data/`: 175 tiendas, 6 plantas (4 activas), 38 almacenes, una flota inicial, un usuario por cada
tienda, planta y almacén, y los datos del optimizador (parámetros, tipos de vehículo, tarifas y distancias).
A partir de ahí **la base de datos es la única fuente**: los CSV solo sirven para crearla. Para empezar de cero
(por ejemplo, si cambian los CSV): `python -m gestion.semilla --reset` (¡borra todo lo hecho en la app!).
Para uso real (PostgreSQL, servidor, copias) ver [§12](#12-uso-real-base-de-datos-y-despliegue).

**Accesos iniciales** (cámbialos en *Mi cuenta*):

| Usuario | Contraseña |
|---|---|
| `admin` | `admin` |
| tiendas `td-001` … `td-175`, plantas `pl-mad`, `pl-val`…, almacenes `al-madrid`, `al-sevilla`… | `modare2026` |

Todas las variables de entorno están explicadas en [`code/.env.example`](../code/.env.example).

---

## 2. Conceptos básicos

```
 PLANTA  ──camión troncal──▶  ALMACÉN  ──camión de reparto──▶  TIENDA
 (prendas)   (R12, R18, tráiler)  (cajas mixtas)   (furgoneta, 7,5 t…)    (prendas)
```

- **Categorías de prenda**: segmento (Mujer, Hombre, Niños) × tipo (partes de arriba, partes de abajo,
  abrigos, zapatos, bolsos) = 15 categorías. El admin puede añadir más.
- **Tiendas y plantas** guardan su stock en **prendas por categoría**.
- **Almacenes** son puntos de paso (*cross-dock*): guardan **cajas mixtas** y tienen un **stock máximo**.
- **Cajas**: la ropa viaja en cajas mixtas de ≈ 12,5 kg. Cada categoría tiene un peso medio por prenda,
  así que `cajas = ⌈ Σ (prendas × kg por prenda) / 12,5 ⌉`.
- **Semana de entrega**: semana ISO (`2026-W48`). Los pedidos se cierran el **viernes** anterior.
- Hay **dos flotas distintas**: camiones troncales (con base en una planta) y camiones de reparto
  (con base en un almacén). Los camiones grandes no entran en las ciudades.

---

## 3. Usuarios y lo que puede hacer cada uno

Cada usuario solo ve su propia tienda, planta o almacén. El administrador lo ve todo y puede abrir el panel
de cualquiera.

### Cómo se usa la interfaz

- **Menú lateral** agrupado (Operación, Red, Flota, Sistema para el admin). En el móvil se abre con el botón ☰.
- **Pestañas** en los paneles de tienda, planta y almacén. El número junto a una pestaña indica lo pendiente
  (pedidos activos, reposiciones por expedir…). La app recuerda la última pestaña abierta.
- **Totales en vivo**: al registrar ventas o pedir, debajo de la tabla se ven las prendas, los kg y las cajas
  aproximadas mientras se escribe.
- **Buscadores** encima de las tablas largas: filtran al momento, sin recargar (ignoran tildes y mayúsculas).
- **Avisos** arriba a la derecha: los de éxito se cierran solos; los de error se quedan hasta cerrarlos.
- Los botones muestran que están trabajando y no se pueden pulsar dos veces. Las acciones que no tienen vuelta
  atrás (expedir, cerrar semana, eliminar) piden confirmación.
- **Modo oscuro** automático si el sistema lo usa.
- **Marca**: logo RMS (letras de tela vaquera). La estética de la app sale de él: azul denim, contorno azul marino
  y costuras doradas. Los ficheros están en `code/static/marca/`; el original y el script que quita el fondo y
  genera los tamaños web, en `docs/marca/` (`python procesar_logo.py original.png carpeta_salida`, requiere Pillow).

### Tienda

| Sección | Para qué |
|---|---|
| **Stock actual** | Prendas por segmento × tipo y, debajo, cuántos días cubren al ritmo de venta actual. |
| **Registrar ventas** | Resta del stock y alimenta el pedido automático. Se puede indicar la fecha (no futura). |
| **Pedido adicional** | Pedido extra, por categoría, para una semana aún abierta. Se puede marcar como urgente y dejar un comentario. |
| **Pedido automático** | Activarlo o no, frecuencia (diaria L-V / semanal / mensual), día y días de stock de seguridad. Muestra lo que pediría hoy. |
| **Poner / quitar prendas** | Ajustes de inventario (recuentos, mermas, retiradas). Positivo suma, negativo resta. |
| **Mis pedidos** | Histórico con estado, ruta y camión. Cancelar mientras esté *pendiente*; **Recibido** cuando llegue (suma el stock). Los pedidos en camino aparecen además en un aviso arriba del panel. |

### Planta

- Stock por categoría frente a su **stock máximo**.
- **Poner / quitar prendas**: entradas de ropa clasificada, mermas, otros canales.
- **Reposiciones a almacenes**: lo que tiene que enviar cada semana. **Expedir** descuenta el stock
  (si no hay suficiente de alguna categoría, lo avisa y no deja expedir).
- Sus rutas troncales, sus camiones y los almacenes que la han vetado.

### Almacén

- Cajas en stock frente al **máximo**, con lo previsto (entradas − salidas) para la semana.
- **Entradas desde planta**: **Recibir** suma las cajas (avisa si se supera el máximo).
- **Salidas a tiendas**: **Cargar en camión** pedido a pedido o **Cargar ruta** completa (resta cajas).
- Sus vehículos de reparto, las tiendas servidas en el último plan y las que lo han vetado.
- **Plantas vetadas**: de qué plantas no quiere recibir ropa (ver §6).

### Administrador

| Menú | Qué hace |
|---|---|
| **Panel** | Indicadores, alertas, ocupación de plantas y almacenes; botón para generar pedidos automáticos. |
| **Planificación** | El ciclo semanal (ver §4). |
| **Pedidos** | Todos los pedidos, con filtros por semana, estado, tipo y tienda. |
| **Tiendas, Plantas, Almacenes** | Alta, edición, baja. Al dar de alta se crea su usuario con contraseña aleatoria (se muestra una vez). En los almacenes, también sus costes (fijo semanal y manipulación) y el tiempo de descarga; en las tiendas, si están en la muestra de pruebas y la planta que las sirve hoy. |
| **Optimizador → Parámetros, Tipos de vehículo, Tarifas** | Los supuestos con los que calcula el optimizador (kg por palé, jornada, frecuencias, capacidades y emisiones de cada vehículo, €/km, €/h, €/día…). Se editan, no se añaden ni se borran; el cambio entra en la siguiente optimización. |
| **Camiones troncal / reparto** | Flotas por separado: matrícula, tipo, capacidad, operador, conductor, estado, ITV, km. |
| **Usuarios** | Crear, editar, desactivar; asignar rol y tienda/planta/almacén. |
| **Movimientos** | Auditoría de cada cambio de stock: quién, cuándo, cuánto y por qué. |
| **Optimizador avanzado** | Herramienta avanzada original (escenarios con demanda estimada, mapa y analítica). |

**Eliminar**: si el elemento no tiene historial se borra; si lo tiene (ventas, pedidos, viajes,
movimientos…) se **da de baja** y se conserva el histórico. Los dados de baja se ven con
*Mostrar también los dados de baja*. El admin no puede quitarse su propio rol ni dejar el sistema sin administradores.

---

## 4. La semana paso a paso

Ejemplo para entregar en la semana **W48**:

```
 W47 lun-vie                     W47 viernes (admin)                      W48
 ───────────────────────────     ──────────────────────────────────       ─────────────────────────────────────
 tiendas venden y piden    ──▶   1 Cerrar semana W48               ──▶    planta expide ─▶ almacén recibe
 pedidos automáticos             2 Optimizar e importar rutas             almacén carga ─▶ tienda recibe
 (estado: pendiente)             3 Revisar camiones asignados
```

1. **Cerrar semana** (*Planificación*). Los pedidos pendientes pasan a *confirmado* y se suman por tienda en la
   tabla que lee el optimizador. Desde ese momento las tiendas ya no pueden pedir para esa semana.
2. **Optimizar e importar rutas**. Se exportan tiendas, plantas, almacenes y los vetos a `data/`,
   se ejecuta el optimizador (1–2 min, la página se refresca sola) y se importa su resultado:
   - una **ruta** por camión y día (`T-MAD-01` troncal desde Madrid, `R-SEVILLA-02` reparto desde Sevilla);
   - cada ruta recibe un **camión libre** de su base: primero uno del mismo tipo y, si no hay, el más pequeño con
     capacidad suficiente. Se puede cambiar a mano. Las rutas sin camión salen en rojo y como alerta;
   - una **reposición** por almacén: la planta que el optimizador eligió, las cajas y la mezcla de prendas de los
     pedidos de las tiendas que ese almacén reparte;
   - los pedidos pasan a *planificado*, con su ruta y su almacén.
3. **Ejecución**:

| Quién | Acción | Efecto |
|---|---|---|
| Planta | Expedir reposición | − prendas en planta; la ruta troncal pasa a *en curso* |
| Almacén | Recibir reposición | + cajas en almacén |
| Almacén | Cargar pedido / ruta | − cajas en almacén; pedido *en reparto* |
| Tienda | Confirmar recepción | + prendas en tienda; pedido *entregado* |

Cuando todo lo que lleva una ruta está entregado, la ruta pasa a *completada*, su camión vuelve a *disponible*
y se le suman los km.

- **Ver mapa y analítica** abre el plan del optimizador (rutas sobre carretera, cronograma, costes).
- **Reabrir semana** borra rutas y reposiciones y devuelve los pedidos a *pendiente*. Solo se puede si aún no
  ha salido nada.
- **Volver a volcar pedidos** incluye pedidos nuevos sin perder los ya planificados. Después hay que volver a
  optimizar.

**Estados de un pedido**: `pendiente → confirmado → planificado → en reparto → entregado` (o `cancelado`).

---

## 5. Pedidos automáticos

Cada tienda elige **cuándo** se genera su pedido:

| Frecuencia | Día de pedido | Periodo que cubre |
|---|---|---|
| Diaria | de lunes a viernes | 1 día |
| Semanal | 0 = lunes … 6 = domingo | 7 días |
| Mensual | día del mes, 1–28 | 30 días |

El día que toca, para **cada categoría**:

```
pedido = venta media diaria (últimas 4 semanas) × (días del periodo + días de seguridad)
         − stock actual − lo ya pedido que aún no ha llegado
```

- Si el total pesa menos de media caja, no se genera.
- Se generan solos con la primera visita a la app de cada día (o con el botón del panel del admin).
  No se duplican: la app recuerda el último día procesado.
- Lo generado de lunes a viernes se entrega la semana siguiente. Lo del fin de semana, la otra.
  Si la app ha estado días sin abrirse, lo atrasado va a la primera semana aún abierta.
- Los **pedidos adicionales** se suman al automático. Ambos aparecen por separado en el histórico.

---

## 6. Vetos: de dónde no quiero recibir ropa

No hay asociaciones fijas: **cualquier planta puede reponer cualquier almacén y cualquier almacén puede servir
a cualquier tienda**. Cada semana el optimizador elige la combinación más barata que respeta capacidades.

Lo único que se configura son **vetos**, y los decide cada uno desde su panel (el admin también puede, en la ficha):

| Quién | Dónde | Qué veta |
|---|---|---|
| Tienda | pestaña *Almacenes vetados* | almacenes de los que no quiere recibir ropa |
| Almacén | pestaña *Plantas vetadas* | plantas de las que no quiere recibir ropa |

- Por defecto no hay ningún veto. Los cambios se aplican en la **siguiente planificación**.
- Si una tienda veta los almacenes más cercanos, la sirve uno más lejano (más caro).
- Un almacén que veta **todas** las plantas activas no recibe mercancía: el optimizador no lo usa y reparte
  sus tiendas a otros almacenes. Sale como alerta en rojo.
- Una tienda que veta **todos** los almacenes que pueden recibir mercancía no se puede servir: sus pedidos se
  quedan sin ruta, con aviso en el plan y alerta en rojo.
- Cada pedido tiene almacén solo cuando el optimizador se lo asigna; el panel del almacén muestra las tiendas que
  ha servido en el último plan y las que lo han vetado.

## 7. Alertas

| Alerta | Dónde se ve |
|---|---|
| Almacén por encima de su máximo, al ≥ 90 % o que lo superaría con lo pendiente | admin, almacén |
| Almacén que ha vetado todas las plantas activas | admin, almacén |
| Tienda que ha vetado todos los almacenes disponibles | admin, tienda |
| Planta por encima de su stock máximo o al ≥ 90 % | admin, planta |
| Planta sin stock suficiente para las reposiciones planificadas | admin, planta |
| Tienda con categorías agotadas | tienda |
| ITV caducada o que vence en 30 días | admin |
| Rutas planificadas sin camión asignado | admin |

Superar un máximo **no bloquea** la operación (la mercancía ya ha llegado físicamente), pero queda señalado.

---

## 8. Cómo se conecta con el optimizador de rutas

El optimizador es el paquete `code/modare`. La app habla con él **solo a través de
`code/gestion/optimizador.py`**. Si se sustituye la librería, solo hay que adaptar ese fichero.

| Dirección | Qué | Dónde |
|---|---|---|
| App → librería | pedidos de la semana, en kg y cajas por tienda | tabla `pedidos_optimizador` (misma base de datos) |
| App → librería | tiendas, plantas, almacenes y sus vetos (altas, bajas, cambios) | sus tablas (`tiendas`, `plantas`, `almacenes`, `*_vetad*`) |
| App → librería | parámetros, tipos de vehículo, tarifas | tablas `parametros`, `tipos_vehiculo`, `tarifas` (Admin → Optimizador) |
| App → librería | distancias por carretera | tabla `distancias` (botón «Calcular distancias» en Planificación) |
| Librería → app | rutas, paradas, almacén y planta asignados, KPIs | tabla `planes` (y copia en `code/salidas/<semana>/`: `plan.json`, CSV, `mapa.html`) |
| Ambas | estado de cada optimización (calculando / ok / error) | tabla `trabajos_optimizacion` |

- No hay que exportar nada: cada optimización lee la base de datos en ese momento, así que cualquier cambio
  hecho en la app (una tienda nueva, un veto, una tarifa, un parámetro) entra en la siguiente optimización.
- **Parámetros, tipos de vehículo y tarifas** (menú *Optimizador*) se pueden editar pero no añadir ni borrar,
  porque el optimizador los necesita todos. Un parámetro numérico no admite texto.
- Si se añade o se mueve una tienda, planta o almacén, sus distancias por carretera dejan de valer: la app las
  borra y en *Planificación* aparece un aviso con el botón **Calcular distancias (OSRM)**, que calcula solo las
  de esos puntos (unos segundos por punto). Mientras tanto el optimizador usa línea recta × 1,26. Lo mismo desde
  la consola: `python calcular_distancias.py`.
- Las tiendas sin acceso por carretera (Baleares) quedan fuera de las rutas. Se avisan y su pedido se puede
  cargar a mano.

---

## 9. Datos y tablas

**Una sola base de datos** para la app y la librería de rutas (variable `MODARE_DB`; en desarrollo `code/gestion.db`,
que no se sube a git). Modelos en `code/gestion/modelos.py` y `code/modare/bd.py`; el esquema lo crean y actualizan
las migraciones de `code/migrations/`.

| Tabla | Contenido |
|---|---|
| `usuarios` | usuario, contraseña cifrada, rol, tienda/planta/almacén, activo, último acceso |
| `categorias` | segmento, tipo, kg y € por prenda, cuota de ventas |
| `plantas`, `stock_planta` | ubicación, capacidad de salida kg/semana, **stock máximo**, horario; stock por categoría |
| `almacenes` | ubicación, **stock actual y máximo en cajas**, capacidad kg/semana, horario, planta del último plan |
| `almacen_plantas_vetadas`, `tienda_almacenes_vetados` | vetos: plantas que cada almacén no quiere y almacenes que cada tienda no quiere |
| `tiendas`, `stock_tienda` | ubicación, tamaño, ventana de recepción, vehículo máximo, configuración del pedido automático, capacidad, contacto; stock por categoría |
| `ventas` | ventas diarias por tienda y categoría |
| `pedidos`, `lineas_pedido` | tipo (automático/adicional), estado, semana, urgente, almacén y ruta; prendas por categoría |
| `camiones_troncal` | camiones planta → almacén (base: planta) |
| `camiones_reparto` | vehículos almacén → tienda (base: almacén) |
| `viajes` | rutas del plan con horario, km, coste, CO₂ y camión asignado |
| `reposiciones`, `lineas_reposicion` | envíos planta → almacén y su mezcla de prendas |
| `movimientos` | auditoría de cada cambio de stock |
| `ajustes` | estado interno (semanas cerradas, último día de pedidos automáticos) |
| `pedidos_optimizador` | (librería) pedido semanal de cada tienda en kg y cajas: la entrada del optimizador |
| `planes` | (librería) el plan de cada semana en JSON |
| `trabajos_optimizacion` | (librería) estado de cada optimización y cola del ordenador optimizador (fila `latido` = está conectado); una «calculando» de más de 60 min se da por interrumpida |
| `alembic_version` | versión del esquema (migraciones) |

Código: `gestion/vistas_*.py` (pantallas), `gestion/logistica.py` (reglas de negocio),
`gestion/optimizador.py` (puente con la librería), `gestion/semilla.py` (carga inicial), `gestion/basedatos.py`
(preparación de la base y migraciones), `modare/bd.py` (tablas de la librería),
`templates/gestion/` (HTML), `static/remode.css` y `static/remode.js` (diseño e interacción, sin dependencias).

---

## 10. Datos inventados (sintéticos)

Para que la app funcione desde el primer día, la carga inicial **inventa** lo que el reto no da. Todo se puede
cambiar desde la app y conviene sustituirlo por datos reales:

- peso, precio y cuota de ventas de cada categoría de prenda;
- 8 semanas de ventas por tienda y categoría, a partir de sus ventas anuales;
- stock inicial: tiendas ≈ 4 días de venta; plantas al 60 % de un máximo de 3 semanas de salida;
  almacenes al 25 % de un máximo de 2 semanas de capacidad;
- frecuencia de pedido: tiendas pequeñas mensual (día 1), el resto semanal (jueves);
- flota: 3 camiones troncales por planta activa (más un tráiler en Barcelona); 1 furgoneta por almacén y
  un rígido de 7,5 t si mueve ≥ 3.000 kg/semana; matrículas ficticias.

---

## 11. Problemas frecuentes

| Problema | Solución |
|---|---|
| «Formulario caducado» / error 400 | La sesión ha cambiado (p. ej. se reinició el servidor). Recarga la página. |
| Las rutas del mapa salen en línea recta | No se pudo pedir el trazado a OSRM (sin red o certificados). En macOS se resuelve con `certifi`, ya incluido en `requirements.txt`. Reoptimiza la semana. |
| «Asignación infactible» | La demanda supera la capacidad de los almacenes o de las plantas que no están vetadas. Revisa capacidades y vetos. |
| Una ruta «sin camión» | No hay un camión libre en esa base a esa hora. Añade uno en *Camiones* o asígnalo a mano en *Planificación*. |
| Una tienda no pide automáticamente | Revisa que tenga el pedido automático activo, que sea su día y que tenga ventas registradas en las últimas 4 semanas. |
| No deja expedir una reposición | La planta no tiene stock suficiente de alguna categoría: registra la entrada de prendas. |
| Puerto 5000 ocupado (macOS) | `PORT=5050 .venv/bin/python app.py` |

### Pendiente / límites conocidos

- La librería supone que lo repuesto en la semana W se reparte en W+1; la app hoy hace ambas cosas en la misma semana.
- Un pedido que el optimizador reparte en varias entregas se asocia a la primera ruta.
- No hay límite de intentos de inicio de sesión: conviene ponerlo en el proxy (nginx, Cloudflare…).
- La optimización corre en un hilo del proceso web: si el servidor se reinicia mientras calcula, queda marcada
  como interrumpida y hay que relanzarla. Para muchos usuarios a la vez, moverla a una cola de tareas (RQ, Celery).
- `code/salidas/` se escribe en el disco del servidor; la app no depende de ello para funcionar (los planes están
  en la base), pero el mapa HTML de `salidas/` se pierde si el disco es efímero.

---

## 12. Uso real: base de datos y despliegue

### Dónde están los datos

| Qué | Dónde |
|---|---|
| Todo lo de la app y del optimizador (usuarios, stock, pedidos, rutas, planes, estado de las optimizaciones) | **la base de datos** de `MODARE_DB` |
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
| `MODARE_OPTIMIZADOR` | `externo` en servidores pequeños | las rutas las calcula `trabajador.py` en otro ordenador (ver abajo) |

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

### Calcular las rutas en otro ordenador (servidor pequeño o gratuito)

Optimizar necesita potencia. Si la web está en un servidor modesto (p. ej. el plan gratuito de Render), las rutas
se calculan en un ordenador potente que comparte la base de datos:

1. En el servidor: `MODARE_OPTIMIZADOR=externo`. El botón **Optimizar** de *Planificación* ya no calcula: deja la
   petición **en cola**, y la página muestra si el «ordenador optimizador» está conectado.
2. En el ordenador potente (con el repositorio y el entorno instalados):
   ```bash
   cd code
   export MODARE_DB='postgresql://…'      # la misma base que la web
   .venv/bin/python trabajador.py         # Windows: .venv\Scripts\python trabajador.py
   ```
   Déjalo abierto: recoge las peticiones, optimiza con toda su potencia y guarda las rutas en la base; la web se
   actualiza sola. Ctrl + C para pararlo. Si está apagado, las peticiones esperan en cola hasta que se conecte.
3. **Para no tener que abrir la terminal (Mac):** instálalo como servicio. Arranca solo al iniciar sesión, va en
   segundo plano y se reinicia si falla. Una sola vez:
   ```bash
   cd code
   echo "MODARE_DB=postgresql://…" > .env      # la cadena de la base (no se sube a git)
   ./servicio_trabajador.sh instalar
   ```
   `./servicio_trabajador.sh estado` (¿está en marcha?), `registro` (ver lo que hace), `reiniciar` (tras un
   `git pull`), `desinstalar`. Solo calcula con el Mac encendido y despierto; si duerme, las peticiones esperan.

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
