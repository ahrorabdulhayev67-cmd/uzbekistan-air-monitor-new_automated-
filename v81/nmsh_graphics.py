"""NMSH grafikasi: respublika kartasi va Toshkent 72 soatlik paneli (nmu_daily.py chaqiradi).

Noqulay meteorologik sharoitlar — NMSH (rus. неблагоприятные метеорологические условия, НМУ).
Rasmlar Supabase storage'ga yuklanadi: maps/nmsh/latest/{karta,panel}.png va maps/nmsh/arxiv/.
"""
import io, json, time
from datetime import timedelta
from pathlib import Path
import numpy as np, pandas as pd, requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patheffects as pe
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Patch
import geopandas as gpd
from shapely.geometry import Point
from shapely.prepared import prep

HERE = Path(__file__).parent
URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = "wind_speed_10m,boundary_layer_height,temperature_2m,temperature_850hPa,surface_pressure"
STEP, ELEV_MAX = 0.5, 1300                 # to'r qadami (°); bundan baland — barqarorlik ma'nosiz
VC_PAST, VC_YAXSHI = 1000, 3000            # kunduzgi maksimal ventilatsiya toifalari (Toshkent arxivi, 2021–2026)
TASH = (41.31, 69.28)
CITY_ST = [107, 108, 720, 729, 730, 731, 732, 733, 734]
OY = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]
CITIES = {"Toshkent": (41.31, 69.28), "Nukus": (42.46, 59.60), "Urganch": (41.55, 60.63), "Buxoro": (39.77, 64.42),
          "Navoiy": (40.10, 65.38), "Samarqand": (39.65, 66.96), "Qarshi": (38.86, 65.79), "Termiz": (37.22, 67.28),
          "Jizzax": (40.12, 67.84), "Guliston": (40.49, 68.78), "Namangan": (41.00, 71.67), "Andijon": (40.78, 72.34),
          "Farg'ona": (40.38, 71.79)}
LVL_COL = {"NMSH 2-daraja": "#c62828", "NMSH 1-daraja": "#f39c4a"}
HALO = [pe.withStroke(linewidth=3, foreground="white")]
ASP = 1 / np.cos(np.deg2rad(41.3))
SUBTITLE = "Noqulay meteorologik sharoitlar — NMSH (rus. неблагоприятные метеорологические условия, НМУ)"


def fetch(lat, lon, past_days=31, forecast_days=3):
    prm = dict(latitude=",".join(f"{x:.2f}" for x in lat), longitude=",".join(f"{x:.2f}" for x in lon),
               timezone="Asia/Tashkent", past_days=past_days, forecast_days=forecast_days, wind_speed_unit="ms", hourly=HOURLY)
    for k in range(6):
        r = requests.get(URL, params=prm, timeout=180)
        if r.status_code == 429:
            time.sleep(20 * (k + 1)); continue
        r.raise_for_status(); js = r.json()
        return js if isinstance(js, list) else [js]
    raise RuntimeError("Open-Meteo: 429 takrorlandi")


def hourly_frame(j):
    W = pd.DataFrame(j["hourly"]); W["time"] = pd.to_datetime(W["time"]); W = W.set_index("time")
    W["barq"] = W.temperature_850hPa - W.temperature_2m + 9.8
    W["VC"] = W.boundary_layer_height * W.wind_speed_10m
    return W


def daily_feats(W):                         # nmu_daily.py dagi ta'rif bilan aynan bir xil
    d = pd.DataFrame({"shamol": W.wind_speed_10m.resample("D").mean(),
                      "aral_qatlam": W.boundary_layer_height.resample("D").mean(),
                      "barq_tong": W.barq[(W.index.hour >= 3) & (W.index.hour <= 9)].resample("D").max(),
                      "bosim": W.surface_pressure.resample("D").mean()})
    d["bosim_anom"] = d.bosim - d.bosim.shift(1).rolling(31, min_periods=20).mean()
    return d


def flags(r, KP):
    return [int(r.shamol < KP["shamol_lt"]), int(r.aral_qatlam < KP["aral_qatlam_lt"]),
            int(r.barq_tong > KP["barq_gt"]), int(r.bosim_anom > KP["bosim_anom_gt"])]


# ---------------------------------------------------------------- respublika to'ri
def grid_assessment(KP, days, uzb):
    LON = np.round(np.arange(56.0, 73.26, STEP), 2); LAT = np.round(np.arange(37.25, 45.76, STEP), 2)
    poly = prep(uzb.geometry.iloc[0].buffer(0.3))
    G = pd.DataFrame([(la, lo) for la in LAT for lo in LON], columns=["lat", "lon"])
    G = G[[poly.contains(Point(lo, la)) for la, lo in zip(G.lat, G.lon)]].reset_index(drop=True)
    recs = []
    for i in range(0, len(G), 50):
        b = G.iloc[i:i + 50]
        for (_, p), j in zip(b.iterrows(), fetch(b.lat.values, b.lon.values)):
            d = daily_feats(hourly_frame(j))
            for day in days:
                if day not in d.index or d.loc[day].isna().any(): continue
                k = sum(flags(d.loc[day], KP))
                if j.get("elevation", 0) > ELEV_MAX: k = -1
                recs.append(dict(lat=p.lat, lon=p.lon, kun=day, k=k))
        time.sleep(2)
    return pd.DataFrame(recs), LAT, LON


def draw_map(R, LAT, LON, days, KP, adm1, uzb, outside, tash_txt):
    COLS = ["#d9d9d9", "#f5f5f2", "#f39c4a", "#c62828"]                   # tog' | 0–2 | 3 | 4
    cmap, norm = ListedColormap(COLS), BoundaryNorm([-1.5, -0.5, 2.5, 3.5, 4.5], 4)
    fig, axs = plt.subplots(1, len(days), figsize=(8.5 * len(days), 6.3))
    axs = np.atleast_1d(axs)
    for ax, day in zip(axs, days):
        g = R[R.kun == day]
        A = np.full((len(LAT), len(LON)), np.nan)
        for _, r in g.iterrows():
            A[np.searchsorted(LAT, r.lat), np.searchsorted(LON, r.lon)] = r.k
        outside.plot(ax=ax, color="#eeeeec", edgecolor="#c8c8c8", lw=0.5, zorder=0)
        ax.imshow(np.ma.masked_invalid(A), origin="lower", cmap=cmap, norm=norm, interpolation="nearest", zorder=1,
                  extent=[LON[0] - STEP / 2, LON[-1] + STEP / 2, LAT[0] - STEP / 2, LAT[-1] + STEP / 2])
        outside.plot(ax=ax, color="#eeeeec", edgecolor="#c8c8c8", lw=0.5, zorder=2)
        adm1.boundary.plot(ax=ax, color="#9a9a9a", lw=0.5, zorder=3)
        uzb.boundary.plot(ax=ax, color="#3d3d3d", lw=1.1, zorder=4)
        for nm, (la, lo) in CITIES.items():
            ax.scatter(lo, la, s=14, c="#111", zorder=6)
            txt = nm + (f"\n{tash_txt.get(day, '')}" if nm == "Toshkent" else "")
            ax.annotate(txt, (lo, la), xytext=(5, 4), textcoords="offset points", fontsize=8.5, zorder=7,
                        path_effects=HALO, weight="bold" if nm == "Toshkent" else "normal")
        n3, n4 = int((g.k == 3).sum()), int((g.k == 4).sum())
        ax.set_xlim(55.9, 73.3); ax.set_ylim(37.0, 45.7); ax.set_aspect(ASP); ax.set_xticks([]); ax.set_yticks([])
        ax.set_xlabel(""); ax.set_ylabel("")
        ax.set_title(f"{day.day}-{OY[day.month - 1]}", fontsize=13, loc="left", weight="bold")
        ax.text(0.99, 0.97, f"NMSH hududi: {n3 + n4} katak (o'ta noqulay {n4})", transform=ax.transAxes,
                ha="right", va="top", fontsize=8.5, color="#444", path_effects=HALO)
    h = [Patch(fc=COLS[3], label="O'ta noqulay — 4 kompleks"), Patch(fc=COLS[2], label="Noqulay (NMSH) — 3 kompleks"),
         Patch(fc=COLS[1], ec="#bbb", label="NMSH yo'q — 0–2 kompleks"), Patch(fc=COLS[0], label=f"Tog'li hudud (> {ELEV_MAX} m)")]
    axs[0].legend(handles=h, loc="lower left", fontsize=8.5, framealpha=0.95, title="Meteorologik sharoit", title_fontsize=9)
    fig.suptitle("Ifloslanish to'planishi uchun noqulay meteorologik sharoitlar (NMSH) prognozi",
                 x=0.02, y=0.995, ha="left", fontsize=16, weight="bold")
    fig.text(0.02, 0.945, SUBTITLE, fontsize=9.5, color="#555")
    fig.text(0.02, 0.005,
             f"Komplekslar: shamol < {KP['shamol_lt']} m/s; aralashish qatlami < {KP['aral_qatlam_lt']} m; tonggi barqarorlik "
             f"> {KP['barq_gt']} K; bosim 31 kunlik o'rtachadan > +{KP['bosim_anom_gt']} hPa. NMSH: ≥{KP['k']} kompleks "
             "(RD 52.04.52-85 asosida moslashtirilgan).\nChegaralar Toshkent ma'lumotlari bo'yicha kalibrlangan; REK oshishi "
             "ehtimoli faqat Toshkent uchun. Ob-havo prognozi: Open-Meteo. O'zgidromet, sinov rejimi.", fontsize=8.5, color="#555")
    plt.subplots_adjust(left=0.02, right=0.98, top=0.88, bottom=0.08, wspace=0.03)
    return _png(fig)


# ---------------------------------------------------------------- Toshkent paneli
def draw_panel(W, now_h, day_info, pf, ob):
    Wn = W[(W.index >= now_h - pd.Timedelta(hours=6)) & (W.index <= now_h + pd.Timedelta(hours=72))]
    fig, ax = plt.subplots(3, 1, figsize=(15, 9.5), sharex=True, gridspec_kw=dict(height_ratios=[1.2, 0.8, 1.1], hspace=0.08))
    a0, a1, a2 = ax
    for dday in sorted(set(Wn.index.normalize())):
        info = day_info.get(dday)
        if not info: continue
        col = LVL_COL.get(info["daraja"], "#fbe3a1" if info.get("k", 0) >= info.get("kmin", 3) else None)
        if col:
            for a in ax: a.axvspan(dday, dday + pd.Timedelta(days=1), color=col, alpha=0.16, lw=0, zorder=0)
        a0.text(dday + pd.Timedelta(hours=12), 1.02, info["matn"], transform=a0.get_xaxis_transform(), ha="center",
                va="bottom", fontsize=9.5, weight="bold" if info["daraja"] in LVL_COL else "normal")
    # 1) ventilatsiya (Toshkent toifalari: kunduzgi maksimal uchun)
    a0.axhspan(1, VC_PAST, color="#c62828", alpha=0.07); a0.axhspan(VC_PAST, VC_YAXSHI, color="#f39c4a", alpha=0.07)
    a0.plot(Wn.index, Wn.VC.clip(lower=6), color="#1d3557", lw=2)
    a0.set_yscale("log"); a0.set_ylim(5, max(2e4, Wn.VC.max() * 1.3)); a0.set_ylabel("Ventilatsiya\nm²/s")
    for yv, nm in [(VC_PAST, "past"), (VC_YAXSHI, "yaxshi")]:
        a0.axhline(yv, color="#888", lw=0.7, ls="--")
        a0.text(Wn.index[0], yv * 1.1, f" {nm} (kunduzgi maksimum uchun)", fontsize=8, color="#666")
    # 2) barqarorlik
    a1.plot(Wn.index, Wn.barq, color="#6a3d9a", lw=1.8); a1.axhline(5, color="#888", lw=0.7, ls="--")
    a1.set_ylabel("Barqarorlik\nT850−T2m+9.8, K")
    # 3) PM2.5
    if pf is not None and len(pf):
        a2.fill_between(pf.index, pf.p10, pf.p90, color="#e67e22", alpha=0.2, lw=0, label="prognoz p10–p90")
        a2.plot(pf.index, pf.p50, color="#e67e22", lw=2, label="prognoz (ansambl, 9 st. medianasi)")
    if ob is not None and len(ob):
        a2.plot(ob.index, ob.values, color="#222", lw=1.6, label="kuzatuv (9 st. medianasi)")
    a2.axhline(60, color="#c62828", lw=0.9, ls="--"); a2.text(Wn.index[0], 62, " sutkalik REK = 60", fontsize=8, color="#c62828")
    a2.set_ylabel("PM2.5, µg/m³"); a2.legend(loc="upper right", fontsize=8.5, framealpha=0.9); a2.set_ylim(bottom=0)
    for a in ax:
        a.axvline(now_h, color="#444", lw=1); a.grid(alpha=0.25)
    a2.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 6, 12, 18]))
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m\n%H:%M")); a2.set_xlim(Wn.index[0], Wn.index[-1])
    fig.suptitle("Toshkent: ifloslanish to'planishi uchun meteorologik sharoit — 72 soat", x=0.06, ha="left",
                 fontsize=15, weight="bold", y=1.0)
    fig.text(0.06, 0.965, SUBTITLE, fontsize=9.5, color="#555")
    fig.text(0.06, 0.005, f"Ventilatsiya = aralashish qatlami × shamol (10 m). Toifalar Toshkent arxivi bo'yicha (2021–2026): "
             f"kunduzgi maksimum < {VC_PAST} m²/s — REK oshgan kunlar 46–58%, > {VC_YAXSHI} m²/s — 2–7%. "
             "Fon rangi — kunlik NMSH holati. Ob-havo: Open-Meteo.", fontsize=8.3, color="#555")
    return _png(fig)


def _png(fig):
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white"); plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------- hammasi birga
def make_all(sb, MODEL, today, now, pm_prev, rows, prob, daraja):
    KP = MODEL["kompleks"]
    adm1 = gpd.read_file(HERE / "geo" / "uzb_adm1.geojson").to_crs(4326); uzb = adm1.dissolve()
    nb = gpd.read_file(HERE / "geo" / "neighbors.geojson").to_crs(4326)
    outside = gpd.GeoDataFrame(geometry=nb.geometry.difference(uzb.geometry.iloc[0]), crs=4326)
    days = [today + pd.Timedelta(days=1), today + pd.Timedelta(days=2)]
    by_day = {pd.Timestamp(r["target_date"]): r for r in rows}

    # Toshkent: soatlik ob-havo, kunlik holat
    W = hourly_frame(fetch([TASH[0]], [TASH[1]])[0]); dT = daily_feats(W)
    now_h = pd.Timestamp(now).floor("h"); info = {}
    for dday in sorted(set(dT.index)):
        if dday < today or dday > today + pd.Timedelta(days=3) or dT.loc[dday].isna().any(): continue
        k = sum(flags(dT.loc[dday], KP)); r = by_day.get(dday)
        if r:
            info[dday] = dict(daraja=r["daraja"], k=k, kmin=KP["k"],
                              matn=f"{dday:%d.%m}: {r['daraja']}\nkomplekslar {r['komplekslar']}/4 · REK oshishi {r['p_rek1']:.0%}")
        else:
            info[dday] = dict(daraja="", k=k, kmin=KP["k"], matn=f"{dday:%d.%m}: komplekslar {k}/4")
    tash_txt = {d: f"REK oshishi ehtimoli: {by_day[d]['p_rek1']:.0%}" for d in days if d in by_day}

    # PM2.5: ansambl prognozi va kuzatuv
    pf = ob = None
    try:
        last = (sb.table("forecasts_v81").select("run_time").eq("model", "Ensemble").order("run_time", desc=True)
                  .limit(1).execute().data[0]["run_time"])
        fc = pd.DataFrame(sb.table("forecasts_v81").select("station_id,pollutant,target_time,p10,p50,p90").eq("run_time", last)
                            .eq("model", "Ensemble").in_("station_id", CITY_ST).limit(5000).execute().data)
        fc["t"] = pd.to_datetime(fc.target_time, utc=True).dt.tz_convert("Asia/Tashkent").dt.tz_localize(None)
        pf = fc[fc.pollutant.str.contains("2")].groupby("t")[["p10", "p50", "p90"]].median()
        o = pd.DataFrame(sb.table("obs_unified").select("timestamp,station_id,pm25").in_("station_id", CITY_ST)
                           .gte("timestamp", (now - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")).limit(5000).execute().data)
        o["t"] = pd.to_datetime(o.timestamp).dt.tz_localize(None).dt.floor("h"); o["pm25"] = pd.to_numeric(o.pm25, errors="coerce")
        ob = o[o.pm25 >= 1].groupby("t").pm25.median()
    except Exception as e:
        print("PM2.5 qatlami:", repr(e))

    panel = draw_panel(W, now_h, info, pf, ob)
    R, LAT, LON = grid_assessment(KP, days, uzb)
    karta = draw_map(R, LAT, LON, days, KP, adm1, uzb, outside, tash_txt)

    st = sb.storage.from_("maps"); opts = {"content-type": "image/png", "cache-control": "300", "upsert": "true"}
    stamp = f"{today:%Y-%m-%d}"
    for nm, data in [("karta", karta), ("panel", panel)]:
        st.upload(f"nmsh/latest/{nm}.png", data, opts)
        st.upload(f"nmsh/arxiv/{stamp}_{nm}.png", data, opts)
    meta = dict(issue_date=stamp, yaratildi=str(pd.Timestamp(now)), model=MODEL["versiya"],
                kunlar={str(k.date()): v["daraja"] for k, v in info.items() if v["daraja"]},
                nmsh_kataklar={str(d.date()): int((R[R.kun == d].k >= KP["k"]).sum()) for d in days})
    st.upload("nmsh/latest/meta.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"),
              {"content-type": "application/json", "cache-control": "300", "upsert": "true"})
    print("Grafika yuklandi:", meta)
