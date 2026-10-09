"""TashkentAQ V8.1 — operativ prognoz (Toshkent stansiyalari). Har soatda: A, B, C, ansambl → forecasts_v81."""
import os, sys, pickle, logging, argparse, warnings
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, requests, torch
from datetime import datetime, timezone, timedelta
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("v81")
HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else r"C:\uzbekistan_dust\deployment\v81"
MD = os.environ.get("V81_MODEL_DIR", os.path.join(HERE, "models"))
mp = lambda f: os.path.join(MD, f)

SID = 107
# Prognoz faqat Toshkent shahri stansiyalari uchun (region 1726; meteo va modellar Toshkent uchun o'qitilgan).
STATIONS = [107, 108, 733, 732, 734, 730, 731, 720, 729]
_MET = {}                                       # meteo keshi: bir ishga tushirishda barcha stansiyalar uchun bitta
OBS_TS_OFFSET = pd.Timedelta(hours=5)          # kollektor Toshkent vaqtini "+00:00" deb yozadi
LAT, LON = 41.2995, 69.2401
SRC_PTS = {"kyzylkum": (42.0, 64.0), "aralkum": (43.8, 60.2), "karakum": (39.5, 60.0)}
LAGS, SRC = [1, 2, 3, 6, 12, 24], ["kyzylkum", "aralkum", "karakum"]
UZ_HOL = {}                                     # o'qitishda holiday=0 bo'lgan → skew bo'lmasligi uchun
SEASON = {12:"qish",1:"qish",2:"qish",3:"bahor",4:"bahor",5:"bahor",6:"yoz",7:"yoz",8:"yoz",9:"kuz",10:"kuz",11:"kuz"}
GRP = lambda lead: np.digitize(lead, [3.5, 6.5, 12.5])
ENS_MEMBERS = ["A-abs", "B", "C"]             # 2026-10-09: A-rel → A-abs (jonli 3–9.10: skill PM2.5 16→19%, PM10 15→18%)
SHADOW = True                                   # A2/B2: 2026-06 gacha qayta o'qitilgan, ansamblga KIRMAYDI (soya rejimi)
# Onlayn multiplikativ tuzatish (Ansambl-K, soya): p' = (p+1)·e^c − 1, c = oxirgi 14 kunda Ansambl log-xatosining o'rtachasi.
# Sabab: yillararo daraja farqi (oflayn tahlil 2021–2025 kuzlari: tarqoqlik 15–28 → 2–6 punkt). Ma'lumot: v81_skill_daily.sum_lr.
KORR_DAYS, KORR_MIN_N, KORR_CLIP = 14, 24, np.log(2.0)
KORR_D0 = 5                                     # shrinkage: 5 kunlik ma'lumotda tuzatish yarim kuchda
KGRP = ["h1-3", "h4-6", "h7-12", "h13-24"]
KLEADS = {"h1-3": 3, "h4-6": 3, "h7-12": 6, "h13-24": 12}   # guruhdagi muddatlar soni (soatiga juftliklar)

# ------------------------------------------------------------------ yordamchi
def _sincos(x, p): a = 2*np.pi*x/p; return np.sin(a), np.cos(a)
def group(idx):
    return (pd.Series(idx.month.map(SEASON), index=idx) + "_" +
            pd.Series(np.where(idx.hour.isin(range(4, 14)), "kunduz", "tun"), index=idx))
def apply_qm(values, idx, qm):
    g, out = group(idx), pd.Series(np.nan, index=idx)
    for k, (src, tgt) in qm.items():
        m = (g == k) & values.notna()
        out[m] = np.interp(values[m], src, tgt)
    return out

def om(url, lat, lon, hourly, **kw):
    for attempt in range(3):
        try:
            r = requests.get(url, params={"latitude": lat, "longitude": lon, "hourly": ",".join(hourly),
                                          "past_days": 5, "forecast_days": 2, "timezone": "UTC", **kw}, timeout=60)
            r.raise_for_status()
            d = pd.DataFrame(r.json()["hourly"]); d.index = pd.to_datetime(d.pop("time"), utc=True)
            return d
        except Exception as e:
            log.warning("Open-Meteo urinish %d: %s", attempt + 1, e)
    raise RuntimeError("Open-Meteo javob bermadi: " + url)

# ------------------------------------------------------------------ jonli baza
def _load_met(sb, now_utc, MQ):
    """Stansiyaga bog'liq bo'lmagan qism: GFS, manba hududlari, CAMS, meteostansiya 51."""
    V = ["temperature_2m", "relative_humidity_2m", "dew_point_2m", "wind_speed_10m", "wind_direction_10m",
         "precipitation", "surface_pressure", "boundary_layer_height", "cloud_cover", "shortwave_radiation",
         "wind_speed_850hPa", "wind_direction_850hPa", "temperature_850hPa", "geopotential_height_850hPa"]
    F = "https://api.open-meteo.com/v1/forecast"
    b = om(F, LAT, LON, V, models="gfs_seamless", wind_speed_unit="ms").rename(columns={"boundary_layer_height": "blh"})
    for o, m in MQ["pairs"].items():
        b[m + "_qm"] = apply_qm(b[m], b.index, MQ["qm_met"][m])
    t = om(F, LAT, LON, ["temperature_925hPa", "temperature_900hPa", "wind_speed_925hPa",
                         "wind_direction_925hPa", "wind_gusts_10m"], models="gfs_seamless", wind_speed_unit="ms")
    b = b.join(t.add_prefix("tash_"))
    for s, (la, lo) in SRC_PTS.items():
        d = om(F, la, lo, ["wind_speed_10m", "wind_gusts_10m", "soil_moisture_0_to_10cm", "precipitation"],
               models="gfs_seamless", wind_speed_unit="ms")
        b = b.join(d.add_prefix(f"{s}_"))
    c = om("https://air-quality-api.open-meteo.com/v1/air-quality", LAT, LON,
           ["pm10", "pm2_5", "dust", "aerosol_optical_depth", "carbon_monoxide",
            "nitrogen_dioxide", "sulphur_dioxide", "ozone"], domains="cams_global")
    b = b.join(c.add_prefix("cams_"))

    since = (now_utc - timedelta(days=6)).isoformat()
    ms = pd.DataFrame(sb.table("meteo_stations").select("meastime,temp_c,humidity_pct,pressure_hpa,"
                      "wind_dir_deg,wind_speed_ms,precip_mm").eq("station_id", 51)
                      .gte("meastime", since).limit(10000).execute().data)
    ms.index = pd.to_datetime(ms.pop("meastime"), utc=True)
    ms = ms.apply(pd.to_numeric, errors="coerce").resample("h").mean().add_prefix("obs_")
    ms["obs_time"] = ms.index
    ms = ms.dropna(subset=["obs_temp_c"]).reindex(b.index, method="ffill", tolerance=pd.Timedelta("6h"))
    ms["obs_age_h"] = (ms.index - ms.pop("obs_time")).dt.total_seconds() / 3600
    b = b.join(ms)
    b["cal_factor"] = 1.0
    return b

def load_live_base(sb, sid, now_utc, MQ):
    key = pd.Timestamp(now_utc).floor("h")
    if key not in _MET:
        _MET.clear(); _MET[key] = _load_met(sb, now_utc, MQ)
    b = _MET[key].copy()
    since = (now_utc - timedelta(days=6)).isoformat()
    pm = pd.DataFrame(sb.table("obs_unified").select("timestamp,pm10,pm25").eq("station_id", sid)
                        .gte("timestamp", since).limit(10000).execute().data)
    if pm.empty: raise RuntimeError("obs_unified: PM ma'lumoti yo'q")
    pm["t"] = (pd.to_datetime(pm.timestamp, utc=True) - OBS_TS_OFFSET).dt.floor("h")
    pm = pm.groupby("t")[["pm10", "pm25"]].mean().reindex(b.index)
    for p in ["pm10", "pm25"]:
        rep = pm[p].round(3).eq(pm[p].round(3).shift(1)) & pm[p].notna()
        pm.loc[rep, p] = np.nan
    b = b.join(pm)

    return b

# ------------------------------------------------------------------ feature'lar (o'qitish bilan bir xil)
def state_features(b):
    f = pd.DataFrame(index=b.index)
    for p in ["pm10", "pm25"]:
        past = b[p].shift(1)
        for L in LAGS: f[f"{p}_lag{L}"] = b[p].shift(L)
        for w in [3, 6, 24]: f[f"{p}_mean{w}"] = past.rolling(w, min_periods=max(1, w // 3)).mean()
        f[f"{p}_max24"] = past.rolling(24, min_periods=8).max()
        f[f"{p}_last"] = past.ffill(limit=6)
        grp = past.notna().cumsum()
        f[f"{p}_age_h"] = past.groupby(grp).cumcount().where(grp > 0)
        f[f"{p}_trend3"] = f[f"{p}_lag1"] - f[f"{p}_lag3"]
    f["ratio_last"] = f.pm25_last / f.pm10_last
    o = b[[c for c in b if c.startswith("obs_")]].shift(1)
    for c in ["obs_temp_c", "obs_humidity_pct", "obs_pressure_hpa", "obs_wind_speed_ms"]: f[c] = o[c]
    f["obs_age_h"] = o.obs_age_h + 1
    f["obs_wdir_sin"], f["obs_wdir_cos"] = _sincos(o.obs_wind_dir_deg, 360)
    calm = (o.obs_wind_speed_ms <= 1).astype(float).where(o.obs_wind_speed_ms.notna())
    f["obs_calm_h12"] = calm.rolling(12, min_periods=4).sum()
    f["blh_now"] = b.blh; f["blh_d3_now"] = b.blh - b.blh.shift(3)
    f["precip_24h"] = b.precipitation.rolling(24, min_periods=12).sum()
    f["precip_72h"] = b.precipitation.rolling(72, min_periods=36).sum()
    return f

def target_features(b, h):
    fut, f = b.shift(-h), pd.DataFrame(index=b.index)
    f["lead_h"] = h; f["blh_t"] = fut.blh; f["blh_chg"] = fut.blh - b.blh
    f["temp_t"], f["rh_t"], f["wind_t"] = fut.temperature_2m_qm, fut.relative_humidity_2m_qm, fut.wind_speed_10m_qm
    f["wdir_t_sin"], f["wdir_t_cos"] = _sincos(fut.wind_direction_10m, 360)
    f["wind925_t"] = fut.tash_wind_speed_925hPa
    f["inv925_t"] = fut.tash_temperature_925hPa - fut.temperature_2m
    f["inv900_t"] = fut.tash_temperature_900hPa - fut.temperature_2m
    f["vi10_t"] = fut.blh * fut.wind_speed_10m_qm; f["vi925_t"] = fut.blh * fut.tash_wind_speed_925hPa
    f["gust_t"], f["cloud_t"], f["sw_t"], f["precip_t"] = (fut.tash_wind_gusts_10m, fut.cloud_cover,
                                                           fut.shortwave_radiation, fut.precipitation)
    f["hdh_t"] = np.maximum(0, 18 - fut.temperature_2m_qm)
    for c in [c for c in b if c.startswith("cams_")]: f[c + "_t"] = fut[c]
    for s in SRC:
        for back in [12, 24]:
            src = b.shift(-(h - back))
            f[f"{s}_gust_m{back}"] = src[f"{s}_wind_gusts_10m"]
            f[f"{s}_soil_m{back}"] = src[f"{s}_soil_moisture_0_to_10cm"]
    tl = b.index + pd.Timedelta(hours=h + 5)
    f["hour_sin"], f["hour_cos"] = _sincos(tl.hour, 24)
    f["doy_sin"], f["doy_cos"] = _sincos(tl.dayofyear, 365.25)
    f["weekday"] = tl.weekday
    f["holiday"] = pd.Series(tl.date, index=b.index).map(lambda d: int(d in UZ_HOL))
    return f

def build_issue_rows(b, t):
    st = state_features(b).loc[[t]]
    return pd.concat([st.join(target_features(b, h).loc[[t]]) for h in range(1, 25)], ignore_index=True)

def jiang_se(ri): return np.where(ri > 0.4, np.clip(0.275 * np.exp(ri / 6.468) - 0.068, 0, 1), 0.0)
def box_live(b, t, pol, BOXP):
    lam, E, vmin = BOXP[pol]["lam"], BOXP[pol]["E"], BOXP[pol]["vent_min"]
    i0 = b.index.get_loc(t)
    pm = b[pol].shift(1).ffill(limit=6).iloc[i0]
    vent = np.maximum((b.blh * b.wind_speed_10m).values, vmin)
    se = jiang_se(b.precipitation.fillna(0).values)
    out = []
    for k in range(0, 25):
        pm = pm * np.exp(-lam) + E / vent[i0 + k]
        pm *= (1 - se[i0 + k])
        if k >= 1: out.append(pm)
    return np.array(out)

# ------------------------------------------------------------------ C (TFT)
def tft_frame(b, KNOWN, UNKNOWN, T0):
    df = pd.DataFrame(index=b.index)
    for p in ["pm10", "pm25"]:
        df[f"{p}_w"] = b[p].notna().astype("float32")
        df[f"{p}_filled"] = 1 - df[f"{p}_w"]
        df[p] = b[p].interpolate(limit=6).ffill().bfill().clip(lower=0.5)
    sc = _sincos
    df["blh"], df["temp"], df["rh"] = b.blh, b.temperature_2m_qm, b.relative_humidity_2m_qm
    df["wind"], df["wind_raw"] = b.wind_speed_10m_qm, b.wind_speed_10m
    df["wdir_s"], df["wdir_c"] = sc(b.wind_direction_10m, 360)
    df["wind925"] = b.tash_wind_speed_925hPa
    df["inv925"] = b.tash_temperature_925hPa - b.temperature_2m
    df["vent"] = b.blh * b.wind_speed_10m
    df["gust"], df["cloud"], df["sw"], df["precip"] = (b.tash_wind_gusts_10m, b.cloud_cover,
                                                       b.shortwave_radiation, b.precipitation)
    df["hdh"] = np.maximum(0, 18 - b.temperature_2m_qm)
    cams = [c for c in b if c.startswith("cams_")]
    df["cams_avail"] = b[cams[0]].notna().astype("float32")
    for c in cams: df[c] = b[c].fillna(0)
    for s in SRC:
        for lag in [12, 24]:
            df[f"{s}_gust_m{lag}"] = b[f"{s}_wind_gusts_10m"].shift(lag)
            df[f"{s}_soil_m{lag}"] = b[f"{s}_soil_moisture_0_to_10cm"].shift(lag)
    tl = b.index + pd.Timedelta(hours=5)
    df["hour_s"], df["hour_c"] = sc(tl.hour, 24)
    df["doy_s"], df["doy_c"] = sc(tl.dayofyear, 365.25)
    df["weekday"] = tl.weekday.astype("float32")
    for c in ["obs_temp_c", "obs_humidity_pct", "obs_pressure_hpa", "obs_wind_speed_ms", "obs_age_h"]:
        df[c] = b[c]
    df["obs_wd_s"], df["obs_wd_c"] = sc(b.obs_wind_dir_deg, 360)
    num = KNOWN + UNKNOWN
    df[num] = df[num].interpolate(limit=24).ffill().bfill().astype("float32")
    df["time_idx"] = ((df.index - T0) / pd.Timedelta(hours=1)).astype(int)
    df["grp"] = "107"
    return df.rename_axis("datetime").reset_index()

def run_tft(live, t, res):
    from pytorch_forecasting import TimeSeriesDataSet, TemporalFusionTransformer
    from pytorch_forecasting.metrics import QuantileLoss
    CM = pickle.load(open(mp("v81_modelC_meta.pkl"), "rb"))
    KNOWN, UNKNOWN, ENC, PRED = CM["KNOWN"], CM["UNKNOWN"], CM["ENC"], CM["PRED"]
    T0 = pd.Timestamp(CM.get("T0", "2021-02-28 19:00:00+00:00"))
    dfc = tft_frame(live, KNOWN, UNKNOWN, T0)
    dfc = dfc[dfc.datetime <= t + pd.Timedelta(hours=PRED - 1)]
    if len(dfc) < ENC + PRED: raise RuntimeError(f"TFT uchun tarix yetarli emas: {len(dfc)}")
    names = ["h1-3", "h4-6", "h7-12", "h13-24"]
    for target in ["pm10", "pm25"]:
        dsp = pickle.load(open(mp(f"v81_modelC_{target}_dsparams.pkl"), "rb"))
        hp = pickle.load(open(mp(f"v81_modelC_{target}_hparams.pkl"), "rb"))
        ds = TimeSeriesDataSet.from_parameters(dsp, dfc, predict=True)
        m = TemporalFusionTransformer.from_dataset(ds, loss=QuantileLoss([0.1, 0.5, 0.9]), **hp)
        m.load_state_dict(torch.load(mp(f"v81_modelC_{target}_weights.pt"), map_location="cpu"))
        m.eval()
        out = m.predict(ds.to_dataloader(train=False, batch_size=1, num_workers=0),
                        mode="quantiles", trainer_kwargs=dict(logger=False, enable_progress_bar=False)).numpy()[0]
        q = out[1:PRED]
        c = np.array([CM["cqr"][target][names[g]] for g in GRP(np.arange(1, 25))])
        res[(target, "C")] = (q[:, 0] - c, q[:, 1], q[:, 2] + c)

# ------------------------------------------------------------------ onlayn tuzatish (Ansambl-K)
def korr_factors(sb, t):
    """{(station_id, pollutant, lead_grp): c} — Ansambl log-xatosining oxirgi KORR_DAYS kunlik o'rtachasi,
    muddat guruhi bo'yicha alohida, shrinkage bilan: c = Σlr / (n + n0), n0 = KORR_D0 kunlik juftliklar soni.
    Ma'lumot KORR_D0 kun bo'lganda tuzatish yarim kuchda, ko'p bo'lganda — to'liq. Stansiyada juft yo'q bo'lsa — "*" (umumiy)."""
    d0 = ((t + pd.Timedelta(hours=5)).normalize() - pd.Timedelta(days=KORR_DAYS - 1)).date().isoformat()
    rows, s = [], 0
    while True:
        r = (sb.table("v81_skill_daily").select("station_id,pollutant,lead_grp,n,sum_lr")
               .eq("model", "Ensemble").gte("date", d0).range(s, s + 999).execute().data)
        rows += r
        if len(r) < 1000: break
        s += 1000
    D = pd.DataFrame(rows)
    if D.empty or "sum_lr" not in D: return {}
    D = D.dropna(subset=["sum_lr"])
    out = {}
    for (pol, lg), g in D.groupby(["pollutant", "lead_grp"]):
        n0 = KORR_D0 * 24 * KLEADS.get(lg, 6)
        ns = max(g.station_id.nunique(), 1)       # umumiy: har stansiyaga bir xil n0
        out[("*", pol, lg)] = float(np.clip(g.sum_lr.sum() / (g.n.sum() + n0 * ns), -KORR_CLIP, KORR_CLIP))
        for sid, gs in g.groupby("station_id"):
            out[(int(sid), pol, lg)] = float(np.clip(gs.sum_lr.sum() / (gs.n.sum() + n0), -KORR_CLIP, KORR_CLIP))
    return out

# ------------------------------------------------------------------ asosiy
def run(dry=False, issue=None):
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        from dotenv import dotenv_values
        env = dotenv_values(os.environ.get("ENV_FILE", r"C:\uzbekistan_dust\deployment\.env"))
        url, key = env["SUPABASE_URL"], env["SUPABASE_KEY"]
    sb = create_client(url.strip(), key.strip())     # secret oxiridagi bo'shliq/yangi qatorni olib tashlash

    now = pd.Timestamp(issue, tz="UTC") if issue else pd.Timestamp(datetime.now(timezone.utc))
    t = now.floor("h")
    MQ = pickle.load(open(mp("v81_met_qm.pkl"), "rb"))
    M = dict(A=pickle.load(open(mp("v81_modelA.pkl"), "rb")), B=pickle.load(open(mp("v81_modelB.pkl"), "rb")),
             BOXP=pickle.load(open(mp("v81_box_params.pkl"), "rb")))
    for k, f in [("A2", "v81_modelA2.pkl"), ("B2", "v81_modelB2.pkl")]:
        if SHADOW and os.path.exists(mp(f)):
            M[k] = pickle.load(open(mp(f), "rb"))
    log.info("Soya modellari: %s", [k for k in ("A2", "B2") if k in M] or "yo'q")
    try:
        M["K"] = korr_factors(sb, t)
        log.info("Ansambl-K koeffitsientlari e^c: %s",
                 {"-".join(map(str, k)): round(float(np.exp(v)), 2) for k, v in sorted(M["K"].items(), key=str) if k[0] == "*"} or "yo'q")
    except Exception as e:
        M["K"] = {}; log.warning("Ansambl-K: koeffitsient o'qilmadi (%s)", e)
    out, ok, bad = [], [], []
    for sid in STATIONS:
        try:
            out.append(run_station(sb, sid, t, now, MQ, M, dry))
            ok.append(sid)
        except Exception as e:
            bad.append(sid); log.error("Stansiya %s: %s", sid, e)
    log.info("Tayyor: %d stansiya %s | o'tkazib yuborildi: %s", len(ok), ok, bad)
    if not ok: raise RuntimeError("Hech bir stansiya uchun prognoz bo'lmadi")
    return pd.concat(out, ignore_index=True)

def run_station(sb, sid, t, now, MQ, M, dry):
    live = load_live_base(sb, sid, now.to_pydatetime(), MQ)

    past = live[live.index < t]
    last_pm = past.pm10.last_valid_index()
    if last_pm is None or (t - last_pm) > pd.Timedelta(hours=24):
        raise RuntimeError(f"PM10 kuzatuvi eskirgan: oxirgi {last_pm}, prognoz {t}")
    log.info("[%s] prognoz vaqti %s | oxirgi PM %s", sid, t, last_pm)
    A, B, BOXP = M["A"], M["B"], M["BOXP"]
    X = build_issue_rows(live, t); lead = X.lead_h.values
    miss = [c for c in A["features"] if c not in X]
    if miss: raise RuntimeError(f"Feature yetishmaydi: {miss}")

    res = {}
    Xb = X.copy()
    for pp in ["pm10", "pm25"]: Xb[f"box_{pp}"] = box_live(live, t, pp, BOXP)
    for pol in ["pm10", "pm25"]:
        m24 = X[f"{pol}_mean24"].values
        for mode in ["abs", "rel"]:
            q, cqr = A["models"][(pol, mode)], A["cqr"][(pol, mode)]
            P = {k: q[k].predict(X[A["features"]]) for k in ["q10", "q50", "q90"]}
            if mode == "rel": P = {k: np.expm1(v + np.log1p(m24)) for k, v in P.items()}
            c = np.array([cqr[g] for g in GRP(lead)])
            res[(pol, f"A-{mode}")] = (P["q10"] - c, P["q50"], P["q90"] + c)
        q, cqr = B["models"][pol], B["cqr"][pol]
        bx = Xb[f"box_{pol}"].values
        c = np.array([cqr[g] for g in GRP(lead)])
        res[(pol, "B")] = tuple(q[k].predict(Xb[B["features"]]) + bx + s * c
                               for k, s in [("q10", -1), ("q50", 0), ("q90", 1)])
        res[(pol, "persistence")] = (X[f"{pol}_last"].values,) * 3
        # Soya modellari (faqat kuzatish uchun; ansambl va ogohlantirishga ta'sir qilmaydi)
        if "A2" in M:
            q, cqr = M["A2"]["models"][(pol, "rel")], M["A2"]["cqr"][(pol, "rel")]
            P = {k: np.expm1(q[k].predict(X[M["A2"]["features"]]) + np.log1p(m24)) for k in ["q10", "q50", "q90"]}
            c = np.array([cqr[g] for g in GRP(lead)])
            res[(pol, "A2-rel")] = (P["q10"] - c, P["q50"], P["q90"] + c)
        if "B2" in M:
            q, cqr = M["B2"]["models"][pol], M["B2"]["cqr"][pol]
            c = np.array([cqr[g] for g in GRP(lead)])
            res[(pol, "B2")] = tuple(q[k].predict(Xb[M["B2"]["features"]]) + bx + s * c
                                    for k, s in [("q10", -1), ("q50", 0), ("q90", 1)])
    try:
        run_tft(live, t, res)
    except Exception as e:
        log.error("C (TFT) ishlamadi, ansambl A-rel+B bilan: %s", e)

    for pol in ["pm10", "pm25"]:
        mem = [res[(pol, m)] for m in ENS_MEMBERS if (pol, m) in res]
        res[(pol, "Ensemble")] = tuple(np.nanmean([m[i] for m in mem], axis=0) for i in range(3))
        K = M.get("K", {})
        cg = [K.get((int(sid), pol, g), K.get(("*", pol, g))) for g in KGRP]
        if any(c is not None for c in cg):          # soya: portal/bot/ogohlantirishga ta'sir qilmaydi
            cv = np.array([cg[g] or 0.0 for g in GRP(lead)])
            res[(pol, "Ensemble-K")] = tuple(np.expm1(np.log1p(np.clip(v, 0, None)) + cv) for v in res[(pol, "Ensemble")])

    prob = {"A": A["clf"].predict(X[A["features"]]), "B": B["clf"].predict(Xb[B["features"]])}
    thr = {"A": A["thr"], "B": B["thr"]}

    rows, run_time = [], t.isoformat()
    f = lambda v: None if (v is None or not np.isfinite(v)) else round(float(v), 2)
    for (pol, mdl), (lo, p50, hi) in res.items():
        lo, p50, hi = [np.clip(np.asarray(v, float), 0, None) for v in (lo, p50, hi)]
        lo, hi = np.minimum(lo, p50), np.maximum(hi, p50)          # kvantil tartibi
        for i, h in enumerate(lead):
            ph = None; al = None
            if pol == "pm10" and mdl in ("A-abs", "A-rel", "Ensemble"):
                ph = f(prob["A"][i]); al = bool(prob["A"][i] >= thr["A"])
            elif pol == "pm10" and mdl == "B":
                ph = f(prob["B"][i]); al = bool(prob["B"][i] >= thr["B"])
            rows.append(dict(run_time=run_time, station_id=int(sid), model=mdl, pollutant=pol, lead_h=int(h),
                             target_time=(t + pd.Timedelta(hours=int(h))).isoformat(),
                             p10=f(lo[i]), p50=f(p50[i]), p90=f(hi[i]), prob_high=ph, alert=al))

    ens = pd.DataFrame([r for r in rows if r["model"] == "Ensemble"])
    log.info("[%s] ansambl PM10 p50 (h1,6,12,24): %s", sid,
             ens[ens.pollutant == "pm10"].set_index("lead_h").p50.loc[[1, 6, 12, 24]].to_dict())
    if dry:
        log.info("DRY-RUN: %d qator yozilmadi", len(rows)); return pd.DataFrame(rows)
    sb.table("forecasts_v81").upsert(rows, on_conflict="run_time,station_id,model,pollutant,lead_h").execute()
    log.info("forecasts_v81 ga %d qator yozildi", len(rows))
    return pd.DataFrame(rows)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--issue", default=None, help="UTC, masalan '2026-09-28 03:00' (qayta hisoblash uchun)")
    a = ap.parse_args()
    try:
        run(dry=a.dry_run, issue=a.issue)
    except Exception as e:
        log.exception("V8.1 inference xatosi: %s", e); sys.exit(1)
