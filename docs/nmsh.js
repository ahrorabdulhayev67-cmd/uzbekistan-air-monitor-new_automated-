/* Toshkent havo sifati — "Noqulay meteorologik sharoitlar (NMSH)" kartasi.
   Ma'lumot: nmu_daily jadvali (v81/nmu_daily.py har kuni 16:07 da yozadi), rasmlar: storage maps/nmsh/latest/. */
(() => {
  "use strict";
  const C = window.TAQ_CONFIG;
  if (!C || !C.SUPABASE_URL) return;
  const PUB = `${C.SUPABASE_URL}/storage/v1/object/public/maps/nmsh/latest`;
  const OY = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"];
  const COL = { "NMSH 2-daraja": "#c62828", "NMSH 1-daraja": "#f39c4a" };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const dlab = (iso) => { const d = new Date(iso + "T00:00:00"); return `${d.getDate()}-${OY[d.getMonth()]}`; };
  const pct = (x) => (x == null ? "—" : `${Math.round(100 * x)}%`);

  const st = document.createElement("style");
  st.textContent = `
    #nmsh-card .nm-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:6px}
    @media (max-width:760px){#nmsh-card .nm-grid{grid-template-columns:1fr}}
    #nmsh-card .nm-day{border:1px solid var(--line);border-radius:12px;padding:12px 14px;border-left:5px solid var(--nmc,#cfd6df)}
    #nmsh-card .nm-h{font:600 12px Inter,sans-serif;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
    #nmsh-card .nm-s{font:700 16px Inter,sans-serif;margin:4px 0 6px}
    #nmsh-card .nm-t{margin:0;line-height:1.55}
    #nmsh-card .nm-img{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}
    @media (max-width:760px){#nmsh-card .nm-img{grid-template-columns:1fr}}
    #nmsh-card .nm-img a{display:block;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#fff}
    #nmsh-card .nm-img img{display:block;width:100%;height:auto}
    #nmsh-card .nm-ver{margin-top:10px}`;
  document.head.appendChild(st);

  const card = document.createElement("section");
  card.className = "card wide"; card.id = "nmsh-card";
  card.innerHTML = `
    <div class="card-head"><div>
      <h2>Noqulay meteorologik sharoitlar (NMSH)</h2>
      <p class="muted" id="nm-sub">rus. неблагоприятные метеорологические условия (НМУ)</p>
    </div></div>
    <div id="nm-body">Yuklanmoqda…</div>
    <div class="nm-img">
      <a id="nm-a-panel" target="_blank" rel="noopener"><img id="nm-panel" alt="Toshkent: 72 soatlik meteorologik sharoit" loading="lazy"></a>
      <a id="nm-a-karta" target="_blank" rel="noopener"><img id="nm-karta" alt="NMSH prognozi kartasi" loading="lazy"></a>
    </div>
    <p class="muted small nm-ver" id="nm-ver"></p>
    <p class="muted small">NMSH — ifloslantiruvchi moddalarning yer yuzasida to'planishiga olib keladigan ob-havo: kuchsiz shamol yoki shtil,
      aralashish qatlamining pastligi, inversiya, antitsiklon. Metodika RD 52.04.52-85 asosida moslashtirilgan; REK oshishi
      ehtimoli 2021–2026 yillar ma'lumotlari bo'yicha hisoblanadi. Sinov rejimi.</p>`;
  const anchor = document.getElementById("explain-card");
  if (anchor) anchor.after(card); else (document.querySelector("main") || document.body).appendChild(card);

  const K = [["k_shamol", "kuchsiz shamol (< 1,5 m/s)"], ["k_qatlam", "past aralashish qatlami"], ["k_barq", "inversiya"], ["k_bosim", "antitsiklon"]];
  function dayBlock(r, title) {
    const on = K.filter(([k]) => r[k]).map(([, n]) => n);
    const lvl = r.daraja === "Yo'q" ? "NMSH kutilmaydi" : r.daraja;
    return `<div class="nm-day" style="--nmc:${COL[r.daraja] || (r.komplekslar >= 3 ? "#f2cf73" : "#cfd6df")}">
      <div class="nm-h">${title}, ${dlab(r.target_date)}</div>
      <div class="nm-s">${esc(lvl)}</div>
      <p class="nm-t">Meteorologik komplekslar: <b>${r.komplekslar}/4</b>${on.length ? " — " + on.join(", ") : ""}.<br>
        Sutkalik PM2.5 ning REK (60 µg/m³) dan oshish ehtimoli: <b>${pct(r.p_rek1)}</b>; 2 REK (120) dan: ${pct(r.p_rek2)}.</p></div>`;
  }

  async function q(params) {
    const u = new URL(`${C.SUPABASE_URL}/rest/v1/nmu_daily`);
    for (const [k, v] of params) u.searchParams.append(k, v);
    const r = await fetch(u, { headers: { apikey: C.SUPABASE_KEY } });
    if (!r.ok) throw new Error(`nmu_daily: ${r.status}`);
    return r.json();
  }

  async function load() {
    const body = document.getElementById("nm-body");
    try {
      const rows = await q([["select", "*"], ["order", "issue_date.desc,target_date.asc"], ["limit", "4"]]);
      if (!rows.length) { body.textContent = "Baho hali tayyor emas."; return; }
      const iss = rows[0].issue_date, cur = rows.filter((r) => r.issue_date === iss);
      document.getElementById("nm-sub").textContent =
        `rus. неблагоприятные метеорологические условия (НМУ) · baho: ${dlab(iss)}, 16:07`;
      body.innerHTML = `<div class="nm-grid">${cur.map((r) => dayBlock(r, r.lead_days === 1 ? "Ertaga" : "Indinga")).join("")}</div>`;
      for (const nm of ["panel", "karta"]) {
        const u = `${PUB}/${nm}.png?d=${iss}`;
        const im = document.getElementById(`nm-${nm}`), an = document.getElementById(`nm-a-${nm}`);
        an.style.display = ""; im.onerror = () => { an.style.display = "none"; }; im.src = u; an.href = u;
      }
      const ver = await q([["select", "target_date,daraja,p_rek1,obs_pm25"], ["obs_pm25", "not.is.null"], ["lead_days", "eq.1"],
                           ["order", "target_date.desc"], ["limit", "1"]]);
      if (ver.length) {
        const v = ver[0], hit = v.obs_pm25 > 60;
        document.getElementById("nm-ver").textContent =
          `Oxirgi tekshiruv — ${dlab(v.target_date)}: baho «${v.daraja}», REK oshishi ehtimoli ${pct(v.p_rek1)}; ` +
          `haqiqiy sutkalik PM2.5 = ${Math.round(v.obs_pm25)} µg/m³ (${hit ? "REK oshgan" : "REK oshmagan"}).`;
      }
    } catch (e) { console.warn("NMSH", e); body.textContent = "NMSH bahosini yuklab bo'lmadi."; }
  }
  load();
  setInterval(load, 30 * 60e3);
})();
