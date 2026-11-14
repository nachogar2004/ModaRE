"""Genera salidas/<semana>/mapa.html: grafo de rutas (troncal planta→almacén y reparto almacén→tienda)
sobre el mapa + cronograma semanal.

Se llama automáticamente al guardar un plan. Para regenerarlo desde un plan.json existente:
    python -m modare.visualizar 2026-W47
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config as C


def escribir_mapa(plan: dict, out_dir: Path) -> Path:
    datos = json.dumps(plan, ensure_ascii=False, default=str).replace("</", "<\\/")
    html = PLANTILLA.replace("__TITULO__", f"Rutas {plan['resumen']['semana']}").replace("__DATOS__", datos)
    f = out_dir / "mapa.html"
    f.write_text(html, encoding="utf-8")
    return f


PLANTILLA = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITULO__ · Moda re-</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { --bg:#f6f5f2; --panel:#fff; --ink:#1f2328; --muted:#6b7280; --line:#e5e3de; --accent:#0f766e; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; color:var(--ink); background:var(--bg); }
  header { padding:10px 16px; background:var(--panel); border-bottom:1px solid var(--line); display:flex; flex-wrap:wrap; gap:6px 20px; align-items:baseline; }
  header h1 { font-size:17px; margin:0 12px 0 0; }
  .kpi { color:var(--muted); } .kpi b { color:var(--ink); font-variant-numeric:tabular-nums; }
  main { display:grid; grid-template-columns: 360px 1fr; height: calc(100vh - 52px - 260px); min-height:380px; }
  #lista { overflow:auto; background:var(--panel); border-right:1px solid var(--line); }
  #map { height:100%; }
  .filtros { position:sticky; top:0; background:var(--panel); padding:8px 10px; border-bottom:1px solid var(--line); display:flex; flex-wrap:wrap; gap:6px; z-index:2; }
  .chip { border:1px solid var(--line); background:#fff; border-radius:14px; padding:3px 10px; cursor:pointer; font-size:13px; }
  .chip.on { background:var(--ink); color:#fff; border-color:var(--ink); }
  .grupo { padding:8px 10px 2px; font-weight:600; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .ruta { padding:7px 10px; border-left:5px solid transparent; cursor:pointer; border-bottom:1px solid #f1efea; }
  .ruta:hover { background:#faf9f6; }
  .ruta.sel { background:#eef7f5; }
  .ruta .t { display:flex; justify-content:space-between; gap:8px; }
  .ruta .t b { font-variant-numeric:tabular-nums; }
  .ruta .s { color:var(--muted); font-size:12px; }
  .ruta ol { margin:6px 0 2px; padding-left:22px; font-size:12px; }
  .ruta ol li.tarde { color:#b42318; }
  .bar { height:5px; background:#eee; border-radius:3px; overflow:hidden; margin-top:3px; }
  .bar i { display:block; height:100%; background:var(--accent); }
  #gantt { height:260px; overflow:auto; background:var(--panel); border-top:1px solid var(--line); padding:6px 10px 10px; }
  #gantt h2 { font-size:13px; margin:2px 0 6px; color:var(--muted); font-weight:600; }
  .g-row { display:grid; grid-template-columns:120px 1fr; align-items:center; height:18px; cursor:pointer; }
  .g-row:hover .g-lbl { text-decoration:underline; }
  .g-lbl { font-size:11px; font-variant-numeric:tabular-nums; }
  .g-track { position:relative; height:12px; }
  .g-bar { position:absolute; top:1px; height:10px; border-radius:3px; opacity:.85; }
  .g-row.dim .g-bar { opacity:.15; }
  .g-axis { display:grid; grid-template-columns:120px 1fr; position:sticky; top:0; background:var(--panel); z-index:1; }
  .g-days { position:relative; height:16px; font-size:11px; color:var(--muted); }
  .g-days span { position:absolute; border-left:1px solid var(--line); padding-left:3px; height:100%; }
  .g-night { position:absolute; top:0; bottom:0; background:rgba(30,40,80,.06); pointer-events:none; }
  .num { background:#fff; border:2px solid; border-radius:50%; width:22px; height:22px; line-height:18px; text-align:center; font-size:11px; font-weight:700; }
  .almacen-ico { width:12px; height:12px; background:#fff; border:3px solid #111; transform:rotate(45deg); }
  .planta-ico { background:#111; color:#fff; border-radius:4px; padding:2px 5px; font-size:11px; font-weight:700; white-space:nowrap; }
  .leyenda { background:#fff; padding:6px 8px; border-radius:6px; font-size:12px; box-shadow:0 1px 4px rgba(0,0,0,.2); }
  @media (max-width: 800px) { main { grid-template-columns:1fr; height:auto; } #lista { max-height:40vh; } #map { height:60vh; } }
</style>
</head>
<body>
<header id="cab"></header>
<main>
  <div id="lista"><div class="filtros" id="filtros"></div><div id="rutas"></div></div>
  <div id="map"></div>
</main>
<section id="gantt"></section>

<script>
const PLAN = __DATOS__;
const VEH = {"VEH-FUR":"Furgoneta 3,5 t","VEH-R75":"Camión 7,5 t","VEH-R12":"Camión 12 t","VEH-R18":"Camión 18 t","VEH-ART":"Articulado 40 t"};
const PALETA = ["#e6194b","#3cb44b","#4363d8","#f58231","#911eb4","#42d4f4","#f032e6","#9a6324","#469990","#800000","#808000","#000075","#bfef45","#fabed4","#dcbeff","#ffd8b1"];
const DIAS = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"];
const eur = n => n.toLocaleString("es-ES",{maximumFractionDigits:0}) + " €";
const num = (n,d=0) => n.toLocaleString("es-ES",{maximumFractionDigits:d});
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

const ESC_NOMBRE = {troncal: "Troncal (planta → almacén)", reparto: "Reparto (almacén → tienda)"};
const COL_PLANTA = {"PL-MAD": "#1d3557", "PL-VAL": "#9c2c13", "PL-BCN": "#5b2a86", "PL-BIO": "#1b6b3a", "PL-AST": "#7a5c00", "PL-SCQ": "#0b6e75"};
const lugares = {};
for (const p of PLAN.plantas) lugares[p.id] = {...p, clase: "planta"};
for (const a of PLAN.almacenes || []) lugares[a.id] = {...a, clase: "almacen"};
let iRep = 0;
const rutas = PLAN.rutas.map(r => {
  const escalon = r.escalon || "reparto", origen = r.origen || r.planta;
  const color = escalon === "troncal" ? (COL_PLANTA[r.planta] || "#111") : PALETA[iRep++ % PALETA.length];
  return {...r, escalon, origen, nombre: r.nombre || r.id, color};
});
let filtroPlanta = null, filtroEscalon = null, seleccion = null;
const visible = r => (!filtroPlanta || r.planta === filtroPlanta) && (!filtroEscalon || r.escalon === filtroEscalon);

// ---------- cabecera ----------
const R = PLAN.resumen;
const T = R.troncal, Rp = R.reparto;
document.getElementById("cab").innerHTML =
  `<h1>Rutas ${esc(R.semana)}</h1>` +
  [["Rutas", T ? `${R.rutas} <span class="kpi">(${T.rutas} troncal + ${Rp.rutas} reparto)</span>` : R.rutas],
   ["Camiones", R.camiones], ["km", num(R.km)],
   ["Coste", T ? `${eur(R.coste_eur)} <span class="kpi">(${eur(T.coste_eur)} + ${eur(Rp.coste_eur)})</span>` : eur(R.coste_eur)],
   ["€/kg", num(R.eur_por_kg, 3)], ["CO₂", num(R.co2_kg / 1000, 1) + " t"],
   ["Servicio", num(100 * R.nivel_servicio, 1) + " %"], ["Almacenes", R.almacenes_usados ?? "–"]]
  .map(([k, v]) => `<span class="kpi">${k} <b>${v}</b></span>`).join("");

// ---------- mapa ----------
const map = L.map("map", {preferCanvas: false});
// Teselas sin API key que funcionan abriendo el HTML como fichero local (file://).
// (CARTO pide clave y OpenStreetMap bloquea peticiones sin cabecera Referer.)
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
const fondoGris = L.layerGroup([
  L.tileLayer(ESRI + "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    {attribution: "Tiles © Esri — Esri, HERE, Garmin, © OpenStreetMap contributors", maxZoom: 16}),
  L.tileLayer(ESRI + "Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}", {maxZoom: 16}),
]).addTo(map);
const fondoCalles = L.tileLayer(ESRI + "World_Street_Map/MapServer/tile/{z}/{y}/{x}",
  {attribution: "Tiles © Esri — Esri, HERE, Garmin, © OpenStreetMap contributors", maxZoom: 19});
L.control.layers({"Gris (claro)": fondoGris, "Callejero": fondoCalles}, null, {position: "topright"}).addTo(map);

const capas = {};
for (const r of rutas) {
  const o = lugares[r.origen];
  const tro = r.escalon === "troncal";
  const pts = [[o.lat, o.lon], ...r.paradas.map(p => [p.lat, p.lon]), [o.lat, o.lon]];
  // un tramo por pareja de puntos consecutivos: trazado por carretera (OSRM) o, si falta, línea recta
  const tramos = pts.slice(0, -1).map((a, i) => {
    const g = (r.geometria || [])[i];
    return g ? decodificar(g) : [a, pts[i + 1]];
  });
  const ida = L.polyline(tramos.slice(0, -1), {color: r.color, weight: tro ? 5 : 3, opacity: .85});
  const vuelta = L.polyline(tramos[tramos.length - 1], {color: r.color, weight: 2, opacity: .6, dashArray: "6 6"});
  const flechas = tramos.slice(0, -1).map(t => flecha(t, r.color));
  const nodos = r.paradas.map((p, k) => L.marker([p.lat, p.lon], {zIndexOffset: 2000,
      icon: L.divIcon({className: "", html: `<div class="num" style="border-color:${r.color}">${k + 1}</div>`, iconSize: [22, 22], iconAnchor: [11, 11]})
    }).bindPopup(popupParada(r, p, k)));
  const puntos = tro ? [] : r.paradas.map((p, k) => L.circleMarker([p.lat, p.lon],
      {radius: 4, color: r.color, weight: 2, fillColor: "#fff", fillOpacity: 1}).bindPopup(popupParada(r, p, k)));
  const tip = `<b>${esc(r.nombre)}</b> · ${ESC_NOMBRE[r.escalon]}<br>${VEH[r.tipo] || r.tipo} · ${r.salida} → ${r.vuelta} · ${r.n_paradas} paradas`;
  ida.bindTooltip(tip, {sticky: true}); vuelta.bindTooltip(tip + "<br><i>vuelta en vacío</i>", {sticky: true});
  ida.on("click", () => seleccionar(r.nombre)); vuelta.on("click", () => seleccionar(r.nombre));
  capas[r.nombre] = {base: L.layerGroup([ida, vuelta, ...puntos]), detalle: L.layerGroup([...flechas, ...nodos]), ida, vuelta};
}
for (const a of PLAN.almacenes || []) {
  L.marker([a.lat, a.lon], {zIndexOffset: 500, icon: L.divIcon({className: "",
      html: `<div class="almacen-ico" style="border-color:${COL_PLANTA[a.planta] || "#111"}"></div>`, iconSize: [12, 12], iconAnchor: [6, 6]})})
   .bindTooltip(esc(a.nombre) + (a.poligono ? " · " + esc(a.poligono) : ""))
   .bindPopup(`<b>${esc(a.nombre)}</b> <span style="color:#6b7280">(${esc(a.id)})</span><br>${a.poligono ? esc(a.poligono) + (a.municipio ? " (" + esc(a.municipio) + ")" : "") + "<br>" : ""}Lo repone ${esc(a.planta)}<br>` +
              `${a.tiendas} tiendas · ${num(a.kg)} / ${num(a.capacidad)} kg/semana (${num(100 * a.kg / a.capacidad)} %)`)
   .addTo(map);
}
for (const p of PLAN.plantas) {
  L.marker([p.lat, p.lon], {zIndexOffset: 1000, icon: L.divIcon({className: "",
      html: `<div class="planta-ico" style="background:${COL_PLANTA[p.id] || "#111"}">■ ${esc(p.id.replace("PL-", ""))}</div>`, iconSize: null, iconAnchor: [20, 10]})})
   .bindPopup(`<b>${esc(p.nombre)}</b><br>${p.almacenes ?? "–"} almacenes · ${p.tiendas} tiendas<br>${num(p.kg)} / ${num(p.capacidad)} kg (${num(100 * p.kg / p.capacidad)} %)`)
   .on("click", () => filtrar("planta", p.id)).addTo(map);
}
const leyenda = L.control({position: "bottomright"});
leyenda.onAdd = () => { const d = L.DomUtil.create("div", "leyenda");
  d.innerHTML = `<span class="planta-ico" style="background:#333">■ planta</span> &nbsp;<span class="almacen-ico" style="display:inline-block;border-color:#333"></span> almacén &nbsp;○ tienda<br>` +
    `<b>línea gruesa</b> = troncal (color de la planta) · fina = reparto · discontinua = vuelta · trazado por carretera (OSRM)<br>Clic en una ruta para ver el orden de paradas` +
    ((PLAN.avisos || []).length ? `<br><span style="color:#b42318">${PLAN.avisos.length} tiendas fuera del VRP (islas)</span>` : "");
  return d; };
leyenda.addTo(map);

function decodificar(s) {  // polyline de Google, precisión 5 -> [[lat, lon], ...]
  const pts = []; let i = 0, lat = 0, lon = 0;
  while (i < s.length) {
    for (let eje = 0; eje < 2; eje++) {
      let res = 0, shift = 0, b;
      do { b = s.charCodeAt(i++) - 63; res |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
      const v = res & 1 ? ~(res >> 1) : res >> 1;
      if (eje === 0) lat += v; else lon += v;
    }
    pts.push([lat / 1e5, lon / 1e5]);
  }
  return pts;
}
function flecha(tramo, color) {  // flecha a mitad del tramo, orientada en el sentido de la marcha
  const k = Math.max(0, Math.floor((tramo.length - 1) / 2));
  const a = tramo[k], b = tramo[Math.min(k + 1, tramo.length - 1)];
  const m = tramo.length > 2 ? a : [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const ang = Math.atan2(b[0] - a[0], (b[1] - a[1]) * Math.cos(a[0] * Math.PI / 180)) * 180 / Math.PI;
  return L.marker(m, {interactive: false, icon: L.divIcon({className: "", iconSize: [14, 14], iconAnchor: [7, 7],
    html: `<svg width="14" height="14" viewBox="0 0 14 14" style="transform:rotate(${-ang}deg)"><path d="M2 2 L12 7 L2 12 Z" fill="${color}"/></svg>`})});
}
function popupParada(r, p, k) {
  return `<b>${k + 1}. ${esc(p.nombre)}</b><br>${esc(p.municipio)} · ${esc(p.tienda)}<br>` +
    `Ruta <b>${esc(r.nombre)}</b> (${VEH[r.tipo] || r.tipo})<br>Llega ${p.llegada} · descarga ${p.inicio_descarga}<br>` +
    `${num(p.kg)} kg · ${p.cajas} cajas · ventana ${esc(p.ventana)}` + (p.a_tiempo ? "" : `<br><b style="color:#b42318">Fuera de ventana</b>`);
}

// ---------- filtros y lista ----------
const hayEscalones = rutas.some(r => r.escalon === "troncal");
document.getElementById("filtros").innerHTML =
  (hayEscalones ? `<span class="chip on" data-k="escalon" data-v="">Todo</span>` +
    ["troncal", "reparto"].map(e => `<span class="chip" data-k="escalon" data-v="${e}">${e === "troncal" ? "Troncal" : "Reparto"} (${rutas.filter(r => r.escalon === e).length})</span>`).join("") +
    `<span style="flex-basis:100%;height:0"></span>` : "") +
  `<span class="chip on" data-k="planta" data-v="">Todas las plantas</span>` +
  PLAN.plantas.map(p => `<span class="chip" data-k="planta" data-v="${p.id}">${esc(p.id.replace("PL-", ""))}</span>`).join("");
document.getElementById("filtros").onclick = e => { const k = e.target.dataset.k; if (k) filtrar(k, e.target.dataset.v || null); };

function pintarLista() {
  const cont = document.getElementById("rutas"); cont.innerHTML = "";
  let grupo = null;
  for (const r of rutas.filter(visible)) {
    const g = r.escalon + "|" + r.origen;
    if (g !== grupo) {
      grupo = g;
      const o = lugares[r.origen], n = rutas.filter(x => x.escalon === r.escalon && x.origen === r.origen).length;
      const extra = r.escalon === "reparto" ? ` <span style="text-transform:none">← ${esc(r.planta.replace("PL-", ""))}</span>` : "";
      cont.insertAdjacentHTML("beforeend", `<div class="grupo">${r.escalon === "troncal" ? "Troncal · " : ""}${esc(o ? o.nombre : r.origen)}${extra} · ${n} ruta${n > 1 ? "s" : ""}</div>`);
    }
    const sel = seleccion === r.nombre;
    cont.insertAdjacentHTML("beforeend", `
      <div class="ruta ${sel ? "sel" : ""}" data-r="${esc(r.nombre)}" style="border-left-color:${r.color}">
        <div class="t"><b>${esc(r.nombre)}</b><span>${eur(r.coste_eur)}</span></div>
        <div class="s">${VEH[r.tipo] || r.tipo} · ${esc(r.operador)} · ${r.salida} → ${r.vuelta}${r.noches ? ` · ${r.noches} noche${r.noches > 1 ? "s" : ""}` : ""}</div>
        <div class="s">${r.n_paradas} paradas · ${num(r.km)} km · ${num(r.kg)} kg · ocupación ${num(100 * r.ocupacion_cajas)} %</div>
        <div class="bar"><i style="width:${Math.min(100, 100 * r.ocupacion_cajas)}%;background:${r.color}"></i></div>
        ${sel ? `<ol>${r.paradas.map(x => `<li class="${x.a_tiempo ? "" : "tarde"}">${esc(x.nombre)} <span class="s">· ${x.llegada} · ${num(x.kg)} kg</span></li>`).join("")}</ol>` : ""}
      </div>`);
  }
  cont.querySelectorAll(".ruta").forEach(el => el.onclick = () => seleccionar(el.dataset.r));
}

// ---------- cronograma ----------
function pintarGantt() {
  const visibles = rutas.filter(visible);
  const finMin = Math.max(5 * 1440, ...visibles.map(r => r.t_vuelta));
  const dias = Math.ceil(finMin / 1440), total = dias * 1440;
  const pct = m => (100 * m / total) + "%";
  let h = `<h2>Cronograma semanal (barra = vehículo fuera de su planta/almacén, zonas sombreadas = noche)</h2>`;
  let eje = "", noches = `<div class="g-night" style="left:0;width:${pct(360)}"></div>`;
  for (let d = 0; d < dias; d++) {
    eje += `<span style="left:${pct(d * 1440)}">${DIAS[d % 7]}</span>`;
    const ini = d * 1440 + 1140;  // 19:00 -> 06:00
    noches += `<div class="g-night" style="left:${pct(ini)};width:${pct(Math.min(660, total - ini))}"></div>`;
  }
  h += `<div class="g-axis"><div></div><div class="g-days">${eje}</div></div>`;
  for (const r of visibles) {
    const dim = seleccion && seleccion !== r.nombre ? "dim" : "";
    h += `<div class="g-row ${dim}" data-r="${esc(r.nombre)}" title="${esc(r.nombre)} · ${r.salida} → ${r.vuelta}">
      <div class="g-lbl">${esc(r.nombre)}</div>
      <div class="g-track">${noches}<div class="g-bar" style="left:${pct(r.t_salida)};width:${pct(Math.max(20, r.t_vuelta - r.t_salida))};background:${r.color}"></div></div></div>`;
  }
  const g = document.getElementById("gantt"); g.innerHTML = h;
  g.querySelectorAll(".g-row").forEach(el => el.onclick = () => seleccionar(el.dataset.r));
}

// ---------- interacción ----------
function pintarMapa(encuadrar) {
  const b = L.latLngBounds([]);
  for (const r of rutas) {
    const c = capas[r.nombre];
    const vis = visible(r);
    const activa = !seleccion || seleccion === r.nombre;
    if (vis) { c.base.addTo(map); } else { c.base.remove(); }
    const w = r.escalon === "troncal" ? 5 : 3;
    c.ida.setStyle({opacity: activa ? .85 : .12, weight: seleccion === r.nombre ? w + 2 : w});
    c.vuelta.setStyle({opacity: activa ? .6 : .08});
    c.base.eachLayer(l => l instanceof L.CircleMarker && l.setStyle({opacity: activa ? 1 : .2, fillOpacity: activa ? 1 : .2}));
    if (seleccion === r.nombre) { c.detalle.addTo(map); c.ida.bringToFront(); } else c.detalle.remove();
    if (vis && activa) { b.extend(c.ida.getBounds()); }
  }
  if (encuadrar && b.isValid()) map.fitBounds(b, {padding: [30, 30], maxZoom: 12});
}
function seleccionar(nombre) {
  seleccion = seleccion === nombre ? null : nombre;
  if (seleccion) {
    const r = rutas.find(x => x.nombre === seleccion);
    if (!visible(r)) { filtroPlanta = null; filtroEscalon = null; marcarChips(); }
  }
  refrescar(true);
  const el = document.querySelector(`.ruta[data-r="${CSS.escape(nombre)}"]`); if (el && seleccion) el.scrollIntoView({block: "nearest"});
}
function marcarChips() {
  document.querySelectorAll(".chip").forEach(c => {
    const v = c.dataset.v || null;
    c.classList.toggle("on", c.dataset.k === "planta" ? v === filtroPlanta : v === filtroEscalon);
  });
}
function filtrar(k, v) {
  if (k === "planta") filtroPlanta = v; else filtroEscalon = v;
  seleccion = null; marcarChips(); refrescar(true);
}
function refrescar(encuadrar) { pintarLista(); pintarGantt(); pintarMapa(encuadrar); }
refrescar(true);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    semana = sys.argv[1] if len(sys.argv) > 1 else None
    if not semana:
        sys.exit("Uso: python -m modare.visualizar 2026-W47")
    from .planificador import cargar_plan, guardar
    plan = cargar_plan(semana)
    if plan is None:
        sys.exit(f"No hay plan para {semana} en {C.SALIDAS_DIR}")
    if any(not r.get("geometria") for r in plan["rutas"]):
        from .osrm import añadir_geometrias
        añadir_geometrias(plan)
    guardar(plan)
    print(f"Escrito {C.SALIDAS_DIR / semana / 'mapa.html'}")
