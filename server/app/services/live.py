"""Live market data from free, keyless public sources.

- FX: Frankfurter (central-bank reference rates), cached 12 h.
- Metals and the US steel price index: FRED CSV downloads, cached 7 days.

Lookups never block or fail an estimate. A lookup returns what is in memory and,
if that is missing or older than its TTL, starts a background refresh (5 s timeout
per request). With nothing in memory it returns None and the caller uses the value
stored in app/data (status "fallback"). SC_LIVE_RATES=off disables all fetching.
"""

import csv
import io
import json
import ssl
import threading
import time
import urllib.request
from dataclasses import dataclass

import certifi

from app.config import settings

TIMEOUT_S = 5
FX_TTL_S = 12 * 3600
FRED_TTL_S = 7 * 24 * 3600

RETRY_AFTER_FAILURE_S = 300
FX_URL = "https://api.frankfurter.dev/v2/rates?base=USD&quotes={quotes}"
FX_SOURCE = "Frankfurter (central-bank reference rates)"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# IMF monthly world prices (USD per metric tonne) and the BLS producer price index
# for iron and steel (1982 = 100), all downloaded from FRED.
COMMODITY_SERIES = {
    "aluminium": "PALUMUSDM",
    "copper": "PCOPPUSDM",
    "zinc": "PZINCUSDM",
    "lead": "PLEADUSDM",
    "nickel": "PNICKUSDM",
}
STEEL_INDEX_SERIES = "WPU101"
FRED_SERIES = [*COMMODITY_SERIES.values(), STEEL_INDEX_SERIES]


def enabled() -> bool:
    return settings.live_rates


# certifi's CA bundle: some Python builds (python.org on macOS) ship without one.
_TLS = ssl.create_default_context(cafile=certifi.where())


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "should-costing/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_TLS) as r:
        return r.read()


@dataclass(frozen=True)
class Quote:
    value: float
    as_of: str    # date of the observation, not of the fetch
    status: str   # "live" (fetched within TTL) or "cached" (older; refresh pending or failed)
    source: str


class Feed:
    """In-memory cache of one source, refreshed in the background when stale."""

    def __init__(self, ttl_s: float, fetch, source: str):
        self.ttl_s, self._fetch, self.source = ttl_s, fetch, source
        self.data: dict = {}           # key -> payload returned by fetch
        self.fetched_at: float | None = None
        self.error: str | None = None
        self._retry_at = 0.0
        self._lock = threading.Lock()
        self._refreshing = False

    def _stale(self) -> bool:
        return self.fetched_at is None or time.time() - self.fetched_at > self.ttl_s

    def refresh(self) -> None:
        """Fetch now (blocking). Keeps the previous data if the fetch fails."""
        try:
            data = self._fetch()
            with self._lock:
                self.data, self.fetched_at, self.error = data, time.time(), None
        except Exception as e:  # network, HTTP or parse error: keep serving the old data
            self.error = f"{type(e).__name__}: {e}"
            self._retry_at = time.time() + RETRY_AFTER_FAILURE_S  # don't hammer a source that is down
        finally:
            self._refreshing = False

    def ensure_fresh(self) -> None:
        """Start a background refresh if stale; never waits for it."""
        if not enabled() or not self._stale() or time.time() < self._retry_at:
            return
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
        threading.Thread(target=self.refresh, daemon=True).start()

    def status(self) -> str:
        return "cached" if self._stale() else "live"

    def payload(self, key):
        """Raw cached payload for `key` (None if absent or live rates are off)."""
        if not enabled():
            return None
        self.ensure_fresh()
        return self.data.get(key)


# ---------- FX ----------

def _fx_currencies() -> list[str]:
    from app.services.catalog import locations  # local import: catalog imports this module

    return sorted({l.currency for l in locations().values()} - {"USD"})


def _fetch_fx() -> dict[str, tuple[float, str]]:
    rows = json.loads(_http_get(FX_URL.format(quotes=",".join(_fx_currencies()))))
    return {r["quote"]: (float(r["rate"]), r["date"]) for r in rows if r.get("base") == "USD"}


fx_feed = Feed(FX_TTL_S, _fetch_fx, FX_SOURCE)


def fx(currency: str) -> Quote | None:
    """Units of `currency` per USD, or None when no live value is available."""
    hit = fx_feed.payload(currency)
    if hit is None:
        return None
    return Quote(hit[0], hit[1], fx_feed.status(), FX_SOURCE)


# ---------- FRED: metal prices and the steel index ----------

def _parse_fred_csv(body: bytes) -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, value), ...] oldest first; missing observations ('.') skipped."""
    rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
    out = [(d, float(v)) for d, v in rows[1:] if v not in ("", ".")]
    if not out:
        raise ValueError("FRED CSV has no observations")
    return out


def _fetch_fred() -> dict[str, list[tuple[str, float]]]:
    return {s: _parse_fred_csv(_http_get(FRED_URL.format(series=s))) for s in FRED_SERIES}


fred_feed = Feed(FRED_TTL_S, _fetch_fred, "FRED")


def fred(series: str) -> Quote | None:
    """Latest observation of a FRED series, or None when no live value is available."""
    history = fred_feed.payload(series)
    if not history:
        return None
    date, value = history[-1]
    return Quote(value, date[:7], fred_feed.status(), f"FRED {series}")


FEEDS = [fx_feed, fred_feed]


def warm() -> None:
    """Start background refreshes of every feed (called at app startup)."""
    for feed in FEEDS:
        feed.ensure_fresh()
