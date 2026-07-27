"""
unified_pipeline.py — O'zbekiston havo monitoringi
To'rt manbadan ma'lumot yig'ib Supabase ga saqlaydi

Manbalar:
  1. monitoring.meteo.uz  → PM10, PM2.5, CO, SO2, NO, NO2, O3 (28 stantsiya)
  2. Open-Meteo           → harorat, namlik, bosim, shamol, T850
  3. data.meteo.uz/getcurrent → 14 viloyat harorati
  4. data.meteo.uz/getStation → 101 meteo stantsiya (harorat, namlik, bosim, shamol)

Xavfsizlik:
  - Barcha tokenlar/parollar .env dan o'qiladi
  - Kod ichida hech qanday maxfiy qiymat YOZILMAYDI
  - Session avtomatik yangilanadi (auth_manager.py)

Ishlatish:
  python unified_pipeline.py

GitHub Actions yoki Task Scheduler bilan har soatda ishga tushirish.
"""

"""
unified_pipeline.py — O'zbekiston havo monitoringi
To'rt manbadan ma'lumot yig'ib Supabase ga saqlaydi

Manbalar:
  1. monitoring.meteo.uz  → PM10, PM2.5, CO, SO2, NO, NO2, O3 (28 stantsiya)
  2. Open-Meteo           → harorat, namlik, bosim, shamol, T850
  3. data.meteo.uz/getcurrent → 14 viloyat harorati
  4. data.meteo.uz/getStation → 101 meteo stantsiya (harorat, namlik, bosim, shamol)

Tezlashtirish:
  - ThreadPoolExecutor bilan parallel so'rovlar
  - datameteo: 10 parallel, PM scraping: 5 parallel

Xavfsizlik:
  - Barcha tokenlar/parollar .env dan o'qiladi
  - Kod ichida hech qanday maxfiy qiymat YOZILMAYDI

Ishlatish:
  python unified_pipeline.py
"""

import os
import re
import json
import time
import logging
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── .env yuklash ──────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Auth manager ──────────────────────────────────────────────
from auth_manager import get_auth

# ── Supabase ──────────────────────────────────────────────────
try:
    from supabase import create_client
    _url = os.environ.get("SUPABASE_URL", "")
    _key = os.environ.get("SUPABASE_KEY", "")
    if not _url or not _key:
        raise ValueError("SUPABASE_URL yoki SUPABASE_KEY .env da topilmadi!")
    supabase = create_client(_url, _key)
except ImportError:
    supabase = None
    logging.warning("supabase-py o'rnatilmagan — Supabase ga saqlanmaydi")
except ValueError as e:
    supabase = None
    logging.warning("Supabase: %s", e)

# ── Logging ───────────────────────────────────────────────────
LOG_PATH = os.environ.get("LOG_PATH", "unified_pipeline.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

# ── Sozlamalar ────────────────────────────────────────────────
TASHKENT_LAT = 41.2995
TASHKENT_LON = 69.2401
STALE_HOURS  = 3

DATAMETEO_STATION_IDS = list(range(1, 150))

VAR_MAP = {
    "Temp.Dry.10min.Average":        "temp_c",
    "RelHumidity.10min.Average":     "humidity_pct",
    "Press.Station.10min.Average":   "pressure_hpa",
    "QNH.10min.Average":             "qnh_hpa",
    "Wind.Speed.10min.Average":      "wind_speed_ms",
    "Wind.Dir.10min.Average":        "wind_dir_deg",
    "Prec.Rain.Gauge2.10min.Sum":    "precip_mm",
    "Solar.Radiation.10min.Average": "solar_wm2",
    "Temp.DewPoint":                 "dewpoint_c",
}


# ════════════════════════════════════════════════════════════════
# 1. monitoring.meteo.uz — PM va gazlar
# ════════════════════════════════════════════════════════════════

def get_monitoring_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
        "Accept":     "application/json, text/plain, */*",
        "Referer":    "https://monitoring.meteo.uz/",
    })
    session.get("https://monitoring.meteo.uz/", timeout=15)
    return session


def get_horiba_station_ids(session) -> list:
    r    = session.get("https://monitoring.meteo.uz/api/maps", timeout=15)
    data = r.json()
    stations = []
    for group in data.get("data", []):
        for st in group.get("stations", []):
            if st.get("is_horiba"):
                stations.append({
                    "id":        int(st["id"]),
                    "alias":     st.get("alias", ""),
                    "region_id": st.get("region_id", ""),
                    "lat":       float(st.get("lat", 0)),
                    "lon":       float(st.get("lon", 0)),
                })
    log.info("Horiba stantsiyalar: %d ta", len(stations))
    return stations


def scrape_station_pm(session, station_id: int) -> dict:
    url  = f"https://monitoring.meteo.uz/ru/map/view/{station_id}"
    r    = session.get(url, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")

    result    = {"station_id": station_id}
    param_map = {
        "PM 2.5":              "pm25",
        "PM 10":               "pm10",
        "Оксид углерода (CO)": "co",
        "Диоксид серы":        "so2",
        "Оксид азота (NO)":    "no",
        "Диоксид азота (NO2)": "no2",
        "Озон (O3)":           "o3",
        "Аммиак":              "nh3",
        "Сероводород":         "h2s",
    }

    for item in soup.find_all("li", class_="col-xl-6"):
        spans = item.find_all("span")
        if len(spans) < 2:
            continue
        param    = spans[0].get_text(strip=True).rstrip(":")
        val_text = spans[1].get_text(strip=True)
        nums     = re.findall(r"-?[\d.]+", val_text)
        if not nums:
            continue
        val = float(nums[0])
        if val == -9999:
            val = None
        key = param_map.get(param, param)
        result[key] = val

    date_el = soup.find(string=lambda t: t and "Обновлено" in str(t))
    if date_el:
        result["updated_raw"] = date_el.strip()
        try:
            result["timestamp"] = datetime.strptime(
                date_el.strip().replace("Обновлено: ", ""),
                "%d.%m.%Y %H:%M"
            ).replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            result["timestamp"] = None

    return result


def collect_pm_data(session, stations: list) -> list:
    now = datetime.now(timezone.utc)

    def scrape_one(st):
        try:
            d = scrape_station_pm(session, st["id"])
            d["alias"]     = st["alias"]
            d["region_id"] = st["region_id"]
            d["lat"]       = st["lat"]
            d["lon"]       = st["lon"]
            d["source"]    = "monitoring.meteo.uz"

            if d.get("timestamp"):
                ts        = datetime.fromisoformat(d["timestamp"])
                hours_old = (now - ts).total_seconds() / 3600
                if hours_old > STALE_HOURS:
                    log.warning("Stantsiya %s eski (%dh) — o'tkazildi",
                                st["id"], int(hours_old))
                    return None

            log.info("✓ %s (%s): PM10=%s PM2.5=%s",
                     st["id"], st["alias"],
                     d.get("pm10", "?"), d.get("pm25", "?"))
            return d

        except Exception as e:
            log.error("Stantsiya %s xato: %s", st["id"], e)
            return None

    results = []
    # ASSUMPTION: monitoring.meteo.uz scraping uchun 5 parallel yetarli
    # Server yukini kamaytirish uchun ko'proq qo'ymadik
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(scrape_one, st) for st in stations]
        for future in as_completed(futures):
            row = future.result()
            if row:
                results.append(row)

    log.info("PM ma'lumotlar: %d/%d stantsiya yangi",
             len(results), len(stations))
    return results


# ════════════════════════════════════════════════════════════════
# 2. Open-Meteo — meteo parametrlar
# ════════════════════════════════════════════════════════════════

def fetch_open_meteo(lat: float = TASHKENT_LAT,
                     lon: float = TASHKENT_LON) -> dict:
    url    = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":        lat,
        "longitude":       lon,
        "hourly": [
            "temperature_2m",
            "relativehumidity_2m",
            "windspeed_10m",
            "winddirection_10m",
            "surface_pressure",
            "temperature_850hPa",
        ],
        "wind_speed_unit": "kmh",
        "forecast_days":   1,
        "timezone":        "Asia/Tashkent",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        hourly = r.json()["hourly"]
        h      = datetime.now().hour

        result = {
            "temperature_2m":  hourly["temperature_2m"][h],
            "humidity":        hourly["relativehumidity_2m"][h],
            "wind_speed_ms":   round(hourly["windspeed_10m"][h] / 3.6, 2),
            "wind_direction":  hourly["winddirection_10m"][h],
            "pressure_hpa":    hourly["surface_pressure"][h],
            "temperature_850": hourly["temperature_850hPa"][h],
            "source":          "open-meteo",
        }
        log.info("Open-Meteo: T=%.1f°C, WS=%.1f m/s, Hum=%.0f%%",
                 result["temperature_2m"],
                 result["wind_speed_ms"],
                 result["humidity"])
        return result

    except Exception as e:
        log.error("Open-Meteo xato: %s", e)
        return {}


# ════════════════════════════════════════════════════════════════
# 3. data.meteo.uz/getcurrent — 14 viloyat harorati
# ════════════════════════════════════════════════════════════════

def fetch_data_meteo_regions() -> list:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
        "Accept":     "application/json, text/plain, */*",
        "Referer":    "https://data.meteo.uz/",
    })
    try:
        session.get("https://data.meteo.uz/", timeout=15)
        xsrf = session.cookies.get("XSRF-TOKEN", "")
        if xsrf:
            session.headers["X-Xsrf-Token"] = requests.utils.unquote(xsrf)

        r       = session.get("https://data.meteo.uz/map/getcurrent", timeout=10)
        current = r.json()
        now     = datetime.now(timezone.utc)
        results = []

        for item in current:
            city   = item.get("city", {})
            dt_str = item.get("datetime", "")
            try:
                dt        = datetime.strptime(
                    dt_str, "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                hours_old = (now - dt).total_seconds() / 3600
                if hours_old > STALE_HOURS:
                    continue
            except Exception:
                continue

            results.append({
                "region_id":    item.get("region_id"),
                "city_name":    city.get("name"),
                "city_title":   city.get("title"),
                "lat":          city.get("latitude"),
                "lon":          city.get("longitude"),
                "air_t":        item.get("air_t"),
                "weather_code": item.get("weather_code"),
                "cloud_amount": item.get("cloud_amount"),
                "timestamp":    dt.isoformat(),
                "source":       "data.meteo.uz",
            })

        log.info("data.meteo.uz viloyatlar: %d/%d yangi",
                 len(results), len(current))
        return results

    except Exception as e:
        log.error("data.meteo.uz/getcurrent xato: %s", e)
        return []


# ════════════════════════════════════════════════════════════════
# 4. data.meteo.uz/getStation — 149 meteo stantsiya (parallel)
# ════════════════════════════════════════════════════════════════

def fetch_datameteo_stations() -> list:
    auth = get_auth()

    def fetch_one(sid):
        try:
            r = auth.session.post(
                f"{auth.BASE_URL}/map/awd/getStation",
                json={"token": auth.api_token, "id": sid},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "X-XSRF-TOKEN":     auth.xsrf_decoded,
                    "Content-Type":     "application/json;charset=UTF-8",
                    "Accept":           "application/json, */*",
                },
                timeout=10,
            )
            if r.status_code != 200 or len(r.content) < 5:
                return None

            data = r.json()
            st   = data.get("Stations", {})
            if not st:
                return None

            sources = st.get("Sources", {})
            if isinstance(sources, dict):
                sources = [sources]

            row = {
                "station_id":   sid,
                "station_name": st.get("StationName", ""),
                "source":       "data.meteo.uz/stations",
                "fetched_at":   datetime.now(timezone.utc).isoformat(),
            }
            for src in sources:
                for var in src.get("Variables", []):
                    vname   = var.get("VariableName", "")
                    val_obj = var.get("Value", {})
                    if not val_obj:
                        continue
                    if "meastime" not in row and val_obj.get("Meastime"):
                        row["meastime"] = val_obj.get("Meastime")
                    if vname in VAR_MAP:
                        row[VAR_MAP[vname]] = val_obj.get("Value")
            return row

        except Exception as e:
            log.debug("Stantsiya %s xato: %s", sid, e)
            return None

    results = []
    log.info("data.meteo.uz: %d stantsiya parallel yuklanmoqda...",
             len(DATAMETEO_STATION_IDS))

    # ASSUMPTION: server 10 parallel so'rovni ko'tara oladi
    # Agar server xato bersa, max_workers=5 ga tushiring
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(fetch_one, sid): sid
            for sid in DATAMETEO_STATION_IDS
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            row = future.result()
            if row:
                results.append(row)
            if done % 50 == 0:
                log.info("  data.meteo.uz: %d/%d tekshirildi...",
                         done, len(DATAMETEO_STATION_IDS))

    results.sort(key=lambda x: x["station_id"])
    log.info("data.meteo.uz stantsiyalar: %d topildi", len(results))
    return results


# ════════════════════════════════════════════════════════════════
# 5. Supabase ga saqlash
# ════════════════════════════════════════════════════════════════

def save_pm_to_supabase(pm_data: list, meteo: dict) -> int:
    if not supabase or not pm_data:
        return 0

    rows = []
    for d in pm_data:
        rows.append({
            "timestamp":       d.get("timestamp"),
            "station_id":      d.get("station_id"),
            "alias":           d.get("alias"),
            "region_id":       d.get("region_id"),
            "lat":             d.get("lat"),
            "lon":             d.get("lon"),
            "pm25":            d.get("pm25"),
            "pm10":            d.get("pm10"),
            "co":              d.get("co"),
            "so2":             d.get("so2"),
            "no":              d.get("no"),
            "no2":             d.get("no2"),
            "o3":              d.get("o3"),
            "nh3":             d.get("nh3"),
            "h2s":             d.get("h2s"),
            "temperature_2m":  meteo.get("temperature_2m"),
            "humidity":        meteo.get("humidity"),
            "wind_speed_ms":   meteo.get("wind_speed_ms"),
            "wind_direction":  meteo.get("wind_direction"),
            "pressure_hpa":    meteo.get("pressure_hpa"),
            "temperature_850": meteo.get("temperature_850"),
            "collected_at":    datetime.now(timezone.utc).isoformat(),
        })

    try:
        supabase.table("obs_unified").upsert(
            rows, on_conflict="timestamp,station_id"
        ).execute()
        log.info("Supabase obs_unified: %d qator saqlandi", len(rows))
        return len(rows)
    except Exception as e:
        log.error("Supabase obs_unified xato: %s", e)
        return 0


def save_meteo_regions_to_supabase(regions: list) -> int:
    if not supabase or not regions:
        return 0
    try:
        supabase.table("meteo_regions").upsert(
            regions, on_conflict="timestamp,region_id"
        ).execute()
        log.info("Supabase meteo_regions: %d qator saqlandi", len(regions))
        return len(regions)
    except Exception as e:
        log.error("Supabase meteo_regions xato: %s", e)
        return 0


def save_stations_to_supabase(stations: list) -> int:
    if not supabase or not stations:
        return 0

    valid   = [s for s in stations if s.get("meastime")]
    skipped = len(stations) - len(valid)
    if skipped:
        log.warning("meteo_stations: %d stantsiya NULL — o'tkazildi", skipped)
    if not valid:
        log.warning("meteo_stations: saqlash uchun ma'lumot yo'q")
        return 0

    try:
        supabase.table("meteo_stations").upsert(
            valid, on_conflict="station_id,meastime"
        ).execute()
        log.info("Supabase meteo_stations: %d qator saqlandi", len(valid))
        return len(valid)
    except Exception as e:
        log.error("Supabase meteo_stations xato: %s", e)
        return 0


# ════════════════════════════════════════════════════════════════
# 6. JSON eksport (dashboard uchun)
# ════════════════════════════════════════════════════════════════

def export_dashboard_json(pm_data: list, meteo: dict,
                          regions: list, stations: list):
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meteo":      meteo,
        "stations":   pm_data,
        "regions":    regions,
        "meteo_stations": stations,
        "summary": {
            "total_pm_stations":    len(pm_data),
            "total_meteo_stations": len(stations),
            "max_pm10":  max((d.get("pm10") or 0 for d in pm_data), default=0),
            "max_pm25":  max((d.get("pm25") or 0 for d in pm_data), default=0),
            "avg_pm10":  round(
                sum(d.get("pm10") or 0 for d in pm_data) / max(len(pm_data), 1), 1),
            "avg_pm25":  round(
                sum(d.get("pm25") or 0 for d in pm_data) / max(len(pm_data), 1), 1),
        }
    }

    path = os.environ.get("DASHBOARD_JSON_PATH", "dashboard_data.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log.info("Dashboard JSON: %s", path)
    except Exception as e:
        log.error("Dashboard JSON xato: %s", e)


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    t0 = time.time()
    log.info("═" * 60)
    log.info("Unified pipeline ishga tushdi: %s",
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    # 1 & 2 — parallel ishga tushiramiz (PM scraping + Open-Meteo bir vaqtda)
    log.info("--- 1+2. PM scraping va Open-Meteo (parallel) ---")
    with ThreadPoolExecutor(max_workers=2) as executor:
        mon_session  = get_monitoring_session()
        stations_fut = executor.submit(get_horiba_station_ids, mon_session)
        meteo_fut    = executor.submit(fetch_open_meteo)
        meteo        = meteo_fut.result()
        stations     = stations_fut.result()

    pm_data = collect_pm_data(mon_session, stations)

    # 3 & 4 — parallel ishga tushiramiz (viloyatlar + meteo stantsiyalar)
    log.info("--- 3+4. data.meteo.uz (parallel) ---")
    with ThreadPoolExecutor(max_workers=2) as executor:
        regions_fut  = executor.submit(fetch_data_meteo_regions)
        stations_fut = executor.submit(fetch_datameteo_stations)
        regions        = regions_fut.result()
        meteo_stations = stations_fut.result()

    # 5. Supabase ga saqlash
    log.info("--- 5. Supabase ---")
    n_pm       = save_pm_to_supabase(pm_data, meteo)
    n_regions  = save_meteo_regions_to_supabase(regions)
    n_stations = save_stations_to_supabase(meteo_stations)

    # 6. Dashboard JSON
    export_dashboard_json(pm_data, meteo, regions, meteo_stations)

    elapsed = time.time() - t0
    log.info("═" * 60)
    log.info("NATIJA (%.1f sekund):", elapsed)
    log.info("  PM stantsiyalar:    %d ta", len(pm_data))
    log.info("  Meteo stantsiyalar: %d ta", len(meteo_stations))
    log.info("  Viloyatlar:         %d ta", len(regions))
    log.info("  Supabase PM:        %d qator", n_pm)
    log.info("  Supabase regions:   %d qator", n_regions)
    log.info("  Supabase stations:  %d qator", n_stations)
    if meteo:
        log.info("  Meteo: T=%.1f°C, WS=%.1f m/s, Hum=%.0f%%",
                 meteo.get("temperature_2m", 0),
                 meteo.get("wind_speed_ms", 0),
                 meteo.get("humidity", 0))
    log.info("Pipeline tugadi ✓")
    log.info("═" * 60)


if __name__ == "__main__":
    main()
