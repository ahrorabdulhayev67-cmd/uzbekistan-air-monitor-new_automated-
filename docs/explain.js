/* Toshkent havo sifati — "Havo holati sharhi" kartasi (v81_explain jadvali; v81_explain.py yaratadi) */
(() => {
  "use strict";
  const C = window.TAQ_CONFIG;
  const card = document.getElementById("explain-card");
  if (!C || !card) return;
  const NAMES = { 107: "O'zgidromet", 108: "Chilonzor", 720: "TTZ-4", 729: "Yangi O'zbekiston", 730: "Safia",
                  731: "Green University", 732: "Tashselmash", 733: "Uchtepa", 734: "Olmazor" };
  const CATCOL = { "yaxshi": "#2ecc71", "qoniqarli": "#f1c40f", "o'rtacha ifloslangan": "#e67e22",
                   "yuqori ifloslangan": "#e74c3c", "qoniqarsiz, xavfli": "#8e44ad" };
  const TZ = 5 * 3600e3;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hhmm = (iso) => { const d = new Date(Date.parse(iso) + TZ); return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")} ${String(d.getUTCHours()).padStart(2, "0")}:00`; };

  const st = document.createElement("style");
  st.textContent = `
    #explain-card .ex-sel{height:34px;border-radius:9px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:0 10px;font:500 13px Inter,sans-serif}
    #explain-card .ex-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:6px}
    @media (max-width:760px){#explain-card .ex-grid{grid-template-columns:1fr}}
    #explain-card .ex-block{border:1px solid var(--line);border-radius:12px;padding:12px 14px;border-left:5px solid var(--exc,#9aa5b4)}
    #explain-card .ex-h{font:600 12px Inter,sans-serif;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:6px}
    #explain-card .ex-t{line-height:1.55;margin:0}
    #explain-card .ex-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
    #explain-card .ex-chip{font:500 12px Inter,sans-serif;padding:4px 9px;border-radius:999px;background:var(--bg);border:1px solid var(--line)}`;
  document.head.appendChild(st);

  const sel = document.getElementById("ex-st");
  for (const [id, n] of Object.entries(NAMES)) { const o = document.createElement("option"); o.value = id; o.textContent = n; sel.appendChild(o); }
  sel.value = String(C.FORECAST_STATION || 107);
  sel.onchange = () => load();

  function block(e, title) {
    if (!e) return "";
    return `<div class="ex-block" style="--exc:${CATCOL[e.category] || "#9aa5b4"}">
      <div class="ex-h">${title}</div><p class="ex-t">${esc(e.text_uz)}</p></div>`;
  }

  async function load() {
    const body = document.getElementById("ex-body"), sub = document.getElementById("ex-sub");
    try {
      const u = new URL(`${C.SUPABASE_URL}/rest/v1/v81_explain`);
      u.searchParams.set("select", "station_id,kind,run_time,lead_h,category,text_uz,factors");
      u.searchParams.set("station_id", `eq.${sel.value}`);
      const r = await fetch(u, { headers: { apikey: C.SUPABASE_KEY } });
      if (!r.ok) throw new Error(`${r.status}`);
      const rows = await r.json(), by = Object.fromEntries(rows.map((x) => [x.kind, x]));
      if (!rows.length) { body.textContent = "Sharh hali tayyor emas — keyingi prognoz bilan paydo bo'ladi."; sub.textContent = ""; return; }
      const age = (Date.now() - Date.parse(rows[0].run_time)) / 3600e3;
      sub.textContent = `${NAMES[sel.value]} stansiyasi · prognoz ${hhmm(rows[0].run_time)} (Toshkent vaqti)` + (age > 3 ? " · eskirgan" : "");
      body.innerHTML = `<div class="ex-grid">${block(by.now, "Hozir va kelgusi 3 soat")}${block(by.peak, "Kelgusi 24 soat")}</div>`;
    } catch (e) {
      console.warn("sharh", e); body.textContent = "Sharhni yuklab bo'lmadi.";
    }
  }
  load();
  setInterval(load, (C.REFRESH_MIN || 10) * 60e3);
})();
