"""EIA API v2: Henry Hub spot (daily) and Lower-48 working gas in storage (weekly)."""
from __future__ import annotations

import datetime as dt

from ..cache import cache
from ..config import EIA_API_KEY
from ..http import get_json

BASE = "https://api.eia.gov/v2"


async def _series(route: str, freq: str, series: str, length: int) -> list[list]:
    if not EIA_API_KEY:
        raise RuntimeError("EIA_API_KEY nicht gesetzt")
    params = {
        "api_key": EIA_API_KEY, "frequency": freq, "data[0]": "value",
        "facets[series][]": series, "sort[0][column]": "period", "sort[0][direction]": "desc",
        "length": length,
    }
    data = await get_json(f"{BASE}/{route}/data/", params=params, timeout=45)
    rows = ((data or {}).get("response") or {}).get("data") or []
    out = []
    for r in rows:
        try:
            out.append([r["period"], float(r["value"])])
        except (KeyError, TypeError, ValueError):
            continue
    out.sort()
    if not out:
        raise ValueError(f"EIA {series}: keine Daten")
    return out


async def henry_hub_spot() -> tuple[list[list], dict]:
    return await cache.get("eia:hh", 3 * 3600,
                           lambda: _series("natural-gas/pri/fut", "daily", "RNGWHHD", 400))


async def us_storage() -> tuple[dict, dict]:
    async def fetch():
        s = await _series("natural-gas/stor/wkly", "weekly", "NW2_EPG0_SWO_R48_BCF", 400)
        last_day = dt.date.fromisoformat(s[-1][0])
        year = last_day.year

        def week(d: str) -> int:
            return dt.date.fromisoformat(d).isocalendar()[1]

        cur = [[d, v] for d, v in s if d.startswith(str(year))]
        prev = [[d, v] for d, v in s if d.startswith(str(year - 1))]
        band: dict[int, list[float]] = {}
        for d, v in s:
            y = int(d[:4])
            if year - 5 <= y <= year - 1:
                band.setdefault(week(d), []).append(v)
        stats = {w: {"min": min(v), "max": max(v), "avg": sum(v) / len(v)} for w, v in band.items() if len(v) >= 3}
        lw = week(s[-1][0])
        last = s[-1][1]
        chg = last - s[-2][1] if len(s) > 1 else None
        avg5 = stats.get(lw, {}).get("avg")
        ly = next((v for d, v in reversed(prev) if week(d) == lw), None)
        return {
            "current": cur, "last_year": prev, "band": {str(k): v for k, v in sorted(stats.items())},
            "kpi": {"date": s[-1][0], "bcf": last, "chg": chg, "vs5y": (last - avg5) if avg5 else None,
                    "vs5y_pct": (last / avg5 - 1) * 100 if avg5 else None, "vsly": (last - ly) if ly else None},
        }

    return await cache.get("eia:stor", 6 * 3600, fetch)
