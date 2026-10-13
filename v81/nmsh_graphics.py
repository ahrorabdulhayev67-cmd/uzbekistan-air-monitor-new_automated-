"""NMSH grafikasi (2-versiya): respublika kartasi va Toshkent 72 soatlik paneli.

Noqulay meteorologik sharoitlar — NMSH (rus. неблагоприятные метеорологические условия, НМУ).
Kartada komplekslar "yumshoq" ko'rinishda (har bir shart chegaraga yaqinligi bo'yicha 0…1) qo'shiladi,
0.5° to'rdan 0.05° ga interpolyatsiya qilinib silliqlanadi va O'zbekiston chegarasi bilan kesiladi.
Bu faqat ko'rinish uchun: rasmiy baho (nmu_daily jadvali) qat'iy chegaralar bo'yicha hisoblanadi.
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
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as MPath
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter
import geopandas as gpd
from shapely.geometry import Point
from shapely.prepared import prep
try:
    from shapely import contains_xy
except ImportError:
    from shapely.vectorized import contains as contains_xy

HERE = Path(__file__).parent
URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = "wind_speed_10m,boundary_layer_height,temperature_2m,temperature_850hPa,surface_pressure"
STEP, FINE, ELEV_MAX = 0.5, 0.05, 1300
VC_PAST, VC_YAXSHI = 1000, 3000            # kunduzgi maksimal ventilatsiya toifalari (Toshkent arxivi, 2021–2026)
TASH = (41.31, 69.28)
CITY_ST = [107, 108, 720, 729, 730, 731, 732, 733, 734]
OY = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]
HAFTA = ["dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"]
CITIES = {"Toshkent": (41.31, 69.28), "Nukus": (42.46, 59.60), "Urganch": (41.55, 60.63), "Buxoro": (39.77, 64.42),
          "Navoiy": (40.10, 65.38), "Samarqand": (39.65, 66.96), "Qarshi": (38.86, 65.79), "Termiz": (37.22, 67.28),
          "Jizzax": (40.12, 67.84), "Guliston": (40.49, 68.78), "Namangan": (41.00, 71.67), "Andijon": (40.78, 72.34),
          "Farg'ona": (40.38, 71.79)}
LABEL_OFF = {"Namangan": (5, 5, "left"), "Andijon": (6, -2, "left"), "Farg'ona": (6, -8, "left"),
             "Guliston": (5, -7, "left"), "Jizzax": (-5, 4, "right"), "Navoiy": (-5, 4, "right"), "Toshkent": (-6, 6, "right")}
C_NOQ, C_OTA, C_TOG, C_LAND, C_OUT = "#f2a25c", "#c8372d", "#d5d5d0", "#f7f6f2", "#e9e9e6"
LVL_COL = {"NMSH 2-daraja": C_OTA, "NMSH 1-daraja": C_NOQ}
INK, MUTED = "#1c2633", "#6b7480"
HALO = [pe.withStroke(linewidth=2.6, foreground="white")]
ASP = 1 / np.cos(np.deg2rad(41.3))
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": "#c9cdd2", "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED})


def fetch(lat, lon, past_days=31, forecast_days=3):
    prm = dict(latitude=",".join(f"{x:.2f}" for x in lat), longitude=",".join(f"{x:.2f}" for x in lon),
               timezone="Asia/Tashkent", past_days=past_days, forecast_days=forecast_days, wind_speed_unit="ms", hourly=HOURLY)
    last = None
    for k in range(6):
        try:
            r = requests.get(URL, params=prm, timeout=300)
        except requests.exceptions.RequestException as e:      # timeout / uzilish — kutib qayta urinish
            last = e; time.sleep(15 * (k + 1)); continue
        if r.status_code == 429:
            time.sleep(20 * (k + 1)); continue
        r.raise_for_status(); js = r.json()
        return js if isinstance(js, list) else [js]
    raise RuntimeError(f"Open-Meteo: javob olinmadi ({last!r})")


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


def soft_score(R, KP):
    """Komplekslar soni, uzluksiz ko'rinishda: har bir shart 0…1 (chegarada 0.5). Faqat kartani silliq chizish uchun."""
    sg = lambda z: 1 / (1 + np.exp(-z))
    return (sg((KP["shamol_lt"] - R.shamol) / 0.25) + sg((np.log(KP["aral_qatlam_lt"]) - np.log(R.aral_qatlam.clip(lower=10))) / 0.15)
            + sg((R.barq_tong - KP["barq_gt"]) / 1.0) + sg((R.bosim_anom - KP["bosim_anom_gt"]) / 1.0))


# ---------------------------------------------------------------- respublika to'ri
def grid_assessment(KP, days, uzb):
    LON = np.round(np.arange(56.0, 73.26, STEP), 2); LAT = np.round(np.arange(37.25, 45.76, STEP), 2)
    poly = prep(uzb.geometry.iloc[0].buffer(0.6))
    G = pd.DataFrame([(la, lo) for la in LAT for lo in LON], columns=["lat", "lon"])
    G = G[[poly.contains(Point(lo, la)) for la, lo in zip(G.lat, G.lon)]].reset_index(drop=True)
    recs = []
    for i in range(0, len(G), 25):
        b = G.iloc[i:i + 25]
        for (_, p), j in zip(b.iterrows(), fetch(b.lat.values, b.lon.values)):
            d = daily_feats(hourly_frame(j))
            for day in days:
                if day not in d.index or d.loc[day].isna().any(): continue
                r = d.loc[day]
                recs.append(dict(lat=p.lat, lon=p.lon, kun=day, elev=float(j.get("elevation", 0)), k=sum(flags(r, KP)),
                                 **{c: float(r[c]) for c in ["shamol", "aral_qatlam", "barq_tong", "bosim_anom"]}))
        time.sleep(2)
    return pd.DataFrame(recs)


def _geom_patch(geom, **kw):
    polys = getattr(geom, "geoms", [geom]); verts, codes = [], []
    for pg in polys:
        for ring in [pg.exterior, *pg.interiors]:
            xy = np.asarray(ring.coords); verts.append(xy)
            codes += [MPath.MOVETO] + [MPath.LINETO] * (len(xy) - 2) + [MPath.CLOSEPOLY]
    return PathPatch(MPath(np.vstack(verts), codes), **kw)


def _smooth_field(x, y, v, X, Y, sigma=1.4):
    pts = np.c_[x, y]
    f = griddata(pts, v, (X, Y), method="linear")
    f = np.where(np.isnan(f), griddata(pts, v, (X, Y), method="nearest"), f)
    return gaussian_filter(f, sigma)


def draw_map(R, days, KP, adm1, uzb, outside, issued):
    geom = uzb.geometry.iloc[0]
    lon_f = np.arange(55.9, 73.3 + 1e-9, FINE); lat_f = np.arange(37.0, 45.7 + 1e-9, FINE)
    X, Y = np.meshgrid(lon_f, lat_f)
    fig, axs = plt.subplots(1, len(days), figsize=(8.2 * len(days), 5.6))
    axs = np.atleast_1d(axs)
    for ax, day in zip(axs, days):
        g = R[R.kun == day]
        S = _smooth_field(g.lon.values, g.lat.values, soft_score(g, KP).values, X, Y)
        E = _smooth_field(g.lon.values, g.lat.values, (g.elev > ELEV_MAX).astype(float).values, X, Y, sigma=1.0)
        outside.plot(ax=ax, color=C_OUT, edgecolor="#d3d3cf", lw=0.4, zorder=0)
        uzb.plot(ax=ax, color=C_LAND, edgecolor="none", zorder=1)
        clip = _geom_patch(geom, transform=ax.transData, facecolor="none", edgecolor="none")
        ax.add_patch(clip)
        for cs in [ax.contourf(X, Y, S, levels=[2.5, 3.5, 99], colors=[C_NOQ, C_OTA], alpha=0.88, zorder=2, antialiased=True),
                   ax.contourf(X, Y, E, levels=[0.5, 99], colors=[C_TOG], alpha=0.9, zorder=2.5)]:
            cs.set_clip_path(clip)
        adm1.boundary.plot(ax=ax, color="white", lw=0.9, zorder=3)
        adm1.boundary.plot(ax=ax, color="#a7aaa6", lw=0.35, zorder=3.1)
        uzb.boundary.plot(ax=ax, color="#4a4f55", lw=0.9, zorder=4)
        for nm, (la, lo) in CITIES.items():
            ax.scatter(lo, la, s=12 if nm != "Toshkent" else 22, c=INK, zorder=6, edgecolor="white", linewidth=0.6)
            dx, dy, ha = LABEL_OFF.get(nm, (5, 3, "left"))
            ax.annotate(nm, (lo, la), xytext=(dx, dy), textcoords="offset points", ha=ha, fontsize=8.5 if nm != "Toshkent" else 9.5,
                        color=INK, zorder=7, path_effects=HALO, weight="bold" if nm == "Toshkent" else "normal")
        ax.set_xlim(55.9, 73.3); ax.set_ylim(37.0, 45.7); ax.set_aspect(ASP)
        ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel("")
        for s_ in ax.spines.values(): s_.set_visible(False)
        ax.set_title(f"{day.day}-{OY[day.month - 1]}, {HAFTA[day.dayofweek]}", fontsize=13, loc="left", color=INK, weight="bold", pad=6)
    h = [Patch(fc=C_OTA, label="O'ta noqulay"), Patch(fc=C_NOQ, label="Noqulay (NMSH)"),
         Patch(fc=C_LAND, ec="#c9c9c4", label="NMSH yo'q"), Patch(fc=C_TOG, label="Tog'li hudud")]
    fig.legend(handles=h, loc="lower center", ncol=4, frameon=False, fontsize=10, bbox_to_anchor=(0.5, 0.035),
               handlelength=1.6, columnspacing=2.2)
    fig.suptitle("Noqulay meteorologik sharoitlar (NMSH) prognozi", x=0.02, y=0.985, ha="left", fontsize=17, color=INK, weight="bold")
    fig.text(0.02, 0.925, f"rus. неблагоприятные метеорологические условия (НМУ)   ·   baho: {issued}", fontsize=10, color=MUTED)
    plt.subplots_adjust(left=0.02, right=0.98, top=0.86, bottom=0.11, wspace=0.04)
    return _png(fig)


# ---------------------------------------------------------------- Toshkent paneli
def draw_panel(W, now_h, day_info, pf, ob, issued):
    Wn = W[(W.index >= now_h - pd.Timedelta(hours=6)) & (W.index <= now_h + pd.Timedelta(hours=72))]
    x0, x1 = Wn.index[0], Wn.index[-1]
    fig = plt.figure(figsize=(14, 9.2))
    gs = fig.add_gridspec(4, 1, height_ratios=[0.22, 1.15, 0.75, 1.05], hspace=0.12)
    ah = fig.add_subplot(gs[0]); a0 = fig.add_subplot(gs[1], sharex=ah); a1 = fig.add_subplot(gs[2], sharex=ah)
    a2 = fig.add_subplot(gs[3], sharex=ah)
    # --- kunlik holat qatori ---
    for dday in sorted(set(Wn.index.normalize())):
        a, b = max(dday, x0), min(dday + pd.Timedelta(days=1), x1)
        if b <= a: continue
        info = day_info.get(dday, {})
        col = LVL_COL.get(info.get("daraja"), "#f6dfa6" if info.get("k", 0) >= info.get("kmin", 3) else "#eef0f2")
        ah.axvspan(a, b, color=col, lw=0); ah.axvline(dday, color="white", lw=3)
        if (b - a) >= pd.Timedelta(hours=8):
            ah.text(a + (b - a) / 2, 0.5, info.get("qisqa", f"{dday:%d.%m}"), ha="center", va="center", fontsize=9.5, color=INK,
                    weight="bold" if info.get("daraja") in LVL_COL else "normal")
    ah.set_ylim(0, 1); ah.set_yticks([]); ah.tick_params(labelbottom=False, bottom=False)
    for s_ in ah.spines.values(): s_.set_visible(False)
    # --- 1) ventilatsiya ---
    a0.axhspan(1, VC_PAST, color=C_OTA, alpha=0.07, lw=0); a0.axhspan(VC_PAST, VC_YAXSHI, color=C_NOQ, alpha=0.08, lw=0)
    a0.plot(Wn.index, Wn.VC.clip(lower=6), color="#1d3557", lw=2)
    a0.set_yscale("log"); a0.set_ylim(5, max(2e4, Wn.VC.max() * 1.3)); a0.set_ylabel("Ventilatsiya, m²/s")
    for yv, nm in [(np.sqrt(5 * VC_PAST), "past"), (np.sqrt(VC_PAST * VC_YAXSHI), "chegaraviy"), (VC_YAXSHI * 2.2, "yaxshi")]:
        a0.text(1.005, yv, nm, transform=a0.get_yaxis_transform(), va="center", fontsize=8.5, color=MUTED)
    # --- 2) barqarorlik ---
    a1.axhspan(5, 40, color="#6a3d9a", alpha=0.06, lw=0)
    a1.plot(Wn.index, Wn.barq, color="#6a3d9a", lw=1.8)
    a1.set_ylim(min(-2, Wn.barq.min() - 1), max(12, Wn.barq.max() + 1)); a1.set_ylabel("Barqarorlik, K")
    a1.text(1.005, 5 + (a1.get_ylim()[1] - 5) / 2, "inversiya", transform=a1.get_yaxis_transform(), va="center", fontsize=8.5, color=MUTED)
    # --- 3) PM2.5 ---
    if pf is not None and len(pf):
        a2.fill_between(pf.index, pf.p10, pf.p90, color="#e67e22", alpha=0.18, lw=0, label="prognoz oralig'i (p10–p90)")
        a2.plot(pf.index, pf.p50, color="#e67e22", lw=2, label="prognoz")
    if ob is not None and len(ob):
        a2.plot(ob.index, ob.values, color=INK, lw=1.8, label="kuzatuv")
    a2.axhline(60, color=C_OTA, lw=0.9, ls=(0, (4, 3)))
    a2.text(1.005, 60, "REK 60", transform=a2.get_yaxis_transform(), va="center", fontsize=8.5, color=C_OTA)
    a2.set_ylabel("PM2.5, µg/m³"); a2.set_ylim(0, max(80, (pf.p90.max() if pf is not None and len(pf) else 0) * 1.1))
    a2.legend(loc="upper left", fontsize=8.5, frameon=False, ncol=3)
    for a in (a0, a1, a2):
        a.axvline(now_h, color="#8b939c", lw=1, ls=(0, (2, 2)))
        for dday in sorted(set(Wn.index.normalize()))[1:]: a.axvline(dday, color="#d9dde1", lw=0.8)
        a.grid(axis="y", alpha=0.25); a.spines["top"].set_visible(False); a.spines["right"].set_visible(False)
        a.tick_params(labelsize=9)
    for a in (a0, a1): a.tick_params(labelbottom=False)
    a2.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 6, 12, 18]))
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M\n%d.%m")); a2.set_xlim(x0, x1)
    fig.suptitle("Toshkent: noqulay meteorologik sharoitlar, 72 soat", x=0.07, y=0.985, ha="left", fontsize=16, color=INK, weight="bold")
    fig.text(0.07, 0.945, f"baho: {issued}", fontsize=10, color=MUTED)
    fig.subplots_adjust(top=0.91, bottom=0.08)
    return _png(fig)


def _png(fig):
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=160, bbox_inches="tight", facecolor="white"); plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------- hammasi birga
def make_all(sb, MODEL, today, now, pm_prev, rows, prob, daraja):
    KP = MODEL["kompleks"]
    adm1 = gpd.read_file(HERE / "geo" / "uzb_adm1.geojson").to_crs(4326); uzb = adm1.dissolve()
    nb = gpd.read_file(HERE / "geo" / "neighbors.geojson").to_crs(4326)
    outside = gpd.GeoDataFrame(geometry=nb.geometry.difference(uzb.geometry.iloc[0]), crs=4326)
    days = [today + pd.Timedelta(days=1), today + pd.Timedelta(days=2)]
    by_day = {pd.Timestamp(r["target_date"]): r for r in rows}
    issued = f"{pd.Timestamp(now):%d.%m.%Y %H:%M}"

    W = hourly_frame(fetch([TASH[0]], [TASH[1]])[0]); dT = daily_feats(W)
    now_h = pd.Timestamp(now).floor("h"); info = {}
    short = {"NMSH 2-daraja": "NMSH 2-daraja", "NMSH 1-daraja": "NMSH 1-daraja", "Yo'q": "NMSH yo'q"}
    for dday in sorted(set(dT.index)):
        if dday < today or dday > today + pd.Timedelta(days=3) or dT.loc[dday].isna().any(): continue
        k = sum(flags(dT.loc[dday], KP)); r = by_day.get(dday)
        if r:
            lvl = short.get(r["daraja"], "Noqulay sharoit")
            info[dday] = dict(daraja=r["daraja"], k=k, kmin=KP["k"], qisqa=f"{dday:%d.%m}  ·  {lvl}  ·  REK ehtimoli {r['p_rek1']:.0%}")
        else:
            info[dday] = dict(daraja="", k=k, kmin=KP["k"], qisqa=f"{dday:%d.%m}")

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

    st = sb.storage.from_("maps"); opts = {"content-type": "image/png", "cache-control": "300", "upsert": "true"}
    stamp = f"{today:%Y-%m-%d}"
    def put(nm, data):
        st.upload(f"nmsh/latest/{nm}.png", data, opts)
        st.upload(f"nmsh/arxiv/{stamp}_{nm}.png", data, opts)
    put("panel", draw_panel(W, now_h, info, pf, ob, issued)); print("Panel yuklandi")
    kat = {}
    try:
        R = grid_assessment(KP, days, uzb)
        put("karta", draw_map(R, days, KP, adm1, uzb, outside, issued)); print("Karta yuklandi")
        kat = {str(d.date()): int((R[R.kun == d].k >= KP["k"]).sum()) for d in days}
    except Exception as e:
        print("Karta xatosi:", repr(e))
    meta = dict(issue_date=stamp, yaratildi=str(pd.Timestamp(now)), model=MODEL["versiya"],
                kunlar={str(k.date()): v["daraja"] for k, v in info.items() if v["daraja"]}, nmsh_kataklar=kat)
    st.upload("nmsh/latest/meta.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"),
              {"content-type": "application/json", "cache-control": "300", "upsert": "true"})
    print("Grafika yuklandi:", meta)
