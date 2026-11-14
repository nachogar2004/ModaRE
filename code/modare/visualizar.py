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
  main { display:grid; grid-template-columns: 360px 1fr; height: calc(100vh - 52px - 40px - 260px); min-height:380px; }
  /* pestañas y analíticas */
  :root { --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; }
  nav.tabs { display:flex; gap:2px; padding:0 12px; background:var(--panel); border-bottom:1px solid var(--line); overflow-x:auto; }
  nav.tabs button { border:0; background:none; padding:10px 14px 8px; font:inherit; color:var(--muted); cursor:pointer; border-bottom:2px solid transparent; white-space:nowrap; }
  nav.tabs button:hover { color:var(--ink); }
  nav.tabs button.on { color:var(--ink); border-bottom-color:var(--accent); font-weight:600; }
  .tab { display:none; } .tab.on { display:block; }
  .panel { padding:14px 16px 32px; max-width:1500px; margin:0 auto; }
  .panel h2 { font-size:16px; margin:0 0 4px; } .panel .sub { color:var(--muted); margin:0 0 12px; font-size:13px; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; margin-bottom:14px; }
  .tile { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
  .tile .k { color:var(--muted); font-size:12px; } .tile .v { font-size:20px; font-weight:600; font-variant-numeric:tabular-nums; margin-top:2px; }
  .tile .d { color:var(--muted); font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .ctrl { display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; margin-bottom:10px; font-size:13px; }
  .ctrl label { color:var(--muted); } .ctrl select, .ctrl input, .ctrl button { font:inherit; padding:4px 8px; border:1px solid var(--line); border-radius:6px; background:#fff; color:var(--ink); }
  .ctrl button { cursor:pointer; } .ctrl button:hover { background:#faf9f6; }
  .tabla-wrap { background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:auto; max-height:calc(100vh - 330px); min-height:240px; }
  .card .tabla-wrap { max-height:none; min-height:0; border:0; }
  table.t { border-collapse:collapse; width:100%; font-size:13px; }
  table.t th { position:sticky; top:0; z-index:1; background:#faf9f6; text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); cursor:pointer; white-space:nowrap; user-select:none; font-weight:600; color:#3b3f45; }
  table.t th.n, table.t td.n { text-align:right; font-variant-numeric:tabular-nums; }
  table.t td { padding:5px 8px; border-bottom:1px solid #f1efea; white-space:nowrap; }
  table.t tfoot td { position:sticky; bottom:0; background:#faf9f6; font-weight:600; border-top:1px solid var(--line); }
  table.t tbody tr:hover { background:#faf9f6; } table.t tr.click { cursor:pointer; }
  table.t th[data-dir="asc"]::after { content:" ▲"; font-size:10px; } table.t th[data-dir="desc"]::after { content:" ▼"; font-size:10px; }
  .m { color:var(--muted); font-size:12px; }
  .stk { display:flex; gap:2px; height:10px; width:160px; } .stk i { display:block; height:100%; min-width:0; }
  .stk i:first-child { border-radius:4px 0 0 4px; } .stk i:last-child { border-radius:0 4px 4px 0; } .stk i:only-child { border-radius:4px; }
  .ley { display:inline-flex; flex-wrap:wrap; gap:4px 12px; font-size:12px; color:var(--muted); }
  .ley i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; vertical-align:-1px; }
  .nota { color:var(--muted); font-size:12px; margin:8px 0 0; line-height:1.5; }
  .grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr)); gap:14px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; min-width:0; }
  .card h3 { margin:0 0 8px; font-size:14px; display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; }
  .aviso { color:#b42318; }
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
<nav class="tabs" id="tabs">
  <button data-tab="mapa" class="on">Mapa y cronograma</button>
  <button data-tab="tiendas">Coste por tienda</button>
  <button data-tab="rutas-a">Rutas</button>
  <button data-tab="almacenes">Almacenes</button>
  <button data-tab="resumen">Resumen de costes</button>
</nav>
<div class="tab on" id="tab-mapa">
<main>
  <div id="lista"><div class="filtros" id="filtros"></div><div id="rutas"></div></div>
  <div id="map"></div>
</main>
<section id="gantt"></section>
</div>
<div class="tab" id="tab-tiendas"><div class="panel">
  <h2>Coste de llevar la ropa a cada tienda</h2>
  <p class="sub">Coste por tienda = su parte del <b>troncal</b> (planta → almacén) + su parte del <b>reparto</b> (almacén → tienda). Clic en una fila para ver su ruta de reparto en el mapa.</p>
  <div class="ctrl" id="ctrl-tiendas"></div>
  <div class="tiles" id="tiles-tiendas"></div>
  <div class="tabla-wrap" id="tabla-tiendas"></div>
  <p class="nota" id="nota-tiendas"></p>
</div></div>
<div class="tab" id="tab-rutas-a"><div class="panel">
  <h2>Coste por ruta</h2>
  <p class="sub">Coste facturable de cada ruta (simulación exacta) y de qué se compone. Clic en una fila para verla en el mapa.</p>
  <div class="ctrl" id="ctrl-rutas"></div>
  <div class="tiles" id="tiles-rutas"></div>
  <div class="tabla-wrap" id="tabla-rutas"></div>
</div></div>
<div class="tab" id="tab-almacenes"><div class="panel">
  <h2>Almacenes (cross-dock)</h2>
  <p class="sub">Lo que cuesta reponer cada almacén desde su planta y repartir desde él a sus tiendas. No incluye el coste de operar el almacén (hoy 0).</p>
  <div class="ctrl" id="ctrl-almacenes"></div>
  <div class="tabla-wrap" id="tabla-almacenes"></div>
</div></div>
<div class="tab" id="tab-resumen"><div class="panel">
  <h2>Resumen de costes</h2>
  <p class="sub">Dónde se va el dinero: por escalón, componente de la tarifa, tipo de vehículo, operador y planta.</p>
  <div class="tiles" id="tiles-resumen"></div>
  <div class="grid2" id="cards-resumen"></div>
</div></div>

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

// =====================================================================
// ANALÍTICAS (pestañas)
// =====================================================================
const eur2 = (n, d = 2) => n.toLocaleString("es-ES", {minimumFractionDigits: d, maximumFractionDigits: d}) + " €";
const pctf = (n, d = 0) => num(100 * n, d) + " %";
const sum = (xs, f = x => x) => xs.reduce((s, x) => s + (f(x) || 0), 0);
const nombreLugar = id => (lugares[id] && lugares[id].nombre) || id;
const corto = id => String(id || "").replace(/^(PL|AL)-/, "");
const COMP = [["km", "Kilómetros", "var(--s1)"], ["horas", "Horas", "var(--s2)"], ["fijo", "Fijo/día", "var(--s3)"], ["pernoctas", "Pernoctas", "var(--s4)"]];
const ESCC = [["troncal", "Troncal", "var(--s1)"], ["reparto", "Reparto", "var(--s2)"]];
function hav(a, b) {  // km en línea recta
  const R = 6371, rad = Math.PI / 180, dLa = (b.lat - a.lat) * rad, dLo = (b.lon - a.lon) * rad;
  const h = Math.sin(dLa / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLo / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
function cuantil(xs, q) { if (!xs.length) return 0; const s = [...xs].sort((a, b) => a - b); const i = (s.length - 1) * q, lo = Math.floor(i);
  return s[lo] + (s[Math.min(lo + 1, s.length - 1)] - s[lo]) * (i - lo); }
function barra(partes, max) {  // partes: [[valor, color, etiqueta]]
  const tot = sum(partes, p => p[0]); if (!tot || !max) return `<div class="stk"></div>`;
  const ancho = 160 * tot / max;
  return `<div class="stk" style="width:${Math.max(2, ancho)}px" title="${esc(partes.filter(p => p[0] > 0).map(p => `${p[2]}: ${eur(p[0])} (${pctf(p[0] / tot)})`).join(" · "))}">` +
    partes.filter(p => p[0] > 0).map(p => `<i style="flex:${p[0]};background:${p[1]}"></i>`).join("") + `</div>`;
}
const leyenda_ = items => `<span class="ley">${items.map(([, t, c]) => `<span><i style="background:${c}"></i>${t}</span>`).join("")}</span>`;

// ---------- tablas ordenables ----------
const ORDEN = {};
function pintarTabla(id, cols, filas, {onRow, pie, redraw, orden} = {}) {
  const st = ORDEN[id] || (ORDEN[id] = orden ? {...orden} : {k: null, dir: -1});
  if (st.k) {
    const c = cols.find(c => c.k === st.k), v = c.v || (r => r[c.k]);
    filas = [...filas].sort((a, b) => { const x = v(a), y = v(b);
      return (typeof x === "string" ? String(x).localeCompare(String(y), "es") : (x ?? -Infinity) - (y ?? -Infinity)) * st.dir; });
  }
  let h = `<table class="t"><thead><tr>${cols.map(c => `<th class="${c.n ? "n" : ""}" data-k="${c.k}"` +
    `${st.k === c.k ? ` data-dir="${st.dir > 0 ? "asc" : "desc"}"` : ""}${c.title ? ` title="${esc(c.title)}"` : ""}>${c.t}</th>`).join("")}</tr></thead><tbody>`;
  filas.forEach((r, i) => { h += `<tr class="${onRow ? "click" : ""}" data-i="${i}">${cols.map(c => `<td class="${c.n ? "n" : ""}">${c.f ? c.f(r) : esc(r[c.k] ?? "")}</td>`).join("")}</tr>`; });
  h += `</tbody>${pie ? `<tfoot><tr>${pie}</tr></tfoot>` : ""}</table>`;
  const el = document.getElementById(id); el.innerHTML = h;
  el.querySelectorAll("th").forEach(th => th.onclick = () => {
    const k = th.dataset.k; if (k === "_") return;
    if (st.k === k) st.dir = -st.dir; else { st.k = k; st.dir = cols.find(c => c.k === k).n ? -1 : 1; }
    redraw && redraw();
  });
  if (onRow) el.querySelectorAll("tbody tr").forEach(tr => tr.onclick = () => onRow(filas[+tr.dataset.i]));
}
function descargarCSV(nombre, cabecera, filas) {
  const q = v => typeof v === "number" ? String(v).replace(".", ",") : `"${String(v ?? "").replace(/"/g, '""')}"`;
  const txt = "﻿" + [cabecera.map(q).join(";"), ...filas.map(f => f.map(q).join(";"))].join("\r\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], {type: "text/csv"}));
  a.download = nombre; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function irAMapa(nombreRuta) {
  mostrarTab("mapa");
  filtroPlanta = null; filtroEscalon = null; marcarChips(); seleccion = null;
  seleccionar(nombreRuta);
}

// ---------- reparto del coste de una ruta entre sus paradas ----------
// "kg": a cada parada le toca coste × kg_parada / kg_ruta.
// "kgkm": ponderado por kg × distancia en línea recta desde el depósito (las paradas lejanas pagan más).
let MODO = "kg";
function cuotas(r) {
  const o = lugares[r.origen];
  const w = r.paradas.map(p => MODO === "kgkm" && o ? p.kg * Math.max(1, hav(o, p)) : p.kg);
  const W = sum(w);
  return w.map(x => W ? r.coste_eur * x / W : r.coste_eur / r.paradas.length);
}
const tro = rutas.filter(r => r.escalon === "troncal"), rep = rutas.filter(r => r.escalon === "reparto");
function calcular() {
  const alm = {};
  for (const a of PLAN.almacenes || []) alm[a.id] = {...a, kgRecibido: 0, cTro: 0, rutasTro: new Set(), kgRep: 0, cRep: 0, kmRep: 0, rutasRep: [], tiendasSet: new Set()};
  const getAlm = id => alm[id] || (alm[id] = {id, nombre: nombreLugar(id), planta: "", kg: 0, capacidad: 0, kgRecibido: 0, cTro: 0, rutasTro: new Set(), kgRep: 0, cRep: 0, kmRep: 0, rutasRep: [], tiendasSet: new Set()});
  for (const r of tro) {
    const c = cuotas(r);
    r.paradas.forEach((p, i) => { const a = getAlm(p.tienda); a.kgRecibido += p.kg; a.cTro += c[i]; a.rutasTro.add(r.nombre); });
  }
  const tiendas = {};
  for (const r of rep) {
    const c = cuotas(r), a = getAlm(r.origen);
    a.cRep += r.coste_eur; a.kmRep += r.km; a.rutasRep.push(r.nombre);
    r.paradas.forEach((p, i) => {
      const t = tiendas[p.tienda] || (tiendas[p.tienda] = {id: p.tienda, nombre: p.nombre, municipio: p.municipio, lat: p.lat, lon: p.lon,
        almacen: r.origen, planta: r.planta, kg: 0, visitas: 0, cRep: 0, rutas: [], tarde: 0});
      t.kg += p.kg; t.visitas += 1; t.cRep += c[i]; if (!t.rutas.includes(r.nombre)) t.rutas.push(r.nombre);
      if (!p.a_tiempo) t.tarde += 1;
      a.kgRep += p.kg; a.tiendasSet.add(p.tienda);
    });
  }
  for (const a of Object.values(alm)) {
    a.eurKgTro = a.kgRecibido ? a.cTro / a.kgRecibido : 0;
    a.eurKgRep = a.kgRep ? a.cRep / a.kgRep : 0;
    a.total = a.cTro + a.cRep;
    a.eurKg = a.eurKgTro + a.eurKgRep;
    a.nTiendas = a.tiendasSet.size;
    a.uso = a.capacidad ? a.kg / a.capacidad : 0;
  }
  for (const t of Object.values(tiendas)) {
    const a = alm[t.almacen];
    t.eurKgTro = a ? a.eurKgTro : 0;
    t.cTro = t.kg * t.eurKgTro;          // el almacén es un depósito con stock: se reparte su €/kg troncal
    t.total = t.cTro + t.cRep;
    t.eurKg = t.kg ? t.total / t.kg : 0;
    t.dist = a ? hav(a, t) : 0;
  }
  return {alm, tiendas: Object.values(tiendas)};
}
let CALC = calcular();

// ---------- pestaña: coste por tienda ----------
const FT = {planta: "", almacen: "", q: ""};
function opcionesSel(lista, sel, todos) { return `<option value="">${todos}</option>` + lista.map(([v, t]) => `<option value="${esc(v)}"${v === sel ? " selected" : ""}>${esc(t)}</option>`).join(""); }
function pintarCtrlTiendas() {
  const plantas = [...new Set(CALC.tiendas.map(t => t.planta))].sort().map(p => [p, nombreLugar(p)]);
  const almacenes = [...new Set(CALC.tiendas.filter(t => !FT.planta || t.planta === FT.planta).map(t => t.almacen))]
    .map(a => [a, nombreLugar(a)]).sort((x, y) => x[1].localeCompare(y[1], "es"));
  document.getElementById("ctrl-tiendas").innerHTML =
    `<span><label>Reparto del coste de cada ruta </label><select id="t-modo">
       <option value="kg"${MODO === "kg" ? " selected" : ""}>proporcional a kg</option>
       <option value="kgkm"${MODO === "kgkm" ? " selected" : ""}>proporcional a kg × distancia al depósito</option></select></span>` +
    `<span><label>Planta </label><select id="t-planta">${opcionesSel(plantas, FT.planta, "Todas")}</select></span>` +
    `<span><label>Almacén </label><select id="t-almacen">${opcionesSel(almacenes, FT.almacen, "Todos")}</select></span>` +
    `<span><input id="t-q" type="search" placeholder="Buscar tienda o municipio" value="${esc(FT.q)}"></span>` +
    `<button id="t-csv">Descargar CSV</button>` + leyenda_(ESCC);
  document.getElementById("t-modo").onchange = e => { MODO = e.target.value; CALC = calcular(); pintarAnaliticas(); };
  document.getElementById("t-planta").onchange = e => { FT.planta = e.target.value; FT.almacen = ""; pintarCtrlTiendas(); pintarTiendas(); };
  document.getElementById("t-almacen").onchange = e => { FT.almacen = e.target.value; pintarTiendas(); };
  document.getElementById("t-q").oninput = e => { FT.q = e.target.value; pintarTiendas(); };
  document.getElementById("t-csv").onclick = () => descargarCSV(`coste_tiendas_${R.semana}.csv`,
    ["tienda", "nombre", "municipio", "almacen", "planta", "kg_semana", "visitas", "rutas", "km_almacen_linea_recta", "coste_troncal_eur", "coste_reparto_eur", "coste_total_eur", "eur_por_kg"],
    filtrarTiendas().map(t => [t.id, t.nombre, t.municipio, t.almacen, t.planta, +t.kg.toFixed(1), t.visitas, t.rutas.join(" "), +t.dist.toFixed(1),
      +t.cTro.toFixed(2), +t.cRep.toFixed(2), +t.total.toFixed(2), +t.eurKg.toFixed(4)]));
}
function filtrarTiendas() {
  const q = FT.q.trim().toLowerCase();
  return CALC.tiendas.filter(t => (!FT.planta || t.planta === FT.planta) && (!FT.almacen || t.almacen === FT.almacen) &&
    (!q || (t.nombre + " " + t.municipio + " " + t.id).toLowerCase().includes(q)));
}
function pintarTiendas() {
  const fs = filtrarTiendas(), max = Math.max(0, ...fs.map(t => t.total));
  const ek = fs.map(t => t.eurKg), kg = sum(fs, t => t.kg), tot = sum(fs, t => t.total);
  const cara = fs.reduce((m, t) => !m || t.eurKg > m.eurKg ? t : m, null), barata = fs.reduce((m, t) => !m || t.eurKg < m.eurKg ? t : m, null);
  document.getElementById("tiles-tiendas").innerHTML = [
    ["Tiendas", num(fs.length), `${num(kg)} kg/semana`],
    ["Coste asignado", eur(tot), `${eur(sum(fs, t => t.cTro))} troncal + ${eur(sum(fs, t => t.cRep))} reparto`],
    ["€/kg medio (ponderado)", kg ? eur2(tot / kg, 3) : "–", `mediana ${eur2(cuantil(ek, .5), 3)}`],
    ["Rango €/kg (p10 – p90)", `${eur2(cuantil(ek, .1))} – ${eur2(cuantil(ek, .9))}`, "el 80 % de las tiendas está en este rango"],
    ["Tienda más cara por kg", cara ? eur2(cara.eurKg, 3) : "–", cara ? cara.nombre : ""],
    ["Tienda más barata por kg", barata ? eur2(barata.eurKg, 3) : "–", barata ? barata.nombre : ""],
  ].map(([k, v, d]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d" title="${esc(d)}">${esc(d)}</div></div>`).join("");
  const cols = [
    {k: "nombre", t: "Tienda", f: t => `${esc(t.nombre)} <span class="m">${esc(t.id)}</span>`},
    {k: "municipio", t: "Municipio"},
    {k: "almacen", t: "Almacén", v: t => nombreLugar(t.almacen), f: t => esc(corto(t.almacen))},
    {k: "planta", t: "Planta", f: t => esc(corto(t.planta))},
    {k: "kg", t: "kg/sem", n: 1, f: t => num(t.kg)},
    {k: "visitas", t: "Entregas", n: 1, title: "Entregas en la semana (pasa el ratón para ver las rutas)",
     f: t => `<span title="${esc(t.rutas.join(", "))}">${t.visitas}</span>` + (t.tarde ? ` <span class="aviso" title="entregas fuera de ventana">(${t.tarde} tarde)</span>` : "")},
    {k: "dist", t: "km al almacén", n: 1, title: "Distancia en línea recta almacén → tienda", f: t => num(t.dist)},
    {k: "cTro", t: "Troncal", n: 1, title: "kg de la tienda × €/kg troncal de su almacén", f: t => eur2(t.cTro)},
    {k: "cRep", t: "Reparto", n: 1, title: "Parte de su(s) ruta(s) de reparto", f: t => eur2(t.cRep)},
    {k: "total", t: "Total", n: 1, f: t => `<b>${eur2(t.total)}</b>`},
    {k: "eurKg", t: "€/kg", n: 1, f: t => eur2(t.eurKg, 3)},
    {k: "_", t: "Troncal · Reparto", f: t => barra([[t.cTro, "var(--s1)", "Troncal"], [t.cRep, "var(--s2)", "Reparto"]], max)},
  ];
  const pie = `<td>Total (${fs.length})</td><td></td><td></td><td></td><td class="n">${num(kg)}</td><td class="n">${sum(fs, t => t.visitas)}</td><td></td>` +
    `<td class="n">${eur(sum(fs, t => t.cTro))}</td><td class="n">${eur(sum(fs, t => t.cRep))}</td><td class="n">${eur(tot)}</td><td class="n">${kg ? eur2(tot / kg, 3) : ""}</td><td></td>`;
  pintarTabla("tabla-tiendas", cols, fs, {pie, redraw: pintarTiendas, orden: {k: "eurKg", dir: -1}, onRow: t => irAMapa(t.rutas[0])});
  const totTro = sum(tro, r => r.coste_eur), totRep = sum(rep, r => r.coste_eur);
  const asigTro = sum(CALC.tiendas, t => t.cTro), asigRep = sum(CALC.tiendas, t => t.cRep);
  document.getElementById("nota-tiendas").innerHTML =
    `<b>Cómo se calcula.</b> El coste de cada ruta (troncal o reparto) se reparte entre sus paradas ${MODO === "kg" ? "en proporción a los kg entregados" : "en proporción a kg × distancia en línea recta desde el depósito"}. ` +
    `Como el almacén trabaja con stock, cada almacén tiene un <b>€/kg troncal</b> (coste troncal que le llega ÷ kg recibidos) y cada tienda paga ese €/kg por sus kg. ` +
    `Es una convención de imputación, no una factura: el coste de una ruta compartida no tiene un reparto “verdadero”.<br>` +
    `Cuadre: troncal ${eur(asigTro)} imputado de ${eur(totTro)} · reparto ${eur(asigRep)} de ${eur(totRep)}. ` +
    `No incluye el coste de operar los almacenes.` +
    ((PLAN.avisos || []).length ? ` <span class="aviso">${PLAN.avisos.length} tiendas de islas fuera del VRP (sin coste calculado): ${esc(PLAN.avisos.map(a => a.nombre).join(", "))}.</span>` : "");
}

// ---------- pestaña: rutas ----------
const FR = {escalon: "", planta: "", operador: ""};
function pintarCtrlRutas() {
  const ops = [...new Set(rutas.map(r => r.operador))].sort().map(o => [o, o]);
  document.getElementById("ctrl-rutas").innerHTML =
    `<span><label>Escalón </label><select id="r-esc">${opcionesSel([["troncal", "Troncal"], ["reparto", "Reparto"]], FR.escalon, "Ambos")}</select></span>` +
    `<span><label>Planta </label><select id="r-planta">${opcionesSel(PLAN.plantas.map(p => [p.id, p.nombre]), FR.planta, "Todas")}</select></span>` +
    `<span><label>Operador </label><select id="r-op">${opcionesSel(ops, FR.operador, "Todos")}</select></span>` +
    `<button id="r-csv">Descargar CSV</button>` + leyenda_(COMP);
  document.getElementById("r-esc").onchange = e => { FR.escalon = e.target.value; pintarRutasA(); };
  document.getElementById("r-planta").onchange = e => { FR.planta = e.target.value; pintarRutasA(); };
  document.getElementById("r-op").onchange = e => { FR.operador = e.target.value; pintarRutasA(); };
  document.getElementById("r-csv").onclick = () => descargarCSV(`coste_rutas_${R.semana}.csv`,
    ["ruta", "escalon", "origen", "planta", "tipo", "operador", "salida", "vuelta", "paradas", "km", "horas", "kg", "ocupacion_cajas", "coste_eur", "eur_km", "eur_kg", "coste_km", "coste_horas", "coste_fijo", "coste_pernoctas", "co2_kg"],
    filtrarRutas().map(r => { const d = r.desglose_coste || {}; return [r.nombre, r.escalon, r.origen, r.planta, r.tipo, r.operador, r.salida, r.vuelta, r.n_paradas,
      r.km, r.horas_trabajo, r.kg, r.ocupacion_cajas, r.coste_eur, r.km ? +(r.coste_eur / r.km).toFixed(3) : 0, r.kg ? +(r.coste_eur / r.kg).toFixed(4) : 0,
      d.km || 0, d.horas || 0, d.fijo || 0, d.pernoctas || 0, r.co2_kg]; }));
}
const filtrarRutas = () => rutas.filter(r => (!FR.escalon || r.escalon === FR.escalon) && (!FR.planta || r.planta === FR.planta) && (!FR.operador || r.operador === FR.operador));
function pintarRutasA() {
  const fs = filtrarRutas(), max = Math.max(0, ...fs.map(r => r.coste_eur));
  const c = sum(fs, r => r.coste_eur), km = sum(fs, r => r.km), kg = sum(fs, r => r.kg);
  const cara = fs.reduce((m, r) => !m || r.coste_eur > m.coste_eur ? r : m, null);
  document.getElementById("tiles-rutas").innerHTML = [
    ["Rutas", num(fs.length), `${num(sum(fs, r => r.n_paradas))} paradas`],
    ["Coste", eur(c), `media ${fs.length ? eur(c / fs.length) : "–"} por ruta`],
    ["€/km", km ? eur2(c / km) : "–", `${num(km)} km`],
    ["€/kg movido", kg ? eur2(c / kg, 3) : "–", `${num(kg)} kg`],
    ["Ocupación media (cajas)", fs.length ? pctf(sum(fs, r => r.ocupacion_cajas) / fs.length) : "–", `${num(sum(fs, r => r.noches))} pernoctas`],
    ["Ruta más cara", cara ? eur(cara.coste_eur) : "–", cara ? `${cara.nombre} · ${VEH[cara.tipo] || cara.tipo}` : ""],
  ].map(([k, v, d]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d" title="${esc(d)}">${esc(d)}</div></div>`).join("");
  const dz = (r, k) => (r.desglose_coste || {})[k] || 0;
  const cols = [
    {k: "nombre", t: "Ruta", f: r => `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${r.color};margin-right:5px"></span><b>${esc(r.nombre)}</b>`},
    {k: "escalon", t: "Escalón", f: r => r.escalon === "troncal" ? "Troncal" : "Reparto"},
    {k: "origen", t: "Origen", v: r => nombreLugar(r.origen), f: r => esc(corto(r.origen))},
    {k: "tipo", t: "Vehículo", f: r => esc(VEH[r.tipo] || r.tipo)},
    {k: "operador", t: "Operador", f: r => esc(r.operador.replace("Operador ", ""))},
    {k: "t_salida", t: "Salida → vuelta", n: 1, f: r => `${r.salida} → ${r.vuelta}`},
    {k: "n_paradas", t: "Paradas", n: 1},
    {k: "km", t: "km", n: 1, f: r => num(r.km)},
    {k: "horas_trabajo", t: "Horas", n: 1, f: r => num(r.horas_trabajo, 1)},
    {k: "kg", t: "kg", n: 1, f: r => num(r.kg)},
    {k: "ocupacion_cajas", t: "Ocup.", n: 1, title: "Ocupación en cajas", f: r => pctf(r.ocupacion_cajas)},
    {k: "coste_eur", t: "Coste", n: 1, f: r => `<b>${eur2(r.coste_eur)}</b>`},
    {k: "ekm", t: "€/km", n: 1, v: r => r.km ? r.coste_eur / r.km : 0, f: r => r.km ? eur2(r.coste_eur / r.km) : "–"},
    {k: "ekg", t: "€/kg", n: 1, v: r => r.kg ? r.coste_eur / r.kg : 0, f: r => r.kg ? eur2(r.coste_eur / r.kg, 3) : "–"},
    {k: "co2_kg", t: "CO₂ kg", n: 1, f: r => num(r.co2_kg)},
    {k: "_", t: "Desglose del coste", f: r => barra(COMP.map(([k, t, col]) => [dz(r, k), col, t]), max)},
  ];
  const pie = `<td>Total (${fs.length})</td><td></td><td></td><td></td><td></td><td></td><td class="n">${sum(fs, r => r.n_paradas)}</td><td class="n">${num(km)}</td>` +
    `<td class="n">${num(sum(fs, r => r.horas_trabajo))}</td><td class="n">${num(kg)}</td><td></td><td class="n">${eur(c)}</td><td class="n">${km ? eur2(c / km) : ""}</td>` +
    `<td class="n">${kg ? eur2(c / kg, 3) : ""}</td><td class="n">${num(sum(fs, r => r.co2_kg))}</td><td></td>`;
  pintarTabla("tabla-rutas", cols, fs, {pie, redraw: pintarRutasA, orden: {k: "coste_eur", dir: -1}, onRow: r => irAMapa(r.nombre)});
}

// ---------- pestaña: almacenes ----------
let FA = "";
function pintarAlmacenes() {
  document.getElementById("ctrl-almacenes").innerHTML =
    `<span><label>Planta </label><select id="a-planta">${opcionesSel(PLAN.plantas.map(p => [p.id, p.nombre]), FA, "Todas")}</select></span>` + leyenda_(ESCC) +
    `<span class="m">€/kg según el método elegido en “Coste por tienda”.</span>`;
  document.getElementById("a-planta").onchange = e => { FA = e.target.value; pintarAlmacenes(); };
  const fs = Object.values(CALC.alm).filter(a => (a.kgRecibido || a.kgRep) && (!FA || a.planta === FA));
  const max = Math.max(0, ...fs.map(a => a.total));
  const cols = [
    {k: "nombre", t: "Almacén", f: a => `${esc(a.nombre)} <span class="m">${esc(a.id)}</span>`},
    {k: "planta", t: "Planta", f: a => esc(corto(a.planta))},
    {k: "nTiendas", t: "Tiendas", n: 1},
    {k: "kg", t: "kg/sem", n: 1, f: a => num(a.kgRep || a.kg)},
    {k: "uso", t: "Uso capacidad", n: 1, f: a => a.capacidad ? `${pctf(a.uso)} <span class="m">de ${num(a.capacidad)}</span>` : "–"},
    {k: "nTro", t: "Rutas troncal", n: 1, v: a => a.rutasTro.size, f: a => `${a.rutasTro.size} <span class="m">${esc([...a.rutasTro].join(", "))}</span>`},
    {k: "nRep", t: "Rutas reparto", n: 1, v: a => a.rutasRep.length, f: a => a.rutasRep.length},
    {k: "kmRep", t: "km reparto", n: 1, f: a => num(a.kmRep)},
    {k: "cTro", t: "Coste troncal", n: 1, f: a => eur2(a.cTro)},
    {k: "cRep", t: "Coste reparto", n: 1, f: a => eur2(a.cRep)},
    {k: "total", t: "Total", n: 1, f: a => `<b>${eur2(a.total)}</b>`},
    {k: "eurKgTro", t: "€/kg troncal", n: 1, f: a => eur2(a.eurKgTro, 3)},
    {k: "eurKgRep", t: "€/kg reparto", n: 1, f: a => eur2(a.eurKgRep, 3)},
    {k: "eurKg", t: "€/kg total", n: 1, f: a => `<b>${eur2(a.eurKg, 3)}</b>`},
    {k: "_", t: "Troncal · Reparto", f: a => barra([[a.cTro, "var(--s1)", "Troncal"], [a.cRep, "var(--s2)", "Reparto"]], max)},
  ];
  const kg = sum(fs, a => a.kgRep), ct = sum(fs, a => a.cTro), cr = sum(fs, a => a.cRep);
  const pie = `<td>Total (${fs.length})</td><td></td><td class="n">${sum(fs, a => a.nTiendas)}</td><td class="n">${num(kg)}</td><td></td>` +
    `<td class="n">${new Set(fs.flatMap(a => [...a.rutasTro])).size}</td><td class="n">${sum(fs, a => a.rutasRep.length)}</td><td class="n">${num(sum(fs, a => a.kmRep))}</td>` +
    `<td class="n">${eur(ct)}</td><td class="n">${eur(cr)}</td><td class="n">${eur(ct + cr)}</td><td></td><td></td><td class="n">${kg ? eur2((ct + cr) / kg, 3) : ""}</td><td></td>`;
  pintarTabla("tabla-almacenes", cols, fs, {pie, redraw: pintarAlmacenes, orden: {k: "eurKg", dir: -1},
    onRow: a => { mostrarTab("tiendas"); FT.planta = ""; FT.almacen = a.id; FT.q = ""; pintarCtrlTiendas(); pintarTiendas(); }});
}

// ---------- pestaña: resumen ----------
function agrupar(xs, clave) {
  const g = {};
  for (const r of xs) {
    const k = clave(r), o = g[k] || (g[k] = {k, rutas: 0, km: 0, kg: 0, coste: 0, ocup: 0, horas: 0, co2: 0, rs: []});
    o.rutas++; o.km += r.km; o.kg += r.kg; o.coste += r.coste_eur; o.ocup += r.ocupacion_cajas; o.horas += r.horas_trabajo; o.co2 += r.co2_kg; o.rs.push(r);
  }
  return Object.values(g);
}
function camionesDe(filtro) { return sum((PLAN.flota || []).filter(filtro), f => f.camiones); }
function pintarResumen() {
  const cT = sum(tro, r => r.coste_eur), cR = sum(rep, r => r.coste_eur), c = cT + cR;
  const kgT = sum(CALC.tiendas, t => t.kg), km = sum(rutas, r => r.km);
  document.getElementById("tiles-resumen").innerHTML = [
    ["Coste total semana", eur(c), `${num(rutas.length)} rutas · ${R.camiones} camiones`],
    ["Troncal (planta → almacén)", eur(cT), `${pctf(cT / c)} del total · ${tro.length} rutas`],
    ["Reparto (almacén → tienda)", eur(cR), `${pctf(cR / c)} del total · ${rep.length} rutas`],
    ["€ por kg entregado", kgT ? eur2(c / kgT, 3) : "–", `${num(kgT)} kg a tiendas`],
    ["€ por km", km ? eur2(c / km) : "–", `${num(km)} km`],
    ["Coste medio por tienda", CALC.tiendas.length ? eur(c / CALC.tiendas.length) : "–", `${CALC.tiendas.length} tiendas servidas`],
  ].map(([k, v, d]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${esc(d)}</div></div>`).join("");

  const cards = [];
  // 1. componentes de tarifa por escalón
  const comp = [["troncal", tro], ["reparto", rep], ["total", rutas]].map(([k, xs]) => {
    const o = {k, total: sum(xs, r => r.coste_eur)}; for (const [ck] of COMP) o[ck] = sum(xs, r => (r.desglose_coste || {})[ck]); return o; });
  const maxC = Math.max(...comp.map(o => o.total));
  cards.push(["Coste por componente de la tarifa", leyenda_(COMP), "res-comp", [
    {k: "k", t: "Escalón", f: o => o.k === "total" ? "<b>Total</b>" : (o.k === "troncal" ? "Troncal" : "Reparto")},
    ...COMP.map(([ck, t]) => ({k: ck, t, n: 1, f: o => `${eur(o[ck])} <span class="m">${o.total ? pctf(o[ck] / o.total) : ""}</span>`})),
    {k: "total", t: "Total", n: 1, f: o => `<b>${eur(o.total)}</b>`},
    {k: "_", t: "", f: o => barra(COMP.map(([ck, t, col]) => [o[ck], col, t]), maxC)},
  ], comp, null]);
  // 2. por tipo de vehículo
  const ordenVeh = Object.keys(VEH);
  const veh = agrupar(rutas, r => r.escalon + "|" + r.tipo).map(o => { const [e, t] = o.k.split("|");
    return {...o, escalon: e, tipo: t, camiones: camionesDe(f => f.escalon === e && f.tipo === t)}; })
    .sort((a, b) => a.escalon.localeCompare(b.escalon) || ordenVeh.indexOf(a.tipo) - ordenVeh.indexOf(b.tipo));
  const colsGrupo = (primera) => [primera,
    {k: "rutas", t: "Rutas", n: 1}, {k: "km", t: "km", n: 1, f: o => num(o.km)}, {k: "kg", t: "kg", n: 1, f: o => num(o.kg)},
    {k: "ocupm", t: "Ocup. media", n: 1, v: o => o.ocup / o.rutas, f: o => pctf(o.ocup / o.rutas)},
    {k: "coste", t: "Coste", n: 1, f: o => `<b>${eur(o.coste)}</b>`},
    {k: "ekm", t: "€/km", n: 1, v: o => o.km ? o.coste / o.km : 0, f: o => o.km ? eur2(o.coste / o.km) : "–"},
    {k: "ekg", t: "€/kg", n: 1, v: o => o.kg ? o.coste / o.kg : 0, f: o => o.kg ? eur2(o.coste / o.kg, 3) : "–"}];
  cards.push(["Por tipo de vehículo", "", "res-veh", [
    {k: "tipo", t: "Vehículo", v: o => o.escalon + ordenVeh.indexOf(o.tipo), f: o => `${esc(VEH[o.tipo] || o.tipo)} <span class="m">${o.escalon}</span>`},
    ...colsGrupo({k: "camiones", t: "Camiones", n: 1, title: "Camiones físicos estimados (solapamiento de rutas)"})], veh, null]);
  // 3. por operador
  const ops = agrupar(rutas, r => r.escalon + "|" + r.operador).map(o => { const [e, op] = o.k.split("|"); return {...o, escalon: e, operador: op}; })
    .sort((a, b) => a.escalon.localeCompare(b.escalon) || a.operador.localeCompare(b.operador));
  cards.push(["Por operador", "", "res-op", [
    {k: "operador", t: "Operador", v: o => o.escalon + o.operador, f: o => `${esc(o.operador)} <span class="m">${o.escalon}</span>`},
    ...colsGrupo({k: "horas", t: "Horas", n: 1, f: o => num(o.horas)})], ops, null]);
  // 4. por planta: cadena completa (troncal desde la planta + reparto de los almacenes que repone)
  const pl = PLAN.plantas.map(p => {
    const t = tro.filter(r => r.planta === p.id), rr = rep.filter(r => r.planta === p.id);
    const ts = CALC.tiendas.filter(x => x.planta === p.id), kg = sum(ts, x => x.kg);
    const cT = sum(t, r => r.coste_eur), cR = sum(rr, r => r.coste_eur);
    return {id: p.id, nombre: p.nombre, almacenes: p.almacenes ?? new Set(rr.map(r => r.origen)).size, tiendas: ts.length, kg, uso: p.capacidad ? p.kg / p.capacidad : 0,
      cT, cR, total: cT + cR, eurKg: kg ? (cT + cR) / kg : 0, kmT: sum(t, r => r.km)};
  }).filter(p => p.total > 0);
  const maxP = Math.max(0, ...pl.map(p => p.total));
  cards.push(["Por planta (troncal + reparto de sus almacenes)", leyenda_(ESCC), "res-pl", [
    {k: "nombre", t: "Planta", f: p => `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${COL_PLANTA[p.id] || "#111"};margin-right:5px"></span><span title="${esc(p.nombre)}">${esc(p.nombre.replace(/^Planta /, "").replace(/\s*\(.*\)$/, ""))}</span>`},
    {k: "almacenes", t: "Alm.", n: 1, title: "Almacenes que repone"}, {k: "tiendas", t: "Tiendas", n: 1},
    {k: "kg", t: "kg/sem", n: 1, f: p => num(p.kg)}, {k: "uso", t: "Uso cap.", n: 1, f: p => pctf(p.uso)},
    {k: "cT", t: "Troncal", n: 1, f: p => eur(p.cT)}, {k: "cR", t: "Reparto", n: 1, f: p => eur(p.cR)},
    {k: "total", t: "Total", n: 1, f: p => `<b>${eur(p.total)}</b>`}, {k: "eurKg", t: "€/kg", n: 1, f: p => eur2(p.eurKg, 3)},
    {k: "_", t: "", f: p => barra([[p.cT, "var(--s1)", "Troncal"], [p.cR, "var(--s2)", "Reparto"]], maxP)}], pl, null]);
  // 5. flota
  const flota = (PLAN.flota || []).map(f => ({...f, coste: sum(rutas.filter(r => r.origen === f.origen && r.tarifa === f.tarifa), r => r.coste_eur)}));
  cards.push(["Flota por depósito y tarifa", `<span class="m">${sum(flota, f => f.camiones)} camiones · ${sum(flota, f => f.rutas)} rutas</span>`, "res-flota", [
    {k: "origen", t: "Depósito", v: f => (f.escalon === "troncal" ? "0" : "1") + nombreLugar(f.origen), f: f => `${esc(nombreLugar(f.origen))} <span class="m">${f.escalon}</span>`},
    {k: "tipo", t: "Vehículo", f: f => esc(VEH[f.tipo] || f.tipo)}, {k: "tarifa", t: "Tarifa"},
    {k: "rutas", t: "Rutas", n: 1}, {k: "camiones", t: "Camiones", n: 1}, {k: "coste", t: "Coste", n: 1, f: f => eur(f.coste)}], flota, {k: "origen", dir: 1}]);

  document.getElementById("cards-resumen").innerHTML = cards.map(([t, extra, id]) =>
    `<div class="card"${id === "res-flota" ? ` style="grid-column:1/-1"` : ""}><h3><span>${t}</span>${extra}</h3><div class="tabla-wrap" id="${id}"${id === "res-flota" ? ` style="max-height:420px"` : ""}></div></div>`).join("") +
    ((PLAN.avisos || []).length ? `<div class="card" style="grid-column:1/-1"><h3>Fuera del cálculo</h3><ul class="nota" style="margin:0">${PLAN.avisos.map(a => `<li>${esc(a.nombre)} (${num(a.kg)} kg): ${esc(a.motivo)}</li>`).join("")}</ul></div>` : "");
  // reordenar re-pinta sólo esa tabla
  for (const [, , id, cols, filas, orden] of cards) { const re = () => pintarTabla(id, cols, filas, {orden, redraw: re}); re(); }
}

// ---------- navegación entre pestañas ----------
function mostrarTab(id) {
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === id));
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("on", t.id === "tab-" + id));
  if (id === "mapa") setTimeout(() => map.invalidateSize(), 0);
  try { history.replaceState(null, "", "#" + id); } catch (e) {}
}
document.getElementById("tabs").onclick = e => { const b = e.target.closest("button"); if (b) mostrarTab(b.dataset.tab); };
function pintarAnaliticas() { pintarCtrlTiendas(); pintarTiendas(); pintarCtrlRutas(); pintarRutasA(); pintarAlmacenes(); pintarResumen(); }
pintarAnaliticas();
const inicial = location.hash.slice(1);
if (inicial && document.getElementById("tab-" + inicial)) mostrarTab(inicial);
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
