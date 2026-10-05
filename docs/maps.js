/* Toshkent havosi — "Xaritalar" sahifasi: SILAM / CAMS (O'zbekiston) va Toshkent 100 m qatlami */
(() => {
  "use strict";
  const C = window.TAQ_CONFIG;
  const PUB = `${C.SUPABASE_URL}/storage/v1/object/public/maps`;
  const H = 3600e3, TZ = 5 * H;

  // JSST (WHO) 2021 yo'riqnomasi darajalari asosidagi diskret shkala (Jupyter'da tasdiqlangan)
  const LEVELS = {
    pm25: [0, 5, 15, 25, 37.5, 50, 75, 150, 300],
    pm10: [0, 20, 45, 50, 75, 100, 150, 300, 600],
    no2:  [0, 5, 10, 25, 50, 120, 200],
    so2:  [0, 5, 10, 20, 40, 50, 125, 250],
    o3:   [0, 40, 60, 80, 100, 120, 160, 240],
    co:   [0, 0.1, 0.2, 0.4, 1, 4, 7, 10],
    dust: [0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.5],
  };
  // Toshkent 100 m rejimi: shahar ichidagi farqni ko'rsatish uchun maydaroq shkala (pastki qismi WHO bilan bir xil)
  const TASH_LEVELS = {
    pm25: [0, 10, 15, 20, 25, 30, 37.5, 50, 75],
    pm10: [0, 30, 45, 60, 75, 90, 110, 150, 250],
  };
  const PAL = ["#e6f4ea", "#b7e1c1", "#7cc49a", "#f6e27f", "#f4b55f", "#ec7a52", "#d04a5c", "#9b3a8c", "#5b2a6e"];
  const VAR = { pm25: "PM2.5", pm10: "PM10", no2: "NO₂", so2: "SO₂", o3: "O₃", co: "CO", dust: "Chang (AOD)" };
  const MODEL_VARS = ["pm25", "pm10", "no2", "so2", "o3", "co", "dust"];
  const TASH_VARS = ["pm25", "pm10"];
  const TASH_STATIONS = [107, 108, 720, 729, 730, 731, 732, 733, 734, 738, 739];
  const UZ_BOUNDS = [[55.5, 37.0], [73.5, 46.0]];
  const ALPHA = 200;
  const BUFFER_M = 2000;                               // shahar chegarasidan tashqariga ko'rsatiladigan masofa
  const levFor = (v) => (S.mode === "tashkent" ? TASH_LEVELS[v] : LEVELS[v]);                                   // qatlam shaffofligi (0–255)

  const S = { mode: "silam", v: "pm25", step: 0, t: null, corr: true, playing: null, frames: [] };
  const nearest = (arr, t) => { let b = 0; for (let i = 1; i < arr.length; i++) if (Math.abs(arr[i] - t) < Math.abs(arr[b] - t)) b = i; return b; };
  const cache = { silam: null, cams: null, tash: null };
  let map, ptChart, ptLngLat = null;

  // ---------- yordamchilar ----------
  const $ = (id) => document.getElementById(id);
  const fmtT = (ms) => { const d = new Date(ms + TZ); return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")} ${String(d.getUTCHours()).padStart(2, "0")}:00`; };
  const banner = (m) => { $("banner").textContent = m || ""; $("banner").classList.toggle("hidden", !m); };
  const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
  const imerc = (y) => (360 / Math.PI) * Math.atan(Math.exp(y)) - 90;
  const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  const PALRGB = PAL.map(hex);
  function classify(v, lev) { if (!(v >= 0)) return -1; let i = 0; while (i < lev.length - 1 && v >= lev[i + 1]) i++; return i; }
  async function getJSON(u) { const r = await fetch(u, { cache: "no-cache" }); if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); }
  async function getU16(u) { const r = await fetch(u, { cache: "no-cache" }); if (!r.ok) throw new Error(`${u}: ${r.status}`); return new Uint16Array(await r.arrayBuffer()); }
  async function q(table, params) {
    const u = new URL(`${C.SUPABASE_URL}/rest/v1/${table}`);
    for (const [k, v] of params) u.searchParams.append(k, v);
    const r = await fetch(u, { headers: { apikey: C.SUPABASE_KEY } });
    if (!r.ok) throw new Error(`${table}: ${r.status}`);
    return r.json();
  }

  // ---------- model ma'lumoti (SILAM / CAMS) ----------
  async function loadModel(m) {
    if (cache[m]) return cache[m];
    const meta = await getJSON(`${PUB}/${m}/latest/meta.json`);
    const lat = meta.lat.slice(), lon = meta.lon.slice(), flip = lat[0] > lat[lat.length - 1];
    cache[m] = { meta, times: meta.times.map((t) => Date.parse(t)), lat: flip ? lat.reverse() : lat, lon,
                 flip, ny: lat.length, nx: lon.length, data: {}, ratio: {} };
    return cache[m];
  }
  async function modelVar(M, v) {
    if (!M.data[v]) {
      const raw = await getU16(`${PUB}/${M.meta.model}/latest/${v}.bin`), sc = M.meta.vars[v].scale;
      const f = new Float32Array(raw.length); for (let i = 0; i < raw.length; i++) f[i] = raw[i] * sc;
      M.data[v] = f;
    }
    if ((v === "pm25" || v === "pm10") && M.meta.ratio && M.meta.ratio[v] && !M.ratio[v]) {
      const raw = await getU16(`${PUB}/${M.meta.model}/latest/ratio_${v}.bin`);
      M.ratio[v] = Float32Array.from(raw, (x) => x * M.meta.ratio[v].scale);
    }
    return M.data[v];
  }
  // panjaradagi qiymat (bilinear), iy — janubdan shimolga
  function sampleModel(M, v, t, fy, fx, useRatio) {
    const { ny, nx, flip } = M, a = M.data[v], off = t * ny * nx;
    fy = Math.max(0, Math.min(ny - 1, fy)); fx = Math.max(0, Math.min(nx - 1, fx));
    const y0 = Math.floor(fy), x0 = Math.floor(fx), y1 = Math.min(y0 + 1, ny - 1), x1 = Math.min(x0 + 1, nx - 1);
    const wy = fy - y0, wx = fx - x0;
    const at = (arr, base, iy, ix) => arr[base + (flip ? ny - 1 - iy : iy) * nx + ix];
    const bil = (arr, base) => (at(arr, base, y0, x0) * (1 - wx) + at(arr, base, y0, x1) * wx) * (1 - wy) +
                               (at(arr, base, y1, x0) * (1 - wx) + at(arr, base, y1, x1) * wx) * wy;
    let val = bil(a, off);
    if (useRatio && M.ratio[v]) val *= bil(M.ratio[v], 0);
    return val;
  }
  // Ko'rsatish uchun bikubik (Catmull–Rom) interpolyatsiya: 20–40 km katakchalar "zinapoya" bo'lib ko'rinmaydi
  const cr = (p0, p1, p2, p3, x) => p1 + 0.5 * x * (p2 - p0 + x * (2 * p0 - 5 * p1 + 4 * p2 - p3 + x * (3 * (p1 - p2) + p3 - p0)));
  function sampleCubic(M, v, t, fy, fx, useRatio) {
    const { ny, nx, flip } = M, a = M.data[v], off = t * ny * nx;
    fy = Math.max(0, Math.min(ny - 1, fy)); fx = Math.max(0, Math.min(nx - 1, fx));
    const y1 = Math.floor(fy), x1 = Math.floor(fx), wy = fy - y1, wx = fx - x1;
    const at = (iy, ix) => { iy = Math.max(0, Math.min(ny - 1, iy)); ix = Math.max(0, Math.min(nx - 1, ix));
                             return a[off + (flip ? ny - 1 - iy : iy) * nx + ix]; };
    const row = (iy) => cr(at(iy, x1 - 1), at(iy, x1), at(iy, x1 + 1), at(iy, x1 + 2), wx);
    let val = Math.max(0, cr(row(y1 - 1), row(y1), row(y1 + 1), row(y1 + 2), wy));
    if (useRatio && M.ratio[v]) val *= sampleModel({ ...M, data: { r: M.ratio[v] } }, "r", 0, fy, fx, false);
    return val;
  }
  function renderModel(M, v, t) {
    const up = Math.max(4, Math.ceil(720 / M.nx)), W = M.nx * up, Hh = M.ny * up;
    const dlat = M.lat[1] - M.lat[0], dlon = M.lon[1] - M.lon[0];
    const latMin = M.lat[0] - dlat / 2, latMax = M.lat[M.ny - 1] + dlat / 2;
    const lonMin = M.lon[0] - dlon / 2, lonMax = M.lon[M.nx - 1] + dlon / 2;
    const yT = merc(latMax), yB = merc(latMin), lev = LEVELS[v];
    const useRatio = S.corr && !!M.ratio[v];
    const cv = document.createElement("canvas"); cv.width = W; cv.height = Hh;
    const ctx = cv.getContext("2d"), img = ctx.createImageData(W, Hh), d = img.data;
    for (let r = 0; r < Hh; r++) {
      const lat = imerc(yT + ((r + 0.5) / Hh) * (yB - yT)), fy = (lat - M.lat[0]) / dlat;
      for (let c = 0; c < W; c++) {
        const lon = lonMin + ((c + 0.5) / W) * (lonMax - lonMin), fx = (lon - M.lon[0]) / dlon;
        const k = classify(sampleCubic(M, v, t, fy, fx, useRatio), lev), p = (r * W + c) * 4;
        if (k < 0) continue;
        const rgb = PALRGB[k]; d[p] = rgb[0]; d[p + 1] = rgb[1]; d[p + 2] = rgb[2]; d[p + 3] = ALPHA;
      }
    }
    ctx.putImageData(img, 0, 0);
    return { url: cv.toDataURL(), coords: [[lonMin, latMax], [lonMax, latMax], [lonMax, latMin], [lonMin, latMin]] };
  }

  // ---------- Toshkent qatlami ----------
  async function loadTashkent() {
    if (cache.tash) return cache.tash;
    const p = await getJSON(`${PUB}/tashkent/tashkent_lur.json`);
    const [Hh, W] = p.shape, fac = {};
    for (const v of TASH_VARS) {
      const raw = await getU16(`${PUB}/tashkent/factor_${v}.bin`);
      fac[v] = Float32Array.from(raw, (x) => x * p.pollutants[v].scale);
    }
    // shahar darajasi: o'tgan 24 soat — stansiyalar medianasi; keyingi 24 soat — V8.1 ansambli / F107
    const since = new Date(Date.now() + TZ - 26 * H).toISOString();
    const obs = await q("obs_unified", [["select", "station_id,timestamp,pm10,pm25"], ["timestamp", `gte.${since}`],
      ["station_id", `in.(${TASH_STATIONS.join(",")})`], ["limit", "5000"]]);
    const byH = new Map(), st = new Map();
    for (const o of obs) {
      const t = Math.floor((Date.parse(o.timestamp) - TZ) / H) * H;
      const b = byH.get(t) || { pm25: [], pm10: [] };
      if (o.pm25 != null) b.pm25.push(+o.pm25); if (o.pm10 != null) b.pm10.push(+o.pm10); byH.set(t, b);
      st.set(`${o.station_id}|${t}`, { pm25: o.pm25 == null ? null : +o.pm25, pm10: o.pm10 == null ? null : +o.pm10 });
    }
    let boundary = null, stInfo = [];
    try { boundary = await getJSON(`${PUB}/tashkent/tashkent_boundary.geojson`); } catch (e) { console.warn("chegara", e); }
    try {
      const li = await q("obs_unified", [["select", "station_id,lat,lon"], ["station_id", `in.(${TASH_STATIONS.join(",")})`],
        ["order", "timestamp.desc"], ["limit", "200"]]);
      const seen = new Set();
      for (const r of li) if (!seen.has(r.station_id)) { seen.add(r.station_id); stInfo.push({ id: r.station_id, lat: +r.lat, lon: +r.lon }); }
    } catch (e) { console.warn("stansiyalar", e); }
    const med = (a) => { if (a.length < 4) return null; const s = a.slice().sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
    const frames = [...byH.entries()].sort((a, b) => a[0] - b[0])
      .map(([t, b]) => ({ t, kind: "kuzatuv", pm25: med(b.pm25), pm10: med(b.pm10) })).filter((f) => f.pm25 != null && f.pm10 != null);
    try {
      const last = await q("forecasts_v81", [["select", "run_time"], ["station_id", `eq.${C.FORECAST_STATION}`], ["model", "eq.Ensemble"],
        ["order", "run_time.desc"], ["limit", "1"]]);
      if (last.length) {
        const fc = await q("forecasts_v81", [["select", "pollutant,target_time,p50"], ["run_time", `eq.${last[0].run_time}`],
          ["station_id", `eq.${C.FORECAST_STATION}`], ["model", "eq.Ensemble"], ["order", "lead_h.asc"]]);
        const tmax = frames.length ? frames[frames.length - 1].t : 0, fm = new Map();
        for (const r of fc) { const t = Date.parse(r.target_time); if (t <= tmax) continue;
          const f = fm.get(t) || { t, kind: "prognoz" }; f[r.pollutant] = r.p50 / p.pollutants[r.pollutant].F107; fm.set(t, f); }
        frames.push(...[...fm.values()].filter((f) => f.pm25 != null && f.pm10 != null).sort((a, b) => a.t - b.t));
      }
    } catch (e) { console.warn("prognoz", e); }
    cache.tash = { p, Hh, W, fac, frames, st, stInfo, mask: boundary ? buildMask(p, Hh, W, boundary) : null };
    return cache.tash;
  }
  // Shahar chegarasi + BUFFER_M: niqob (1 = ko'rsatiladi). Chegaradan uzoqda model ishonchsiz (stansiyalar yo'q).
  function buildMask(p, Hh, W, gj) {
    const [w, s, e, n] = p.bbox, cv = document.createElement("canvas"); cv.width = W; cv.height = Hh;
    const ctx = cv.getContext("2d"), px = (lon, lat) => [((lon - w) / (e - w)) * W, ((n - lat) / (n - s)) * Hh];
    const mPerPx = ((e - w) / W) * 111320 * Math.cos(((s + n) / 2) * Math.PI / 180);
    ctx.fillStyle = ctx.strokeStyle = "#000"; ctx.lineJoin = ctx.lineCap = "round"; ctx.lineWidth = (2 * BUFFER_M) / mPerPx;
    const polys = [];
    for (const ft of gj.features || [gj]) {
      const g = ft.geometry || ft;
      if (g.type === "Polygon") polys.push(g.coordinates); else if (g.type === "MultiPolygon") polys.push(...g.coordinates);
    }
    for (const poly of polys) {
      ctx.beginPath();
      for (const ring of poly) ring.forEach(([lo, la], k) => { const [x, y] = px(lo, la); k ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
      ctx.closePath(); ctx.fill("evenodd"); ctx.stroke();
    }
    const a = ctx.getImageData(0, 0, W, Hh).data, m = new Uint8Array(W * Hh);
    let on = 0;
    for (let k = 0; k < m.length; k++) { m[k] = a[k * 4 + 3] > 0 ? 1 : 0; on += m[k]; }
    if (on < m.length * 0.01) { console.warn("Chegara niqobi bo'sh (koordinatalar lon/lat emasmi?) — niqobsiz ko'rsatiladi"); return null; }
    return m;
  }

  function renderTashkent(T, v, i) {
    const f = T.frames[i], level = f[v], lev = TASH_LEVELS[v], F = T.fac[v];
    const cv = document.createElement("canvas"); cv.width = T.W; cv.height = T.Hh;
    const ctx = cv.getContext("2d"), img = ctx.createImageData(T.W, T.Hh), d = img.data;
    for (let k = 0; k < F.length; k++) {
      if (F[k] <= 0 || (T.mask && !T.mask[k])) continue;
      const c = classify(level * F[k], lev); if (c < 0) continue;
      const rgb = PALRGB[c], p = k * 4; d[p] = rgb[0]; d[p + 1] = rgb[1]; d[p + 2] = rgb[2]; d[p + 3] = ALPHA;
    }
    ctx.putImageData(img, 0, 0);
    const [w, s, e, n] = T.p.bbox;
    return { url: cv.toDataURL(), coords: [[w, n], [e, n], [e, s], [w, s]] };
  }
  const tashFactorAt = (T, v, lng, lat) => {
    const [w, s, e, n] = T.p.bbox;
    const c = Math.floor(((lng - w) / (e - w)) * T.W), r = Math.floor(((n - lat) / (n - s)) * T.Hh);
    return c >= 0 && c < T.W && r >= 0 && r < T.Hh ? T.fac[v][r * T.W + c] : null;
  };

  // Stansiyalar: kuzatuv soatlarida o'lchangan qiymat (xaritani o'lchov bilan solishtirish uchun)
  const stMarkers = new Map();
  function renderStations(T, v, i) {
    const f = T.frames[i];
    for (const s of T.stInfo) {
      const o = f.kind === "kuzatuv" ? T.st.get(`${s.id}|${f.t}`) : null, val = o ? o[v] : null;
      const c = val == null ? -1 : classify(val, TASH_LEVELS[v]);
      let mk = stMarkers.get(s.id);
      if (!mk) {
        const el = document.createElement("div"); el.className = "mk";
        mk = new maplibregl.Marker({ element: el }).setLngLat([s.lon, s.lat]).addTo(map); stMarkers.set(s.id, mk);
      }
      const el = mk.getElement();
      el.textContent = val == null ? "" : Math.round(val);
      el.style.background = c < 0 ? "#9aa5b4" : PAL[c];
      el.style.width = el.style.height = val == null ? "14px" : "34px";
      el.title = `Stansiya ${s.id}${val == null ? "" : `: ${Math.round(val)} µg/m³ (o'lchov)`}`;
    }
  }
  function hideStations() { for (const mk of stMarkers.values()) mk.remove(); stMarkers.clear(); }

  // ---------- asos xarita: O'zbekiston chegarasi va o'zbekcha nomlar ----------
  const BASE_LABEL_MINZOOM = 7.5;                     // undan pastda — o'zimizning o'zbekcha yozuvlar
  async function setupBase() {
    // 1) Asos xaritaning yozuvlari: kichik masshtabda yashiriladi, yaqinlashganda OSM'ning o'zbekcha nomi (bo'lsa)
    for (const l of map.getStyle().layers) {
      if (l.type !== "symbol") continue;
      map.setLayerZoomRange(l.id, Math.max(l.minzoom || 0, BASE_LABEL_MINZOOM), l.maxzoom || 24);
      if (l.layout && l.layout["text-field"]) map.setLayoutProperty(l.id, "text-field", ["coalesce", ["get", "name:uz"], ["get", "name"]]);
    }
    // 2) O'zbekiston: tashqi hududni xiralashtirish + chegara chizig'i
    try {
      const uz = await getJSON("uzbekistan.geojson");
      const holes = [];
      for (const f of uz.features) {
        const g = f.geometry;
        (g.type === "Polygon" ? [g.coordinates] : g.coordinates).forEach((poly) => holes.push(poly[0]));
      }
      const mask = { type: "Feature", geometry: { type: "Polygon",
        coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]], ...holes] } };
      const firstSymbol = (map.getStyle().layers.find((l) => l.type === "symbol") || {}).id;
      map.addSource("uz-mask", { type: "geojson", data: mask });
      map.addSource("uz", { type: "geojson", data: uz });
      map.addLayer({ id: "uz-mask", type: "fill", source: "uz-mask", paint: { "fill-color": "#ffffff", "fill-opacity": 0.55 } }, firstSymbol);
      map.addLayer({ id: "uz-line-halo", type: "line", source: "uz", paint: { "line-color": "#ffffff", "line-width": 4.5, "line-opacity": 0.9 } }, firstSymbol);
      map.addLayer({ id: "uz-line", type: "line", source: "uz", paint: { "line-color": "#1d3557", "line-width": 1.8 } }, firstSymbol);
    } catch (e) { console.warn("O'zbekiston chegarasi", e); }
    // 3) O'zbekcha yozuvlar: davlatlar, viloyatlar, markazlar, shaharlar
    try {
      map.addSource("uz-labels", { type: "geojson", data: await getJSON("uz_labels.geojson") });
      const K = (k) => ["==", ["get", "kind"], k];
      map.addLayer({ id: "uz-lab-region", type: "symbol", source: "uz-labels", maxzoom: BASE_LABEL_MINZOOM, filter: K("region"),
        layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Italic"], "text-size": ["interpolate", ["linear"], ["zoom"], 4, 9, 7, 12],
                  "text-letter-spacing": 0.04, "text-max-width": 8, "symbol-sort-key": 4 },
        paint: { "text-color": "#5d6b7e", "text-halo-color": "#ffffff", "text-halo-width": 1.4 } });
      map.addLayer({ id: "uz-lab-country", type: "symbol", source: "uz-labels", maxzoom: BASE_LABEL_MINZOOM, filter: ["any", K("country"), K("uz")],
        layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Bold"], "text-transform": "uppercase", "text-letter-spacing": 0.15,
                  "text-size": ["case", K("uz"), ["interpolate", ["linear"], ["zoom"], 4, 13, 7, 20], ["interpolate", ["linear"], ["zoom"], 4, 10, 7, 14]] },
        paint: { "text-color": ["case", K("uz"), "#1d3557", "#8a94a3"], "text-halo-color": "#ffffff", "text-halo-width": 1.6 } });
      map.addLayer({ id: "uz-lab-city", type: "symbol", source: "uz-labels", maxzoom: BASE_LABEL_MINZOOM,
        filter: ["any", K("capital"), K("center"), K("city"), K("foreign")],
        layout: { "text-field": ["get", "name"], "symbol-sort-key": ["get", "rank"],
                  "text-font": ["case", ["any", K("capital"), K("center")], ["literal", ["Noto Sans Bold"]], ["literal", ["Noto Sans Regular"]]],
                  "text-size": ["match", ["get", "kind"], "capital", 15, "center", 12.5, "city", 11, 11],
                  "text-anchor": "left", "text-offset": [0.6, 0], "text-optional": true },
        paint: { "text-color": ["case", K("foreign"), "#7a8594", "#142033"], "text-halo-color": "#ffffff", "text-halo-width": 1.5 } });
      map.addLayer({ id: "uz-lab-dot", type: "circle", source: "uz-labels", maxzoom: BASE_LABEL_MINZOOM,
        filter: ["any", K("capital"), K("center"), K("city"), K("foreign")],
        paint: { "circle-radius": ["match", ["get", "kind"], "capital", 4.5, "center", 3.2, 2.4],
                 "circle-color": ["case", K("foreign"), "#9aa5b4", "#142033"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.2 } }, "uz-lab-city");
    } catch (e) { console.warn("Yozuvlar", e); }
  }

  // ---------- xarita ----------
  function setRaster(img) {
    const src = map.getSource("aq");
    if (src) { src.updateImage({ url: img.url, coordinates: img.coords }); return; }
    map.addSource("aq", { type: "image", url: img.url, coordinates: img.coords });
    const firstSymbol = map.getLayer("uz-mask") ? "uz-mask" : (map.getStyle().layers.find((l) => l.type === "symbol") || {}).id;
    map.addLayer({ id: "aq", type: "raster", source: "aq", paint: { "raster-opacity": 1, "raster-resampling": "linear", "raster-fade-duration": 0 } }, firstSymbol);
  }

  async function draw() {
    try {
      if (S.mode === "tashkent") {
        const T = await loadTashkent();
        if (!T.frames.length) { banner("Toshkent qatlami uchun so'nggi kuzatuvlar topilmadi."); return; }
        S.frames = T.frames.map((f) => f.t);
        S.step = S.t != null ? nearest(S.frames, S.t) : Math.min(S.step, S.frames.length - 1);
        setRaster(renderTashkent(T, S.v, S.step));
        renderStations(T, S.v, S.step);
        const f = T.frames[S.step];
        $("maptime").textContent = `${fmtT(f.t)} · ${f.kind === "prognoz" ? "prognoz" : "kuzatuv"}`;
      } else {
        hideStations();
        const M = await loadModel(S.mode);
        await modelVar(M, S.v);
        S.frames = M.times;
        S.step = S.t != null ? nearest(S.frames, S.t) : Math.min(S.step, S.frames.length - 1);
        setRaster(renderModel(M, S.v, S.step));
        const dh = Math.round((S.frames[S.step] - Date.now()) / H);
        $("maptime").textContent = `${fmtT(S.frames[S.step])} · ${Math.abs(dh) <= 1 ? "hozir" : dh > 0 ? `+${dh} soat` : `${dh} soat`}`;
      }
      banner("");
      S.t = S.frames[S.step];
      const sl = $("slider"); sl.max = S.frames.length - 1; sl.value = S.step;
      $("tlabel").textContent = `${fmtT(S.frames[0])} → ${fmtT(S.frames[S.frames.length - 1])}`;
      renderPanel();
      if (ptLngLat) renderPoint();
      if (S.mode !== "tashkent") {                         // ikkinchi model: nuqta grafigi uchun
        const other = S.mode === "silam" ? "cams" : "silam";
        loadModel(other).then((M2) => modelVar(M2, S.v)).then(() => ptLngLat && renderPoint()).catch(() => {});
      }
    } catch (e) {
      console.error(e);
      banner(S.mode === "tashkent" ? "Toshkent qatlami fayllari hali yuklanmagan yoki o'qib bo'lmadi."
                                   : `${S.mode.toUpperCase()} ma'lumotini yuklab bo'lmadi. Birozdan keyin qayta urinib ko'ring.`);
    }
  }

  // ---------- panel ----------
  function renderPanel() {
    // moddalar
    const vars = S.mode === "tashkent" ? TASH_VARS : MODEL_VARS;
    if (!vars.includes(S.v)) S.v = vars[0];
    $("vars").innerHTML = MODEL_VARS.map((v) =>
      `<button data-v="${v}" class="${v === S.v ? "on" : ""}" ${vars.includes(v) ? "" : "disabled"}>${VAR[v]}</button>`).join("");
    $("vars").querySelectorAll("button").forEach((b) => b.onclick = () => { S.v = b.dataset.v; draw(); });
    // tuzatish
    const M = cache[S.mode], rinfo = M && M.meta.ratio && M.meta.ratio[S.v];
    const canCorr = S.mode !== "tashkent" && (S.v === "pm25" || S.v === "pm10");
    $("corr-ctl").classList.toggle("hidden", !canCorr);
    $("corr").disabled = !rinfo; $("corr").checked = !!rinfo && S.corr;
    $("corr-note").textContent = !canCorr ? "" : rinfo
      ? `Oxirgi ${rinfo.days} kun, ${rinfo.stations} stansiya. Model o'rtacha ${rinfo.median_ratio}× past ko'rsatgan.`
      : "Tuzatish uchun stansiyalar tarixi hali yig'ilmoqda (kamida 7 kun kerak).";
    // shkala
    const lev = levFor(S.v), unit = S.mode === "tashkent" ? "µg/m³" : (M ? M.meta.vars[S.v].unit : "");
    const rows = [];
    for (let i = lev.length - 1; i >= 0; i--) {
      const lab = i === lev.length - 1 ? `> ${lev[i]}` : `${lev[i]} – ${lev[i + 1]}`;
      rows.push(`<div class="row"><div class="sw" style="background:${PAL[i]}"></div><div>${lab} ${unit}</div></div>`);
    }
    $("legend").innerHTML = rows.join("");
    $("legend-note").textContent = S.v === "dust" ? "Aerozol optik qalinligi (550 nm), o'lchamsiz."
      : S.mode === "tashkent" ? "Shahar ichidagi farqlarni ko'rsatish uchun maydaroq shkala. Doiralar — stansiyalarda o'lchangan qiymat."
      : "Chegaralar JSST (WHO) 2021 yo'riqnomasi darajalariga asoslangan. Yo'riqnoma sutkalik o'rtacha uchun, xarita esa soatlik qiymatni ko'rsatadi.";
    // ma'lumot
    let info;
    if (S.mode === "tashkent") {
      const T = cache.tash, pp = T ? T.p.pollutants[S.v] : null;
      const nul = pp && (pp.null_rmse || pp.loo_rmse_null);
      info = `<b>Toshkent, ~100 m.</b> Shahar darajasi × fazoviy koeffitsiyent. Darajasi: o'tgan soatlarda stansiyalar medianasi, ` +
        `kelajakda V8.1 ansambl prognozi. Koeffitsiyent o'rta ko'chalargacha (OSM secondary/tertiary) masofaga bog'liq: ` +
        `ko'cha yonida yuqori, ~150 m da fonga tushadi (LUR, 11 stansiya` +
        (pp && nul ? `, xatoni ${(100 * (1 - pp.loo_rmse / nul)).toFixed(0)}% kamaytiradi` : "") +
        `). Shahar chegarasidan ${BUFFER_M / 1000} km gacha ko'rsatiladi; stansiyalardan uzoqda qiymatlar taxminiy.`;
    } else if (M) {
      info = `<b>${M.meta.source}</b><br>Ishga tushirish: ${M.meta.run} UTC.` +
        (["no2", "so2", "o3", "co"].includes(S.v)
          ? `<br><b>Diqqat:</b> gazlar uchun model mintaqaviy fonni ko'rsatadi. Shahar va sanoat manbalari to'liq hisobga olinmagan, yerdagi o'lchovlar bilan tasdiqlanmagan.`
          : "");
    }
    $("info").innerHTML = info || "";
    $("foot").textContent = "Manbalar: FMI SILAM (CC BY 4.0), Copernicus CAMS, stansiyalar o'lchovlari, OpenStreetMap. Toifalar va chegaralar ma'lumot uchun.";
    $("updated").textContent = M ? `Model: ${M.meta.run.slice(0, 16)} UTC` : "";
  }

  // ---------- nuqta grafigi ----------
  function renderPoint() {
    const { lng, lat } = ptLngLat, series = [];
    let unit = "µg/m³";
    if (S.mode === "tashkent") {
      const T = cache.tash, f = T && tashFactorAt(T, S.v, lng, lat);
      if (f == null) return;
      series.push({ name: `${VAR[S.v]} (Toshkent qatlami)`, type: "line", showSymbol: false, lineStyle: { width: 2.5 },
        data: T.frames.map((fr) => [fr.t, fr[S.v] * f]) });
    } else {
      for (const m of ["silam", "cams"]) {
        const M = cache[m]; if (!M || !M.data[S.v]) continue;
        const dlat = M.lat[1] - M.lat[0], dlon = M.lon[1] - M.lon[0];
        const fy = (lat - M.lat[0]) / dlat, fx = (lng - M.lon[0]) / dlon;
        if (fy < -0.5 || fy > M.ny - 0.5 || fx < -0.5 || fx > M.nx - 0.5) continue;
        const useR = S.corr && !!M.ratio[S.v];
        unit = M.meta.vars[S.v].unit;
        series.push({ name: `${m.toUpperCase()}${useR ? " (tuzatilgan)" : ""}`, type: "line", showSymbol: false, smooth: 0.2,
          lineStyle: { width: 2.5, color: m === "silam" ? "#2f6fdb" : "#d9771f" }, itemStyle: { color: m === "silam" ? "#2f6fdb" : "#d9771f" },
          data: M.times.map((t, i) => [t, sampleModel(M, S.v, i, fy, fx, useR)]) });
      }
    }
    $("pt-card").classList.remove("hidden");
    $("pt-title").textContent = `${VAR[S.v]} · ${lat.toFixed(3)}° N, ${lng.toFixed(3)}° E`;
    $("pt-sub").textContent = S.mode === "tashkent" ? "Toshkent qatlami: kuzatuv va prognoz" : "Ikkala model, 5 kunlik prognoz (Toshkent vaqti)";
    const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    const lev = LEVELS[S.v];      // WHO darajasi chizig'i (barcha rejimlarda bir xil)
    ptChart.setOption({
      grid: { left: 48, right: 16, top: 30, bottom: 28 }, legend: { top: 0, textStyle: { color: css("--muted") } },
      tooltip: { trigger: "axis", valueFormatter: (x) => (x == null ? "—" : (+x).toFixed(S.v === "co" || S.v === "dust" ? 2 : 0)) },
      xAxis: { type: "time", axisLabel: { color: css("--muted"), formatter: (v) => fmtT(v).slice(6), hideOverlap: true } },
      yAxis: { type: "value", name: unit, min: 0, axisLabel: { color: css("--muted") }, splitLine: { lineStyle: { color: css("--line") } } },
      series: series.map((s, i) => i ? s : { ...s, markLine: { silent: true, symbol: "none", label: { color: css("--muted") },
        data: [{ xAxis: S.frames[S.step], lineStyle: { color: css("--muted") }, label: { show: false } },
               ...(S.v !== "dust" ? [{ yAxis: lev[2], lineStyle: { color: "#7cc49a", type: "dashed" } }] : [])] } }),
    }, true);
    ptChart.resize();
  }

  // ---------- boshqaruv ----------
  function setStep(i) { S.step = Math.max(0, Math.min(S.frames.length - 1, i)); S.t = S.frames[S.step]; draw(); }
  function togglePlay() {
    if (S.playing) { clearInterval(S.playing); S.playing = null; $("play").textContent = "▶"; return; }
    $("play").textContent = "❚❚";
    S.playing = setInterval(() => setStep(S.step + 1 >= S.frames.length ? 0 : S.step + 1), +$("speed").value);
  }

  function init() {
    if (!C || !C.SUPABASE_URL) { banner("config.js topilmadi."); return; }
    map = new maplibregl.Map({ container: "map", style: "https://tiles.openfreemap.org/styles/positron",
      bounds: UZ_BOUNDS, fitBoundsOptions: { padding: 20 }, attributionControl: { compact: true } });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("click", (e) => { ptLngLat = e.lngLat; renderPoint(); });
    ptChart = echarts.init($("pt-chart"));
    addEventListener("resize", () => ptChart.resize());
    $("pt-close").onclick = () => { $("pt-card").classList.add("hidden"); ptLngLat = null; };

    document.querySelectorAll("#mode button").forEach((b) => b.onclick = () => {
      document.querySelectorAll("#mode button").forEach((x) => x.classList.toggle("on", x === b));
      const prev = S.mode; S.mode = b.dataset.v;
      if ((prev === "tashkent") !== (S.mode === "tashkent")) { $("pt-card").classList.add("hidden"); ptLngLat = null; }
      if (S.mode === "tashkent" && prev !== "tashkent") {
        S.step = 0;
        loadTashkent().then((T) => {
          const [w, s, e, n] = T.p.bbox; map.fitBounds([[w, s], [e, n]], { padding: 20 });
          S.t = Date.now() - H; draw();
        }).catch(() => draw());
        return;
      }
      if (prev === "tashkent") map.fitBounds(UZ_BOUNDS, { padding: 20 });
      draw();
    });
    $("corr").onchange = (e) => { S.corr = e.target.checked; draw(); };
    $("slider").oninput = (e) => setStep(+e.target.value);
    $("play").onclick = togglePlay;
    $("speed").onchange = () => { if (S.playing) { togglePlay(); togglePlay(); } };
    addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" && e.target.type !== "range") return;
      if (e.key === "ArrowRight") setStep(S.step + 1);
      if (e.key === "ArrowLeft") setStep(S.step - 1);
      if (e.key === " ") { e.preventDefault(); togglePlay(); }
    });

    map.on("load", async () => {
      await setupBase();
      try {
        await loadModel("silam"); S.t = Date.now();
      } catch (e) { /* draw() xabar beradi */ }
      draw();
      loadModel("cams").then((M) => modelVar(M, S.v)).catch(() => {});      // nuqta grafigi uchun oldindan
    });
  }
  document.addEventListener("DOMContentLoaded", init);
})();
