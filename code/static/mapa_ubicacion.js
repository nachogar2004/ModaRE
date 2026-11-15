/* ReMode Studio · mapa de ubicación de tiendas, plantas y almacenes (Leaflet + Nominatim a través del servidor).
   Formulario (#ubic-mapa): clic o arrastre = lat/lon; buscador de direcciones; relleno de dirección desde el punto.
   Lista (#lista-mapa): instalaciones existentes y «clic en el mapa → nueva aquí». */
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const ESPANA = [40.2, -3.5];
  const fmt = (n, d = 1) => n.toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d });
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || "#2d5f9e";
  const TIPOS = { tiendas: "--denim", plantas: "--ok", almacenes: "--stitch" };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  const km = (a, b, c, d) => {            // haversine, en línea recta
    const r = Math.PI / 180, h = Math.sin((c - a) * r / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin((d - b) * r / 2) ** 2;
    return 12742 * Math.asin(Math.sqrt(h));
  };
  /* misma regla que el servidor (gestion/peninsula.py): las zonas llegan en data-peninsula, no se duplican */
  const dentroDe = (poli, lat, lon) => {          // trazado de rayos
    let d = false;
    for (let i = 0, n = poli.length; i < n; i++) {
      const [la1, lo1] = poli[i], [la2, lo2] = poli[(i + 1) % n];
      if ((la1 > lat) !== (la2 > lat) && lon < lo1 + (lat - la1) * (lo2 - lo1) / (la2 - la1)) d = !d;
    }
    return d;
  };
  const zona = (cfg, lat, lon) => {               // "peninsula" | "baleares" | null  (= peninsula.zona)
    if (!cfg.poligono || dentroDe(cfg.poligono, lat, lon)) return "peninsula";
    const [la1, la2, lo1, lo2] = cfg.baleares || [];
    return lat >= la1 && lat <= la2 && lon >= lo1 && lon <= lo2 ? "baleares" : null;
  };
  const INSULAR = "Tienda insular: se gestiona en la plataforma, pero el optimizador de rutas no la incluye (transporte marítimo).";
  const motivoFuera = (cfg, z) => z === "peninsula" || (z === "baleares" && cfg.islas) ? ""
    : z === "baleares" ? "Está en las Islas Baleares: las plantas y los almacenes solo pueden estar en la España peninsular."
    : "Está fuera de la zona admitida (España peninsular" + (cfg.islas ? " y Baleares" : "") +
      "; no se admiten Canarias, Ceuta, Melilla ni otros países).";

  function base(el) {
    const mapa = L.map(el).setView(ESPANA, 6);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap", maxZoom: 19 }).addTo(mapa);
    return mapa;
  }

  /* instalaciones existentes como círculos por tipo; devuelve la lista para calcular cercanías */
  function contexto(mapa, url, excluir, alClic) {
    return fetch(url, { credentials: "same-origin" }).then(r => r.ok ? r.json() : Promise.reject()).then(d => {
      const todas = [];
      Object.entries(TIPOS).forEach(([clave, token]) => (d[clave] || []).forEach(o => {
        if (excluir && excluir.tipo === clave && excluir.id === o.id) return;
        const it = { ...o, tipo: clave };
        todas.push(it);
        L.circleMarker([o.lat, o.lon], { radius: clave === "tiendas" ? 5 : 7, color: css(token), weight: 1.5,
          fillColor: css(token), fillOpacity: o.activo ? .75 : .2,
          bubblingMouseEvents: !alClic })
          .bindTooltip(`${esc(o.id)} · ${esc(o.nombre)}${o.activo ? "" : " (de baja)"}`).addTo(mapa)
          .on("click", () => alClic && alClic(it));
      }));
      return todas;
    });
  }

  /* ------------------------------------------------------------------ formulario */
  function formulario(el) {
    const $f = n => document.getElementById("f-" + n);
    const lat = $f("lat"), lon = $f("lon");
    const info = $("#ubic-info"), aviso = $("#ubic-aviso"), estado = $("#ubic-estado"), res = $("#ubic-res");
    const cfg = JSON.parse(el.dataset.peninsula || "{}");
    const acceso = document.querySelector('input[name="acceso_carretera"]');
    let previoAcceso = null;            // valor de la casilla antes de entrar en Baleares
    let instalaciones = [], marcador = null, timer = null, pet = 0;
    const mapa = base(el);
    const num = i => parseFloat(String(i.value).replace(",", "."));

    /* campos de dirección: se rellenan si están vacíos o si los rellenó antes el mapa; nunca pisan lo escrito a mano */
    const CAMPOS = ["direccion", "municipio", "provincia", "ccaa", "poligono"];
    CAMPOS.forEach(n => { const i = $f(n); if (i) i.addEventListener("input", () => delete i.dataset.auto); });
    const rellenar = (d, forzar) => {
      CAMPOS.forEach(n => {
        const i = $f(n);
        if (i && d[n] && (forzar || !i.value.trim() || i.dataset.auto)) { i.value = d[n]; i.dataset.auto = "1"; }
      });
    };

    const refrescar = () => {
      const la = num(lat), lo = num(lon);
      if (isNaN(la) || isNaN(lo)) { info.textContent = ""; aviso.hidden = true; return; }
      const z = zona(cfg, la, lo), msg = motivoFuera(cfg, z), insular = !msg && z === "baleares";
      if (acceso) {                       // en Baleares sin carretera (el servidor también lo fuerza); al volver, lo que había
        if (insular && previoAcceso === null) { previoAcceso = acceso.checked; acceso.checked = false; }
        else if (!insular && previoAcceso !== null) { acceso.checked = previoAcceso; previoAcceso = null; }
      }
      const sinCarretera = !msg && !insular && acceso && !acceso.checked;
      aviso.hidden = !msg && !insular && !sinCarretera;
      aviso.classList.toggle("err", !!msg); aviso.classList.toggle("info", insular);
      $("span", aviso).textContent = msg ? msg + " El servidor no dejará guardarla." : insular ? INSULAR
        : "Tienda peninsular sin «Acceso por carretera»: el optimizador de rutas no la incluirá. Márcalo si se le reparte en camión.";
      const alm = instalaciones.filter(o => o.tipo === "almacenes" && o.activo)
        .map(o => [km(la, lo, o.lat, o.lon), o]).sort((a, b) => a[0] - b[0])[0];
      info.textContent = alm ? `Almacén más cercano: ${alm[1].id} · ${alm[1].nombre}, a ${fmt(alm[0])} km en línea recta.` : "";
    };

    const poner = (la, lo, centrar) => {
      la = +la.toFixed(6); lo = +lo.toFixed(6);
      lat.value = la; lon.value = lo;
      if (!marcador) {
        marcador = L.marker([la, lo], { draggable: true }).addTo(mapa);
        marcador.on("dragend", () => { const p = marcador.getLatLng(); poner(p.lat, p.lng); pedirDireccion(); });
      } else marcador.setLatLng([la, lo]);
      if (centrar) mapa.setView([la, lo], Math.max(mapa.getZoom(), 14));
      refrescar();
    };

    const pedirDireccion = forzar => {
      clearTimeout(timer);
      timer = setTimeout(() => {          // espera a que el usuario deje de mover el punto (Nominatim: 1 petición/s)
        const la = num(lat), lo = num(lon), mi = ++pet;
        if (isNaN(la) || isNaN(lo)) return;
        estado.textContent = "Buscando la dirección del punto…";
        fetch(`${el.dataset.urlInversa}?lat=${la}&lon=${lo}`, { credentials: "same-origin" })
          .then(r => r.json().then(d => ({ ok: r.ok, d })))
          .then(({ ok, d }) => {
            if (mi !== pet) return;
            if (!ok) { estado.textContent = d.error || "No se pudo obtener la dirección."; return; }
            rellenar(d, forzar);
            estado.textContent = d.etiqueta ? "Dirección del punto: " + d.etiqueta : "";
          }).catch(() => { if (mi === pet) estado.textContent = "No se pudo obtener la dirección (sin conexión). Escríbela a mano."; });
      }, forzar ? 0 : 700);
    };

    mapa.on("click", e => { poner(e.latlng.lat, e.latlng.lng); pedirDireccion(); });
    const desdeCampos = () => {
      const la = num(lat), lo = num(lon);
      if (!isNaN(la) && !isNaN(lo) && Math.abs(la) <= 90 && Math.abs(lo) <= 180) poner(la, lo, true);
      else {
        if (marcador && (isNaN(la) || isNaN(lo))) { mapa.removeLayer(marcador); marcador = null; }   // campos vaciados
        refrescar();
      }
    };
    [lat, lon].forEach(i => i.addEventListener("change", desdeCampos));
    if (acceso) acceso.addEventListener("change", refrescar);
    $("#ubic-rellenar").addEventListener("click", () => pedirDireccion(true));

    /* buscador de direcciones */
    const buscar = () => {
      const q = $("#ubic-q").value.trim();
      if (q.length < 3) return;
      res.innerHTML = '<li class="vacio">Buscando…</li>';
      fetch(`${el.dataset.urlBuscar}?q=${encodeURIComponent(q)}`, { credentials: "same-origin" })
        .then(r => r.json().then(d => ({ ok: r.ok, d })))
        .then(({ ok, d }) => {
          if (!ok) { res.innerHTML = `<li class="vacio">${esc(d.error || "No se pudo buscar.")}</li>`; return; }
          if (!d.length) { res.innerHTML = '<li class="vacio">Sin resultados en España. Prueba con otra dirección.</li>'; return; }
          res.innerHTML = "";
          d.forEach(r => {
            const li = document.createElement("li"), b = document.createElement("button");
            b.type = "button"; b.textContent = r.etiqueta || `${r.lat}, ${r.lon}`;
            b.addEventListener("click", () => { res.innerHTML = ""; poner(r.lat, r.lon, true); rellenar(r); estado.textContent = ""; });
            li.appendChild(b); res.appendChild(li);
          });
        }).catch(() => { res.innerHTML = '<li class="vacio">Sin conexión con el servicio de direcciones. Puedes marcar el punto en el mapa.</li>'; });
    };
    $("#ubic-buscar").addEventListener("click", buscar);
    $("#ubic-q").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); buscar(); } });

    /* arranque: en edición (o con ?lat=&lon=), centrado en la ubicación actual */
    const la0 = num(lat), lo0 = num(lon);
    if (!isNaN(la0) && !isNaN(lo0)) { poner(la0, lo0, false); mapa.setView([la0, lo0], 14); }
    contexto(mapa, el.dataset.urlInstalaciones, el.dataset.id ? { tipo: el.dataset.ent, id: el.dataset.id } : null)
      .then(t => { instalaciones = t; refrescar(); }).catch(() => { info.textContent = "No se pudieron cargar las demás instalaciones."; });
  }

  /* ------------------------------------------------------------------ lista */
  function lista(el) {
    const mapa = base(el);
    const raiz = el.dataset.urlNuevo.replace(/[^/]+\/nuevo$/, "");      // /admin/
    /* clic en una instalación: enlace a su ficha (no «nueva aquí») */
    contexto(mapa, el.dataset.urlInstalaciones, null, o => L.popup().setLatLng([o.lat, o.lon]).setContent(
      `<a href="${raiz}${o.tipo}/${encodeURIComponent(o.id)}/editar"><b>${esc(o.id)} · ${esc(o.nombre)}</b></a><br><small>Abrir su ficha</small>`).openOn(mapa)).catch(() => {});
    mapa.on("click", e => {
      const la = e.latlng.lat.toFixed(6), lo = e.latlng.lng.toFixed(6);
      L.popup().setLatLng(e.latlng).setContent(
        `<a href="${el.dataset.urlNuevo}?lat=${la}&lon=${lo}"><b>Nueva ${esc(el.dataset.singular)} aquí</b></a><br><small>${la}, ${lo}</small>`).openOn(mapa);
    });
  }

  const f = document.getElementById("ubic-mapa");
  if (f) {
    if (typeof L === "undefined") { $("#ubic-sinmapa").hidden = false; $("#ubic-con-mapa").hidden = true; }
    else formulario(f);
  }
  const l = document.getElementById("lista-mapa");
  if (l) {
    const det = l.closest("details");
    if (typeof L === "undefined") l.textContent = "No se pudo cargar el mapa (sin conexión).";
    else det.addEventListener("toggle", () => { if (det.open && !l._leaflet_id) lista(l); });
  }
})();
