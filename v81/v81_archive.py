"""TashkentAQ V8.1 — forecasts_v81 arxivi (2-versiya, kunlik).

Har kecha: kechagacha bo'lgan, hali arxivlanmagan kunlar Storage'dagi oylik Parquet (zstd) fayliga qo'shiladi,
fayl qayta yuklab tekshiriladi va manifest.json ga yoziladi; faqat shundan keyin bazadan tozalanadi:
  Ensemble — RETAIN_ENS kun (14), boshqa modellar — RETAIN_OTHER kun (5), DROP_MODELS — arxivdan keyin darhol.
v81_verify oxirgi 3 mahalliy kunni nishon vaqti bo'yicha qayta hisoblaydi (lead <= 24 soat) — 5 kun zaxira bilan yetarli.
"""
import os, io, sys, json, time, logging
import pandas as pd
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
for n in ["httpx", "httpcore"]: logging.getLogger(n).setLevel(logging.WARNING)
log = logging.getLogger("v81_archive")
TABLE, BUCKET = "forecasts_v81", "v81-archive"
RETAIN_ENS = int(os.environ.get("RETAIN_ENS", "14"))
RETAIN_OTHER = int(os.environ.get("RETAIN_OTHER", "5"))
DROP_MODELS = ["Ensemble-K"]
KEY_COLS = ["run_time", "station_id", "model", "pollutant", "lead_h"]
TS_COLS = ["run_time", "target_time", "created_at"]
MAN = "forecasts_v81/manifest.json"
LVL = {"none": 0, "drop": 1, "other": 2, "all": 3}
try:
    from postgrest.types import ReturnMethod
    MIN = dict(returning=ReturnMethod.minimal)
except Exception:
    MIN = {}


def retry(fn, n=5):
    for k in range(n):
        try:
            return fn()
        except Exception as e:
            if k == n - 1:
                raise
            log.warning("qayta urinish (%s: %s)", type(e).__name__, str(e)[:120]); time.sleep(5 * (k + 1))


def fetch_run(sb, rt):
    out, i = [], 0
    while True:
        ch = retry(lambda: sb.table(TABLE).select("*").eq("run_time", rt).order("id").range(i, i + 999).execute().data)
        out += ch
        if len(ch) < 1000:
            return out
        i += 1000


def count(sb, a, b):
    return retry(lambda: sb.table(TABLE).select("run_time", count="exact").gte("run_time", a).lt("run_time", b)
                   .limit(1).execute().count)


def load(st, path):
    try:
        return pd.read_parquet(io.BytesIO(st.download(path)))
    except Exception:
        return None


def ts(df):
    for c in TS_COLS:
        if c in df:
            df[c] = pd.to_datetime(df[c], utc=True)
    return df


def man_load(st):
    try:
        return json.loads(st.download(MAN).decode("utf-8"))
    except Exception:
        return {}


def man_save(st, man):
    st.upload(MAN, json.dumps(man, ensure_ascii=False, indent=1).encode("utf-8"),
              {"content-type": "application/json", "upsert": "true"})


def archive_day(sb, st, d, man):
    key = f"{d:%Y-%m-%d}"
    a, b = d.isoformat(), (d + pd.Timedelta(days=1)).isoformat()
    n = count(sb, a, b)
    if n == 0:
        return
    rows = []
    for h in range(24):
        rows += fetch_run(sb, (d + pd.Timedelta(hours=h)).isoformat())
    df = ts(pd.DataFrame(rows))
    if len(df) != n:
        raise RuntimeError(f"{key}: yuklangan {len(df)} != serverda {n} — arxivlanmadi")
    path = f"forecasts_v81/{d:%Y-%m}.parquet"
    old = load(st, path)
    full = df if old is None else pd.concat([ts(old), df]).drop_duplicates(subset=KEY_COLS, keep="last")
    buf = io.BytesIO(); full.to_parquet(buf, compression="zstd", index=False)
    retry(lambda: st.upload(path, buf.getvalue(), {"content-type": "application/octet-stream", "upsert": "true"}))
    chk = load(st, path)
    got = 0 if chk is None else int(((pd.to_datetime(chk.run_time, utc=True) >= d) &
                                     (pd.to_datetime(chk.run_time, utc=True) < d + pd.Timedelta(days=1))).sum())
    if got < n:
        raise RuntimeError(f"{key}: tekshiruv o'tmadi (faylda {got} < {n}) — o'chirilmaydi")
    man[key] = {"n": int(n), "file": path, "level": "none"}
    man_save(st, man)
    log.info("%s: %d qator arxivlandi → %s (%.1f MB)", key, n, path, len(buf.getvalue()) / 1e6)


def clean_day(sb, st, key, man, today):
    d = pd.Timestamp(key, tz="UTC"); age = (today - d).days
    need = "all" if age > RETAIN_ENS else "other" if age > RETAIN_OTHER else "drop"
    if LVL[man[key].get("level", "none")] >= LVL[need]:
        return
    for h in range(24):
        rt = (d + pd.Timedelta(hours=h)).isoformat()
        def f():
            q = sb.table(TABLE).delete(**MIN).eq("run_time", rt)
            if need == "other":
                q = q.neq("model", "Ensemble")
            elif need == "drop":
                q = q.in_("model", DROP_MODELS)
            return q.execute()
        retry(f)
    man[key]["level"] = need; man_save(st, man)
    left = count(sb, d.isoformat(), (d + pd.Timedelta(days=1)).isoformat())
    log.info("%s: tozalandi (%s) — bazada qoldi %d / %d", key, need, left, man[key]["n"])


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    st = sb.storage.from_(BUCKET)
    today = pd.Timestamp.now(tz="UTC").normalize()
    man = man_load(st)
    first = retry(lambda: sb.table(TABLE).select("run_time").order("run_time").limit(1).execute().data)
    if first:
        for d in pd.date_range(pd.Timestamp(first[0]["run_time"]).normalize(), today - pd.Timedelta(days=1), freq="D"):
            if f"{d:%Y-%m-%d}" not in man:
                archive_day(sb, st, d, man)
    for key in sorted(man):
        clean_day(sb, st, key, man, today)
    try:
        h = sb.rpc("db_hajmi").execute().data[0]
        log.info("baza %s MB | forecasts_v81 %s MB | storage %s MB", h["baza_mb"], h["forecasts_mb"], h["storage_mb"])
    except Exception as e:
        log.warning("db_hajmi: %r", e)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Arxiv xatosi: %s", e); sys.exit(1)
