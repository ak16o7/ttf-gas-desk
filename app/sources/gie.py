"""GIE AGSI+ (storage) and ALSI (LNG terminals). Requires GIE_API_KEY (free)."""
from __future__ import annotations

import asyncio
import datetime as dt

from ..cache import cache
from ..config import GIE_API_KEY
from ..http import get_json

AGSI = "https://agsi.gie.eu/api"
ALSI = "https://alsi.gie.eu/api"
_sem = asyncio.Semaphore(3)


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


async def _paged(base: str, area: str, start: dt.date, end: dt.date) -> list[dict]:
    if not GIE_API_KEY:
        raise RuntimeError("GIE_API_KEY nicht gesetzt")
    params = {"from": start.isoformat(), "to": end.isoformat(), "size": 300}
    if area == "EU":
        params["type"] = "eu"
    else:
        params["country"] = area
    headers = {"x-key": GIE_API_KEY}

    async def page(n: int) -> dict:
        async with _sem:
            return await get_json(base, params={**params, "page": n}, headers=headers, timeout=45)

    first = await page(1)
    rows = list(first.get("data") or [])
    last = int(first.get("last_page") or 1)
    if last > 1:
        rest = await asyncio.gather(*[page(n) for n in range(2, min(last, 12) + 1)])
        for r in rest:
            rows.extend(r.get("data") or [])
    return rows


async def storage(area: str) -> tuple[list[dict], dict]:
    years = 5  # 5 full previous years (5y band) + current year: <= 7 pages of 300

    async def fetch():
        end = dt.date.today()
        start = dt.date(end.year - years, 1, 1)
        rows = await _paged(AGSI, area, start, end)
        out = {}
        for r in rows:
            d = r.get("gasDayStart")
            if not d:
                continue
            out[d] = {
                "d": d, "full": _f(r.get("full")), "gis": _f(r.get("gasInStorage")),
                "inj": _f(r.get("injection")), "wd": _f(r.get("withdrawal")),
                "wgv": _f(r.get("workingGasVolume")), "status": r.get("status"),
            }
        if not out:
            raise ValueError(f"AGSI {area}: keine Daten")
        return [out[k] for k in sorted(out)]

    return await cache.get(f"agsi:{area}", 3 * 3600, fetch)


async def lng(area: str) -> tuple[list[dict], dict]:
    async def fetch():
        end = dt.date.today()
        start = end - dt.timedelta(days=760)
        rows = await _paged(ALSI, area, start, end)
        out = {}
        for r in rows:
            d = r.get("gasDayStart")
            if not d:
                continue
            inv = r.get("inventory") or {}
            dtmi = r.get("dtmi") or {}
            out[d] = {"d": d, "sendout": _f(r.get("sendOut")), "inv": _f(inv.get("gwh")),
                      "dtmi": _f(dtmi.get("gwh")), "dtrs": _f(r.get("dtrs")), "status": r.get("status")}
        if not out:
            raise ValueError(f"ALSI {area}: keine Daten")
        return [out[k] for k in sorted(out)]

    return await cache.get(f"alsi:{area}", 3 * 3600, fetch)
