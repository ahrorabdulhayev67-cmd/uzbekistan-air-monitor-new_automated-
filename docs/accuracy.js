/* Toshkent havosi — "Aniqlik" sahifasi: v81_skill_daily jadvalidan dinamik verifikatsiya */
(() => {
  "use strict";
  const C = window.TAQ_CONFIG;
  const TZ = 5 * 3600e3;

  const STATIONS = {
    107: "O'zgidromet", 108: "Chilonzor", 733: "Uchtepa", 732: "Tashselmash", 734: "Olmazor",
    730: "Safia", 731: "Green University", 720: "TTZ-4", 729: "Yangi O'zbekiston",
  };
  const MODELS = [
    { id: "Ensemble",    name: "Ansambl",               color: "#e67e22" },
    { id: "A-rel",       name: "A · LightGBM (nisbiy)", color: "#16a085" },
    { id: "A-abs",       name: "A · LightGBM (mutlaq)", color: "#27ae60" },
    { id: "B",           name: "B · Box-model",         color: "#8e44ad" },
    { id: "C",           name: "C · TFT",               color: "#c0392b" },
    { id: "A2-rel",      name: "A2 · nisbiy (sinov)",   color: "#5dade2" },
    { id: "B2",          name: "B2 · box (sinov)",      color: "#d2b4de" },
    { id: "persistence", name: "Persistence",           color: "#7f8c8d" },
  ];
  const FC = MODELS.filter((m) => m.id !== "persistence");
  const GRPS = ["h1-3", "h4-6", "h7-12", "h13-24"];
  const GRP_LABEL = { "h1-3": "1–3 soat", "h4-6": "4–6 soat", "h7-12": "7–12 soat", "h13-24": "13–24 soat" };
  const LABEL = { pm25: "PM2.5", pm10: "PM10" };

  const state = { pol: "pm25", days: 7, st: "all", rows: [] };
  const charts = {};

  // ---------- yordamchilar ----------
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const f1 = (x) => (x == null || !isFinite(x) ? "—" : x.toFixed(1));
  const f2 = (x) => (x == null || !isFinite(x) ? "—" : x.toFixed(2));
  const pct = (x) => (x == null || !isFinite(x) ? "—" : `${x > 0 ? "+" : ""}${Math.round(x)}%`);
  const sgn = (x) => (x == null || !isFinite(x) ? "" : x > 0 ? "pos" : "neg");
  const todayTZ = () => new Date(Date.now() + TZ).toISOString().slice(0, 10);
  const addDays = (iso, n) => new Date(Date.parse(iso) + n * 864e5).toISOString().slice(0, 10);
  const dm = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
  function banner(msg) { const b = document.getElementById("banner"); b.textContent = msg; b.classList.toggle("hidden", !msg); }

  async function q(table, params) {
    const out = [];
    for (let off = 0; ; off += 1000) {
      const u = new URL(`${C.SUPABASE_URL}/rest/v1/${table}`);
      for (const [k, v] of params) u.searchParams.append(k, v);
      u.searchParams.set("limit", 1000); u.searchParams.set("offset", off);
      const r = await fetch(u, { headers: { apikey: C.SUPABASE_KEY } });
      if (!r.ok) throw new Error(`${table}: ${r.status} ${await r.text()}`);
      const d = await r.json(); out.push(...d);
      if (d.length < 1000) return out;
    }
  }

  // Yig'indilardan metrika: guruhlab qo'shib, keyin bo'lamiz (har bir juftlik teng og'irlikda)
  function agg(rows, keyFn) {
    const m = new Map();
    for (const r of rows) {
      const k = keyFn(r);
      const a = m.get(k) || { n: 0, ae: 0, err: 0, se: 0, in80: 0, ok: 0, nok: 0 };
      a.n += r.n; a.ae += r.sum_ae; a.err += r.sum_err; a.se += r.sum_se; a.in80 += r.n_in80;
      if (r.n_ok != null && isFinite(r.n_ok)) { a.ok += r.n_ok; a.nok += r.n; }
      m.set(k, a);
    }
    for (const a of m.values()) {
      a.MAE = a.ae / a.n; a.bias = a.err / a.n; a.RMSE = Math.sqrt(a.se / a.n); a.cov = a.in80 / a.n;
      a.just = a.nok ? (a.ok / a.nok) * 100 : null;
    }
    return m;
  }
  const skill = (m, keyOf) => (id, ...rest) => {
    const a = m.get(keyOf(id, ...rest)), p = m.get(keyOf("persistence", ...rest));
    return a && p && p.MAE > 0 ? (1 - a.MAE / p.MAE) * 100 : null;
  };

  // ---------- ma'lumot ----------
  async function load() {
    const since = addDays(todayTZ(), -60);
    const rows = await q("v81_skill_daily", [
      ["select", "date,station_id,model,pollutant,lead_grp,n,sum_ae,sum_err,sum_se,n_in80,n_ok"],
      ["date", `gte.${since}`], ["order", "date.asc"]]);
    state.rows = rows.map((r) => ({ ...r, n: +r.n, sum_ae: +r.sum_ae, sum_err: +r.sum_err, sum_se: +r.sum_se, n_in80: +r.n_in80, n_ok: r.n_ok == null ? null : +r.n_ok }));
  }

  function filtered(withStation = true) {
    const end = todayTZ(), start = state.days ? addDays(end, -(state.days - 1)) : "0000";
    return state.rows.filter((r) => r.pollutant === state.pol && r.date >= start &&
      (!withStation || state.st === "all" || r.station_id === Number(state.st)));
  }

  // ---------- chizish ----------
  const base = () => ({
    grid: { left: 46, right: 16, top: 56, bottom: 30 },
    textStyle: { fontFamily: "Inter, sans-serif" },
    legend: { top: 0, type: "scroll", textStyle: { color: css("--muted") } },
    tooltip: { trigger: "axis", backgroundColor: css("--card"), borderColor: css("--line"), textStyle: { color: css("--ink") },
               valueFormatter: (v) => (v == null ? "—" : Math.round(v * 10) / 10) },
  });
  const yAx = (name, extra = {}) => ({ type: "value", name, nameTextStyle: { color: css("--muted") },
    axisLabel: { color: css("--muted") }, splitLine: { lineStyle: { color: css("--line") } }, ...extra });
  const xCat = (data) => ({ type: "category", data, axisLabel: { color: css("--muted"), hideOverlap: true },
    axisLine: { lineStyle: { color: css("--line") } } });
  const zero = { silent: true, symbol: "none", data: [{ yAxis: 0 }], lineStyle: { color: css("--muted"), type: "solid" }, label: { show: false } };

  function renderTable(rows) {
    const m = agg(rows, (r) => r.model), sk = skill(m, (id) => id);
    const list = MODELS.filter((x) => m.has(x.id)).map((x) => ({ ...x, a: m.get(x.id), s: sk(x.id) }));
    list.sort((a, b) => (a.id === "persistence") - (b.id === "persistence") || (b.s ?? -1e9) - (a.s ?? -1e9));
    document.getElementById("t-models").innerHTML =
      `<tr><th>Model</th><th>Juftlik</th><th>MAE</th><th>Bias</th><th>RMSE</th><th>Qamrov 80%</th><th>Oqlanish</th><th>Skill</th></tr>` +
      list.map((x) => `<tr class="${x.id === "Ensemble" ? "ens" : ""}"><td><span class="sw" style="background:${x.color}"></span>${x.name}</td>
        <td>${x.a.n.toLocaleString("ru-RU")}</td><td>${f1(x.a.MAE)}</td><td>${x.a.bias > 0 ? "+" : ""}${f1(x.a.bias)}</td>
        <td>${f1(x.a.RMSE)}</td><td>${x.id === "persistence" ? "—" : f2(x.a.cov)}</td>
        <td>${x.a.just == null ? "—" : Math.round(x.a.just) + "%"}</td>
        <td class="${x.id === "persistence" ? "" : sgn(x.s)}">${x.id === "persistence" ? "—" : pct(x.s)}</td></tr>`).join("");

    const best = list.filter((x) => x.id !== "persistence")[0], ens = m.get("Ensemble");
    const p = m.get("persistence");
    document.getElementById("kpis").innerHTML = [
      { l: "Ansambl skill", v: pct(sk("Ensemble")), c: sgn(sk("Ensemble")), n: "persistence'ga nisbatan" },
      { l: "Ansambl MAE", v: ens ? f1(ens.MAE) : "—", n: `µg/m³ · persistence: ${p ? f1(p.MAE) : "—"}` },
      { l: "Eng yaxshi model", v: best ? best.name.split(" (")[0] : "—", n: best ? `skill ${pct(best.s)}` : "" },
      { l: "Ansambl oqlanishi", v: ens && ens.just != null ? Math.round(ens.just) + "%" : "—",
        n: `persistence: ${p && p.just != null ? Math.round(p.just) + "%" : "—"} · ${ens ? ens.n.toLocaleString("ru-RU") : 0} juftlik` },
    ].map((k) => `<div class="kpi"><div class="k-label">${k.l}</div><div class="k-val ${k.c || ""}">${k.v}</div><div class="k-note">${k.n}</div></div>`).join("");

    const dates = [...new Set(rows.map((r) => r.date))].sort();
    const stTxt = state.st === "all" ? `${new Set(rows.map((r) => r.station_id)).size} ta stansiya` : STATIONS[state.st] || state.st;
    document.getElementById("t-sub").textContent = dates.length
      ? `${LABEL[state.pol]} · ${dm(dates[0])} – ${dm(dates.at(-1))} · ${stTxt} · barcha muddatlar (1–24 soat)`
      : "Tanlangan davrda ma'lumot yo'q";
  }

  function renderDaily(rows) {
    const m = agg(rows, (r) => `${r.model}|${r.date}`), sk = skill(m, (id, d) => `${id}|${d}`);
    const dates = [...new Set(rows.map((r) => r.date))].sort();
    charts.daily.setOption({
      ...base(), xAxis: xCat(dates.map(dm)), yAxis: yAx("%"),
      series: FC.map((x, i) => ({
        name: x.name, type: "line", data: dates.map((d) => sk(x.id, d)), connectNulls: true, symbolSize: 5,
        lineStyle: { width: x.id === "Ensemble" ? 3 : 1.6, color: x.color }, itemStyle: { color: x.color },
        markLine: i === 0 ? zero : undefined,
      })),
    }, true);
  }

  function renderLead(rows) {
    const m = agg(rows, (r) => `${r.model}|${r.lead_grp}`), sk = skill(m, (id, g) => `${id}|${g}`);
    charts.lead.setOption({
      ...base(), tooltip: { ...base().tooltip, trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: xCat(GRPS.map((g) => GRP_LABEL[g])), yAxis: yAx("%"),
      series: FC.map((x, i) => ({ name: x.name, type: "bar", data: GRPS.map((g) => sk(x.id, g)), itemStyle: { color: x.color, borderRadius: 3 },
                                  markLine: i === 0 ? zero : undefined })),
    }, true);
  }

  function renderBias(rows) {
    const m = agg(rows, (r) => `${r.model}|${r.date}`);
    const dates = [...new Set(rows.map((r) => r.date))].sort();
    charts.bias.setOption({
      ...base(), xAxis: xCat(dates.map(dm)), yAxis: yAx("µg/m³"),
      series: MODELS.map((x, i) => ({
        name: x.name, type: "line", data: dates.map((d) => m.get(`${x.id}|${d}`)?.bias ?? null), connectNulls: true, symbolSize: 5,
        lineStyle: { width: x.id === "Ensemble" ? 3 : 1.4, color: x.color, type: x.id === "persistence" ? "dotted" : "solid" },
        itemStyle: { color: x.color }, markLine: i === 0 ? zero : undefined,
      })),
    }, true);
  }

  function renderStations() {
    const rows = filtered(false);
    const m = agg(rows, (r) => `${r.model}|${r.station_id}`), sk = skill(m, (id, s) => `${id}|${s}`);
    const sids = Object.keys(STATIONS).map(Number).filter((s) => m.has(`Ensemble|${s}`));
    const vals = sids.map((s) => sk("Ensemble", s));
    const order = sids.map((s, i) => [s, vals[i]]).sort((a, b) => (b[1] ?? -1e9) - (a[1] ?? -1e9));
    charts.st.setOption({
      ...base(), legend: { show: false },
      tooltip: { ...base().tooltip, axisPointer: { type: "shadow" } },
      grid: { left: 130, right: 30, top: 10, bottom: 30 },
      yAxis: { type: "category", inverse: true, data: order.map(([s]) => STATIONS[s]), axisLabel: { color: css("--muted") },
               axisLine: { lineStyle: { color: css("--line") } } },
      xAxis: yAx("%"),
      series: [{ name: "Ansambl skill", type: "bar", barMaxWidth: 18,
        data: order.map(([s, v]) => ({ value: v, itemStyle: { borderRadius: 3,
          color: v == null ? css("--line") : v >= 0 ? "#1e9e57" : "#d64545",
          opacity: state.st === "all" || Number(state.st) === s ? 1 : 0.35 } })),
        label: { show: true, position: "right", color: css("--muted"), formatter: (p) => pct(p.value) } }],
    }, true);
  }

  function render() {
    const rows = filtered();
    if (!state.rows.length) banner("Aniqlik ma'lumotlari hali yig'ilmagan. Verifikatsiya har 3 soatda ishlaydi.");
    else if (!rows.length) banner("Tanlangan davr yoki stansiya uchun ma'lumot yo'q.");
    else banner("");
    renderTable(rows); renderDaily(rows); renderLead(rows); renderBias(rows); renderStations();
    const last = state.rows.at(-1)?.date;
    document.getElementById("updated").textContent = last ? `Oxirgi kun: ${dm(last)}` : "—";
  }

  // ---------- PDF ----------
  function openPdf(days) {
    const url = `${C.SUPABASE_URL}/storage/v1/object/public/reports/accuracy/latest_${days}d.pdf?t=${Date.now()}`;
    const tg = window.Telegram && Telegram.WebApp;
    if (tg && tg.initData) tg.openLink(url);           // Telegram ichida: tashqi brauzerda ochiladi va yuklanadi
    else window.open(url, "_blank", "noopener");
  }

  // ---------- boshqaruv ----------
  function bindSeg(id, key, conv) {
    document.querySelectorAll(`#${id} button`).forEach((b) => b.onclick = () => {
      document.querySelectorAll(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b));
      state[key] = conv(b.dataset.v); render();
    });
  }

  async function init() {
    if (!C || !C.SUPABASE_KEY) { banner("config.js faylida SUPABASE_KEY ko'rsatilmagan."); return; }
    const sel = document.getElementById("c-st");
    sel.innerHTML = `<option value="all">Barcha stansiyalar</option>` +
      Object.entries(STATIONS).map(([id, n]) => `<option value="${id}">${n}</option>`).join("");
    sel.onchange = () => { state.st = sel.value; render(); };
    bindSeg("c-pol", "pol", (v) => v);
    bindSeg("c-days", "days", Number);
    document.querySelectorAll(".pdf-btns .btn").forEach((b) => b.onclick = () => openPdf(b.dataset.days));
    for (const k of ["daily", "lead", "bias", "st"]) charts[k] = echarts.init(document.getElementById(`ch-${k}`));
    addEventListener("resize", () => Object.values(charts).forEach((c) => c.resize()));
    try { await load(); render(); }
    catch (e) { console.error(e); banner("Ma'lumotni yuklab bo'lmadi. Birozdan keyin sahifani yangilang."); }
  }
  document.addEventListener("DOMContentLoaded", init);
})();
