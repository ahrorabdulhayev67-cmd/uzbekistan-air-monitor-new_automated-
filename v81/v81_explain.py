"""TashkentAQ V8.1 — "Havo holati sharhi": prognozning matnli tushuntirishi.

Ikki manba birlashtiriladi:
  1) A-abs (LightGBM) modelining TreeSHAP hissalari (pred_contrib; Lundberg & Lee, 2017) — belgilar
     tushunarli guruhlarga yig'iladi (turg'unlik, shamol, yog'in, mintaqaviy fon, ...);
  2) fizik qoidalar — har bir omil matnga faqat o'lchangan/prognoz qilingan ko'rsatkich tasdiqlasa kiradi
     (masalan, "havo turg'un" — faqat aralashish qatlami < 600 m bo'lsa).
"Hozir" qismi kuzatuvdan (PM2.5/PM10 nisbati: < 0.3 — chang, > 0.5 — yonish manbalari).
Raqamlar — ansambl prognozidan (portal va bot bilan bir xil). SHAP sababni emas, model qaroriga
nima ta'sir qilganini ko'rsatadi, shuning uchun matn "asosiy omillar" deydi.
Toifalar: DSanQN 0276-09 (portal va bot bilan bir xil); ikki moddadan og'irrog'i olinadi.
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
THR = {"pm25": [60, 120, 180, 300], "pm10": [75, 150, 250, 350]}          # DSanQN 0276-09
CATS = ["yaxshi", "qoniqarli", "o'rtacha ifloslangan", "yuqori ifloslangan", "qoniqarsiz, xavfli"]
PEAK_H = 24          # "eng yuqori soat" qidiriladigan oyna (kechki pikni ham qamraydi)
PEAK_FROM = 4        # birinchi soatlar "now" sharhida — eng yuqori soat undan keyin qidiriladi
NOW_H = 3            # "kelgusi soatlar" uchun muddat


def _lvl(pol, v):
    return -1 if v is None or not np.isfinite(v) else int(sum(v >= x for x in THR[pol]))


def cat(p25, p10=np.nan):
    """Portal bilan bir xil: ikki moddadan og'irrog'i."""
    i = max(_lvl("pm25", p25), _lvl("pm10", p10))
    return CATS[i] if i >= 0 else "—"


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
                add("mix", g, val, "havoning kuchli turg'unligi (aralashish qatlami 50 m dan past)" if blh < 50
                    else f"havoning turg'unligi (aralashish qatlami atigi ~{blh:.0f} m)")
            elif not pos and blh > 1200:
                add("mix", g, val, f"havoning yaxshi aralashishi (aralashish qatlami ~{blh / 1000:.1f} km)")
        elif g == "Shamol":
            if pos and wind <= 1.5: add("wind", g, val, "shamolning yo'qligi")
            elif not pos and wind >= 3: add("wind", g, val, f"shamol ({wind:.0f} m/s)")
        elif g == "Yog'in" and not pos and prc > 0.1:
            add("rain", g, val, "yog'in")
        elif g == "Sutka vaqti":
            if pos and hl >= 18: add("time", g, val, "kechki soatlarda chiqindilarning yer yuzasida to'planishi")
            elif pos and hl <= 5: add("time", g, val, "tungi soatlarda chiqindilarning yer yuzasida to'planishi")
            elif pos and 6 <= hl <= 10: add("time", g, val, "ertalabki tirbandlik va tunda to'plangan ifloslanish")
            elif not pos and 11 <= hl <= 17: add("mix", g, val, "kunduzgi kuchli aralashish")
        elif g == "Mintaqaviy fon":
            add("reg", g, val, "atrof hududlardagi yuqori ifloslanish foni" if pos else "mintaqadagi past ifloslanish foni")
        elif g == "Tashqi chang" and pos and (cdust > 30 or gust > 12 or dust):
            add("dust", g, val, "cho'l hududlaridan kelayotgan chang")
        elif g == "Harorat" and pos and tmp < 10:
            add("heat", g, val, "sovuq havo sababli isitish chiqindilarining ko'payishi")
    return out


def build_explanations(sid, t, X, A, res, lead):
    """v81_explain jadvali uchun ikki yozuv: 'now' (hozir + kelgusi 3 soat) va 'peak' (24 soat ichidagi eng yuqori soat)."""
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
            R.append({"key": "now", "group": "Hozirgi ifloslanish", "shap": 0.0, "text": "hozirgi yuqori ifloslanishning sekin tarqalishi"})
        return hl, R, R

    def sentence(R, future):
        """Tabiiy jumla: 'Bunga X, Y va Z sabab bo'lmoqda.' / '... yordam bermoqda.'"""
        if not R: return ""
        t = [r["text"] for r in R]
        lst = t[0] if len(t) == 1 else ", ".join(t[:-1]) + " va " + t[-1]
        high = R[0]["shap"] > 0 or R[0]["key"] == "now"
        verb = ("sabab bo'ladi" if future else "sabab bo'lmoqda") if high else ("yordam beradi" if future else "yordam bermoqda")
        return f"Bunga {lst} {verb}."

    warn = "Chang hodisalarida prognoz aniqligi past — kuzatuvlarni kuzatib boring." if dust else ""
    out = []

    # --- now ---
    i = int(np.argmin(np.abs(lead - NOW_H)))
    hl, R, _ = reasons_at(i)
    why = sentence(R, future=False)
    if np.isfinite(n25):
        l1 = f"Hozir: PM2.5 — {n25:.0f}" + (f", PM10 — {n10:.0f}" if np.isfinite(n10) else "") + f" µg/m³ ({cat(n25, n10)})."
        if dust: l1 += " Asosan yirik zarrachalar — havoda chang bor."
        elif ratio > 0.5 and n25 > 35: l1 += " Asosan mayda zarrachalar — isitish, transport va chiqindi yoqish tutuni."
        trend = "oshadi" if e25[i] > n25 * 1.15 else "kamayadi" if e25[i] < n25 * 0.85 else "deyarli o'zgarmaydi"
    else:
        l1, trend = "Hozirgi o'lchov mavjud emas.", None
    l2 = (f"Kelgusi {NOW_H} soatda ifloslanish {trend}: PM2.5 ~{e25[i]:.0f} µg/m³ ({cat(e25[i], e10[i])})." if trend
          else f"Kelgusi {NOW_H} soatda PM2.5 ~{e25[i]:.0f} µg/m³ ({cat(e25[i], e10[i])}) kutilmoqda.")
    out.append(dict(kind="now", lead=int(lead[i]), pm25=e25[i], pm10=e10[i], category=cat(e25[i], e10[i]), trend=trend,
                    text=" ".join(x for x in [l1, l2, why, warn] if x), factors=R))

    # --- peak ---
    w = np.where((lead >= PEAK_FROM) & (lead <= PEAK_H))[0]
    i = int(w[np.argmax(e25[w])])
    hl, R, _ = reasons_at(i)
    why = sentence(R, future=True)
    c = cat(e25[i], e10[i])
    d_now, d_t = (t + pd.Timedelta(hours=5)).date(), (t + pd.Timedelta(hours=int(lead[i]) + 5)).date()
    when = (f"bugun soat {hl:02d}:00 da" if d_t == d_now else
            f"bugun tunda, soat {hl:02d}:00 da" if hl < 6 else f"ertaga soat {hl:02d}:00 da")
    l1 = (f"Kelgusi {PEAK_H} soatda havo sifati {c} darajada qoladi (eng yuqori qiymat {when} ~{e25[i]:.0f} µg/m³)."
          if c in ("yaxshi", "qoniqarli") else
          f"Kelgusi {PEAK_H} soatda eng yuqori ifloslanish {when} kutilmoqda: PM2.5 ~{e25[i]:.0f} µg/m³ ({c}).")
    out.append(dict(kind="peak", lead=int(lead[i]), pm25=e25[i], pm10=e10[i], category=c, trend=None,
                    text=" ".join(x for x in [l1, why, warn] if x), factors=R))

    f = lambda v: None if v is None or not np.isfinite(v) else round(float(v), 1)
    return [dict(station_id=int(sid), kind=o["kind"], run_time=t.isoformat(),
                 target_time=(t + pd.Timedelta(hours=o["lead"])).isoformat(), lead_h=o["lead"],
                 pm25=f(o["pm25"]), pm10=f(o["pm10"]), category=o["category"], trend=o["trend"],
                 text_uz=o["text"], factors=o["factors"]) for o in out]
