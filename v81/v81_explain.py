"""TashkentAQ V8.1 — "Havo holati sharhi": prognozning matnli tushuntirishi.

Ikki manba birlashtiriladi:
  1) A-abs (LightGBM) modelining TreeSHAP hissalari (pred_contrib; Lundberg & Lee, 2017) — belgilar
     tushunarli guruhlarga yig'iladi (turg'unlik, shamol, yog'in, mintaqaviy fon, ...);
  2) fizik qoidalar — har bir omil matnga faqat o'lchangan/prognoz qilingan ko'rsatkich tasdiqlasa kiradi
     (masalan, "havo turg'un" — faqat aralashish qatlami < 600 m bo'lsa).
"Hozir" qismi kuzatuvdan (PM2.5/PM10 nisbati: < 0.3 — chang, > 0.5 — yonish manbalari).
Raqamlar — ansambl prognozidan (portal va bot bilan bir xil). SHAP sababni emas, model qaroriga
nima ta'sir qilganini ko'rsatadi, shuning uchun matn "asosiy omillar" deydi.
Toifalar: US EPA AQI (2024) chegaralari.
"""
import re
import numpy as np, pandas as pd

GROUPS = [
    ("Turg'unlik", r"^(blh|inv\d|vi\d|vent)"),
    ("Shamol", r"^(wind|wdir|gust|obs_wind|obs_wdir|obs_calm)"),
    ("Yog'in", r"(precip|rain)"),
    ("Quyosh va bulut", r"^(sw_t|cloud)"),
    ("Tashqi chang", r"(cams_dust|aerosol_optical|kyzylkum|aralkum|karakum)"),
    ("Mintaqaviy fon", r"^cams_"),
    ("Harorat", r"^(temp|hdh|rh_t|obs_temp|obs_hum|obs_press|dew)"),
    ("Hozirgi ifloslanish", r"^(pm10_|pm25_|ratio_last)"),
    ("Sutka vaqti", r"^(hour|doy|weekday|holiday|lead_h)"),
]
AQI = {"pm25": [(9.0, "yaxshi"), (35.4, "o'rtacha"), (55.4, "sezgir guruhlar uchun zararli"),
                (125.4, "zararli"), (225.4, "juda zararli")],
       "pm10": [(54, "yaxshi"), (154, "o'rtacha"), (254, "sezgir guruhlar uchun zararli"),
                (354, "zararli"), (424, "juda zararli")]}
PEAK_H = 24          # "eng yuqori soat" qidiriladigan oyna (kechki pikni ham qamraydi)
PEAK_FROM = 4        # birinchi soatlar "now" sharhida — eng yuqori soat undan keyin qidiriladi
NOW_H = 3            # "kelgusi soatlar" uchun muddat


def cat(pol, v):
    return next((w for t, w in AQI[pol] if v <= t), "xavfli")


def _group_map(features):
    def g(f):
        for name, pat in GROUPS:
            if re.search(pat, f): return name
        return "Boshqa"
    return pd.Series({f: g(f) for f in features})


def _contrib(A, X):
    feats = A["features"]; gm = _group_map(feats); out = {}
    for pol in ["pm25", "pm10"]:
        c = A["models"][(pol, "abs")]["q50"].predict(X[feats], pred_contrib=True)
        out[pol] = (pd.DataFrame(c[:, :-1], columns=feats).T.groupby(gm).sum().T, c[:, -1])
    return out


def _num(row, k, default=np.nan):
    try:
        v = float(row[k]); return v if np.isfinite(v) else default
    except Exception:
        return default


def _reasons(row, S, high, hl, dust):
    blh, wind, prc, tmp = (_num(row, k) for k in ("blh_t", "wind_t", "precip_t", "temp_t"))
    cdust = _num(row, "cams_dust_t", 0)
    gust = max(_num(row, f"{s}_gust_m24", 0) for s in ("kyzylkum", "aralkum", "karakum"))
    out, used = [], set()

    def add(key, group, val, text):
        if key not in used:
            used.add(key); out.append({"key": key, "group": group, "shap": round(float(val), 2), "text": text})

    for g, val in S.sort_values(key=abs, ascending=False).items():
        if len(out) >= 3 or abs(val) < 1.5: break
        pos = val > 0
        if pos != high: continue
        if g == "Turg'unlik":
            if pos and blh < 600:
                add("mix", g, val, "havo turg'un — aralashish qatlami juda past (50 m dan kam)" if blh < 50
                    else f"havo turg'un — aralashish qatlami atigi ~{blh:.0f} m")
            elif not pos and blh > 1200:
                add("mix", g, val, f"havo yaxshi aralashadi (aralashish qatlami ~{blh / 1000:.1f} km)")
        elif g == "Shamol":
            if pos and wind <= 1.5: add("wind", g, val, "shamol yo'q")
            elif not pos and wind >= 3: add("wind", g, val, f"shamol ({wind:.0f} m/s) ifloslanishni tarqatadi")
        elif g == "Yog'in" and not pos and prc > 0.1:
            add("rain", g, val, "yog'in havoni tozalaydi")
        elif g == "Sutka vaqti":
            if pos and hl >= 18: add("time", g, val, "kechqurun chiqindilar yer yuzasida to'planadi")
            elif pos and hl <= 5: add("time", g, val, "tunda chiqindilar yer yuzasida to'planadi")
            elif pos and 6 <= hl <= 10: add("time", g, val, "ertalabki tirbandlik va tunda to'plangan ifloslanish")
            elif not pos and 11 <= hl <= 17: add("mix", g, val, "kunduzi havo yaxshi aralashadi")
        elif g == "Mintaqaviy fon":
            add("reg", g, val, "atrofdagi hududlarda ham havo ifloslangan" if pos else "mintaqada havo toza")
        elif g == "Tashqi chang" and pos and (cdust > 30 or gust > 12 or dust):
            add("dust", g, val, "cho'l hududlaridan chang kelmoqda")
        elif g == "Harorat" and pos and tmp < 10:
            add("heat", g, val, "sovuq havo — isitish chiqindilari ko'payadi")
    return out


def build_explanations(sid, t, X, A, res, lead):
    """v81_explain jadvali uchun ikki yozuv: 'now' (hozir + kelgusi 3 soat) va 'peak' (12 soat ichidagi eng yuqori soat)."""
    C = _contrib(A, X)
    e25 = np.asarray(res[("pm25", "Ensemble")][1], float); e10 = np.asarray(res[("pm10", "Ensemble")][1], float)
    lead = np.asarray(lead)
    r0 = X.iloc[0]
    n25 = _num(r0, "pm25_last", _num(r0, "pm25_lag1")); n10 = _num(r0, "pm10_last", _num(r0, "pm10_lag1"))
    ratio = n25 / n10 if (np.isfinite(n25) and np.isfinite(n10) and n10 > 0) else np.nan
    dust = bool(ratio < 0.3 and n10 > 100)

    def reasons_at(i):
        hl = int((t + pd.Timedelta(hours=int(lead[i]) + 5)).hour)
        S = C["pm25"][0].iloc[i] + C["pm10"][0].iloc[i] / 2.5
        high = e25[i] > C["pm25"][1][i]
        R = _reasons(X.iloc[i], S, high, hl, dust)
        if high and len(R) < 2 and np.isfinite(n25) and n25 > 35:
            R.append({"key": "now", "group": "Hozirgi ifloslanish", "shap": 0.0, "text": "hozirgi ifloslanish tez tarqalmaydi"})
        head = "Asosiy omillar: " if high else "Ifloslanishni kamaytiruvchi omillar: "
        return hl, R, (head + "; ".join(r["text"] for r in R) + ".") if R else ""

    warn = "Chang hodisalarida prognoz aniqligi past — kuzatuvlarni kuzatib boring." if dust else ""
    out = []

    # --- now ---
    i = int(np.argmin(np.abs(lead - NOW_H)))
    hl, R, why = reasons_at(i)
    if np.isfinite(n25):
        l1 = f"Hozir: PM2.5 — {n25:.0f} µg/m³ ({cat('pm25', n25)})"
        if dust: l1 += f", PM10 — {n10:.0f} µg/m³. Asosan yirik zarrachalar — havoda chang bor."
        elif ratio > 0.5 and n25 > 35: l1 += ". Asosan mayda zarrachalar — isitish, transport va chiqindi yoqish tutuni."
        else: l1 += "."
        trend = "oshadi" if e25[i] > n25 * 1.15 else "kamayadi" if e25[i] < n25 * 0.85 else "deyarli o'zgarmaydi"
    else:
        l1, trend = "Hozirgi o'lchov mavjud emas.", None
    l2 = (f"Kelgusi {NOW_H} soatda ifloslanish {trend}: PM2.5 ~{e25[i]:.0f} µg/m³ ({cat('pm25', e25[i])})." if trend
          else f"Kelgusi {NOW_H} soatda PM2.5 ~{e25[i]:.0f} µg/m³ ({cat('pm25', e25[i])}) kutilmoqda.")
    out.append(dict(kind="now", lead=int(lead[i]), pm25=e25[i], pm10=e10[i], category=cat("pm25", e25[i]), trend=trend,
                    text=" ".join(x for x in [l1, l2, why, warn] if x), factors=R))

    # --- peak ---
    w = np.where((lead >= PEAK_FROM) & (lead <= PEAK_H))[0]
    i = int(w[np.argmax(e25[w])])
    hl, R, why = reasons_at(i)
    c = cat("pm25", e25[i])
    d_now, d_t = (t + pd.Timedelta(hours=5)).date(), (t + pd.Timedelta(hours=int(lead[i]) + 5)).date()
    when = (f"bugun soat {hl:02d}:00 da" if d_t == d_now else
            f"bugun tunda, soat {hl:02d}:00 da" if hl < 6 else f"ertaga soat {hl:02d}:00 da")
    l1 = (f"Kelgusi {PEAK_H} soatda havo sifati {c} darajada qoladi (eng yuqori qiymat {when} ~{e25[i]:.0f} µg/m³)."
          if c in ("yaxshi", "o'rtacha") else
          f"Kelgusi {PEAK_H} soatda eng yuqori ifloslanish {when} kutilmoqda: PM2.5 ~{e25[i]:.0f} µg/m³ ({c}).")
    out.append(dict(kind="peak", lead=int(lead[i]), pm25=e25[i], pm10=e10[i], category=c, trend=None,
                    text=" ".join(x for x in [l1, why, warn] if x), factors=R))

    f = lambda v: None if v is None or not np.isfinite(v) else round(float(v), 1)
    return [dict(station_id=int(sid), kind=o["kind"], run_time=t.isoformat(),
                 target_time=(t + pd.Timedelta(hours=o["lead"])).isoformat(), lead_h=o["lead"],
                 pm25=f(o["pm25"]), pm10=f(o["pm10"]), category=o["category"], trend=o["trend"],
                 text_uz=o["text"], factors=o["factors"]) for o in out]
