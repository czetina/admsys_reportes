/* Comportamiento común: alertas, descarga de Excel por fetch, búsqueda y niveles. */
(function () {
  "use strict";

  const azul = "#2563eb";
  const Toast = Swal.mixin({ toast: true, position: "top-end", showConfirmButton: false, timer: 4000, timerProgressBar: true });

  function avisoError(texto) {
    Swal.fire({ icon: "error", title: "Atención", text: texto, confirmButtonColor: azul, confirmButtonText: "Entendido" });
  }

  function cargando(titulo, texto) {
    Swal.fire({ title: titulo, text: texto, allowOutsideClick: false, allowEscapeKey: false, didOpen: () => Swal.showLoading() });
  }

  function nombreArchivo(cabecera) {
    const utf = /filename\*=UTF-8''([^;]+)/i.exec(cabecera || "");
    if (utf) return decodeURIComponent(utf[1]);
    const simple = /filename="?([^";]+)"?/i.exec(cabecera || "");
    return simple ? simple[1] : "reporte.xlsx";
  }

  async function descargarExcel(datos, url) {
    cargando("Generando Excel…", "Calculando saldos y armando el archivo");
    try {
      const r = await fetch(url, { method: "POST", body: datos, headers: { "X-Requested-With": "fetch" } });
      const tipo = r.headers.get("Content-Type") || "";
      if (r.ok && tipo.includes("spreadsheetml")) {
        const blob = await r.blob();
        const nombre = nombreArchivo(r.headers.get("Content-Disposition"));
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = nombre;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 10000);
        Swal.fire({
          icon: "success", title: "Excel generado",
          html: `<div class="fw-semibold">${nombre}</div><div class="small text-secondary mt-1">Listo en ${r.headers.get("X-Tiempo") || "?"} s</div>`,
          timer: 3500, showConfirmButton: false,
        });
        return;
      }
      if (r.url.includes("/login")) return avisoError("Su sesión expiró. Vuelva a ingresar.");
      if (tipo.includes("json")) return avisoError((await r.json()).error || "No se pudo generar el reporte.");
      avisoError(`No se pudo generar el reporte (error ${r.status}).`);
    } catch (e) {
      avisoError("No hay conexión con el servidor.");
    }
  }

  // ---------------------------------------------------------------- Búsqueda de cuentas / directorios
  function iniciaBusquedas(form) {
    const campos = form.querySelectorAll("input[data-buscar]");
    if (!campos.length) return;
    const urlDe = (tipo, params) => form.dataset.urlBuscar.replace("TIPO", tipo) + "?" + new URLSearchParams(params);
    const periodo = () => {
      const v = (n) => (form.querySelector(`[name=${n}]`) || {}).value || "";
      return { anio: v("anio"), mes: v("mes"), fecha: v("fecha_fin") };
    };

    // Ventana única reutilizada por todos los campos
    const modal = document.createElement("div");
    modal.className = "modal fade";
    modal.tabIndex = -1;
    modal.innerHTML = `
      <div class="modal-dialog modal-lg modal-dialog-scrollable"><div class="modal-content">
        <div class="modal-header py-2"><h5 class="modal-title" data-b-titulo></h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button></div>
        <div class="px-3 pt-3"><div class="input-group">
          <span class="input-group-text bg-white"><i class="bi bi-search"></i></span>
          <input type="search" class="form-control" data-b-q placeholder="Código, nombre o NIT…" autocomplete="off"></div>
          <div class="small text-secondary mt-1" data-b-info></div></div>
        <div class="modal-body pt-2"><table class="table table-sm table-hover tabla-busqueda mb-0">
          <thead class="sticky-top bg-white"><tr data-b-cols></tr></thead><tbody data-b-filas></tbody></table></div>
      </div></div>`;
    document.body.appendChild(modal);
    const bsModal = new bootstrap.Modal(modal);
    const q = modal.querySelector("[data-b-q]");
    const cuerpo = modal.querySelector("[data-b-filas]");
    let destino = null, pedido = 0, temporizador = null, sel = -1;

    const marca = (i) => {
      const trs = cuerpo.querySelectorAll("tr");
      if (!trs.length) return;
      sel = Math.max(0, Math.min(i, trs.length - 1));
      trs.forEach((tr, k) => tr.classList.toggle("table-primary", k === sel));
      trs[sel].scrollIntoView({ block: "nearest" });
    };
    const elegir = (fila) => {
      destino.value = fila.codigo;
      destino.dispatchEvent(new Event("change"));
      bsModal.hide();
      destino.focus();
    };
    async function carga() {
      const n = ++pedido;
      const r = await fetch(urlDe(destino.dataset.buscar, { q: q.value.trim(), ...periodo() }));
      if (n !== pedido || !r.ok) return;
      const d = await r.json();
      modal.querySelector("[data-b-titulo]").textContent = d.titulo + (d.periodo ? ` · periodo ${d.periodo}` : "");
      const cols = modal.querySelector("[data-b-cols]");
      cols.replaceChildren(...d.columnas.map((c) => Object.assign(document.createElement("th"), { textContent: c })));
      cuerpo.replaceChildren(...d.filas.map((f) => {
        const tr = document.createElement("tr");
        [f.codigo, f.nombre, ...f.extra].forEach((t) => tr.appendChild(Object.assign(document.createElement("td"), { textContent: t })));
        tr.addEventListener("click", () => elegir(f));
        return tr;
      }));
      modal.querySelector("[data-b-info]").textContent = d.filas.length
        ? `${d.filas.length}${d.mas ? "+ (escriba más para acotar)" : ""} resultado${d.filas.length === 1 ? "" : "s"} · ↑↓ y Enter para elegir`
        : "Sin resultados";
      cuerpo._datos = d.filas;
      marca(0);
    }
    q.addEventListener("input", () => { clearTimeout(temporizador); temporizador = setTimeout(carga, 250); });
    q.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); marca(sel + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); marca(sel - 1); }
      else if (e.key === "Enter") { e.preventDefault(); if (cuerpo._datos && cuerpo._datos[sel]) elegir(cuerpo._datos[sel]); }
    });
    modal.addEventListener("shown.bs.modal", () => q.focus());

    const abrir = (input) => {
      destino = input;
      q.placeholder = input.dataset.buscar === "cuentas" ? "Código o nombre de la cuenta…" : "Código, nombre o NIT…";
      q.value = input.value.trim();
      cuerpo.replaceChildren();
      bsModal.show();
      carga();
    };

    campos.forEach((input) => {
      // Lupa al lado del campo y nombre debajo (como el cuadro gris de VFP)
      const grupo = document.createElement("div");
      grupo.className = "input-group";
      input.parentNode.insertBefore(grupo, input);
      grupo.appendChild(input);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-outline-secondary";
      btn.title = "Buscar (F2)";
      btn.innerHTML = '<i class="bi bi-search"></i>';
      grupo.appendChild(btn);
      const nombre = document.createElement("div");
      nombre.className = "form-text text-truncate";
      grupo.after(nombre);
      btn.addEventListener("click", () => abrir(input));
      input.addEventListener("keydown", (e) => { if (e.key === "F2") { e.preventDefault(); abrir(input); } });

      // Valida lo escrito a mano y muestra el nombre
      const valida = async () => {
        const codigo = input.value.trim();
        input.classList.remove("is-invalid");
        nombre.classList.remove("text-danger");
        if (!codigo) { nombre.textContent = ""; return; }
        try {
          const r = await fetch(urlDe(input.dataset.buscar, { codigo, ...periodo() }));
          const d = await r.json();
          if (d.filas.length) nombre.textContent = d.filas[0].nombre;
          else { nombre.textContent = "Código no existe"; nombre.classList.add("text-danger"); input.classList.add("is-invalid"); }
        } catch (e) { nombre.textContent = ""; }
      };
      input.addEventListener("change", valida);
      if (input.value.trim()) valida();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    // Mensajes del servidor (django.contrib.messages)
    const datos = document.getElementById("mensajes-data");
    if (datos) {
      JSON.parse(datos.textContent).forEach((m) => {
        if (m.nivel === "error") avisoError(m.texto);
        else Toast.fire({ icon: m.nivel === "success" ? "success" : "info", title: m.texto });
      });
    }
    const errorForm = document.getElementById("error-form");
    if (errorForm) avisoError(JSON.parse(errorForm.textContent));

    // Formulario de parámetros
    const form = document.getElementById("form-reporte");
    if (form) {
      form.addEventListener("submit", (ev) => {
        const salida = (form.querySelector("input[name=salida]:checked") || {}).value;
        if (salida === "excel") {
          ev.preventDefault();
          descargarExcel(new FormData(form), form.action);
        } else {
          cargando("Generando reporte…", "Calculando saldos");
        }
      });
    }

    // Listas de selección múltiple (centros de costo, clasificaciones): filtro, Todos / Ninguno y contador
    document.querySelectorAll("[data-lista]").forEach((lista) => {
      const items = lista.querySelectorAll(".centro-item");
      const cuenta = lista.querySelector("[data-lista-cuenta]");
      const filtro = lista.querySelector("[data-lista-filtro]");
      const actualiza = () => {
        const n = lista.querySelectorAll(".centro-item input:checked").length;
        cuenta.textContent = n ? `${n} seleccionado${n === 1 ? "" : "s"}` : "Todos";
      };
      filtro.addEventListener("input", () => {
        const q = filtro.value.trim().toLowerCase();
        items.forEach((it) => it.classList.toggle("d-none", q && !it.textContent.toLowerCase().includes(q)));
      });
      const marcar = (v) => { items.forEach((it) => { if (!it.classList.contains("d-none")) it.querySelector("input").checked = v; }); actualiza(); };
      lista.querySelector("[data-lista-todos]").addEventListener("click", () => marcar(true));
      lista.querySelector("[data-lista-ninguno]").addEventListener("click", () => marcar(false));
      items.forEach((it) => it.querySelector("input").addEventListener("change", actualiza));
      actualiza();
    });

    // Ayuda de búsqueda de cuentas y directorios (F2 o lupa), como el «Maestro de …» de VFP
    if (form && form.dataset.urlBuscar) iniciaBusquedas(form);

    // Exportar a Excel desde la vista en pantalla
    document.querySelectorAll("[data-exportar]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const f = document.getElementById(btn.dataset.exportar);
        const fd = new FormData(f);
        fd.set("salida", "excel");
        descargarExcel(fd, f.action);
      });
    });

    // Menú por categorías: una categoría a la vez; la búsqueda recorre todas
    const menuLista = document.querySelector(".menu-lista");
    if (menuLista) {
      const botones = [...document.querySelectorAll("[data-categoria]")];
      const paneles = [...document.querySelectorAll("[data-panel]")];
      const buscar = document.getElementById("buscar-reporte");
      const sinResultados = document.getElementById("sin-resultados");
      const sinAcentos = (t) => t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
      let actual = botones[0]?.dataset.categoria;

      const grupos = [...document.querySelectorAll("[data-modulo]")];
      const abreModulo = (grupo) => grupos.forEach((g) => {
        g.classList.toggle("abierto", g === grupo);
        const b = g.querySelector("[data-modulo-btn]");
        if (b) b.setAttribute("aria-expanded", g === grupo ? "true" : "false");
      });
      // El panel de reportes a la vista: si la página quedó más abajo, subir hasta él
      const panelVisible = () => {
        const arriba = menuLista.getBoundingClientRect().top;
        if (arriba < 0 || arriba > window.innerHeight * 0.6) {
          menuLista.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      };

      const mostrar = (clave) => {
        if (!paneles.some((p) => p.dataset.panel === clave)) clave = botones[0].dataset.categoria;
        actual = clave;
        const boton = botones.find((b) => b.dataset.categoria === clave);
        if (boton) abreModulo(boton.closest("[data-modulo]"));
        botones.forEach((b) => {
          b.classList.toggle("activa", b.dataset.categoria === clave);
          // en celular la barra de categorías es horizontal: llevar la activa a la vista
          const barra = b.closest(".menu-categorias");
          if (b.dataset.categoria === clave && barra.scrollWidth > barra.clientWidth) {
            const ficha = b.closest("[data-modulo]")?.querySelector("[data-modulo-btn]") || b;
            barra.scrollLeft = ficha.offsetLeft - barra.offsetLeft - 16;
          }
        });
        paneles.forEach((p) => { p.hidden = p.dataset.panel !== clave; });
        try { localStorage.setItem("menu-categoria", clave); } catch (e) { /* sin almacenamiento */ }
      };
      const filtrar = () => {
        const q = sinAcentos(buscar.value.trim());
        menuLista.classList.toggle("buscando", !!q);
        if (!q) {
          document.querySelectorAll(".reporte-fila").forEach((f) => { f.hidden = false; });
          sinResultados.hidden = true;
          mostrar(actual);
          return;
        }
        let total = 0;
        botones.forEach((b) => b.classList.remove("activa"));
        paneles.forEach((p) => {
          let visibles = 0;
          p.querySelectorAll(".reporte-fila").forEach((f) => {
            const ok = q.split(/\s+/).every((palabra) => sinAcentos(f.dataset.buscar).includes(palabra));
            f.hidden = !ok;
            if (ok) visibles++;
          });
          p.hidden = visibles === 0;
          total += visibles;
        });
        sinResultados.hidden = total > 0;
      };

      const elegir = (clave) => {
        if (buscar.value) { buscar.value = ""; filtrar(); }
        mostrar(clave);
        history.replaceState(null, "", "#" + clave);
        panelVisible();
      };
      botones.forEach((b) => b.addEventListener("click", () => elegir(b.dataset.categoria)));
      // Cabecera de módulo: abre el módulo y muestra su primera categoría (o la que estaba activa en él)
      document.querySelectorAll("[data-modulo-btn]").forEach((mb) => mb.addEventListener("click", () => {
        const grupo = mb.closest("[data-modulo]");
        if (grupo.classList.contains("abierto") && !buscar.value) {
          grupo.classList.remove("abierto");
          mb.setAttribute("aria-expanded", "false");
          return;
        }
        const activa = grupo.querySelector(".categoria-btn.activa") || grupo.querySelector(".categoria-btn");
        if (activa) elegir(activa.dataset.categoria);
      }));
      buscar.addEventListener("input", filtrar);
      buscar.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {            // Enter abre el primer resultado
          const primero = menuLista.querySelector("[data-panel]:not([hidden]) .reporte-fila:not([hidden]) .reporte-principal");
          if (primero) { e.preventDefault(); primero.click(); }
        } else if (e.key === "Escape") { buscar.value = ""; filtrar(); }
      });
      let guardada = null;
      try { guardada = localStorage.getItem("menu-categoria"); } catch (e) { /* sin almacenamiento */ }
      mostrar(location.hash.slice(1) || guardada || actual);
    }

    // Niveles visibles en la vista en pantalla
    const filas = document.querySelectorAll("tr[data-nivel]");
    function mostrarNivel(n) {
      filas.forEach((tr) => tr.classList.toggle("d-none", n > 0 && Number(tr.dataset.nivel) > n));
      document.querySelectorAll("[data-nivel-btn]").forEach((b) => b.classList.toggle("active", Number(b.dataset.nivelBtn) === n));
    }
    document.querySelectorAll("[data-nivel-btn]").forEach((b) => b.addEventListener("click", () => mostrarNivel(Number(b.dataset.nivelBtn))));
    const inicial = document.getElementById("nivel-inicial");
    if (inicial) mostrarNivel(Number(inicial.textContent));

    // Hipervínculos entre hojas (RESUMEN -> hoja del centro) en la vista en pantalla
    document.querySelectorAll("[data-ir-hoja]").forEach((a) => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        const destino = [...document.querySelectorAll(".hojas-tabs .nav-link")]
          .find((b) => b.textContent.trim() === a.dataset.irHoja);
        if (destino) { bootstrap.Tab.getOrCreateInstance(destino).show(); window.scrollTo({ top: 0, behavior: "smooth" }); }
      });
    });

    // Confirmar salida
    document.querySelectorAll("form[data-confirmar]").forEach((f) => {
      f.addEventListener("submit", (ev) => {
        if (f.dataset.ok) return;
        ev.preventDefault();
        Swal.fire({
          title: f.dataset.confirmar, icon: "question", showCancelButton: true,
          confirmButtonText: "Sí, salir", cancelButtonText: "Cancelar", confirmButtonColor: azul,
        }).then((r) => { if (r.isConfirmed) { f.dataset.ok = "1"; f.submit(); } });
      });
    });
  });
})();
