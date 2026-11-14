/* ReMode Studio · interacción de la app de gestión (sin dependencias). */
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const fmt = (n, d = 0) => n.toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d });
  const almacen = {
    get(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* modo privado */ } },
  };

  /* --- menú lateral en móvil */
  const shell = $(".shell");
  $$("[data-menu]").forEach(b => b.addEventListener("click", () => shell && shell.classList.toggle("menu-open")));
  document.addEventListener("click", e => {
    if (shell && shell.classList.contains("menu-open") && !e.target.closest(".side") && !e.target.closest("[data-menu]")) {
      shell.classList.remove("menu-open");
    }
  });

  /* --- notificaciones: se cierran solas las de éxito; los errores se quedan hasta cerrarlos */
  $$(".toast").forEach(t => {
    const cerrar = () => { t.classList.add("sale"); setTimeout(() => t.remove(), 220); };
    const b = $("button", t);
    if (b) b.addEventListener("click", cerrar);
    if (!t.classList.contains("error")) setTimeout(cerrar, 7000);
  });

  /* --- confirmaciones: <form data-confirm="¿Seguro?"> */
  document.addEventListener("submit", e => {
    const f = e.target;
    if (f.dataset.confirm && !window.confirm(f.dataset.confirm)) { e.preventDefault(); return; }
    /* evita el doble envío: el botón muestra que está trabajando */
    if (f.method && f.method.toLowerCase() === "post" && !f.hasAttribute("data-no-busy")) {
      const b = e.submitter || $("button[type=submit], button:not([type])", f);
      if (b) setTimeout(() => { b.disabled = true; b.classList.add("busy"); }, 0);
    }
  }, true);
  /* al volver con el botón "atrás" del navegador, reactivar botones */
  window.addEventListener("pageshow", () => $$("button.busy").forEach(b => { b.disabled = false; b.classList.remove("busy"); }));

  /* --- autoenvío de selects: <select data-autosubmit> */
  $$("select[data-autosubmit]").forEach(s => s.addEventListener("change", () => s.form.requestSubmit ? s.form.requestSubmit() : s.form.submit()));

  /* --- pestañas: <div class="tabs" data-tabs="clave"><button data-tab="id">… + <section class="tab-panel" id="tab-id"> */
  $$("[data-tabs]").forEach(barra => {
    const clave = "tab:" + location.pathname + ":" + barra.dataset.tabs;
    const botones = $$("[data-tab]", barra);
    const mostrar = (id, guardar = true) => {
      if (!botones.some(b => b.dataset.tab === id)) id = botones[0].dataset.tab;
      botones.forEach(b => {
        const on = b.dataset.tab === id;
        b.setAttribute("aria-selected", on ? "true" : "false");
        b.tabIndex = on ? 0 : -1;
        const p = document.getElementById("tab-" + b.dataset.tab);
        if (p) p.hidden = !on;
      });
      if (guardar) { almacen.set(clave, id); history.replaceState(null, "", "#" + id); }
    };
    botones.forEach((b, i) => {
      b.setAttribute("role", "tab");
      b.addEventListener("click", () => mostrar(b.dataset.tab));
      b.addEventListener("keydown", e => {
        const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (d) { const n = botones[(i + d + botones.length) % botones.length]; n.focus(); mostrar(n.dataset.tab); }
      });
    });
    barra.setAttribute("role", "tablist");
    const hash = location.hash.slice(1);
    mostrar(hash || almacen.get(clave) || botones[0].dataset.tab, !!hash);
  });

  /* --- filtro de tablas: <input data-filter="#tabla"> (+ data-count="#contador") */
  $$("input[data-filter]").forEach(inp => {
    const tabla = $(inp.dataset.filter);
    if (!tabla) return;
    const filas = $$("tbody tr", tabla).filter(tr => !tr.querySelector(".empty") && !tr.querySelector("th"));
    const contador = inp.dataset.count ? $(inp.dataset.count) : null;
    const aplicar = () => {
      const q = inp.value.trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
      let n = 0;
      filas.forEach(tr => {
        const t = tr.textContent.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
        const ok = !q || q.split(/\s+/).every(p => t.includes(p));
        tr.hidden = !ok; if (ok) n++;
      });
      if (contador) contador.textContent = fmt(n);
    };
    inp.addEventListener("input", aplicar);
    aplicar();
  });

  /* --- totales en vivo de las rejillas de prendas: <div data-total-vivo data-kg-caja="12.5"> dentro del form */
  $$("[data-total-vivo]").forEach(caja => {
    const form = caja.closest("form");
    const kgCaja = parseFloat(caja.dataset.kgCaja || "12.5");
    const inputs = $$("input[data-kg]", form);
    const pintar = () => {
      let u = 0, kg = 0;
      inputs.forEach(i => { const v = parseInt(i.value, 10) || 0; u += v; kg += v * parseFloat(i.dataset.kg); });
      $("[data-u]", caja).textContent = fmt(u);
      const kgEl = $("[data-kgt]", caja); if (kgEl) kgEl.textContent = fmt(kg, 1);
      const cjEl = $("[data-cj]", caja); if (cjEl) cjEl.textContent = fmt(kg > 0 ? Math.ceil(kg / kgCaja) : 0);
    };
    inputs.forEach(i => i.addEventListener("input", pintar));
    pintar();
  });

  /* --- marcar/desmarcar todas: <button data-check-all="plantas" data-valor="1|0"> */
  $$("[data-check-all]").forEach(b => b.addEventListener("click", e => {
    e.preventDefault();
    $$(`input[type=checkbox][name="${b.dataset.checkAll}"]`).forEach(c => { c.checked = b.dataset.valor === "1"; });
  }));
})();
