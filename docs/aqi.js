/* Toshkent havo sifati — xalqaro AQI (AQSh EPA, 2024) ko'rsatkichi "Hozir" sahifasida.
   Milliy toifa (DSanQN 0276-09) asosiy bo'lib qoladi; AQI — xalqaro taqqoslash uchun (IQAir, AirNow bilan bir xil shkala).
   Usul: shahar medianasi (stansiyalar) → soatlik qator → EPA NowCast (12 soat, w = max(cmin/cmax, 0.5)) →
   AQI = chiziqli interpolyatsiya (40 CFR Part 58, App. G; PM2.5 chegaralari 2024-yil yangilanishi) → max(PM2.5, PM10). */
(() => {
  "use strict";
  const C = window.TAQ_CONFIG;
  const box = document.querySelector(".hero-metrics");
  if (!C || !box) return;
  const STATIONS = [107, 108, 720, 729, 730, 731, 732, 733, 734];
  const H = 3600e3, TZ = 5 * H, STALE_H = 3;
  // [C_quyi, C_yuqori, I_quyi, I_yuqori]
  const BP = {
    pm25: [[0, 9.0, 0, 50], [9.1, 35.4, 51, 100], [35.5, 55.4, 101, 150], [55.5, 125.4, 151, 200],
           [125.5, 225.4, 201, 300], [225.5, 325.4, 301, 500]],
    pm10: [[0, 54, 0, 50], [55, 154, 51, 100], [155, 254, 101, 150], [255, 354, 151, 200],
           [355, 424, 201, 300], [425, 604, 301, 500]],
  };
  const CAT = [
    { max: 50,  name: "Yaxshi",                        color: "#00e400" },
    { max: 100, name: "O'rtacha",                      color: "#ffff00" },
    { max: 150, name: "Sezgir guruhlar uchun zararli", color: "#ff7e00" },
    { max: 200, name: "Zararli",                       color: "#ff0000" },
    { max: 300, name: "Juda zararli",                  color: "#8f3f97" },
    { max: 1e9, name: "Xavfli",                        color: "#7e0023" },
  ];

  // EPA NowCast (PM): c[0] — eng oxirgi soat
  function nowcast(c) {
    if (c.slice(0, 3).filter((x) => x != null).length < 2) return null;     // oxirgi 3 soatdan kamida 2 tasi kerak
    const v = c.filter((x) => x != null); if (!v.length) return null;
    const w = Math.max(Math.min(...v) / Math.max(...v), 0.5);
    let num = 0, den = 0;
    c.forEach((x, i) => { if (x != null) { const k = Math.pow(w, i); num += k * x; den += k; } });
    return num / den;
  }
  function aqi(pol, conc) {
    if (conc == null || isNaN(conc)) return null;
    const cc = pol === "pm25" ? Math.floor(conc * 10) / 10 : Math.floor(conc);        // EPA: kesib olish
    const b = BP[pol].find(([lo, hi]) => cc <= hi) || BP[pol][BP[pol].length - 1];
    const [clo, chi, ilo, ihi] = b;
    return Math.min(500, Math.round(((ihi - ilo) / (chi - clo)) * (Math.min(cc, chi) - clo) + ilo));
  }
  const med = (a) => { const s = a.filter((x) => x != null).sort((x, y) => x - y); if (!s.length) return null; const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

  // ---------- katakcha ----------
  const st = document.createElement("style");
  st.textContent = `
    .metric.aqi .aqi-row{display:flex;align-items:center;gap:8px}
    .metric.aqi .aqi-dot{width:12px;height:12px;border-radius:50%;flex:0 0 auto;border:1px solid rgba(0,0,0,.15)}
    .metric.aqi .aqi-cat{font:500 12px Inter,sans-serif;color:var(--muted)}
    .hero-metrics{grid-template-columns:repeat(4,minmax(0,1fr))}
    @media (max-width:900px){.hero-metrics{grid-template-columns:1fr 1fr}.hero-metrics .metric:last-child{grid-column:auto}}`;
  document.head.appendChild(st);
  const el = document.createElement("div");
  el.className = "metric aqi";
  el.title = "Xalqaro havo sifati indeksi (AQSh EPA, 2024). NowCast usuli: so'nggi 12 soat, yangi soatlar kuchliroq hisobga olinadi. " +
             "IQAir va AirNow bilan bir xil shkala. Milliy toifa (DSanQN 0276-09) bilan farq qilishi mumkin — standartlar turlicha qat'iy.";
  el.innerHTML = `<span class="m-label">AQI · AQSh EPA</span>
    <span class="aqi-row"><span class="aqi-dot" id="aqi-dot"></span><span class="m-val" id="aqi-val">—</span></span>
    <span class="aqi-cat" id="aqi-cat">xalqaro shkala</span>`;
  box.appendChild(el);

  async function load() {
    try {
      const since = new Date(Date.now() + TZ - 14 * H).toISOString();
      const u = new URL(`${C.SUPABASE_URL}/rest/v1/obs_unified`);
      u.searchParams.set("select", "station_id,timestamp,pm10,pm25");
      u.searchParams.set("timestamp", `gte.${since}`);
      u.searchParams.set("station_id", `in.(${STATIONS.join(",")})`);
      u.searchParams.set("limit", "5000");
      const r = await fetch(u, { headers: { apikey: C.SUPABASE_KEY } });
      if (!r.ok) throw new Error(r.status);
      const rows = await r.json();
      // soatlik: avval stansiya bo'yicha o'rtacha, keyin stansiyalar medianasi (shahar darajasi)
      const bySt = new Map();
      for (const o of rows) {
        const t = Math.floor((Date.parse(o.timestamp) - TZ) / H) * H, k = `${o.station_id}|${t}`;
        const b = bySt.get(k) || { t, pm25: [], pm10: [] };
        if (o.pm25 != null && +o.pm25 >= 1) b.pm25.push(+o.pm25);
        if (o.pm10 != null && +o.pm10 >= 1) b.pm10.push(+o.pm10);
        bySt.set(k, b);
      }
      const byH = new Map(), mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
      for (const b of bySt.values()) {
        const h = byH.get(b.t) || { pm25: [], pm10: [] };
        h.pm25.push(mean(b.pm25)); h.pm10.push(mean(b.pm10)); byH.set(b.t, h);
      }
      const last = Math.max(...byH.keys());
      if (!isFinite(last) || Date.now() - last > STALE_H * H) throw new Error("eskirgan");
      const series = (pol) => Array.from({ length: 12 }, (_, i) => { const h = byH.get(last - i * H); return h ? med(h[pol]) : null; });
      const i25 = aqi("pm25", nowcast(series("pm25"))), i10 = aqi("pm10", nowcast(series("pm10")));
      const val = Math.max(i25 ?? -1, i10 ?? -1);
      if (val < 0) throw new Error("ma'lumot yetarli emas");
      const c = CAT.find((x) => val <= x.max);
      document.getElementById("aqi-val").textContent = val;
      document.getElementById("aqi-dot").style.background = c.color;
      document.getElementById("aqi-cat").textContent = `${c.name} · ${i25 != null && i25 >= (i10 ?? -1) ? "PM2.5" : "PM10"} bo'yicha`;
    } catch (e) {
      console.warn("AQI", e);
      document.getElementById("aqi-val").textContent = "—";
      document.getElementById("aqi-cat").textContent = "ma'lumot yetarli emas";
    }
  }
  load();
  setInterval(load, (C.REFRESH_MIN || 10) * 60e3);
})();
