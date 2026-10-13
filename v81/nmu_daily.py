"""NMU (noqulay meteorologik sharoit) — kundalik baho, 1-versiya.

Metodika RD 52.04.52-85 asosida moslashtirilgan:
  * meteorologik komplekslar: shamol, aralashish qatlami, tonggi barqarorlik (T850 - T2m + 9.8), bosim anomaliyasi;
  * REK oshishi ehtimoli: logistik model (nmu_model.json), 2021-2026 shahar PM2.5 qatori bo'yicha o'qitilgan;
  * 1-daraja: >=k kompleks va P(PM2.5 > 60) >= 0.3;  2-daraja: P(PM2.5 > 120) >= 0.4 (2 REK; Toshkentda 3 REK sutkalik deyarli uchramaydi).
Har kuni ertangi va indingi kun uchun baho yoziladi; o'tgan kunlar kuzatuv bilan tekshiriladi.
"""
import os, json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import numpy as np, pandas as pd, requests
from supabase import create_client

TZ = ZoneInfo("Asia/Tashkent")
CITY = [107, 108, 720, 729, 730, 731, 732, 733, 734]
REK, P1_THR, P2_THR = 60.0, 0.30, 0.40
MODEL = json.loads((Path(__file__).parent / "nmu_model.json").read_text(encoding="utf-8"))
KP = MODEL["kompleks"]


def weather():
    prm = dict(latitude=41.31, longitude=69.28, timezone="Asia/Tashkent", past_days=31, forecast_days=3,
               wind_speed_unit="ms",
               hourly="wind_speed_10m,boundary_layer_height,temperature_2m,temperature_850hPa,surface_pressure")
    r = requests.get("https://api.open-meteo.com/v1/forecast", params=prm, timeout=60); r.raise_for_status()
    W = pd.DataFrame(r.json()["hourly"]); W["time"] = pd.to_datetime(W["time"]); W = W.set_index("time")
    W["barq"] = W.temperature_850hPa - W.temperature_2m + 9.8
    d = pd.DataFrame({
        "shamol": W.wind_speed_10m.resample("D").mean(),
        "aral_qatlam": W.boundary_layer_height.resample("D").mean(),
        "barq_tong": W.barq[(W.index.hour >= 3) & (W.index.hour <= 9)].resample("D").max(),
        "bosim": W.surface_pressure.resample("D").mean()})
    d["bosim_anom"] = d.bosim - d.bosim.shift(1).rolling(31, min_periods=20).mean()
    return d


def city_hourly(sb, a, b):
    """obs_unified.timestamp — Toshkent vaqti "+00:00" bilan; soatlik stansiyalar medianasi (>=4 stansiya)"""
    r = (sb.table("obs_unified").select("timestamp,station_id,pm25").in_("station_id", CITY)
           .gte("timestamp", a.strftime("%Y-%m-%dT%H:%M:%S")).lt("timestamp", b.strftime("%Y-%m-%dT%H:%M:%S"))
           .limit(5000).execute())
    o = pd.DataFrame(r.data)
    if o.empty: return pd.Series(dtype=float)
    o["t"] = pd.to_datetime(o.timestamp).dt.tz_localize(None).dt.floor("h")
    o["pm25"] = pd.to_numeric(o.pm25, errors="coerce"); o = o[o.pm25 >= 1]
    g = o.groupby("t").pm25
    return g.median()[g.count() >= 4]


def prob(x, target):
    M = MODEL[target]
    z = M["intercept"] + sum(c * (x[f] - mu) / s for f, c, mu, s in zip(MODEL["features"], M["coef"], M["mean"], M["scale"]))
    return float(1 / (1 + np.exp(-z)))


def daraja(k, p1, p2):
    if p2 >= P2_THR: return "NMU 2-daraja"
    if k >= KP["k"] and p1 >= P1_THR: return "NMU 1-daraja"
    if k >= KP["k"]: return "Noqulay meteorologik sharoit"
    return "Yo'q"


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    now = datetime.now(TZ).replace(tzinfo=None)
    today = pd.Timestamp(now.date())

    # 1) bugungi daraja: so'nggi 24 soat
    h = city_hourly(sb, now - timedelta(hours=26), now + timedelta(hours=1))
    h = h[h.index > now - timedelta(hours=24)]
    if len(h) < 12: raise SystemExit(f"Kuzatuv yetarli emas: {len(h)} soat")
    pm_prev = float(h.mean())

    # 2) ertaga va indinga
    d = weather(); rows = []
    for lead in (1, 2):
        day = today + pd.Timedelta(days=lead)
        if day not in d.index or d.loc[day].isna().any(): continue
        r = d.loc[day]
        flags = dict(k_shamol=bool(r.shamol < KP["shamol_lt"]), k_qatlam=bool(r.aral_qatlam < KP["aral_qatlam_lt"]),
                     k_barq=bool(r.barq_tong > KP["barq_gt"]), k_bosim=bool(r.bosim_anom > KP["bosim_anom_gt"]))
        k = sum(int(v) for v in flags.values())
        doy = day.dayofyear
        x = dict(shamol=r.shamol, log_aral=np.log(max(r.aral_qatlam, 10)), barq_tong=r.barq_tong, bosim_anom=r.bosim_anom,
                 log_pm_kecha=np.log(pm_prev), oy_sin=np.sin(2 * np.pi * doy / 365.25), oy_cos=np.cos(2 * np.pi * doy / 365.25))
        p1, p2 = prob(x, "rek1"), prob(x, "rek2")
        rows.append(dict(issue_date=str(today.date()), target_date=str(day.date()), lead_days=lead, komplekslar=k, **flags,
                         shamol=round(float(r.shamol), 2), aral_qatlam=round(float(r.aral_qatlam), 0),
                         barq_tong=round(float(r.barq_tong), 2), bosim_anom=round(float(r.bosim_anom), 2),
                         pm_prev=round(pm_prev, 1), p_rek1=round(p1, 3), p_rek2=round(p2, 3),
                         daraja=daraja(k, p1, p2), model=MODEL["versiya"]))
    if rows:
        sb.table("nmu_daily").upsert(rows, on_conflict="issue_date,target_date").execute()
    for r in rows:
        print(f"{r['target_date']} (+{r['lead_days']}): {r['daraja']:30s} komplekslar {r['komplekslar']}/4, "
              f"P(>60)={r['p_rek1']:.0%}, P(>120)={r['p_rek2']:.0%}")

    # 3) tekshiruv: o'tgan kunlar, kuzatuv hali yozilmagan
    old = (sb.table("nmu_daily").select("issue_date,target_date").is_("obs_pm25", "null")
             .lt("target_date", str(today.date())).limit(200).execute().data)
    for td in sorted({o["target_date"] for o in old}):
        a = datetime.fromisoformat(td); s = city_hourly(sb, a, a + timedelta(days=1))
        if len(s) < 18: continue
        m = float(s.mean())
        (sb.table("nmu_daily").update({"obs_pm25": round(m, 1), "obs_rek1": m > REK, "obs_rek2": m > 2 * REK})
           .eq("target_date", td).execute())
        print(f"Tekshirildi {td}: sutkalik PM2.5 = {m:.0f}")


if __name__ == "__main__":
    main()
