"""TashkentAQ V8.1 — prognoz aniqligini hisoblash (verifikatsiya) va PDF hisobot.

1) forecasts_v81 prognozlarini obs_unified kuzatuvlari bilan juftlaydi;
2) kunlik yig'indilarni v81_skill_daily jadvaliga yozadi (portal "Aniqlik" sahifasi shu jadvaldan o'qiydi);
3) oxirgi 7 va 30 kun uchun PDF hisobot yasab, ochiq `reports` bucket'iga yuklaydi.

Metrikalar (WMO/CAMS verifikatsiya amaliyoti):
  MAE, bias (o'rtacha xato), RMSE, 80% oraliq qamrovi, skill = 1 − MAE_model / MAE_persistence.
Prognoz oqlanishi: |prognoz − kuzatuv| ≤ δ_dop, δ_dop = 0.674·σ_Δ (ehtimoliy xato; Apollov va boshq., 1974),
  σ_Δ — konsentratsiyaning h soatdagi o'zgarishining standart chetlanishi, mavsum bo'yicha (107-stansiya tarixidan, oqlanish_delta.json).
Yig'indilar (n, sum_ae, sum_err, sum_se, n_in80, n_ok) saqlanadi — istalgan davr/stansiya bo'yicha qayta yig'ish uchun.
"""
import os, io, json, logging
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("verify")
for n in ["httpx", "httpcore"]: logging.getLogger(n).setLevel(logging.WARNING)

TZ = pd.Timedelta(hours=5)                       # Toshkent = UTC+5
OBS_TS_OFFSET = pd.Timedelta(hours=5)            # kollektor Toshkent vaqtini "+00:00" deb yozadi
DAYS = int(os.environ.get("VERIFY_DAYS", "3"))   # har safar qayta hisoblanadigan kunlar (mahalliy sana)
BUCKET = "reports"
GRP_EDGES, GRP_NAMES = [0, 3, 6, 12, 24], ["h1-3", "h4-6", "h7-12", "h13-24"]
STATIONS = {107: "O'zgidromet", 108: "Chilonzor", 733: "Uchtepa", 732: "Tashselmash", 734: "Olmazor",
            730: "Safia", 731: "Green University", 720: "TTZ-4", 729: "Yangi O'zbekiston"}
MODELS = ["Ensemble", "A-rel", "A-abs", "B", "C", "persistence"]
MNAME = {"Ensemble": "Ansambl", "A-rel": "A · nisbiy", "A-abs": "A · mutlaq", "B": "B · box", "C": "C · TFT",
         "persistence": "Persistence"}
MCOL = {"Ensemble": "#e67e22", "A-rel": "#16a085", "A-abs": "#27ae60", "B": "#8e44ad", "C": "#c0392b",
        "persistence": "#7f8c8d"}
LABEL = {"pm25": "PM2.5", "pm10": "PM10"}
_DP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oqlanish_delta.json")
DELTA = json.load(open(_DP, encoding="utf-8"))["delta"] if os.path.exists(_DP) else None


def client():
    env_url, env_key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (env_url and env_key):
        from dotenv import dotenv_values
        e = dotenv_values(os.environ.get("ENV_FILE", r"C:\uzbekistan_dust\deployment\.env"))
        env_url, env_key = e["SUPABASE_URL"], e["SUPABASE_KEY"]
    return create_client(env_url.strip(), env_key.strip())


def fetch(sb, table, cols, build, order):
    rows, s = [], 0
    while True:
        q = build(sb.table(table).select(cols))
        for o in order: q = q.order(o)
        r = q.range(s, s + 999).execute().data
        rows += r
        if len(r) < 1000: break
        s += 1000
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 1) juftlash va yig'ish
def verify(sb, now):
    d0 = (now + TZ).normalize() - pd.Timedelta(days=DAYS - 1)      # birinchi mahalliy sana (00:00, "UTC" ko'rinishida)
    t0 = d0 - TZ                                                   # o'sha sananing boshi, haqiqiy UTC
    log.info("Verifikatsiya: mahalliy %s → bugun, nishon vaqti ≥ %s UTC", d0.date(), t0)

    F = fetch(sb, "forecasts_v81", "run_time,station_id,model,pollutant,lead_h,target_time,p10,p50,p90",
              lambda q: q.gte("target_time", t0.isoformat()).lte("target_time", now.isoformat()),
              ["run_time", "station_id", "model", "pollutant", "lead_h"])
    if F.empty:
        log.warning("Prognoz topilmadi"); return 0
    F["tgt"] = pd.to_datetime(F.target_time, utc=True)
    sids = sorted(F.station_id.unique().tolist())

    O = fetch(sb, "obs_unified", "station_id,timestamp,pm10,pm25",
              lambda q: q.in_("station_id", sids).gte("timestamp", (t0 + OBS_TS_OFFSET).isoformat()),
              ["station_id", "timestamp"])
    O["tgt"] = (pd.to_datetime(O.timestamp, utc=True) - OBS_TS_OFFSET).dt.floor("h")
    O = O.groupby(["station_id", "tgt"])[["pm10", "pm25"]].mean().sort_index()
    for p in ["pm10", "pm25"]:                       # inference bilan bir xil: "qotib qolgan" takror qiymatlar → NaN
        v = O[p].round(3)
        O.loc[v.eq(v.groupby(level=0).shift(1)) & v.notna(), p] = np.nan
    O = O.reset_index().melt(id_vars=["station_id", "tgt"], var_name="pollutant", value_name="y").dropna()

    V = F.merge(O, on=["station_id", "tgt", "pollutant"], how="inner").dropna(subset=["p50"])
    if V.empty:
        log.warning("Juftlik yo'q"); return 0
    V["date"] = (V.tgt + TZ).dt.date.astype(str)
    V["lead_grp"] = pd.cut(V.lead_h, GRP_EDGES, labels=GRP_NAMES).astype(str)
    V["err"] = V.p50 - V.y
    V["ae"], V["se"] = V.err.abs(), V.err ** 2
    V["in80"] = ((V.y >= V.p10) & (V.y <= V.p90)).astype(int)
    if DELTA:
        SEAS = {12: "qish", 1: "qish", 2: "qish", 3: "bahor", 4: "bahor", 5: "bahor",
                6: "yoz", 7: "yoz", 8: "yoz", 9: "kuz", 10: "kuz", 11: "kuz"}
        if "kuz" in DELTA:            # mavsumiy chegaralar: DELTA[mavsum][modda][guruh]
            dmap = {(se, p_, g_): v_ for se, ps in DELTA.items() for p_, gs in ps.items() for g_, v_ in gs.items()}
            se = (V.tgt + TZ).dt.month.map(SEAS)
            dd = pd.Series([dmap.get(k, np.nan) for k in zip(se, V.pollutant, V.lead_grp)], index=V.index)
        else:                          # yillik chegaralar: DELTA[modda][guruh]
            dmap = {(p_, g_): v_ for p_, gs in DELTA.items() for g_, v_ in gs.items()}
            dd = pd.Series([dmap.get(k, np.nan) for k in zip(V.pollutant, V.lead_grp)], index=V.index)
        V["ok"] = (V.ae <= dd).astype(int)
    else:
        V["ok"] = np.nan

    A = (V.groupby(["date", "station_id", "model", "pollutant", "lead_grp"])
           .agg(n=("ae", "size"), sum_ae=("ae", "sum"), sum_err=("err", "sum"), sum_se=("se", "sum"),
                n_in80=("in80", "sum"), sum_y=("y", "sum"), n_ok=("ok", "sum")).reset_index())
    if not DELTA: A["n_ok"] = None
    A["updated_at"] = now.isoformat()
    A = A.astype({"station_id": int, "n": int, "n_in80": int})
    if DELTA: A["n_ok"] = A.n_ok.astype(int)
    recs = A.round(3).to_dict("records")
    for i in range(0, len(recs), 500):
        sb.table("v81_skill_daily").upsert(recs[i:i + 500],
                                           on_conflict="date,station_id,model,pollutant,lead_grp").execute()
    log.info("v81_skill_daily: %d qator (%d juftlik, %d stansiya)", len(recs), len(V), V.station_id.nunique())
    return len(recs)


# ------------------------------------------------------------------ 2) PDF hisobot
def summarize(D, keys):
    g = D.groupby(keys)[["n", "sum_ae", "sum_err", "sum_se", "n_in80", "n_ok"]].sum(min_count=1)
    g["MAE"] = g.sum_ae / g.n
    g["bias"] = g.sum_err / g.n
    g["RMSE"] = np.sqrt(g.sum_se / g.n)
    g["qamrov"] = g.n_in80 / g.n
    g["oqlanish"] = g.n_ok / g.n * 100
    return g


def add_skill(g, keys):
    """skill = 1 − MAE_model / MAE_persistence (bir xil guruh ichida)."""
    other = [k for k in keys if k != "model"]
    if not other:
        pm = g["MAE"].get("persistence", np.nan)
        g["skill"] = (1 - g["MAE"] / pm) * 100
        return g
    pers = g.xs("persistence", level="model")["MAE"]
    g["skill"] = (1 - g["MAE"].values / pers.reindex(g.index.droplevel("model")).values) * 100
    return g


def build_pdf(D, days, end_date):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 11,
                         "axes.titleweight": "bold", "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.color": "#e6e8ec", "grid.linewidth": 0.7, "axes.axisbelow": True,
                         "legend.frameon": False})
    start = (pd.Timestamp(end_date) - pd.Timedelta(days=days - 1)).date()
    D = D[(D.date >= str(start)) & (D.date <= str(end_date))]
    models = [m for m in MODELS if m in D.model.unique()]
    buf = io.BytesIO()
    A4 = (8.27, 11.69)

    with PdfPages(buf) as pdf:
        # --- 1-bet: sarlavha va umumiy jadval
        fig = plt.figure(figsize=A4)
        fig.text(0.07, 0.95, "Toshkent havo sifati — prognoz aniqligi", fontsize=18, weight="bold")
        fig.text(0.07, 0.925, f"Davr: {start.strftime('%d.%m.%Y')} – {pd.Timestamp(end_date).strftime('%d.%m.%Y')}"
                 f" ({days} kun) · {D.station_id.nunique()} ta stansiya · V8.1", fontsize=10, color="#555")
        fig.text(0.07, 0.905, "Skill = 1 − MAE(model) / MAE(persistence). Musbat qiymat — model "
                 "\"hozirgi qiymat saqlanadi\" degan oddiy prognozdan yaxshi.", fontsize=8.5, color="#555")
        y = 0.86
        for pol in ["pm25", "pm10"]:
            d = D[D.pollutant == pol]
            if d.empty: continue
            g = add_skill(summarize(d, ["model"]), ["model"]).reindex(models)
            fig.text(0.07, y, f"{LABEL[pol]} — barcha stansiyalar, h1–h24", fontsize=12, weight="bold"); y -= 0.012
            cell = [[MNAME[m], f"{int(r.n):,}".replace(",", " "), f"{r.MAE:.1f}", f"{r.bias:+.1f}", f"{r.RMSE:.1f}",
                     "—" if m == "persistence" else f"{r.qamrov:.2f}", "—" if pd.isna(r.oqlanish) else f"{r.oqlanish:.0f}%",
                     "—" if m == "persistence" else f"{r.skill:+.0f}%"] for m, r in g.iterrows()]
            ax = fig.add_axes([0.07, y - 0.03 - 0.022 * len(cell), 0.86, 0.022 * (len(cell) + 1)]); ax.axis("off")
            t = ax.table(cellText=cell, colLabels=["Model", "Juftlik", "MAE", "Bias", "RMSE", "Qamrov", "Oqlanish", "Skill"],
                         loc="upper left", cellLoc="right", colLoc="right")
            t.auto_set_font_size(False); t.set_fontsize(9); t.scale(1, 1.35)
            for (r_, c_), c in t.get_celld().items():
                c.set_edgecolor("#e3e8ef")
                if r_ == 0: c.set_text_props(weight="bold", color="#555")
                if c_ == 0: c.set_text_props(ha="left"); c._loc = "left"
                if r_ > 0 and cell[r_ - 1][0] == "Ansambl": c.set_text_props(weight="bold")
            y -= 0.05 + 0.022 * (len(cell) + 1) + 0.03
        fig.text(0.07, 0.055, "Oqlanish — |prognoz − kuzatuv| ≤ 0.674·σΔ bo'lgan prognozlar ulushi (σΔ — h soatdagi o'zgarishning "
                 "standart chetlanishi, 107-stansiya tarixi).", fontsize=7.5, color="#777")
        fig.text(0.07, 0.04, "µg/m³. Qamrov — kuzatuv p10–p90 oralig'iga tushgan ulush (ideal 0.80). "
                 f"Yaratilgan: {(pd.Timestamp.now(tz='UTC') + TZ).strftime('%d.%m.%Y %H:%M')} (Toshkent vaqti)",
                 fontsize=7.5, color="#777")
        pdf.savefig(fig); plt.close(fig)

        # --- 2-bet: skill muddat bo'yicha va kunlik dinamika
        fig, axes = plt.subplots(4, 1, figsize=A4, gridspec_kw={"hspace": 0.55})
        for k, pol in enumerate(["pm25", "pm10"]):
            d = D[D.pollutant == pol]
            ax = axes[k]
            if d.empty: ax.axis("off"); continue
            g = add_skill(summarize(d, ["lead_grp", "model"]), ["lead_grp", "model"])
            ms = [m for m in models if m != "persistence"]
            w = 0.8 / len(ms)
            for i, m in enumerate(ms):
                vals = [g.skill.get((lg, m), np.nan) for lg in GRP_NAMES]
                ax.bar(np.arange(4) + (i - (len(ms) - 1) / 2) * w, vals, w * 0.92, color=MCOL[m], label=MNAME[m])
            ax.axhline(0, color="#333", lw=0.8)
            ax.set_xticks(range(4), GRP_NAMES); ax.set_ylabel("Skill, %")
            ax.set_title(f"{LABEL[pol]} — skill prognoz muddati bo'yicha")
            if k == 0: ax.legend(ncol=5, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, 1.32))

            ax = axes[k + 2]
            g = add_skill(summarize(d, ["date", "model"]), ["date", "model"])
            for m in ms:
                s = g.xs(m, level="model").skill if m in g.index.get_level_values("model") else None
                if s is None: continue
                ax.plot(pd.to_datetime(s.index), s.values, marker="o", ms=3, lw=2.2 if m == "Ensemble" else 1.4,
                        color=MCOL[m], label=MNAME[m])
            ax.axhline(0, color="#333", lw=0.8)
            ax.set_ylabel("Skill, %"); ax.set_title(f"{LABEL[pol]} — kunlik skill")
            ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d.%m"))
        pdf.savefig(fig); plt.close(fig)

        # --- 3-bet: stansiyalar bo'yicha (Ansambl) va bias / qamrov
        fig, axes = plt.subplots(3, 1, figsize=A4, gridspec_kw={"hspace": 0.6, "height_ratios": [1.3, 1, 1]})
        g = add_skill(summarize(D, ["pollutant", "station_id", "model"]), ["pollutant", "station_id", "model"])
        ax = axes[0]
        sids = sorted(D.station_id.unique())
        x = np.arange(len(sids))
        for i, pol in enumerate(["pm25", "pm10"]):
            vals = [g.skill.get((pol, s, "Ensemble"), np.nan) for s in sids]
            ax.bar(x + (i - 0.5) * 0.38, vals, 0.36, color=["#2f6fdb", "#d9771f"][i], label=LABEL[pol])
        ax.axhline(0, color="#333", lw=0.8)
        ax.set_xticks(x, [STATIONS.get(s, str(s)) for s in sids], rotation=30, ha="right")
        ax.set_ylabel("Skill, %"); ax.set_title("Ansambl skill — stansiyalar bo'yicha"); ax.legend()
        for k, (metric, title, ref) in enumerate([("bias", "Bias (p50 − kuzatuv), µg/m³", 0),
                                                  ("qamrov", "80% oraliq qamrovi", 0.8)]):
            ax = axes[k + 1]
            gg = summarize(D, ["pollutant", "model"])
            ms = [m for m in models if not (metric == "qamrov" and m == "persistence")]
            w = 0.8 / len(ms)
            for i, m in enumerate(ms):
                vals = [gg[metric].get((pol, m), np.nan) for pol in ["pm25", "pm10"]]
                ax.bar(np.arange(2) + (i - (len(ms) - 1) / 2) * w, vals, w * 0.92, color=MCOL[m], label=MNAME[m])
            ax.axhline(ref, color="#333", lw=0.8, ls="--" if ref else "-")
            ax.set_xticks(range(2), ["PM2.5", "PM10"]); ax.set_title(title)
            if k == 0: ax.legend(ncol=6, fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, 1.28))
        pdf.savefig(fig); plt.close(fig)
    return buf.getvalue()


def reports(sb, now):
    end = (now + TZ).date()
    start = end - timedelta(days=30)
    D = fetch(sb, "v81_skill_daily", "date,station_id,model,pollutant,lead_grp,n,sum_ae,sum_err,sum_se,n_in80,n_ok",
              lambda q: q.gte("date", str(start)), ["date", "station_id", "model", "pollutant", "lead_grp"])
    if D.empty:
        log.warning("v81_skill_daily bo'sh — PDF yasalmadi"); return
    for c in ["n", "sum_ae", "sum_err", "sum_se", "n_in80", "n_ok"]: D[c] = pd.to_numeric(D[c])
    st = sb.storage.from_(BUCKET)
    for days in [7, 30]:
        pdf = build_pdf(D, days, end)
        paths = [f"accuracy/latest_{days}d.pdf"]
        if days == 7 and end.weekday() == 0: paths.append(f"accuracy/weekly/{end}.pdf")       # dushanba
        if days == 30 and end.day == 1: paths.append(f"accuracy/monthly/{end:%Y-%m}.pdf")      # oy boshi
        for p in paths:
            st.upload(p, pdf, {"content-type": "application/pdf", "upsert": "true", "cache-control": "600"})
        log.info("PDF %d kun: %s (%.0f KB)", days, paths, len(pdf) / 1024)


if __name__ == "__main__":
    sb = client()
    now = pd.Timestamp(datetime.now(timezone.utc)).floor("h")
    verify(sb, now)
    reports(sb, now)
