"""TashkentAQ V8.1 — forecasts_v81 arxivi.
RETAIN_DAYS dan eski to'liq oylar Parquet (zstd) ga siqilib Supabase Storage'ga yuklanadi,
tekshiruvdan o'tgach bazadan o'chiriladi."""
import os, io, sys, logging
import pandas as pd
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("v81_archive")
TABLE, BUCKET = "forecasts_v81", "v81-archive"
RETAIN_DAYS = int(os.environ.get("RETAIN_DAYS", "60"))
KEY_COLS = ["run_time", "station_id", "model", "pollutant", "lead_h"]

def fetch(sb, a, b):
    out, i = [], 0
    while True:
        ch = (sb.table(TABLE).select("*").gte("run_time", a).lt("run_time", b)
                .order("id").range(i, i + 999).execute().data)
        out += ch
        if len(ch) < 1000:
            return pd.DataFrame(out)
        i += 1000

def month_start(ts):
    ts = pd.Timestamp(ts).tz_convert("UTC")
    return pd.Timestamp(year=ts.year, month=ts.month, day=1, tz="UTC")

def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    st = sb.storage.from_(BUCKET)
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=RETAIN_DAYS)
    first = sb.table(TABLE).select("run_time").order("run_time").limit(1).execute().data
    if not first:
        log.info("Jadval bo'sh"); return
    m = month_start(first[0]["run_time"])
    done = 0
    while m + pd.offsets.MonthBegin(1) <= cutoff:
        m1 = m + pd.offsets.MonthBegin(1)
        a, b = m.isoformat(), m1.isoformat()
        df = fetch(sb, a, b)
        if df.empty:
            m = m1; continue
        path = f"forecasts_v81/{m:%Y-%m}.parquet"
        try:
            old = pd.read_parquet(io.BytesIO(st.download(path)))
            df = pd.concat([old, df]).drop_duplicates(subset=KEY_COLS, keep="last")
            log.info("%s: mavjud arxivga qo'shildi", path)
        except Exception:
            pass
        buf = io.BytesIO()
        df.to_parquet(buf, compression="zstd", index=False)
        st.upload(path, buf.getvalue(), {"content-type": "application/octet-stream", "upsert": "true"})
        chk = pd.read_parquet(io.BytesIO(st.download(path)))
        if len(chk) < len(df):
            raise RuntimeError(f"{path}: tekshiruv o'tmadi ({len(chk)} < {len(df)}), o'chirilmaydi")
        for d in pd.date_range(m, m1, freq="D", inclusive="left"):
            sb.table(TABLE).delete().gte("run_time", d.isoformat()).lt(
                "run_time", (d + pd.Timedelta(days=1)).isoformat()).execute()
        log.info("%s: %d qator arxivlandi (%.1f MB), bazadan o'chirildi",
                 path, len(chk), len(buf.getvalue()) / 1e6)
        done += 1
        m = m1
    if not done:
        log.info("Arxivlanadigan oy yo'q (chegara: %s)", cutoff.strftime("%Y-%m-%d"))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Arxiv xatosi: %s", e); sys.exit(1)
