# ReMode Studio · manual de puesta en marcha

Este documento es para **quien vaya a poner en producción ReMode Studio en Moda re- y mantenerlo después**:
la persona de sistemas o de operaciones que se encarga de que funcione, aunque no conozca el código.

Responde a cuatro preguntas, en este orden:

1. ¿Qué hay que **decidir** antes de empezar? (§2)
2. ¿Qué **datos reales** hay que preparar y cuáles son inventados? (§3)
3. ¿Cómo se **instala** paso a paso? (§4–§5)
4. ¿Cómo se **opera y se mantiene** después? (§6–§10)

Otros documentos del repositorio:

| Documento | Para qué |
|---|---|
| [`docs/guia_app.md`](guia_app.md) | **Manual de uso**: qué hace cada pantalla y cada rol. Dáselo a quien vaya a usar la app. |
| [`code/.env.example`](../code/.env.example) | Todas las variables de configuración, comentadas. |
| [`CLAUDE.md`](../CLAUDE.md) | Detalle técnico del modelo de optimización y del código (para desarrolladores). |

Al final hay una **lista de comprobación de una página** (§11) para seguir el proceso de principio a fin.

---

## 1. Qué es y de qué piezas se compone

ReMode Studio planifica el transporte de ropa **planta → almacén → tienda**. Las tiendas, las plantas y los
almacenes llevan su stock y sus pedidos en la web. Cuando el administrador lo decide, el sistema agrupa los pedidos
pendientes en un **lote** y calcula las rutas más baratas: qué almacén sirve a cada tienda, cuántos camiones salen,
de qué tipo, en qué orden visitan las paradas y a qué hora. Respeta capacidades, horarios de recepción y normas de
conducción.

```
                         ┌──────────────────────────┐
  usuarios (navegador) ─▶│  WEB  (code/app.py)      │──┐
                         └──────────────────────────┘  │      ┌──────────────────────────┐
                                                       ├─────▶│  BASE DE DATOS           │
                         ┌──────────────────────────┐  │      │  PostgreSQL (MODARE_DB)  │
                         │  ORDENADOR OPTIMIZADOR   │──┘      └──────────────────────────┘
                         │  (code/trabajador.py)    │   opcional: calcula las rutas si la web
                         └──────────────────────────┘   está en un servidor pequeño

  Servicios externos gratuitos (necesitan Internet):
    OSRM ........ distancias y trazados por carretera      (router.project-osrm.org)
    Nominatim ... buscar direcciones en los formularios    (nominatim.openstreetmap.org)
    Mapas ....... teselas de OpenStreetMap / Esri y la librería Leaflet (unpkg.com)
```

| Pieza | Qué es | Imprescindible |
|---|---|---|
| **Web** | Aplicación Python (Flask). Pantallas, usuarios, pedidos, stock y botón de calcular rutas. | Sí |
| **Base de datos** | Guarda **todo**: red, usuarios, stock, pedidos, rutas, parámetros. Es la única fuente de verdad. | Sí |
| **Optimizador** | El motor de rutas (`code/modare`, con Google OR-Tools, gratuito y de código abierto). Se ejecuta dentro de la web o en el ordenador optimizador. | Sí |
| **Ordenador optimizador** | Un ordenador potente que calcula las rutas por la web cuando esta está en un servidor modesto. | Solo con `MODARE_OPTIMIZADOR=externo` |

**Coste de licencias: cero.** Todo el software es libre. Solo se paga el alojamiento, si no se usan planes gratuitos.

---

## 2. Decisiones previas

Decidid esto antes de instalar nada:

| Decisión | Opciones | Recomendación |
|---|---|---|
| **Dónde va la base de datos** | PostgreSQL gestionado (Neon, Supabase, Azure, AWS RDS…) o propio | Gestionado, **alojado en la UE** (hay nombres y emails: RGPD) y con **copias automáticas**. |
| **Dónde va la web** | PaaS (Render, Azure App Service, Railway…), un servidor Linux propio o un ordenador de la oficina | Un PaaS con HTTPS incluido. Con 1–2 GB de RAM basta si las rutas se calculan aparte. |
| **Dónde se calculan las rutas** | `local`: en el propio servidor de la web. `externo`: en un ordenador optimizador | `local` si el servidor tiene ≥ 4 núcleos y 4 GB; si no, `externo` con un ordenador de la oficina. |
| **Quién administra** | Una o dos personas con rol *admin* | Al menos **dos** administradores, para no depender de una sola persona. |
| **Quién usa la app** | Cada tienda, planta y almacén con su usuario, o solo la central | Empezad con la central y unas pocas tiendas piloto; ampliad después. |
| **Dirección web** | Subdominio propio (p. ej. `rutas.modare.org`) o el que da el proveedor | Subdominio propio: más fácil de recordar y de mantener si cambiáis de proveedor. |

**Requisitos técnicos:**
- Python **3.13** (versión fijada en `code/.python-version`).
- Para calcular las rutas de toda la red (~180 tiendas): 4 núcleos y 4 GB de RAM o más. Tarda 1–2 minutos.
- La web en sí consume poco: 1 núcleo y 1–2 GB.
- Conexión a Internet en la web y en el ordenador optimizador (servicios externos de §1).

---

## 3. Preparar los datos reales (la parte más importante)

El sistema viene con datos **del reto del hackathon**: parte son reales (públicos) y parte **inventados**, para que
funcionara desde el primer día. **Unas rutas calculadas con datos inventados no sirven para decidir nada.**
Antes de usarlo de verdad hay que sustituir lo inventado.

Cada fichero de `data/` indica en sus columnas `etiqueta` / `*_etiqueta` si un dato es **REAL**, **ESTIMADO**
(calculado a partir de fuentes públicas) o **SINTÉTICO** (inventado). Estado actual:

| Fichero (`data/`) | Qué contiene | Real | Hay que conseguir / revisar |
|---|---|---|---|
| `plantas.csv` | 6 plantas (4 activas) | ubicación y estado | **Capacidad de salida (kg/semana)** y **horario de carga** de cada planta |
| `almacenes.csv` | 38 almacenes, uno por provincia | **nada** | **Todo**: si existen, dónde están, capacidad, horario y costes (alquiler, manipulación). Hoy son polígonos industriales elegidos por el equipo. |
| `tiendas.csv` | ~180 tiendas | ubicación y horario comercial | **Ventas anuales** (de ellas sale la demanda), **tamaño**, **ventana de recepción de mercancía**, **vehículo máximo** que puede llegar a cada una |
| `vehiculos.csv` | 5 tipos de vehículo | — (estimado de fichas técnicas) | Capacidades de **los vehículos que de verdad se contratan** |
| `tarifas.csv` | Operador A (€/km + €/h) y B (€/día + €/km) | — (Observatorio de Costes del Ministerio) | **Las tarifas reales de vuestros transportistas** y cuántos camiones puede poner cada uno |
| `emisiones.csv` | kg CO₂/km por tipo | — (DEFRA 2025) | Válido como referencia; solo informativo |
| `parametros.csv` | 49 supuestos del modelo | 3 | Sobre todo: `precio_medio_eur_kg`, frecuencias de entrega por tamaño, máximo de palés por entrega, jornada y días de ruta, `kg_por_palet` |
| `distancias.csv` | km y minutos por carretera entre todos los puntos | — (OSRM) | Se **recalcula sola** con la red real (§4.5) |

**Además, la carga inicial inventa** (marcado como SINTÉTICO en la app): el catálogo de **categorías de prenda**
(peso y precio por prenda), el **stock inicial** de tiendas, plantas y almacenes, **8 semanas de ventas** por tienda,
la **flota de camiones** (matrículas ficticias) y la **frecuencia de pedido** de cada tienda. Todo eso se corrige
**desde la app** después de la primera carga (§5).

### Dos formas de cargar los datos reales

**A · Corregir los CSV antes de la primera carga** (recomendado si tenéis los datos en Excel)

1. Abre cada CSV de `data/` en Excel o LibreOffice **conservando las columnas**. Cambia los valores y pon
   `REAL` en las etiquetas de lo que corrijas. Guárdalo como **CSV UTF-8**, separado por comas.
2. Tiendas: mantén el código `id_tienda` (`TD-001`…) si ya existe. Para quitar una tienda, pon `activa = 0`.
   Las de islas, con `acceso_carretera = 0`: se gestionan en la app pero no entran en las rutas.
3. Almacenes: si no vais a usar uno por provincia, borra las filas que sobren o márcalas `activo = 0`.
   No hace falta que `generar_almacenes.py` los invente: rellena tú el CSV.
4. No toques `distancias.csv`: se recalcula en §4.5.

**B · Cargar lo de ejemplo y corregir desde la app**

Sirve si los cambios son pocos. Las pantallas de *Tiendas*, *Plantas*, *Almacenes*, *Parámetros*, *Tipos de
vehículo* y *Tarifas* permiten editarlo todo, y *Tiendas → Importar CSV* da de alta tiendas en bloque (con vista
previa y validación; ver [guía §3](guia_app.md#administrador)).

> Los CSV **solo se leen al crear la base de datos**. Después, todo se cambia en la app; editar un CSV ya no
> cambia nada en una base existente.

---

## 4. Instalación en producción, paso a paso

### 4.1 Base de datos

1. Crea una base **PostgreSQL vacía** en tu proveedor, en una **región de la UE**, con copias de seguridad automáticas.
2. Copia su **cadena de conexión**. Tiene esta forma (también vale `postgres://`):
   `postgresql://usuario:clave@host:5432/nombre_base` (algunos proveedores añaden `?sslmode=require`).
3. Guárdala en un gestor de contraseñas. Es la llave de todos los datos.

No hace falta crear tablas: la app las crea en el primer arranque.

### 4.2 Configuración (variables de entorno)

Se definen en el panel del proveedor (Render, Azure…) o en el servidor. **Nunca se suben a git.** La lista completa,
comentada, está en [`code/.env.example`](../code/.env.example).

| Variable | Valor | Notas |
|---|---|---|
| `MODARE_ENV` | `produccion` | Activa las comprobaciones de seguridad y las cookies seguras. |
| `MODARE_DB` | la cadena de §4.1 | Sin ella, en producción la app no arranca. |
| `MODARE_SECRET_KEY` | ≥ 32 caracteres aleatorios | Genérala con `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Si cambia, se cierran todas las sesiones. |
| `MODARE_PASSWORD_ADMIN` | contraseña del usuario `admin` | **Solo se usa en la primera carga.** Obligatoria en producción. |
| `MODARE_PASSWORD_INICIAL` | contraseña inicial de tiendas, plantas y almacenes | Ídem. Cada usuario la cambia después en *Mi cuenta*. |
| `MODARE_AUTO_MIGRAR` | `1` (un solo proceso) o `0` | Con `0`, ejecuta `flask --app app db upgrade` en cada despliegue (§4.3). |
| `MODARE_OPTIMIZADOR` | `local` o `externo` | Decisión de §2. Con `externo`, sigue §4.6. |
| `MODARE_PROCESOS` | p. ej. `2` | Procesos en paralelo del optimizador. En un servidor de 2 GB, `2`. Por defecto usa todos los núcleos. |
| `MODARE_PROXY` | `1` (defecto) | La app está detrás de un proxy HTTPS, como en casi todos los proveedores. |

### 4.3 Instalar y arrancar la web

En un PaaS se configuran estos mismos comandos en su panel. En un servidor Linux propio, desde la carpeta del repositorio:

```bash
cd code
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# variables de §4.2 ya definidas en el entorno
.venv/bin/flask --app app db upgrade                                   # crea / actualiza las tablas
.venv/bin/gunicorn -w 2 -b 0.0.0.0:8000 --timeout 120 app:app          # Windows: waitress-serve --port 8000 app:app
```

- Pon delante un **proxy con HTTPS** (nginx, Caddy o el del proveedor). Sin HTTPS el inicio de sesión no funciona
  en producción (las cookies son solo seguras).
- El primer arranque con la base vacía **crea las tablas y carga los datos de `data/*.csv`** (§3): red, datos del
  optimizador, categorías, flota y usuarios. Tarda unos segundos.

### 4.4 Primer acceso

1. Entra en la dirección web con `admin` y la contraseña de `MODARE_PASSWORD_ADMIN`.
2. **Cambia la contraseña** en *Mi cuenta*.
3. Crea un **segundo administrador** en *Usuarios*.

### 4.5 Distancias por carretera

Las rutas usan distancias y tiempos reales por carretera. Si has cambiado plantas, almacenes o tiendas respecto a los
datos de ejemplo, calcúlalas una vez con la red real. Desde un ordenador con el repositorio y `MODARE_DB` apuntando a
la base de producción:

```bash
cd code
.venv/bin/python calcular_distancias.py --todos     # ~1 minuto; todos los pares
```

Después, cada vez que se dé de alta o se mueva una tienda, planta o almacén, la app avisa en *Planificación* y el
botón **Calcular distancias (OSRM)** calcula solo las que faltan (unos segundos por punto). Mientras no se calculen,
se usa línea recta × 1,26: funciona, pero es menos exacto.

### 4.6 Ordenador optimizador (solo con `MODARE_OPTIMIZADOR=externo`)

Con esta opción, el botón de calcular rutas de la web **deja la petición en cola**, y un ordenador potente la recoge,
calcula y guarda el resultado en la misma base.

1. En ese ordenador: clona el repositorio, crea el entorno (como en §4.3) y crea el fichero `code/.env` con una línea:
   `MODARE_DB=postgresql://…` (la misma base que la web; este fichero no se sube a git).
2. Prueba: `.venv/bin/python trabajador.py` (Windows: `.venv\Scripts\python trabajador.py`). Debe decir que espera peticiones.
3. **En un Mac**, déjalo como servicio, para que arranque solo y se reinicie si falla:
   ```bash
   cd code
   ./servicio_trabajador.sh instalar        # una vez
   ./servicio_trabajador.sh estado          # ¿está en marcha?
   ```
   En Windows o Linux, configúralo como tarea programada al iniciar sesión o como servicio de systemd.
4. En la web, *Planificación* muestra si el ordenador optimizador está **conectado**. Si está apagado o dormido, las
   peticiones esperan en cola hasta que vuelva.

### 4.7 Comprobación final

- [ ] La web abre por **HTTPS** y se puede entrar como admin.
- [ ] En *Tiendas*, *Plantas* y *Almacenes* se ven los datos reales.
- [ ] En *Planificación* no hay aviso de distancias pendientes (o calcúlalas).
- [ ] Con `externo`: el ordenador optimizador aparece conectado.
- [ ] Un cálculo de prueba termina y muestra rutas en el mapa (§5, paso 6).

---

## 5. Puesta a punto inicial (primera semana)

Con la app instalada, corrige lo que la carga inicial inventó. Todo desde la app, como admin:

1. **Categorías de prenda** (menú *Categorías de prenda*): peso medio y precio por prenda de cada categoría. El peso convierte los
   pedidos en kg y cajas, así que afecta a las rutas.
2. **Flota** (*Camiones troncales* y *Camiones de reparto*): da de baja los camiones de ejemplo (matrículas ficticias)
   y da de alta los reales, con su base, tipo, operador y conductor.
3. **Stock**: en el panel de cada planta, almacén y tienda, ajusta el stock real con *Poner / quitar prendas*.
   O pide a cada usuario que lo haga el primer día.
4. **Pedido automático de cada tienda**: frecuencia, día y días de seguridad (en la ficha de la tienda o en su panel).
   Las ventas de ejemplo de las 8 semanas anteriores salen solas de la media en unas semanas, a medida que se
   registran ventas reales.
5. **Parámetros, tipos de vehículo y tarifas** (menú *Optimizador*): repasa los marcados SINTÉTICO o ESTIMADO (§3).
6. **Prueba piloto**: con unas pocas tiendas reales haciendo pedidos, cierra un lote en *Planificación* y revisa el
   resultado. ¿Las rutas son razonables para quien conoce el terreno? ¿El coste se parece al que pagáis hoy?
   Ajusta parámetros y tarifas hasta que cuadre, y entonces amplía al resto de tiendas.
7. **Usuarios**: entrega a cada tienda, planta y almacén su usuario (`td-001`, `pl-mad`, `al-madrid`…) con la
   contraseña inicial, y pídeles que la cambien. Dales la [guía de uso](guia_app.md), §3.
8. **Vetos**, si alguno lo necesita (un almacén que no debe recibir de cierta planta, una tienda que no quiere cierto
   almacén): en sus fichas. Ver [guía §6](guia_app.md#6-vetos-de-dónde-no-quiero-recibir-ropa).

---

## 6. Operación habitual

El flujo de uso completo está en la [guía §4](guia_app.md#4-de-los-pedidos-a-las-rutas-lotes). Resumen de tareas:

| Cuándo | Quién | Qué |
|---|---|---|
| Cada día | Tiendas | Registrar ventas (alimentan el pedido automático); confirmar lo recibido. |
| Cada día | Plantas / almacenes | Expedir, recibir y cargar según las rutas planificadas. |
| Cuando toque planificar (p. ej. cada semana) | Admin | *Planificación* → revisar pendientes → **Cerrar pedidos y calcular rutas** → revisar el lote, los camiones asignados y las alertas. |
| Al dar de alta o mover un punto de la red | Admin | *Planificación* → **Calcular distancias (OSRM)**. |
| Cada semana | Admin | Revisar el *Panel* (alertas: almacenes llenos, ITV, rutas sin camión). |
| Cada mes | Admin | Revisar tarifas y parámetros; dar de baja usuarios que ya no estén. |

---

## 7. Mantenimiento

### Actualizar a una versión nueva del código

1. Haz una **copia de seguridad** de la base (abajo).
2. Despliega la versión nueva (`git pull` en el servidor, o un nuevo despliegue en el PaaS).
3. Aplica los cambios de esquema: `flask --app app db upgrade` (o se aplican solos al arrancar con `MODARE_AUTO_MIGRAR=1`).
4. Reinicia la web y, si existe, el ordenador optimizador (`./servicio_trabajador.sh reiniciar` tras su `git pull`).
   **Web y ordenador optimizador deben tener la misma versión**, porque comparten la base.

> **Nunca cambies el esquema de la base a mano.** Los cambios de tablas se hacen con migraciones
> (ver [guía §12](guia_app.md#cambiar-el-esquema-añadir-un-campo-una-tabla)).

### Copias de seguridad

- PostgreSQL gestionado: activa las copias automáticas diarias y la **restauración a un punto en el tiempo**.
- Copia manual antes de cada actualización: `pg_dump "$MODARE_DB" > copia_AAAA-MM-DD.sql`.
- **Prueba a restaurar una copia al menos una vez** (en una base aparte): una copia que no se ha probado no es una copia.
- `code/salidas/` (mapas y CSV de cada lote) es prescindible: el plan está en la base.

### Contraseñas y accesos

- Usuario que se va: *Usuarios* → desactivarlo (se conserva su historial).
- Contraseña olvidada: un admin le pone una nueva en *Usuarios* y el usuario la cambia en *Mi cuenta*.
- Si se filtra `MODARE_SECRET_KEY` o la cadena de la base: cámbialas en el proveedor y reinicia. Cambiar la clave de
  sesión cierra la sesión de todos.

---

## 8. Seguridad y datos personales

- La base contiene **datos personales** (nombres, teléfonos y emails de responsables y conductores): alójala en la
  UE, limita quién tiene la cadena de conexión e inclúyela en vuestro registro de tratamientos (RGPD).
- Las contraseñas se guardan cifradas (hash). Las contraseñas generadas en altas e importaciones se muestran **una
  sola vez**.
- La app no limita los intentos de inicio de sesión: configura un límite en el proxy o en el proveedor
  (nginx, Cloudflare…).
- Ficheros que **nunca** van a git: `code/.env`, `code/gestion.db` y copias de la base (ya están en `.gitignore`).

---

## 9. Dependencias externas y límites conocidos

### Servicios gratuitos de terceros

| Servicio | Para qué | Si falla | Para un uso intensivo |
|---|---|---|---|
| **OSRM** (servidor público de demostración) | Distancias por carretera y trazado de las rutas en el mapa | Las rutas se calculan igual (las distancias guardadas no cambian); los tramos nuevos se dibujan en línea recta y las distancias nuevas usan línea recta × 1,26 | El servidor público no garantiza disponibilidad. Para producción seria, montad un OSRM propio (gratuito, con Docker) y cambiad la dirección en `code/modare/osrm.py` (`URL`). |
| **Nominatim** (OpenStreetMap) | Buscar direcciones al dar de alta en el mapa | Se pueden escribir latitud y longitud a mano | Límite de 1 consulta por segundo; suficiente para altas manuales. |
| **Teselas de mapa y Leaflet** | Fondo de los mapas | El mapa sale sin fondo; el resto funciona | — |

### Límites del modelo (decididlos con quien conoce la operación)

- Las rutas son **muy buenas, no matemáticamente óptimas**: el cálculo busca durante un tiempo fijo (30 s por planta).
- Las distancias son de **coche** (tiempo de camión = tiempo de coche × 1,2). No tienen en cuenta restricciones de
  camión (gálibo, peso, zonas de bajas emisiones).
- El troncal (planta → almacén) y el reparto de un lote se planifican a la vez: se supone que los almacenes tienen
  stock para empezar a repartir.
- La vuelta al depósito es en vacío: no se modela la recogida de sacas o de ropa no vendida.
- La flota física necesaria se estima por solapamiento de rutas; no se encadenan explícitamente las rutas de un mismo
  camión.
- Más límites, en la [guía §11](guia_app.md#pendiente--límites-conocidos) y en `CLAUDE.md` («Supuestos y limitaciones»).

---

## 10. Resolución de problemas

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| La web no arranca: «exige MODARE_SECRET_KEY / MODARE_DB» | Falta configuración de producción | Define las variables de §4.2. |
| La web no arranca: «Faltan MODARE_PASSWORD_ADMIN y …» | Primera carga sin contraseñas | Defínelas (solo hacen falta la primera vez). |
| «Can't locate revision …» al arrancar | La base tiene una versión del esquema que este código no conoce (código más antiguo que la base, o de otra rama) | Despliega la misma versión con la que se migró la base, o restaura una copia compatible. **No borres la tabla `alembic_version`.** |
| No se puede iniciar sesión en producción | La web no se sirve por HTTPS | Configura HTTPS en el proxy (§4.3). |
| «Formulario caducado» / error 400 | La sesión cambió (reinicio, otra pestaña) | Recarga la página. |
| Las rutas no se calculan nunca («en cola») | Con `externo`: el ordenador optimizador está apagado o desconectado | Enciéndelo; en Mac, `./servicio_trabajador.sh estado` y `registro`. |
| «La optimización se interrumpió» | La web o el ordenador se reiniciaron mientras calculaba | Vuelve a calcular el lote. |
| «Asignación infactible» | Se pide más ropa de la que pueden mover las plantas y almacenes (o hay vetos que lo impiden) | Revisa capacidades y vetos. |
| Rutas «sin camión» | No hay camiones libres de ese tipo en esa base | Da de alta camiones o asigna uno a mano en *Planificación*. |
| Una tienda nunca aparece en las rutas | Inactiva, sin acceso por carretera, o ha vetado todos los almacenes | Revisa su ficha. |
| Las rutas del mapa salen en línea recta | OSRM no disponible o problema de certificados (macOS) | Reintenta más tarde; en macOS, `requirements.txt` ya incluye `certifi`. |
| El cálculo tarda mucho o el servidor se queda sin memoria | Servidor pequeño | `MODARE_PROCESOS=2` o pasa a `MODARE_OPTIMIZADOR=externo`. |

---

## 11. Lista de comprobación de la puesta en marcha

**Decidir** (§2)
- [ ] Proveedor de la base (PostgreSQL, UE, con copias) y de la web.
- [ ] `MODARE_OPTIMIZADOR`: `local` o `externo` (y qué ordenador hará de optimizador).
- [ ] Dos administradores y la dirección web.

**Datos** (§3)
- [ ] Plantas: capacidad y horario reales.
- [ ] Almacenes: ubicación, capacidad, horario y costes reales (o quitar los que no existan).
- [ ] Tiendas: ventas, tamaño, ventana de recepción y vehículo máximo.
- [ ] Vehículos y tarifas reales de los transportistas.
- [ ] Parámetros SINTÉTICOS revisados.

**Instalar** (§4)
- [ ] Base PostgreSQL creada y cadena guardada.
- [ ] Variables de entorno definidas (incluidas las contraseñas iniciales).
- [ ] Web desplegada con HTTPS; primer arranque correcto.
- [ ] Contraseña de `admin` cambiada y segundo admin creado.
- [ ] `calcular_distancias.py --todos` ejecutado con la red real.
- [ ] Ordenador optimizador instalado y conectado (si `externo`).

**Poner a punto** (§5)
- [ ] Categorías de prenda, flota real y stock real.
- [ ] Pedido automático de cada tienda configurado.
- [ ] Lote de prueba piloto revisado con quien conoce las rutas.
- [ ] Usuarios entregados y guía de uso distribuida.

**Mantener** (§7–§8)
- [ ] Copias automáticas activas y una restauración probada.
- [ ] Límite de intentos de inicio de sesión en el proxy.
- [ ] Procedimiento de actualización acordado (copia → desplegar → migrar → reiniciar web y optimizador).
