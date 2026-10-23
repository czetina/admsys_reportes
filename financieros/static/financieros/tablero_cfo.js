/* Tablero CFO: pide los indicadores (JSON) del cierre elegido y dibuja tarjetas, semáforos y gráficas con d3. */
(function () {
  "use strict";
  const raiz = document.getElementById("cfo");
  if (!raiz) return;
  const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  const css = getComputedStyle(raiz);
  const color = (n) => css.getPropertyValue(n).trim();
  const S = ["--s0", "--s1", "--s2", "--s3", "--s4", "--s5"].map(color);
  const OK = color("--ok"), WARN = color("--warn"), BAD = color("--bad"), GRID = color("--grid"), EJE = color("--eje");
  const fechaMes = (m) => new Date(Date.UTC(+m.slice(0, 4), +m.slice(5, 7) - 1, 1));
  const etqMes = (m) => { const d = typeof m === "string" ? fechaMes(m) : m; return MESES[d.getUTCMonth()] + " " + String(d.getUTCFullYear()).slice(2); };
  const UNIDAD = { pct: [1, " %"], pp: [2, " p.p."], x: [2, "x"], dias: [1, " días"] };
  const fmt = (v, u) => (v == null || !isFinite(v) ? "—" : v.toLocaleString("es-GT", { minimumFractionDigits: UNIDAD[u][0], maximumFractionDigits: UNIDAD[u][0] }) + UNIDAD[u][1]);
  const fmtCambio = (v, u) => {
    if (v == null || !isFinite(v)) return "";
    const s = (v > 0 ? "+" : v < 0 ? "−" : "±") + Math.abs(v).toFixed(UNIDAD[u][0]);
    return s + (u === "pct" || u === "pp" ? " p.p." : u === "x" ? "x" : " días");
  };
  const CLASE = { "En meta": "ok", "Vigilar": "vigilar", "Fuera de rango": "fuera", "Crítica": "fuera", "Advertencia": "vigilar", "Sin alerta": "ok" };
  let datos = null, pagina = "resumen";

  // ---------------------------------------------------------------- utilidades de dibujo
  function mide(svg, textos) {
    const t = svg.append("text").attr("visibility", "hidden");
    const w = d3.max(textos, (s) => (t.text(s), t.node().getComputedTextLength())) || 0;
    t.remove();
    return w;
  }

  function tooltip(caja) {
    let tip = caja.querySelector(".tip");
    if (!tip) { tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; caja.appendChild(tip); }
    return {
      show(x, y, titulo, lineas) {
        tip.replaceChildren();
        const t = document.createElement("div"); t.className = "tt"; t.textContent = titulo; tip.appendChild(t);
        lineas.forEach(([c, nombre, valor]) => {
          const l = document.createElement("div");
          if (c) { const i = document.createElement("i"); i.style.background = c; l.appendChild(i); }
          l.appendChild(document.createTextNode((nombre ? nombre + ": " : "") + valor));
          tip.appendChild(l);
        });
        tip.hidden = false;
        tip.style.left = Math.max(0, Math.min(caja.clientWidth - tip.offsetWidth, x + 12)) + "px";
        tip.style.top = Math.max(0, y - tip.offsetHeight - 8) + "px";
      },
      hide() { tip.hidden = true; },
    };
  }

  function lienzo(el, H) {
    el.querySelector("svg")?.remove();
    const W = el.clientWidth;
    if (!W) return null;
    return { W, H, svg: d3.select(el).append("svg").attr("width", "100%").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H) };
  }

  // Líneas por mes. series [{key, titulo, color}]; op {u, banda, refs:[{v, etq, critico}], area:[k1, k2], desdeCero}
  function lineas(el, filas, series, op) {
    const L = lienzo(el, 230); if (!L) return;
    const { W, H, svg } = L;
    const datosL = filas.map((r) => ({ ...r, fecha: fechaMes(r.mes) }));
    const vals = series.flatMap((s) => datosL.map((d) => d[s.key])).filter((v) => v != null && isFinite(v))
      .concat((op.refs || []).map((r) => r.v)).concat(op.banda || []);
    if (!vals.length) { svg.append("text").attr("x", W / 2).attr("y", H / 2).attr("text-anchor", "middle").text("Sin datos"); return; }
    let [lo, hi] = d3.extent(vals);
    const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.1 || 1;
    const y = d3.scaleLinear([op.desdeCero ? Math.min(0, lo) : lo - pad, hi + pad], [H - 26, 10]).nice(5);
    const ticks = y.ticks(5), fy = (v) => fmt(v, op.u);
    const ml = mide(svg, ticks.map(fy)) + 10;
    const ultimoValido = (k) => [...datosL].reverse().find((d) => d[k] != null);
    const finales = series.map((s) => { const d = ultimoValido(s.key); return d ? fmt(d[s.key], op.u) : ""; });
    const mr = Math.max(14, mide(svg, finales) + 12);
    const x = d3.scaleUtc(d3.extent(datosL, (d) => d.fecha), [ml, W - mr]);
    const g = svg.append("g");
    g.selectAll("line.gr").data(ticks).join("line").attr("x1", ml).attr("x2", W - mr).attr("y1", y).attr("y2", y).attr("stroke", GRID);
    g.selectAll("text.ty").data(ticks).join("text").attr("x", ml - 8).attr("y", (d) => y(d) + 4).attr("text-anchor", "end").text(fy);
    const xt = datosL.filter((d, i) => i % (W < 520 ? 3 : 2) === 0 || i === datosL.length - 1);
    g.selectAll("text.tx").data(xt).join("text").attr("x", (d) => x(d.fecha)).attr("y", H - 6).attr("text-anchor", "middle").text((d) => etqMes(d.fecha));
    if (op.banda) g.append("rect").attr("x", ml).attr("width", W - mr - ml).attr("y", y(op.banda[1])).attr("height", y(op.banda[0]) - y(op.banda[1]))
      .attr("fill", OK).attr("fill-opacity", 0.08);
    (op.refs || []).forEach((r) => {
      g.append("line").attr("x1", ml).attr("x2", W - mr).attr("y1", y(r.v)).attr("y2", y(r.v)).attr("stroke", r.critico ? BAD : EJE).attr("stroke-dasharray", "4 4");
      g.append("text").attr("x", ml + 4).attr("y", y(r.v) - 4).text(r.etq);
    });
    if (op.area) {
      const [a, b] = op.area;
      g.append("path").datum(datosL).attr("fill", series[0].color).attr("fill-opacity", 0.12)
        .attr("d", d3.area().defined((d) => d[a] != null && d[b] != null).x((d) => x(d.fecha)).y0((d) => y(d[b])).y1((d) => y(d[a])));
    }
    series.forEach((s, i) => {
      g.append("path").datum(datosL).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", 2).attr("stroke-linejoin", "round").attr("stroke-linecap", "round")
        .attr("d", d3.line().defined((d) => d[s.key] != null).x((d) => x(d.fecha)).y((d) => y(d[s.key])));
      const u = ultimoValido(s.key);
      if (!u) return;
      g.append("circle").attr("cx", x(u.fecha)).attr("cy", y(u[s.key])).attr("r", 3.5).attr("fill", s.color);
      g.append("text").attr("class", "etq").attr("x", x(u.fecha) + 6).attr("y", y(u[s.key]) + 4).text(finales[i]);
    });
    const etqs = g.selectAll("text.etq").nodes().sort((p, q) => +p.getAttribute("y") - +q.getAttribute("y"));
    for (let i = 1; i < etqs.length; i++) {
      const prev = +etqs[i - 1].getAttribute("y");
      if (+etqs[i].getAttribute("y") - prev < 13) etqs[i].setAttribute("y", prev + 13);
    }
    const tip = tooltip(el);
    const guia = g.append("line").attr("y1", 10).attr("y2", H - 26).attr("stroke", EJE).attr("visibility", "hidden");
    svg.append("rect").attr("x", ml).attr("width", W - mr - ml).attr("height", H).attr("fill", "transparent")
      .on("pointermove", (ev) => {
        const [px] = d3.pointer(ev);
        const d = datosL[d3.minIndex(datosL, (r) => Math.abs(x(r.fecha) - px))];
        guia.attr("x1", x(d.fecha)).attr("x2", x(d.fecha)).attr("visibility", "visible");
        const yv = series.map((s) => d[s.key]).find((v) => v != null);
        tip.show(x(d.fecha), yv == null ? H / 2 : y(yv), etqMes(d.fecha), series.map((s) => [series.length > 1 ? s.color : null, s.titulo, fmt(d[s.key], op.u)]));
      })
      .on("pointerleave", () => { tip.hide(); guia.attr("visibility", "hidden"); });
  }

  function barras(el, filas, campo, op) {
    const L = lienzo(el, 230); if (!L) return;
    const { W, H, svg } = L;
    const vals = filas.map((d) => d[campo]).filter((v) => v != null);
    const y = d3.scaleLinear([Math.min(0, d3.min(vals) || 0), Math.max(d3.max(vals) || 1, op.ref || 0) * 1.12], [H - 26, 10]).nice(5);
    const ticks = y.ticks(5);
    const ml = mide(svg, ticks.map((v) => fmt(v, op.u))) + 10;
    const x = d3.scaleBand(filas.map((d) => d.mes), [ml, W - 8]).padding(0.3);
    svg.selectAll("line.gr").data(ticks).join("line").attr("x1", ml).attr("x2", W - 8).attr("y1", y).attr("y2", y).attr("stroke", GRID);
    svg.selectAll("text.ty").data(ticks).join("text").attr("x", ml - 8).attr("y", (d) => y(d) + 4).attr("text-anchor", "end").text((v) => fmt(v, op.u));
    svg.selectAll("text.tx").data(filas.filter((d, i) => i % (W < 520 ? 3 : 2) === 0 || i === filas.length - 1)).join("text")
      .attr("x", (d) => x(d.mes) + x.bandwidth() / 2).attr("y", H - 6).attr("text-anchor", "middle").text((d) => etqMes(d.mes));
    const tip = tooltip(el);
    const n = filas.length - 1;
    svg.selectAll("rect.b").data(filas.filter((d) => d[campo] != null)).join("rect")
      .attr("x", (d) => x(d.mes)).attr("width", Math.min(32, x.bandwidth()))
      .attr("y", (d) => y(Math.max(0, d[campo]))).attr("height", (d) => Math.abs(y(d[campo]) - y(0))).attr("rx", 4)
      .attr("fill", (d) => (op.malo && op.malo(d[campo]) ? BAD : d.mes === filas[n].mes ? S[0] : "#cbd5e1"))
      .on("pointermove", (ev, d) => tip.show(x(d.mes), y(d[campo]), etqMes(d.mes), [[null, null, fmt(d[campo], op.u)]]))
      .on("pointerleave", () => tip.hide());
    if (op.ref != null) {
      svg.append("line").attr("x1", ml).attr("x2", W - 8).attr("y1", y(op.ref)).attr("y2", y(op.ref)).attr("stroke", EJE).attr("stroke-dasharray", "4 4");
      svg.append("text").attr("x", ml + 4).attr("y", y(op.ref) - 4).text(op.refEtq);
    }
  }

  function gauge(el, valor, op) {
    const L = lienzo(el, 170); if (!L) return;
    const { W, H, svg } = L;
    const r = Math.min(W / 2 - 16, 120), cx = W / 2, cy = r + 14;
    const a = d3.scaleLinear([op.min, op.max], [-Math.PI / 2, Math.PI / 2]).clamp(true);
    const arco = d3.arc().innerRadius(r * 0.72).outerRadius(r);
    op.zonas.forEach(([d, h, c]) => svg.append("path").attr("transform", `translate(${cx},${cy})`).attr("d", arco({ startAngle: a(d), endAngle: a(h) })).attr("fill", c).attr("fill-opacity", 0.35));
    if (valor != null) {
      const ang = a(valor);
      svg.append("line").attr("x1", cx).attr("y1", cy).attr("x2", cx + Math.sin(ang) * r * 0.95).attr("y2", cy - Math.cos(ang) * r * 0.95)
        .attr("stroke", "#0f172a").attr("stroke-width", 2.5).attr("stroke-linecap", "round");
    }
    svg.append("circle").attr("cx", cx).attr("cy", cy).attr("r", 4).attr("fill", "#0f172a");
    svg.append("text").attr("class", "etq").attr("x", cx).attr("y", cy + 26).attr("text-anchor", "middle").style("font-size", "14px")
      .text(op.titulo + " " + fmt(valor, op.u) + (valor != null && valor > op.max ? " (fuera de escala)" : ""));
    svg.append("text").attr("x", cx - r).attr("y", cy + 14).attr("text-anchor", "middle").text(fmt(op.min, op.u));
    svg.append("text").attr("x", cx + r).attr("y", cy + 14).attr("text-anchor", "middle").text(fmt(op.max, op.u));
  }

  function apiladas(el, filas) {
    const L = lienzo(el, 230); if (!L) return;
    const { W, H, svg } = L;
    const comps = [...new Set(filas.map((r) => r.componente))];
    const meses = [...new Set(filas.map((r) => r.mes))].sort();
    const tabla = meses.map((m) => Object.fromEntries([["mes", m], ...comps.map((c) => [c, Math.max(0, (filas.find((r) => r.mes === m && r.componente === c)?.monto || 0) / 1e3)])]));
    const pila = d3.stack().keys(comps)(tabla);
    const y = d3.scaleLinear([0, (d3.max(pila[pila.length - 1], (d) => d[1]) || 1) * 1.08], [H - 26, 10]).nice(5);
    const fq = (v) => "Q " + d3.format(",.0f")(v) + " k";
    const ml = mide(svg, y.ticks(5).map(fq)) + 10;
    const x = d3.scaleBand(meses, [ml, W - 8]).padding(0.3);
    svg.selectAll("line.gr").data(y.ticks(5)).join("line").attr("x1", ml).attr("x2", W - 8).attr("y1", y).attr("y2", y).attr("stroke", GRID);
    svg.selectAll("text.ty").data(y.ticks(5)).join("text").attr("x", ml - 8).attr("y", (d) => y(d) + 4).attr("text-anchor", "end").text(fq);
    svg.selectAll("text.tx").data(meses.filter((m, i) => i % (W < 520 ? 3 : 2) === 0 || i === meses.length - 1)).join("text")
      .attr("x", (m) => x(m) + x.bandwidth() / 2).attr("y", H - 6).attr("text-anchor", "middle").text((m) => etqMes(m));
    const tip = tooltip(el);
    pila.forEach((serie, i) => {
      svg.append("g").selectAll("rect").data(serie).join("rect").attr("x", (d) => x(d.data.mes)).attr("width", Math.min(32, x.bandwidth()))
        .attr("y", (d) => y(d[1])).attr("height", (d) => Math.max(0, y(d[0]) - y(d[1]) - 1)).attr("fill", S[i])
        .on("pointermove", (ev, d) => tip.show(x(d.data.mes), y(d[1]), etqMes(d.data.mes), comps.map((c, j) => [S[j], c, fq(d.data[c])])))
        .on("pointerleave", () => tip.hide());
    });
    document.getElementById("ley-ingresos").replaceChildren(...comps.map((c, i) => {
      const s = document.createElement("span"), k = document.createElement("i");
      k.style.background = S[i];
      s.append(k, document.createTextNode(c + " (" + filas.find((r) => r.componente === c).norma + ")"));
      return s;
    }));
  }

  function spark(el, filas, campo) {
    const L = lienzo(el, 34); if (!L) return;
    const { W, H, svg } = L;
    const v = filas.filter((d) => d[campo] != null);
    if (v.length < 2) return;
    const x = d3.scaleLinear([0, filas.length - 1], [2, W - 4]);
    const y = d3.scaleLinear(d3.extent(v, (d) => d[campo]), [H - 4, 4]);
    svg.append("path").datum(filas).attr("fill", "none").attr("stroke", S[0]).attr("stroke-width", 1.5)
      .attr("d", d3.line().defined((d) => d[campo] != null).x((d, i) => x(i)).y((d) => y(d[campo])));
    const u = filas.length - 1;
    if (filas[u][campo] != null) svg.append("circle").attr("cx", x(u)).attr("cy", y(filas[u][campo])).attr("r", 2.5).attr("fill", S[0]);
  }

  // ---------------------------------------------------------------- contenido
  const pill = (texto) => { const s = document.createElement("span"); s.className = "pill " + (CLASE[texto] || "nd"); s.textContent = texto; return s; };
  const celda = (tr, contenido, clase) => {
    const td = document.createElement("td"); if (clase) td.className = clase;
    contenido instanceof Node ? td.appendChild(contenido) : (td.textContent = contenido ?? "");
    tr.appendChild(td);
  };

  function tiles() {
    const porId = Object.fromEntries(datos.ultimo.map((k) => [k.id, k]));
    document.querySelectorAll("[data-tiles]").forEach((cont) => {
      cont.replaceChildren(...cont.dataset.tiles.split(" ").map((id) => {
        const k = porId[id];
        const t = document.createElement("div"); t.className = "tile" + (k.estado === "Fuera de rango" ? " fuera" : "");
        const nombre = document.createElement("div"); nombre.className = "t-nombre";
        const n = document.createElement("span"); n.textContent = k.indicador;
        nombre.append(n, pill(k.estado));
        const v = document.createElement("div"); v.className = "t-valor"; v.textContent = k.estado === "No aplica" ? "N/A" : fmt(k.valor, k.unidad);
        const sp = document.createElement("div"); sp.className = "t-spark"; sp.dataset.kpi = id;
        const pie = document.createElement("div"); pie.className = "t-pie";
        const c = document.createElement("span");
        if (k.cambio != null) {
          c.textContent = fmtCambio(k.cambio, k.unidad) + " vs mes ant.";
          const bueno = k.direccion === "mayor" ? k.cambio > 0 : k.direccion === "menor" ? k.cambio < 0 : null;
          if (bueno != null && k.cambio) c.className = bueno ? "bien" : "mal";
        }
        const m = document.createElement("span"); m.textContent = "Meta " + k.meta;
        pie.append(c, m);
        t.append(nombre, v, sp, pie);
        return t;
      }));
    });
  }

  function resumen() {
    const modulos = [...new Set(datos.ultimo.map((k) => k.modulo))];
    document.getElementById("semaforos").replaceChildren(...modulos.map((mod) => {
      const card = document.createElement("div"); card.className = "card";
      const body = document.createElement("div"); body.className = "card-body d-grid gap-2";
      const h = document.createElement("h3"); h.textContent = mod;
      const tabla = document.createElement("table"); tabla.className = "table table-sm t mb-0";
      tabla.innerHTML = "<thead><tr><th>Indicador</th><th class='text-end'>Último</th><th class='text-end'>vs mes ant.</th><th>Meta</th><th>Estado</th></tr></thead>";
      const tb = document.createElement("tbody");
      datos.ultimo.filter((k) => k.modulo === mod).forEach((k) => {
        const tr = document.createElement("tr");
        celda(tr, k.indicador); celda(tr, k.estado === "No aplica" ? "N/A" : fmt(k.valor, k.unidad), "text-end");
        celda(tr, fmtCambio(k.cambio, k.unidad), "text-end"); celda(tr, k.meta); celda(tr, pill(k.estado));
        tb.appendChild(tr);
      });
      tabla.appendChild(tb); body.append(h, tabla); card.appendChild(body);
      return card;
    }));
    const tb = document.querySelector("#tabla-alertas tbody");
    tb.replaceChildren(...datos.alertas.map((a) => { const tr = document.createElement("tr"); celda(tr, a.regla); celda(tr, pill(a.nivel)); celda(tr, a.motivo); return tr; }));
    const cuenta = (e) => datos.ultimo.filter((k) => k.estado === e).length;
    const ult = datos.meses[datos.meses.length - 1];
    document.getElementById("takeaway").textContent =
      `${datos.empresa} · cierre ${etqMes(ult)}: ${cuenta("Fuera de rango")} indicadores fuera de rango, ${cuenta("Vigilar")} en vigilancia y ` +
      `${cuenta("En meta")} en meta. Alertas: ${datos.alertas.filter((a) => a.nivel !== "Sin alerta").map((a) => a.regla.toLowerCase() + " (" + a.nivel.toLowerCase() + ")").join(", ") || "ninguna"}.`;
  }

  function matriz() {
    const porId = Object.fromEntries(datos.ultimo.map((k) => [k.id, k]));
    document.querySelector("#tabla-matriz tbody").replaceChildren(...datos.matriz.map((m) => {
      const tr = document.createElement("tr");
      [m.modulo, m.indicador, m.formula, m.fuente, m.frecuencia, m.meta, m.visualizacion].forEach((v) => celda(tr, v));
      const k = porId[m.id];
      celda(tr, k.estado === "No aplica" ? "N/A" : fmt(k.valor, k.unidad), "text-end text-nowrap");
      return tr;
    }));
    filtra();
  }

  const REGLAS = {
    "Deterioro de cartera": ["Morosidad > 90 días", "Más de 3 %, o alza de 0.50 p.p. o más en 3 meses", "Cumple ambas", "Semanal"],
    "Descalce de tasa": ["Spread de arrendamiento", "Menor a 5 p.p., o compresión de 0.75 p.p. o más en 3 meses", "Cumple ambas", "Mensual"],
    "Cobertura y servicio de deuda": ["Cobertura de provisiones y DSCR", "Cobertura menor a 100 % o DSCR menor a 1.40x", "DSCR menor a 1.25x o cobertura menor a 90 %", "Mensual"],
  };
  function alertas() {
    document.getElementById("reglas").replaceChildren(...datos.alertas.map((a, i) => {
      const [ind, dispara, critica, frec] = REGLAS[a.regla];
      const card = document.createElement("div");
      card.className = "card regla" + (a.nivel === "Crítica" ? " critica" : a.nivel === "Advertencia" ? " advertencia" : "");
      const body = document.createElement("div"); body.className = "card-body d-grid gap-2";
      const h = document.createElement("h3"); h.textContent = `${i + 1}. ${a.regla}`;
      const dl = document.createElement("dl");
      [["Indicador", ind], ["Dispara", dispara], ["Crítica", critica], ["Frecuencia", frec], ["Hoy", null]].forEach(([t, v]) => {
        const dt = document.createElement("dt"); dt.textContent = t;
        const dd = document.createElement("dd");
        if (v == null) { dd.append(pill(a.nivel), document.createTextNode(" " + a.motivo)); } else dd.textContent = v;
        dl.append(dt, dd);
      });
      body.append(h, dl); card.appendChild(body);
      return card;
    }));
    const orden = { "Crítica": 0, "Advertencia": 1, "Sin alerta": 2 };
    const a = [...datos.alertas].sort((p, q) => orden[p.nivel] - orden[q.nivel])[0];
    document.getElementById("correo").textContent =
      `Para: CFO; copia: Gerencia de Riesgos\nAsunto: [${a.nivel.toUpperCase()}] ${a.regla} · ${datos.empresa} · cierre ${etqMes(datos.meses[datos.meses.length - 1])}\n\n` +
      `${a.motivo}.\nCondición: ${REGLAS[a.regla][1]}.\n\nEnlace: Tablero CFO, pestaña «Alertas al CFO».`;
  }

  const buscar = document.getElementById("buscar-matriz");
  function filtra() {
    const q = buscar.value.trim().toLowerCase();
    document.querySelectorAll("#tabla-matriz tbody tr").forEach((tr) => { tr.hidden = q && !tr.textContent.toLowerCase().includes(q); });
  }
  buscar.addEventListener("input", filtra);

  function dibuja() {
    if (!datos) return;
    const k = datos.kpis, u = Object.fromEntries(datos.ultimo.map((x) => [x.id, x]));
    document.querySelectorAll(`#${pagina} .t-spark[data-kpi]`).forEach((s) => spark(s, k, s.dataset.kpi));
    const el = (id) => document.getElementById(id);
    if (pagina === "rentabilidad") {
      lineas(el("ch-spread"), k, [{ key: "tasa_activa", titulo: "Tasa activa", color: S[0] }, { key: "costo_fondos", titulo: "Costo de fondos", color: S[1] }], { u: "pct", area: ["tasa_activa", "costo_fondos"] });
      lineas(el("ch-roe"), k, [{ key: "roe", titulo: "ROE", color: S[0] }], { u: "pct", banda: [15, 20] });
      lineas(el("ch-nim"), k, [{ key: "nim", titulo: "NIM", color: S[0] }, { key: "roa", titulo: "ROA", color: S[2] }], { u: "pct", desdeCero: true });
      barras(el("ch-margen"), k, "margen_neto", { u: "pct", ref: 18, refEtq: "Meta 18 %", malo: (v) => v < 14 });
    } else if (pagina === "cartera") {
      lineas(el("ch-npl"), k, [{ key: "npl_30", titulo: "> 30 días", color: S[0] }, { key: "npl_60", titulo: "> 60 días", color: S[1] }, { key: "npl_90", titulo: "> 90 días", color: S[3] }],
        { u: "pct", refs: [{ v: 3, etq: "Límite 90+: 3 %", critico: true }], desdeCero: true });
      gauge(el("ch-cobertura"), u.cobertura.valor, { min: 0, max: 200, u: "pct", titulo: "Cobertura", zonas: [[0, 90, BAD], [90, 100, WARN], [100, 200, OK]] });
      barras(el("ch-dso"), k, "dso", { u: "dias", ref: 30, refEtq: "Límite 30 días", malo: (v) => v > 40 });
    } else if (pagina === "solvencia") {
      lineas(el("ch-dscr"), k, [{ key: "dscr", titulo: "DSCR", color: S[0] }], { u: "x", refs: [{ v: 1.25, etq: "Covenant 1.25x", critico: true }, { v: 1.4, etq: "Ideal 1.40x" }], desdeCero: true });
      lineas(el("ch-de"), k, [{ key: "deuda_patrimonio", titulo: "Deuda / patrimonio", color: S[0] }], { u: "x", refs: [{ v: 5, etq: "Límite 5.0x", critico: true }], desdeCero: true });
      barras(el("ch-tie"), k, "tie", { u: "x", ref: 1.5, refEtq: "Meta 1.5x", malo: (v) => v < 1.3 });
      gauge(el("ch-rc"), u.razon_corriente.valor, { min: 0.5, max: 3, u: "x", titulo: "Razón corriente",
        zonas: [[0.5, 0.875, BAD], [0.875, 1, WARN], [1, 1.5, OK], [1.5, 1.625, WARN], [1.625, 3, BAD]] });
      gauge(el("ch-da"), u.endeudamiento.valor, { min: 50, max: 100, u: "pct", titulo: "Deuda / activo",
        zonas: [[50, 66.25, BAD], [66.25, 70, WARN], [70, 85, OK], [85, 88.75, WARN], [88.75, 100, BAD]] });
    } else if (pagina === "eficiencia") {
      apiladas(el("ch-ingresos"), datos.ingresos);
      lineas(el("ch-eficiencia"), k, [{ key: "eficiencia", titulo: "Eficiencia", color: S[0] }], { u: "pct", refs: [{ v: 45, etq: "Límite 45 %", critico: true }] });
    }
  }

  // ---------------------------------------------------------------- pestañas, cierre y descarga
  const tabs = [...document.querySelectorAll(".cfo .tabs [data-pag]")];
  function muestra(id) {
    if (!document.getElementById(id)) return;
    pagina = id;
    tabs.forEach((t) => t.setAttribute("aria-selected", String(t.dataset.pag === id)));
    document.querySelectorAll(".cfo .pagina").forEach((s) => { s.hidden = s.id !== id; });
    history.replaceState(null, "", "#" + id);
    dibuja();
  }
  tabs.forEach((t) => t.addEventListener("click", () => muestra(t.dataset.pag)));
  window.addEventListener("hashchange", () => { if (datos) muestra(location.hash.slice(1)); });

  const anio = document.getElementById("cierre-anio"), mes = document.getElementById("cierre-mes");
  const consulta = () => new URLSearchParams({ anio: anio.value, mes: mes.value });

  async function carga() {
    document.getElementById("cargando").hidden = false;
    document.getElementById("contenido").hidden = true;
    try {
      const r = await fetch(raiz.dataset.urlDatos + "?" + consulta(), { headers: { "X-Requested-With": "fetch" } });
      const j = await r.json().catch(() => ({ error: `Error ${r.status} del servidor.` }));
      if (!r.ok) throw new Error(j.error || "No se pudo calcular el tablero.");
      datos = j;
      tiles(); resumen(); matriz(); alertas();
      document.getElementById("contenido").hidden = false;
      muestra(location.hash.slice(1) || pagina);
    } catch (e) {
      Swal.fire({ icon: "error", title: "Atención", text: e.message, confirmButtonColor: "#2563eb" });
    } finally {
      document.getElementById("cargando").hidden = true;
    }
  }

  document.getElementById("form-cierre").addEventListener("submit", (ev) => { ev.preventDefault(); history.replaceState(null, "", "?" + consulta() + location.hash); carga(); });
  document.getElementById("btn-excel").addEventListener("click", async () => {
    Swal.fire({ title: "Generando Excel…", allowOutsideClick: false, didOpen: () => Swal.showLoading() });
    try {
      const r = await fetch(raiz.dataset.urlExcel + "?" + consulta(), { headers: { "X-Requested-With": "fetch" } });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `Error ${r.status}`);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `Tablero CFO ${anio.value}-${String(mes.value).padStart(2, "0")}.xlsx`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      Swal.close();
    } catch (e) {
      Swal.fire({ icon: "error", title: "Atención", text: e.message, confirmButtonColor: "#2563eb" });
    }
  });
  let espera;
  window.addEventListener("resize", () => { clearTimeout(espera); espera = setTimeout(dibuja, 150); });
  carga();
})();
