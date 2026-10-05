/* Toshkent havosi — "Hozir" sahifasi (V8.1) */
(() => {
  "use strict";
  const C = window.TAQ_CONFIG;
  const H = 3600e3, TZ = 5 * H;                     // Toshkent = UTC+5

  const STATIONS = {
    107: { name: "O'zgidromet",  lat: 41.3281, lon: 69.2945 },
    108: { name: "Chilonzor",         lat: 41.3109, lon: 69.2407 },
    720: { name: "TTZ-4",             lat: 41.3629, lon: 69.3882 },
    729: { name: "Yangi O'zbekiston", lat: 41.3243, lon: 69.4424 },
    730: { name: "Safia",             lat: 41.3756, lon: 69.2706 },
    731: { name: "Green University",  lat: 41.3870, lon: 69.2915 },
    732: { name: "Tashselmash",       lat: 41.3060, lon: 69.3087 },
    733: { name: "Uchtepa",           lat: 41.2960, lon: 69.1750 },
    734: { name: "Almazar",           lat: 41.3507, lon: 69.2251 },
    738: { name: "Qibray",            lat: 41.4377, lon: 69.4212 },
  };
  // DSanQN 0276-09 chegaralari (bot bilan bir xil)
  const THR = { pm10: [75, 150, 250, 350], pm25: [60, 120, 180, 300] };
  const CAT = [
    { name: "Yaxshi",               color: "#2ecc71", advice: "Odatdagidek faoliyat mumkin." },
    { name: "Qoniqarli",            color: "#f1c40f", advice: "Sezgir guruhlar jismoniy faollikni cheklashi tavsiya etiladi." },
    { name: "O'rtacha ifloslangan", color: "#e67e22", advice: "Ochiq havoda jismoniy faollikni cheklang. Niqob tavsiya etiladi." },
    { name: "Yuqori ifloslangan",   color: "#e74c3c", advice: "FFP2/N95 niqob tavsiya etiladi. Bino ichida bo'ling." },
    { name: "Qoniqarsiz, xavfli",   color: "#8e44ad", advice: "Tashqarida FFP2/N95 niqob majburiy. Uyda qoling." },
  ];
  const LABEL = { pm25: "PM2.5", pm10: "PM10" };
  const STALE_H = 3;
  // Prognoz modellari (forecasts_v81.model) — grafikda alohida kuzatish uchun
  const MODELS = [
    { id: "A-rel",       name: "A · LightGBM (nisbiy)", color: "#16a085" },
    { id: "A-abs",       name: "A · LightGBM (mutlaq)", color: "#27ae60" },
    { id: "B",           name: "B · Box-model",          color: "#8e44ad" },
    { id: "C",           name: "C · TFT",                color: "#c0392b" },
    { id: "persistence", name: "Persistence",            color: "#7f8c8d" },
  ];

  const state = { modelsOn: new Set(), fcAll: [], obs: [], fc: [], fcRun: null, fcPol: "pm25", stPol: "pm25", sel: Number(C.FORECAST_STATION) };
  let map, markers = {}, fcChart, stChart;

  // ---------- yordamchilar ----------
  const lvl = (pol, v) => { if (v == null || isNaN(v)) return -1; let i = 0; while (i < 4 && v >= THR[pol][i]) i++; return i; };
  const catOf = (pm25, pm10) => Math.max(lvl("pm25", pm25), lvl("pm10", pm10));
  const colorOf = (i) => (i < 0 ? "#9aa5b4" : CAT[i].color);
  const hh = (utcMs) => String(new Date(utcMs + TZ).getUTCHours()).padStart(2, "0") + ":00";
  const dateTZ = (utcMs) => { const d = new Date(utcMs + TZ); return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")}`; };
  const r0 = (x) => (x == null || isNaN(x) ? "—" : Math.round(x));
  const median = (a) => { const s = a.filter((x) => x != null && !isNaN(x)).sort((x, y) => x - y); if (!s.length) return null; const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  async function q(table, params) {
    const u = new URL(`${C.SUPABASE_URL}/rest/v1/${table}`);
    for (const [k, v] of params) u.searchParams.append(k, v);
    const r = await fetch(u, { headers: { apikey: C.SUPABASE_KEY } });
    if (!r.ok) throw new Error(`${table}: ${r.status} ${await r.text()}`);
    return r.json();
  }
  function banner(msg) { const b = document.getElementById("banner"); b.textContent = msg; b.classList.toggle("hidden", !msg); }

  // ---------- ma'lumot ----------
  async function loadObs() {
    // obs_unified: Toshkent vaqti "+00:00" belgisi bilan saqlanadi → haqiqiy UTC = qiymat − 5 soat
    const since = new Date(Date.now() + TZ - 49 * H).toISOString();
    const rows = await q("obs_unified", [["select", "station_id,timestamp,pm10,pm25"], ["timestamp", `gte.${since}`],
      ["station_id", `in.(${Object.keys(STATIONS).join(",")})`], ["order", "timestamp.asc"], ["limit", "5000"]]);
    const bucket = new Map();                       // soatlik o'rtacha: `${sid}|${hourUTC}`
    for (const r of rows) {
      const t = Date.parse(r.timestamp) - TZ, hr = Math.floor(t / H) * H, k = `${r.station_id}|${hr}`;
      const b = bucket.get(k) || { sid: r.station_id, t: hr, pm10: [], pm25: [] };
      if (r.pm10 != null) b.pm10.push(+r.pm10); if (r.pm25 != null) b.pm25.push(+r.pm25);
      bucket.set(k, b);
    }
    const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
    state.obs = [...bucket.values()].map((b) => ({ sid: b.sid, t: b.t, pm10: mean(b.pm10), pm25: mean(b.pm25) }))
      .sort((a, b) => a.t - b.t);
  }

  async function loadForecast() {
    const sid = C.FORECAST_STATION;
    const last = await q("forecasts_v81", [["select", "run_time"], ["station_id", `eq.${sid}`], ["model", "eq.Ensemble"],
      ["order", "run_time.desc"], ["limit", "1"]]);
    if (!last.length) { state.fc = []; return; }
    state.fcRun = Date.parse(last[0].run_time);
    const rows = await q("forecasts_v81", [["select", "model,pollutant,lead_h,target_time,p10,p50,p90,prob_high,alert"],
      ["run_time", `eq.${last[0].run_time}`], ["station_id", `eq.${sid}`], ["order", "lead_h.asc"]]);
    const all = rows.map((r) => ({ ...r, t: Date.parse(r.target_time) }));
    state.fcAll = all;
    state.fc = all.filter((r) => r.model === "Ensemble");
  }

  const latestBySid = () => {
    const out = {};
    for (const o of state.obs) out[o.sid] = o;      // obs vaqt bo'yicha tartiblangan → oxirgisi qoladi
    return out;
  };

  // ---------- umumiy holat ----------
  function renderHero() {
    const now = Date.now(), last = latestBySid();
    const fresh = Object.values(last).filter((o) => now - o.t <= STALE_H * H);
    const m25 = median(fresh.map((o) => o.pm25)), m10 = median(fresh.map((o) => o.pm10));
    const ci = catOf(m25, m10);
    const hero = document.getElementById("hero");
    hero.style.setProperty("--cat", colorOf(ci));
    document.getElementById("hero-cat").textContent = ci < 0 ? "Ma'lumot yo'q" : CAT[ci].name;
    document.getElementById("hero-advice").textContent = ci < 0 ? "So'nggi 3 soatda o'lchov kelmagan." : CAT[ci].advice;
    document.getElementById("m-pm25").textContent = r0(m25);
    document.getElementById("m-pm10").textContent = r0(m10);
    const worst = fresh.map((o) => ({ o, c: catOf(o.pm25, o.pm10), v: o.pm25 })).sort((a, b) => b.c - a.c || b.v - a.v)[0];
    document.getElementById("m-worst").textContent = worst ? `${STATIONS[worst.o.sid].name} · PM2.5 ${r0(worst.o.pm25)}` : "—";
    const badge = document.getElementById("hero-badge");
    badge.textContent = `${fresh.length}/${Object.keys(STATIONS).length} stansiya faol`;
    const lastT = Math.max(...fresh.map((o) => o.t), 0);
    document.getElementById("hero-foot").textContent =
      `Stansiyalar medianasi · ${lastT ? dateTZ(lastT) + " " + hh(lastT) : "—"} holatiga · DSanQN 0276-09 toifalari`;
  }

  // ---------- stansiyalar ro'yxati ----------
  function renderStations() {
    const now = Date.now(), last = latestBySid(), pol = state.stPol, alt = pol === "pm25" ? "pm10" : "pm25";
    const ul = document.getElementById("stations");
    ul.innerHTML = "";
    const isStale = (sid) => !last[sid] || now - last[sid].t > STALE_H * H;
    const ids = Object.keys(STATIONS).map(Number).sort((a, b) => (isStale(a) - isStale(b)) || ((last[b]?.[pol] ?? -1) - (last[a]?.[pol] ?? -1)));
    for (const sid of ids) {
      const o = last[sid], stale = !o || now - o.t > STALE_H * H;
      const li = document.createElement("li");
      if (sid === state.sel) li.classList.add("sel");
      li.innerHTML = `
        <span class="dot" style="background:${stale ? "#9aa5b4" : colorOf(lvl(pol, o?.[pol]))}"></span>
        <div><div class="st-name">${STATIONS[sid].name}</div>
             <div class="st-time">${o ? dateTZ(o.t) + " " + hh(o.t) + (stale ? " · ma'lumot eskirgan" : "") : "ma'lumot yo'q"}</div></div>
        <span class="st-val ${stale ? "stale" : ""}">${o ? r0(o[pol]) : "—"}</span>
        <span class="st-alt ${stale ? "stale" : ""}">${LABEL[alt]} ${o ? r0(o[alt]) : "—"}</span>`;
      li.onclick = () => { state.sel = sid; renderStations(); renderStationChart(); flyTo(sid); };
      ul.appendChild(li);
    }
  }

  // ---------- xarita ----------
  function initMap() {
    map = new maplibregl.Map({
      container: "map", style: "https://tiles.openfreemap.org/styles/positron",
      center: [69.30, 41.35], zoom: 10.4, attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const lg = document.getElementById("legend");
    lg.innerHTML = CAT.map((c) => `<span><i style="background:${c.color}"></i>${c.name}</span>`).join("") +
      `<span><i style="background:#9aa5b4"></i>Ma'lumot yo'q / eskirgan</span>`;
  }
  function renderMarkers() {
    if (!map) return;
    const now = Date.now(), last = latestBySid(), pol = state.stPol;
    for (const [sid, s] of Object.entries(STATIONS)) {
      const o = last[sid], stale = !o || now - o.t > STALE_H * H, v = o?.[pol];
      let m = markers[sid];
      if (!m) {
        const el = document.createElement("div"); el.className = "mk";
        el.onclick = () => { state.sel = Number(sid); renderStations(); renderStationChart(); };
        m = markers[sid] = new maplibregl.Marker({ element: el }).setLngLat([s.lon, s.lat])
          .setPopup(new maplibregl.Popup({ offset: 18 })).addTo(map);
      }
      const el = m.getElement();
      el.style.background = stale ? "#9aa5b4" : colorOf(lvl(pol, v));
      el.textContent = o && !stale ? r0(v) : "–";
      m.getPopup().setHTML(`<b>${s.name}</b><br>PM2.5: ${o ? r0(o.pm25) : "—"} · PM10: ${o ? r0(o.pm10) : "—"} µg/m³<br>
        <span style="color:#5d6b7e">${o ? dateTZ(o.t) + " " + hh(o.t) : "ma'lumot yo'q"}</span>`);
    }
  }
  const flyTo = (sid) => map && map.flyTo({ center: [STATIONS[sid].lon, STATIONS[sid].lat], zoom: 11.5 });

  // ---------- grafiklar ----------
  function axisTime() {
    return { type: "time", axisLabel: { color: css("--muted"), formatter: (v) => hh(v), hideOverlap: true },
             splitNumber: innerWidth < 600 ? 4 : 8,
             axisLine: { lineStyle: { color: css("--line") } }, splitLine: { show: false } };
  }
  function axisVal() {
    return { type: "value", name: "µg/m³", nameTextStyle: { color: css("--muted") }, min: 0,
             axisLabel: { color: css("--muted") }, splitLine: { lineStyle: { color: css("--line") } } };
  }
  function tooltip() {
    return { trigger: "axis", backgroundColor: css("--card"), borderColor: css("--line"), textStyle: { color: css("--ink") },
             formatter: (ps) => `<b>${dateTZ(ps[0].value[0])} ${hh(ps[0].value[0])}</b><br>` +
               ps.filter((p) => !p.seriesName.startsWith("_")).map((p) => `${p.marker}${p.seriesName}: <b>${r0(p.value[1])}</b>`).join("<br>") };
  }

  function renderForecastChart() {
    const pol = state.fcPol, sid = Number(C.FORECAST_STATION);
    const obs = state.obs.filter((o) => o.sid === sid && o.t >= Date.now() - 24 * H && o[pol] != null).map((o) => [o.t, o[pol]]);
    const fc = state.fc.filter((r) => r.pollutant === pol);
    const lo = fc.map((r) => [r.t, r.p10]), band = fc.map((r) => [r.t, r.p90 - r.p10]), mid = fc.map((r) => [r.t, r.p50]);
    const thr = THR[pol].slice(0, 2);
    const col = pol === "pm25" ? "#2f6fdb" : "#d9771f";
    fcChart.setOption({
      grid: { left: 48, right: 40, top: 48, bottom: 28 },
      tooltip: tooltip(), xAxis: axisTime(), yAxis: axisVal(),
      series: [
        { name: "Kuzatuv", type: "line", data: obs, showSymbol: false, lineStyle: { width: 2.5, color: css("--ink") }, itemStyle: { color: css("--ink") } },
        { name: "_lo", type: "line", data: lo, stack: "b", showSymbol: false, lineStyle: { opacity: 0 }, silent: true },
        { name: "_band", type: "line", data: band, stack: "b", showSymbol: false, lineStyle: { opacity: 0 },
          areaStyle: { color: col, opacity: 0.18 }, silent: true },
        { name: "Ansambl (p50)", type: "line", data: mid, showSymbol: false, smooth: 0.2, lineStyle: { width: 3, color: col }, itemStyle: { color: col },
          markLine: { silent: true, symbol: "none", label: { color: css("--muted"), formatter: (p) => `${p.value}` },
            data: [
              ...thr.map((v, i) => ({ yAxis: v, lineStyle: { color: CAT[i + 1].color, type: "dashed" } })),
              ...(state.fcRun ? [{ xAxis: Date.now(), lineStyle: { color: css("--muted"), type: "solid" }, label: { formatter: "hozir" } }] : []),
            ] } },
        ...MODELS.map((m) => ({
          name: m.name, type: "line", showSymbol: false, smooth: 0.2,
          data: (state.fcAll || []).filter((r) => r.model === m.id && r.pollutant === pol).map((r) => [r.t, r.p50]),
          lineStyle: { width: 1.6, type: m.id === "persistence" ? "dotted" : "dashed", color: m.color }, itemStyle: { color: m.color },
        })),
      ],
      legend: {
        top: 0, type: "scroll", textStyle: { color: css("--muted") },
        data: ["Kuzatuv", "Ansambl (p50)", ...MODELS.map((m) => m.name)],
        selected: Object.fromEntries(MODELS.map((m) => [m.name, state.modelsOn.has(m.name)])),
      },
    }, true);
    // toifa chizig'i (har soat uchun, p50 va p90 bo'yicha — bot bilan bir xil mantiq)
    const strip = document.getElementById("fc-strip");
    strip.innerHTML = fc.map((r) => {
      const i = Math.max(lvl(pol, r.p50), lvl(pol, r.p90));
      return `<div title="${hh(r.t)} · ${CAT[i].name}" style="background:${CAT[i].color}"></div>`;
    }).join("");
    document.getElementById("fc-sub").textContent = state.fcRun
      ? `O'zgidromet · kuzatuv va prognoz (80% ehtimollik oralig'i) · prognoz ${dateTZ(state.fcRun)} ${hh(state.fcRun)} da berilgan`
      : "Prognoz hozircha mavjud emas";
  }

  // Har bir model: tanlangan muddatlardagi p50 va ansambldan farq
  function renderModelTable() {
    const pol = state.fcPol, leads = [1, 3, 6, 12, 18, 24];
    const rows = [{ id: "Ensemble", name: "Ansambl", color: pol === "pm25" ? "#2f6fdb" : "#d9771f" }, ...MODELS];
    const get = (id, h) => (state.fcAll || []).find((r) => r.model === id && r.pollutant === pol && r.lead_h === h);
    const ens = state.fc.filter((r) => r.pollutant === pol);
    const head = `<tr><th>Model (${LABEL[pol]}, µg/m³)</th>${leads.map((h) => {
      const r = ens.find((x) => x.lead_h === h); return `<th>+${h} soat<br>${r ? hh(r.t) : ""}</th>`; }).join("")}</tr>`;
    const body = rows.map((m) => `<tr class="${m.id === "Ensemble" ? "ens" : ""}"><td><span class="sw" style="background:${m.color}"></span>${m.name}</td>${
      leads.map((h) => { const r = get(m.id, h); return `<td>${r ? r0(r.p50) : "—"}</td>`; }).join("")}</tr>`).join("");
    const spread = leads.map((h) => { const v = MODELS.slice(0, 4).map((m) => get(m.id, h)?.p50).filter((x) => x != null);
      return v.length ? r0(Math.max(...v) - Math.min(...v)) : "—"; });
    document.getElementById("m-table").innerHTML = head + body +
      `<tr><td class="muted">Modellar tarqoqligi (A, B, C: maks − min)</td>${spread.map((x) => `<td class="muted">${x}</td>`).join("")}</tr>`;
  }

  function renderBest() {
    const p25 = state.fc.filter((r) => r.pollutant === "pm25"), p10 = state.fc.filter((r) => r.pollutant === "pm10");
    if (p25.length < 24 || p10.length < 24) { document.getElementById("best-time").textContent = "—"; return; }
    // indeks: har bir modda o'z "qoniqarli" chegarasiga nisbatan (DSanQN), kattasi olinadi
    const idx = p25.map((r, i) => ({ t: r.t, v: Math.max(r.p50 / THR.pm25[0], p10[i].p50 / THR.pm10[0]) }));
    let best = null;
    for (let i = 0; i + 2 < idx.length; i++) {
      const hStart = new Date(idx[i].t + TZ).getUTCHours();
      if (hStart < 7 || hStart > 18) continue;      // 07:00–21:00 oralig'i
      const v = (idx[i].v + idx[i + 1].v + idx[i + 2].v) / 3;
      if (!best || v < best.v) best = { t: idx[i].t, v };
    }
    const el = document.getElementById("best-time"), note = document.getElementById("best-note");
    if (!best) { el.textContent = "—"; return; }
    el.textContent = `${hh(best.t)}–${hh(best.t + 3 * H)}`;
    const day = new Date(best.t + TZ).getUTCDate() === new Date(Date.now() + TZ).getUTCDate() ? "bugun" : "ertaga";
    note.textContent = `${day}, prognoz asosida 07:00–21:00 oralig'idagi eng toza 3 soat` +
      (best.v >= 1 ? ". Diqqat: bu vaqtda ham havo qoniqarli darajadan yuqori bo'lishi kutilmoqda." : "");
  }

  function renderStationChart() {
    const sid = state.sel;
    document.getElementById("st-title").textContent = `Stansiya: ${STATIONS[sid].name}`;
    const o = state.obs.filter((x) => x.sid === sid);
    stChart.setOption({
      grid: { left: 48, right: 16, top: 30, bottom: 28 },
      legend: { top: 0, textStyle: { color: css("--muted") } },
      tooltip: tooltip(), xAxis: axisTime(), yAxis: axisVal(),
      series: [
        { name: "PM2.5", type: "line", showSymbol: false, data: o.filter((x) => x.pm25 != null).map((x) => [x.t, x.pm25]), lineStyle: { width: 2, color: "#2f6fdb" }, itemStyle: { color: "#2f6fdb" } },
        { name: "PM10", type: "line", showSymbol: false, data: o.filter((x) => x.pm10 != null).map((x) => [x.t, x.pm10]), lineStyle: { width: 2, color: "#d9771f" }, itemStyle: { color: "#d9771f" } },
      ],
    }, true);
  }

  // ---------- boshqaruv ----------
  function bindSeg(id, key, after) {
    document.querySelectorAll(`#${id} button`).forEach((b) => b.onclick = () => {
      document.querySelectorAll(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b));
      state[key] = b.dataset.pol; after();
    });
  }

  async function refresh() {
    try {
      await Promise.all([loadObs(), loadForecast()]);
      banner("");
      renderHero(); renderStations(); renderMarkers(); renderForecastChart(); renderModelTable(); renderBest(); renderStationChart();
      document.getElementById("updated").textContent = `Yangilandi: ${dateTZ(Date.now())} ${new Date(Date.now() + TZ).toISOString().slice(11, 16)}`;
    } catch (e) {
      console.error(e);
      banner("Ma'lumotni yuklab bo'lmadi. Internet aloqasini tekshiring yoki birozdan keyin sahifani yangilang.");
    }
  }

  function init() {
    if (!C || !C.SUPABASE_KEY || C.SUPABASE_KEY.includes("BU_YERGA")) {
      banner("config.js faylida SUPABASE_KEY (sb_publishable_...) ko'rsatilmagan."); return;
    }
    fcChart = echarts.init(document.getElementById("fc-chart"));
    fcChart.on("legendselectchanged", (e) => {
      for (const m of MODELS) (e.selected[m.name] ? state.modelsOn.add(m.name) : state.modelsOn.delete(m.name));
    });
    stChart = echarts.init(document.getElementById("st-chart"));
    addEventListener("resize", () => { fcChart.resize(); stChart.resize(); });
    bindSeg("fc-pol", "fcPol", () => { renderForecastChart(); renderModelTable(); });
    bindSeg("st-pol", "stPol", () => { renderStations(); renderMarkers(); });
    try { initMap(); } catch (e) { console.error("xarita", e); }
    refresh();
    setInterval(refresh, (C.REFRESH_MIN || 10) * 60e3);
  }
  document.addEventListener("DOMContentLoaded", init);
})();
