"""ICE delayed futures via the public product-guide charting endpoints.

contract-data : all listed strips with last trade, % change vs. prior settlement, volume.
historical    : daily settlement bars per marketId (one call per contract, span=2 -> ~1 year).

This is a public web endpoint, not a licensed feed: quotes are delayed, and ICE can change or
block it. Everything downstream degrades gracefully if it disappears.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

from ..cache import cache
from ..config import ICE_HISTORY_SCOPE, ICE_PRODUCTS, TZ
from ..contracts import months_ahead, parse_strip
from ..http import get_json

BASE = "https://www.ice.com/marketdata/api/productguide/charting"
BERLIN = ZoneInfo(TZ)
_sem = asyncio.Semaphore(5)


def today_berlin() -> dt.date:
    return dt.datetime.now(BERLIN).date()


def _history_ttl() -> float:
    """Settlements land ~17:30-19:30 CET; poll faster in that window."""
    now = dt.datetime.now(BERLIN)
    if now.weekday() < 5 and (17 <= now.hour < 21):
        return 20 * 60
    return 3 * 3600


def _parse_last_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.strptime(s.replace(" GMT", ""), "%m/%d/%Y %I:%M %p").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _parse_bar_date(s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(s, "%a %b %d %H:%M:%S %Y").date()
    except ValueError:
        return None


async def contract_data(sym: str) -> tuple[list[dict], dict]:
    p = ICE_PRODUCTS[sym]
    url = f"{BASE}/contract-data?productId={p['product']}&hubId={p['hub']}"

    async def fetch():
        async with _sem:
            data = await get_json(url, headers={"Referer": f"https://www.ice.com/products/{p['page']}"})
        if not isinstance(data, list):
            raise ValueError("unexpected ICE contract-data payload")
        return data

    return await cache.get(f"ice:cd:{sym}", 5 * 60, fetch)


async def history(market_id: int) -> list[list]:
    """[[iso_date, settle], ...] ascending. Empty list on failure (never raises)."""
    url = f"{BASE}/data/historical?marketId={market_id}&historicalSpan=2"

    async def fetch():
        async with _sem:
            data = await get_json(url, headers={"Referer": "https://www.ice.com/"})
        out = []
        for d, v in (data or {}).get("bars") or []:
            day = _parse_bar_date(d)
            if day is not None and v is not None:
                out.append([day.isoformat(), float(v)])
        out.sort()
        return out

    try:
        bars, _ = await cache.get(f"ice:h:{market_id}", _history_ttl(), fetch)
        return bars
    except Exception:  # noqa: BLE001
        return []


def _value_on_or_before(bars: list[list], day: dt.date) -> float | None:
    iso = day.isoformat()
    val = None
    for d, v in bars:
        if d <= iso:
            val = v
        else:
            break
    return val


def build_row(raw: dict, strip, bars: list[list], today: dt.date) -> dict:
    last = raw.get("lastPrice")
    last = float(last) if last is not None else None
    lt = _parse_last_time(raw.get("lastTime"))
    traded_today = bool(lt and last is not None and lt.astimezone(BERLIN).date() == today)
    pct = raw.get("change") or 0.0

    settle = settle_date = prev = None
    if bars:
        settle, settle_date = bars[-1][1], bars[-1][0]
        if settle_date == today.isoformat():
            prev = bars[-2][1] if len(bars) > 1 else None
        else:
            prev = settle
    elif traded_today and pct:
        prev = last / (1 + pct / 100.0)

    if settle_date == today.isoformat():
        mark, src = settle, "settle"
    elif traded_today:
        mark, src = last, "last"
    elif prev is not None:
        mark, src = prev, "settle_prev"
    else:
        mark, src = None, None

    def delta(days: int) -> float | None:
        if mark is None or not bars:
            return None
        ref = _value_on_or_before(bars, today - dt.timedelta(days=days))
        return None if ref is None else mark - ref

    return {
        "key": strip.key, "label": strip.label_de, "ice": strip.label, "kind": strip.kind,
        "start": strip.start.isoformat(), "end": strip.end.isoformat(),
        "market_id": raw.get("marketId"),
        "mark": mark, "src": src,
        "last": last, "last_time": lt.isoformat() if lt else None, "traded_today": traded_today,
        "settle": settle, "settle_date": settle_date, "prev_settle": prev,
        "chg_d": (mark - prev) if (mark is not None and prev is not None and src != "settle_prev") else None,
        "chg_1w": delta(7), "chg_1m": delta(30), "chg_3m": delta(91),
        "volume": raw.get("volume"),
        "_bars": bars,
    }


async def curve(sym: str) -> tuple[list[dict], dict]:
    """Full curve for one product with settlement history for in-scope strips."""
    raw, meta = await contract_data(sym)
    today = today_berlin()
    scope = ICE_HISTORY_SCOPE.get(sym, {})
    parsed = []
    for r in raw:
        s = parse_strip(r.get("marketStrip", ""))
        if s is None or s.end < today:
            continue
        horizon = scope.get(s.kind, -1)
        need = bool(r.get("marketId")) and horizon >= 0 and months_ahead(s, today) <= horizon
        parsed.append((r, s, need))
    hist = await asyncio.gather(*[history(r["marketId"]) if need else _empty() for r, s, need in parsed])
    rows = [build_row(r, s, h, today) for (r, s, _), h in zip(parsed, hist)]
    rows.sort(key=lambda x: (x["start"], x["end"]))
    return rows, meta


async def _empty() -> list:
    return []
