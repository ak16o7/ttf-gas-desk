"""FX: live EURUSD / EURGBP (Yahoo) with ECB reference-rate history and fallback.

Conventions: EURUSD = USD per EUR, EURGBP = GBP per EUR.
"""
from __future__ import annotations

import csv
import datetime as dt
import io

from ..cache import cache
from ..http import get_text
from .yahoo import chart

ECB = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD+GBP.EUR.SP00.A"


async def ecb_history() -> tuple[dict, dict]:
    async def fetch():
        start = (dt.date.today() - dt.timedelta(days=420)).isoformat()
        text = await get_text(ECB, params={"startPeriod": start, "format": "csvdata"})
        out: dict[str, dict[str, float]] = {"USD": {}, "GBP": {}}
        for row in csv.DictReader(io.StringIO(text)):
            try:
                out[row["CURRENCY"]][row["TIME_PERIOD"]] = float(row["OBS_VALUE"])
            except (KeyError, ValueError):
                continue
        if not out["USD"]:
            raise ValueError("ECB returned no USD rates")
        return out

    return await cache.get("fx:ecb", 6 * 3600, fetch)


async def spot() -> dict:
    """{'EURUSD': x, 'EURGBP': y, 'source': ..., 'time': ...}. Never raises."""
    out: dict = {"EURUSD": None, "EURGBP": None, "source": {}, "time": {}, "errors": []}
    for pair, sym in (("EURUSD", "EURUSD=X"), ("EURGBP", "EURGBP=X")):
        try:
            d, _ = await chart(sym, rng="5d", interval="1d", ttl=10 * 60)
            if d.get("price"):
                out[pair], out["source"][pair], out["time"][pair] = float(d["price"]), "Yahoo", d.get("time")
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"{pair} Yahoo: {e}")
    if out["EURUSD"] is None or out["EURGBP"] is None:
        try:
            h, _ = await ecb_history()
            for pair, cur in (("EURUSD", "USD"), ("EURGBP", "GBP")):
                if out[pair] is None and h.get(cur):
                    day = max(h[cur])
                    out[pair], out["source"][pair], out["time"][pair] = h[cur][day], "EZB", day
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"ECB: {e}")
    return out


def rate_on(hist: dict[str, float], day: str) -> float | None:
    """ECB rate on or before `day` (ISO)."""
    best = None
    for d in sorted(hist):
        if d <= day:
            best = hist[d]
        else:
            break
    return best
