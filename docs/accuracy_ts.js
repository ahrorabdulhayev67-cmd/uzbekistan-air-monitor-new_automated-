/* Toshkent havosi — "Aniqlik": prognoz va haqiqiy qiymat grafigi (ishonchli modellar bilan).
   Har bir nuqta — aynan tanlangan muddat (lead_h) oldin berilgan prognoz; vaqt — Toshkent vaqti.
   obs_unified: Toshkent vaqti "+00:00" bilan saqlanadi → haqiqiy UTC = qiymat − 5 soat (app.js bilan bir xil). */
(() => {
  const H5 = 5 * 3600e3, HR = 3600e3, DAY = 86400e3;
  const CFG = (typeof C !== "undefined" && C) || window.C || window.CONFIG || window.CFG || window.APP_CONFIG ||
              Object.values(window).find(o => o && typeof o === "object" && typeof o.SUPABASE_URL === "string") || {};
  const KEY = CFG.SUPABASE_ANON_KEY || CFG.SUPABASE_KEY || CFG.ANON_KEY || CFG.KEY ||
              Object.values(CFG).find(v => typeof v === "string" && (v.startsWith("eyJ") || v.startsWith("sb_")));
  const ST = [[107, "O'zgidromet"], [108, "Chilonzor"], [733, "Uchtepa"], [732, "Tashselmash"], [734, "Olmazor"],
              [730, "Safia"], [731, "Green University"], [720, "TTZ-4"], [729, "Yangi O'zbekiston"]];
  // Backtest (15.10–04.11) bo'yicha ishonchli modellar; zaif A-abs, A-rel, B ko'rsatilmaydi
  const MODELS = [["Ensemble", "Ansambl (operativ)", "#e67e22", true], ["Ensemble-K", "Ansambl-K", "#b7950b", true],
                  ["C", "C · TFT", "#c0392b", true], ["Ensemble-P", "Ansambl-P (sinov)", "#1a5276", false],
                  ["A2-rel", "A2 · nisbiy", "#2e86c1", false], ["B2", "B2 · box", "#8e44ad", false]];
  const MN = Object.fromEntries(MODELS.map(m => [m[0], m])), BAND = "Ansambl oralig'i p10–p90";
  const REK = { pm10: 75, pm25: 60 };                      // SanQvaN 0053-23, sutkalik
  const LBL = { pm10: "PM10", pm25: "PM2.5" };
  const S = { sid: 107, pol: "pm10", days: 1, lead: 24, sel: new Set(MODELS.filter(m => m[3]).map(m => m[0])) };
  const $ = (id) => document.getElementById(id);
  let chart, last = null, busy = 0;

  async function get(table, params) {
    const u = new URL(`${CFG.SUPABASE_URL}/rest/v1/${table}`);
    for (const [k, v] of params) u.searchParams.append(k, v);
    const r = await fetch(u, { headers: { apikey: KEY, Authorization: `Bearer ${KEY}` } });
    if (!r.ok) throw new Error(`${table}: ${r.status}`);
    return r.json();
  }
  const p2 = (n) => String(n).padStart(2, "0");
  const loc = (ms) => new Date(ms + H5);
  const fmtFull = (ms) => { const d = loc(ms); return `${p2(d.getUTCDate())}.${p2(d.getUTCMonth() + 1)} ${p2(d.getUTCHours())}:00`; };
  const fmtAxis = (ms) => { const d = loc(ms); return d.getUTCHours() === 0 ? `${p2(d.getUTCDate())}.${p2(d.getUTCMonth() + 1)}` : `${p2(d.getUTCHours())}:00`; };
  const sgn = (x) => (x >= 0 ? "+" : "") + x.toFixed(1);

  async function load() {
    const end = Math.floor(Date.now() / HR) * HR, t0 = end - S.days * DAY;
    const want = [...MODELS.map(m => m[0]), "persistence"];
    const [fc, ob] = await Promise.all([
      get("forecasts_v81", [["select", "model,target_time,p10,p50,p90"], ["station_id", `eq.${S.sid}`], ["pollutant", `eq.${S.pol}`],
        ["lead_h", `eq.${S.lead}`], ["model", `in.(${want.join(",")})`], ["target_time", `gte.${new Date(t0).toISOString()}`],
        ["target_time", `lte.${new Date(end).toISOString()}`], ["order", "target_time"], ["limit", "2000"]]),
      get("obs_unified", [["select", `timestamp,${S.pol}`], ["station_id", `eq.${S.sid}`],
        ["timestamp", `gte.${new Date(t0 + H5).toISOString()}`], ["order", "timestamp"], ["limit", "2000"]])]);
    const obs = new Map();
    for (const r of ob) { const v = r[S.pol]; if (v != null) obs.set(Math.floor((Date.parse(r.timestamp) - H5) / HR) * HR, +v); }
    const by = Object.fromEntries(want.map(k => [k, []]));
    for (const r of fc) if (by[r.model] && r.p50 != null)
      by[r.model].push({ t: Date.parse(r.target_time), p10: +(r.p10 ?? r.p50), p50: +r.p50, p90: +(r.p90 ?? r.p50) });
    return { t0, end, obs, by };
  }

  function metr(rows, obs, pers) {
    let n = 0, ae = 0, er = 0, inb = 0, np = 0, aem = 0, aep = 0;
    for (const r of rows) {
      const y = obs.get(r.t); if (y == null) continue;
      const e = r.p50 - y; n++; ae += Math.abs(e); er += e; if (y >= r.p10 && y <= r.p90) inb++;
      const p = pers.get(r.t); if (p != null) { np++; aem += Math.abs(e); aep += Math.abs(p - y); }
    }
    return n ? { n, mae: ae / n, bias: er / n, q: inb / n, skill: (np && aep > 0) ? 1 - aem / aep : null } : null;
  }

  function band(E) {
    return { name: BAND, type: "custom", silent: true, z: 1, tooltip: { show: false }, itemStyle: { color: "rgba(230,126,34,0.35)" },
      data: E.map(r => [r.t, r.p10, r.p90]), encode: { x: 0, y: [1, 2] },
      renderItem: (params, api) => {
        const i = params.dataIndex; if (i >= E.length - 1) return;
        const a = E[i], b = E[i + 1]; if (b.t - a.t > 1.5 * HR) return;
        return { type: "polygon", shape: { points: [api.coord([a.t, a.p10]), api.coord([b.t, b.p10]), api.coord([b.t, b.p90]), api.coord([a.t, a.p90])] },
                 style: { fill: "rgba(230,126,34,0.16)" } };
      } };
  }

  function render(d) {
    const { t0, end, obs, by } = d;
    const grid = []; for (let t = t0; t <= end; t += HR) grid.push(t);
    const series = [];
    if (S.sel.has("Ensemble") && by["Ensemble"].length) series.push(band(by["Ensemble"]));
    for (const [k, nm, col] of MODELS) {
      if (!S.sel.has(k) || !by[k].length) continue;
      series.push({ name: nm, type: "line", symbol: "none", z: 3, itemStyle: { color: col },
        lineStyle: { width: k === "Ensemble" ? 2.2 : 1.5, color: col, type: k === "Ensemble-P" ? "dashed" : "solid" },
        data: by[k].map(r => [r.t, r.p50]) });
    }
    series.push({ name: "Haqiqiy qiymat", type: "line", symbol: "circle", symbolSize: 3, connectNulls: false, z: 5,
      lineStyle: { width: 2.6, color: "#111827" }, itemStyle: { color: "#111827" },
      data: grid.map(t => [t, obs.has(t) ? obs.get(t) : null]),
      markLine: { silent: true, symbol: "none", lineStyle: { type: "dashed", color: "#c0392b", width: 1 },
        label: { formatter: `Sutkalik REK ${REK[S.pol]} (taqqoslash uchun)`, position: "insideEndTop", color: "#c0392b", fontSize: 10 },
        data: [{ yAxis: REK[S.pol] }] } });
    const E = by["Ensemble"];
    chart.setOption({
      animation: false, grid: { left: 44, right: 14, top: 40, bottom: 30 },
      legend: { top: 0, itemWidth: 16, textStyle: { fontSize: 11 }, data: series.map(s => s.name) },
      tooltip: { trigger: "axis", formatter: (ps) => {
        const rows = ps.filter(p => p.seriesName !== BAND && p.value && p.value[1] != null);
        if (!rows.length) return "";
        const e = E.find(r => r.t === rows[0].value[0]);
        return `<b>${fmtFull(rows[0].value[0])}</b><br>` + rows.map(p => `${p.marker}${p.seriesName}: <b>${(+p.value[1]).toFixed(1)}</b>`).join("<br>")
          + (e && S.sel.has("Ensemble") ? `<br><span style="color:#888">Ansambl p10–p90: ${e.p10.toFixed(0)}–${e.p90.toFixed(0)}</span>` : "") + " <span style='color:#888'>µg/m³</span>"; } },
      xAxis: { type: "time", min: t0, max: end, axisLabel: { formatter: fmtAxis, fontSize: 10, hideOverlap: true } },
      yAxis: { type: "value", name: "µg/m³", nameTextStyle: { fontSize: 10 }, min: 0, axisLabel: { fontSize: 10 } },
      series }, true);

    const pers = new Map(by["persistence"].map(r => [r.t, r.p50]));
    const M = MODELS.filter(m => S.sel.has(m[0])).map(([k, nm, col]) => ({ k, nm, col, r: by[k].length ? metr(by[k], obs, pers) : null }));
    const ok = M.filter(m => m.r).sort((a, b) => a.r.mae - b.r.mae), none = M.filter(m => !m.r);
    const best = ok.length > 1 ? ok[0].k : null;
    const st = ST.find(s => s[0] === S.sid)[1];
    let html = `<div class="ts-cap">${st} · ${LBL[S.pol]} · ${S.lead} soat oldingi prognoz · oxirgi ${S.days * 24} soat</div>`;
    if (ok.length) {
      html += `<table class="ts-tab"><tr><th>Model</th><th>MAE</th><th>Bias</th><th>Qamrov</th><th>Skill</th><th>Soat</th></tr>`
        + ok.map(m => `<tr${m.k === best ? ' class="ts-best"' : ""}><td><i style="background:${m.col}"></i>${m.nm}${m.k === best ? " ★" : ""}</td>`
          + `<td>${m.r.mae.toFixed(1)}</td><td>${sgn(m.r.bias)}</td><td>${(100 * m.r.q).toFixed(0)}%</td>`
          + `<td>${m.r.skill == null ? "—" : (m.r.skill >= 0 ? "+" : "") + (100 * m.r.skill).toFixed(0) + "%"}</td><td>${m.r.n}</td></tr>`).join("")
        + `</table>`;
    }
    if (none.length) html += `<div class="ts-note">${none.map(m => m.nm).join(", ")}: bu modda/muddat uchun hali ma'lumot yo'q</div>`;
    html += `<div class="ts-note">MAE va bias — µg/m³. Qamrov — haqiqiy qiymat p10–p90 oralig'iga tushgan ulush (ideal 80%). `
         + `Skill — persistence ("hozirgi qiymat saqlanadi") prognoziga nisbatan MAE kamayishi.</div>`;
    if (!obs.size) html += `<div class="ts-note" style="color:#c0392b">Bu davrda stansiya o'lchovi kelmagan.</div>`;
    $("ts-info").innerHTML = html;
  }

  async function refresh() {
    const my = ++busy; $("ts-info").textContent = "Yuklanmoqda…";
    try { const d = await load(); if (my !== busy) return; last = d; render(d); }
    catch (e) { if (my === busy) $("ts-info").textContent = `Ma'lumot olinmadi: ${e.message}`; }
  }
  function seg(id, key, conv) {
    $(id).addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-v]"); if (!b) return;
      for (const x of $(id).querySelectorAll("button")) x.classList.toggle("on", x === b);
      S[key] = conv(b.dataset.v); refresh();
    });
  }
  function init() {
    if (!$("ts-chart")) return;
    if (typeof echarts === "undefined") { $("ts-info").textContent = "Grafik kutubxonasi (ECharts) yuklanmadi."; return; }
    if (!CFG.SUPABASE_URL || !KEY) { $("ts-info").textContent = "Supabase sozlamasi topilmadi (config.js)."; return; }
    const sel = $("ts-st"); sel.innerHTML = ST.map(([id, nm]) => `<option value="${id}">${nm}</option>`).join("");
    sel.value = String(S.sid); sel.addEventListener("change", () => { S.sid = +sel.value; refresh(); });
    seg("ts-pol", "pol", String); seg("ts-days", "days", Number); seg("ts-lead", "lead", Number);
    const box = $("ts-models");
    box.innerHTML = MODELS.map(([k, nm, col]) => `<button data-m="${k}" class="${S.sel.has(k) ? "on" : ""}"><i style="background:${col}"></i>${nm}</button>`).join("");
    box.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-m]"); if (!b) return;
      const k = b.dataset.m; S.sel.has(k) ? S.sel.delete(k) : S.sel.add(k); b.classList.toggle("on", S.sel.has(k));
      if (last) render(last);
    });
    chart = echarts.init($("ts-chart")); window.addEventListener("resize", () => chart.resize());
    refresh();
  }
  const safe = () => { try { init(); } catch (e) { const i = $("ts-info"); if (i) i.textContent = `Grafik ishga tushmadi: ${e.message}`; console.error(e); } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", safe); else safe();
})();
