"""Yahoo Finance chart API: continuous front-month histories and FX spot."""
from __future__ import annotations

import datetime as dt

from ..cache import cache
from ..http import get_json

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"


async def chart(symbol: str, rng: str = "1y", interval: str = "1d", ttl: float = 15 * 60) -> tuple[dict, dict]:
    async def fetch():
        data = await get_json(URL.format(sym=symbol), params={"range": rng, "interval": interval})
        res = (data.get("chart") or {}).get("result")
        if not res:
            err = (data.get("chart") or {}).get("error") or {}
            raise ValueError(f"Yahoo {symbol}: {err.get('description', 'no result')}")
        r = res[0]
        meta = r.get("meta") or {}
        ts = r.get("timestamp") or []
        closes = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        off = meta.get("gmtoffset") or 0
        series = []
        for t, c in zip(ts, closes):
            if c is None:
                continue
            day = dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat()
            if series and series[-1][0] == day:
                series[-1][1] = round(c, 4)
            else:
                series.append([day, round(c, 4)])
        rt = meta.get("regularMarketTime")
        return {
            "symbol": symbol, "name": meta.get("shortName"), "currency": meta.get("currency"),
            "price": meta.get("regularMarketPrice"),
            "time": dt.datetime.fromtimestamp(rt, dt.timezone.utc).isoformat() if rt else None,
            "prev_close": meta.get("chartPreviousClose") if rng in ("1d", "5d") else None,
            "series": series,
        }

    return await cache.get(f"yahoo:{symbol}:{rng}:{interval}", ttl, fetch)
