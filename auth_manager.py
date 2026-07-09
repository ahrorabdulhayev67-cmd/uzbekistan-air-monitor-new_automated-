"""
auth_manager.py — data.meteo.uz session boshqaruvi

Login KERAK EMAS — sayt ochiq.
Faqat XSRF token + API token ishlatiladi.
API token .env dan o'qiladi.
"""

import os
import time
import logging
import requests
import urllib.parse

log = logging.getLogger(__name__)

# XSRF token yangilanish davri — 90 daqiqa
SESSION_TTL_SECONDS = 90 * 60


class DataMeteoAuth:
    """data.meteo.uz uchun oddiy session boshqaruvchi (login siz)."""

    BASE_URL = "https://data.meteo.uz"

    def __init__(self):
        self.api_token = os.environ.get("DATAMETEO_API_TOKEN", "")
        if not self.api_token:
            raise ValueError("DATAMETEO_API_TOKEN .env faylida bo'lishi kerak!")

        self.session      = None
        self.xsrf_decoded = ""
        self.initialized_at = None

    def _build_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept":     "application/json, text/plain, */*",
            "Referer":    self.BASE_URL + "/",
            "Origin":     self.BASE_URL,
        })
        return s

    def init_session(self) -> bool:
        """Bosh sahifaga kirib XSRF token olish."""
        log.info("data.meteo.uz session yangilanmoqda...")
        s = self._build_session()
        try:
            s.get(f"{self.BASE_URL}/", timeout=15)
            xsrf_raw = s.cookies.get("XSRF-TOKEN", "")
            if not xsrf_raw:
                log.error("XSRF-TOKEN olinmadi")
                return False

            self.session       = s
            self.xsrf_decoded  = urllib.parse.unquote(xsrf_raw)
            self.initialized_at = time.time()
            log.info("✅ Session tayyor (login siz)")
            return True
        except Exception as e:
            log.error("Session xato: %s", e)
            return False

    def is_fresh(self) -> bool:
        if not self.initialized_at or not self.session:
            return False
        return (time.time() - self.initialized_at) < SESSION_TTL_SECONDS

    def ensure_session(self) -> bool:
        if self.is_fresh():
            return True
        return self.init_session()

    def post(self, endpoint: str, body: dict, retries: int = 2) -> dict | None:
        """XSRF token bilan POST so'rov."""
        for attempt in range(retries + 1):
            if not self.ensure_session():
                log.error("Session yangilanmadi")
                return None
            try:
                r = self.session.post(
                    f"{self.BASE_URL}{endpoint}",
                    json=body,
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "X-XSRF-TOKEN":     self.xsrf_decoded,
                        "Content-Type":     "application/json;charset=UTF-8",
                        "Accept":           "application/json, */*",
                    },
                    timeout=15,
                )
                if r.status_code == 200 and len(r.content) > 5:
                    return r.json()

                # Bo'sh javob — session yangilash
                log.warning("Bo'sh javob (attempt %d/%d), session yangilanmoqda...",
                            attempt + 1, retries + 1)
                self.initialized_at = None
                time.sleep(1)

            except Exception as e:
                log.error("POST %s xato: %s", endpoint, e)
                if attempt < retries:
                    time.sleep(2)

        return None


# Singleton
_auth_instance: DataMeteoAuth | None = None


def get_auth() -> DataMeteoAuth:
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = DataMeteoAuth()
        _auth_instance.init_session()
    return _auth_instance
