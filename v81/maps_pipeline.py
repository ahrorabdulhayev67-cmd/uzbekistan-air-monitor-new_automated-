"""TashkentAQ — kunlik xarita qatlamlari: SILAM va CAMS (O'zbekiston), PM uchun stansiyalar bo'yicha nisbat maydoni.
Natija: Supabase Storage, ochiq `maps` bucket:
  maps/{silam,cams}/latest/meta.json, {var}.bin (Uint16, kod × scale), ratio_{pm25,pm10}.bin
  maps/history/points.csv.gz — modellarning stansiya nuqtalaridagi qiymatlari (nisbat uchun)
"""
import os, io, sys, json, gzip, zipfile, logging, tempfile, warnings
import numpy as np, pandas as pd, xarray as xr, requests
from supabase import create_client

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("maps")

BBOX = dict(lat0=37.0, lat1=46.0, lon0=55.5, lon1=73.5)
STEP_H, HORIZON_H = 3, 120
BUCKET = "maps"
OBS_TS_OFFSET = pd.Timedelta(hours=5)          # obs_unified: Toshkent vaqti "+00:00" bilan
RATIO_DAYS, RATIO_MIN_DAYS = 30, 7
L_KM, PRIOR_W = 150.0, 0.5                     # nisbat maydoni: Gauss masshtabi, medianaga tortish vazni
MW = {"no2": 46.01, "so2": 64.07, "o3": 48.00, "co": 28.01}   # g/mol; havo 28.97
UNITS = {"pm25": "µg/m³", "pm10": "µg/m³", "no2": "µg/m³", "so2": "µg/m³", "o3": "µg/m³", "co": "mg/m³", "dust": "AOD"}

sb = create_client(os.environ["SUPABASE_URL"].strip(), os.environ["SUPABASE_KEY"].strip())
st = sb.storage.from_(BUCKET)
NOW = pd.Timestamp.now(tz="UTC").floor("h").tz_localize(None)


# ------------------------------------------------------------------ SILAM
def fetch_silam():
    url = "https://thredds.silam.fmi.fi/thredds/dodsC/silam_glob_v6_1_sfc/silam_glob_v6_1_sfc_best.ncd"
    ds = xr.open_dataset(url)
    tcoord = "time" if "time" in ds.coords else [c for c in ds.coords if c.startswith("time")][0]
    t = pd.to_datetime(ds[tcoord].values)
    t0 = NOW - pd.Timedelta(hours=NOW.hour % STEP_H)
    want = pd.date_range(t0, t0 + pd.Timedelta(hours=HORIZON_H), freq=f"{STEP_H}h")
    it = [i for i, x in enumerate(t) if x in set(want)]
    if len(it) < 8:
        raise RuntimeError(f"SILAM: vaqt qadamlari yetarli emas ({len(it)}), oxirgi vaqt {t.max()}")
    lat, lon = ds.lat.values, ds.lon.values
    iy = np.where((lat >= BBOX["lat0"]) & (lat <= BBOX["lat1"]))[0]
    ix = np.where((lon >= BBOX["lon0"]) & (lon <= BBOX["lon1"]))[0]
    vv = ["cnc_PM2_5", "cnc_PM10", "vmr_NO2_gas", "vmr_SO2_gas", "vmr_O3_gas", "vmr_CO_gas", "air_dens", "ocd_dust_w550"]
    sub = ds[vv].isel({tcoord: it, "lat": slice(iy.min(), iy.max() + 1), "lon": slice(ix.min(), ix.max() + 1)}).load()
    rho = sub.air_dens.values
    out = {"pm25": sub.cnc_PM2_5.values * 1e9, "pm10": sub.cnc_PM10.values * 1e9, "dust": sub.ocd_dust_w550.values}
    for k, v in [("no2", "vmr_NO2_gas"), ("so2", "vmr_SO2_gas"), ("o3", "vmr_O3_gas"), ("co", "vmr_CO_gas")]:
        out[k] = sub[v].values * (MW[k] / 28.97) * rho * (1e6 if k == "co" else 1e9)
    times = pd.to_datetime(sub[tcoord].values)
    log.info("SILAM: %d kadr, %s → %s, panjara %dx%d", len(times), times[0], times[-1], len(iy), len(ix))
    return dict(model="silam", run=str(times[0]), times=times, lat=sub.lat.values, lon=sub.lon.values, data=out,
                source="FMI SILAM global v6.1 (~20 km), Best Time Series")


# ------------------------------------------------------------------ CAMS
def fetch_cams():
    import cdsapi
    c = cdsapi.Client(url="https://ads.atmosphere.copernicus.eu/api", key=os.environ["ADS_KEY"].strip(), quiet=True)
    area = [BBOX["lat1"], BBOX["lon0"], BBOX["lat0"], BBOX["lon1"]]
    last_err = None
    for back in [0, 1]:
        day = (NOW - pd.Timedelta(days=back)).strftime("%Y-%m-%d")
        base = {"date": day, "time": "00:00", "type": "forecast", "data_format": "netcdf_zip", "area": area,
                "leadtime_hour": [str(h) for h in range(0, HORIZON_H + 1, STEP_H)]}
        try:
            tmp = tempfile.mkdtemp()
            c.retrieve("cams-global-atmospheric-composition-forecasts",
                       {**base, "variable": ["particulate_matter_2.5um", "particulate_matter_10um",
                                             "dust_aerosol_optical_depth_550nm", "surface_pressure", "2m_temperature"]},
                       f"{tmp}/sfc.zip")
            c.retrieve("cams-global-atmospheric-composition-forecasts",
                       {**base, "variable": ["nitrogen_dioxide", "sulphur_dioxide", "ozone", "carbon_monoxide"],
                        "model_level": ["137"]}, f"{tmp}/ml.zip")
            ds = {}
            for z in ["sfc", "ml"]:
                zipfile.ZipFile(f"{tmp}/{z}.zip").extractall(f"{tmp}/{z}")
                f = [os.path.join(f"{tmp}/{z}", x) for x in os.listdir(f"{tmp}/{z}") if x.endswith(".nc")][0]
                ds[z] = xr.open_dataset(f).squeeze(drop=True).load()
            s, m = ds["sfc"], ds["ml"]
            rho = (s.sp / (287.05 * s.t2m)).values
            out = {"pm25": s.pm2p5.values * 1e9, "pm10": s.pm10.values * 1e9, "dust": s.duaod550.values,
                   "no2": m.no2.values * rho * 1e9, "so2": m.so2.values * rho * 1e9,
                   "o3": m.go3.values * rho * 1e9, "co": m.co.values * rho * 1e6}
            times = pd.Timestamp(day) + pd.to_timedelta(s.forecast_period.values)
            log.info("CAMS: %s 00Z, %d kadr, panjara %dx%d", day, len(times), s.latitude.size, s.longitude.size)
            return dict(model="cams", run=f"{day} 00:00", times=pd.DatetimeIndex(times), lat=s.latitude.values,
                        lon=s.longitude.values, data=out, source="Copernicus CAMS global (~40 km)")
        except Exception as e:
            last_err = e
            log.warning("CAMS %s: %s", day, str(e)[:200])
    raise RuntimeError(f"CAMS yuklanmadi: {last_err}")


# ------------------------------------------------------------------ Storage
def put(path, data, ctype):
    st.upload(path, data, {"content-type": ctype, "upsert": "true", "cache-control": "300"})

def get(path):
    try:
        return st.download(path)
    except Exception:
        return None

def encode(arr):
    a = np.nan_to_num(np.asarray(arr, dtype="float64"), nan=0.0).clip(min=0)
    scale = max(float(a.max()), 1e-9) / 65000
    return np.round(a / scale).astype("<u2").tobytes(), scale, float(a.max()), float(np.percentile(a, 99))


# ------------------------------------------------------------------ stansiyalar va nisbat
def load_obs(days):
    since = (NOW - pd.Timedelta(days=days) + OBS_TS_OFFSET).isoformat()
    rows, i = [], 0
    while True:
        ch = (sb.table("obs_unified").select("station_id,lat,lon,timestamp,pm10,pm25").gte("timestamp", since)
                .order("timestamp").range(i, i + 999).execute().data)
        rows += ch
        if len(ch) < 1000: break
        i += 1000
    o = pd.DataFrame(rows)
    if o.empty: return o, pd.DataFrame()
    o["t"] = (pd.to_datetime(o.timestamp, utc=True) - OBS_TS_OFFSET).dt.floor("h").dt.tz_localize(None)
    stations = o.groupby("station_id")[["lat", "lon"]].median().dropna()
    obs = o.groupby(["station_id", "t"])[["pm25", "pm10"]].mean().reset_index()
    return obs, stations

def sample_points(M, stations):
    """Modelning stansiyalarga eng yaqin katakdagi PM qiymatlari (birinchi 24 soat)."""
    rows = []
    lat, lon = np.asarray(M["lat"]), np.asarray(M["lon"])
    k = [i for i, t in enumerate(M["times"]) if t < M["times"][0] + pd.Timedelta(hours=24)]
    for sid, r in stations.iterrows():
        iy, ix = np.abs(lat - r.lat).argmin(), np.abs(lon - r.lon).argmin()
        for i in k:
            rows.append(dict(time=M["times"][i], model=M["model"], station_id=int(sid),
                             pm25=float(M["data"]["pm25"][i, iy, ix]), pm10=float(M["data"]["pm10"][i, iy, ix])))
    return pd.DataFrame(rows)

def ratio_field(M, hist, obs, stations, pol):
    h = hist[(hist.model == M["model"])].copy()
    h["time"] = pd.to_datetime(h["time"])
    j = h.merge(obs, left_on=["station_id", "time"], right_on=["station_id", "t"], suffixes=("_m", "_o"))
    j = j[(j[f"{pol}_o"] > 1) & (j[f"{pol}_m"] > 0.1)]
    if j.empty or j.time.dt.normalize().nunique() < RATIO_MIN_DAYS:
        return None, {}
    lr = np.log(j[f"{pol}_o"] / j[f"{pol}_m"]).groupby(j.station_id).median()
    lr = lr[lr.index.isin(stations.index)]
    med = float(lr.median()); res = lr - med
    LAT, LON = np.meshgrid(M["lat"], M["lon"], indexing="ij")
    num = np.zeros_like(LAT); den = np.full_like(LAT, PRIOR_W)
    for sid, v in res.items():
        d = np.hypot((LAT - stations.loc[sid, "lat"]) * 111.0,
                     (LON - stations.loc[sid, "lon"]) * 111.0 * np.cos(np.radians(41.5)))
        w = np.exp(-(d / L_KM) ** 2); num += w * v; den += w
    field = np.exp(med + num / den)
    info = {"median_ratio": round(float(np.exp(med)), 3), "stations": int(len(lr)),
            "days": int(j.time.dt.normalize().nunique())}
    return field, info


# ------------------------------------------------------------------ asosiy
def publish(M, ratios):
    meta = {"model": M["model"], "source": M["source"], "run": M["run"], "generated": NOW.isoformat() + "Z",
            "times": [t.isoformat() + "Z" for t in M["times"]],
            "lat": [round(float(x), 4) for x in M["lat"]], "lon": [round(float(x), 4) for x in M["lon"]],
            "vars": {}, "ratio": {}}
    for k, a in M["data"].items():
        b, scale, mx, p99 = encode(a)
        put(f"{M['model']}/latest/{k}.bin", b, "application/octet-stream")
        meta["vars"][k] = {"unit": UNITS[k], "scale": scale, "max": round(mx, 3), "p99": round(p99, 3)}
    for pol, (field, info) in ratios.items():
        if field is None: continue
        put(f"{M['model']}/latest/ratio_{pol}.bin", np.round(field * 1000).astype("<u2").tobytes(), "application/octet-stream")
        meta["ratio"][pol] = {"scale": 0.001, **info}
    put(f"{M['model']}/latest/meta.json", json.dumps(meta).encode(), "application/json")
    log.info("%s: e'lon qilindi (%d qatlam, nisbat: %s)", M["model"], len(meta["vars"]), list(meta["ratio"]))


def main():
    obs, stations = load_obs(RATIO_DAYS + 2)
    log.info("Kuzatuv: %d qator, %d stansiya", len(obs), len(stations))
    raw = get("history/points.csv.gz")
    hist = pd.read_csv(io.BytesIO(gzip.decompress(raw)), parse_dates=["time"]) if raw else pd.DataFrame()
    ok = 0
    for fetch in [fetch_silam, fetch_cams]:
        try:
            M = fetch()
        except Exception as e:
            log.error("%s: %s", fetch.__name__, e); continue
        if not stations.empty:
            hist = pd.concat([hist, sample_points(M, stations)], ignore_index=True)
            hist = hist.drop_duplicates(["time", "model", "station_id"], keep="last")
        ratios = {pol: ratio_field(M, hist, obs, stations, pol) for pol in ["pm25", "pm10"]} if not obs.empty else {}
        publish(M, ratios); ok += 1
    if not hist.empty:
        hist = hist[pd.to_datetime(hist.time) >= NOW - pd.Timedelta(days=RATIO_DAYS + 15)]
        put("history/points.csv.gz", gzip.compress(hist.to_csv(index=False).encode()), "application/gzip")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
