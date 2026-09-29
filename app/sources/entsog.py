"""ENTSOG Transparency Platform: physical pipeline imports (Norway in detail + other EU import routes).

Norway: receiving TSOs publish the entry side of each Gassco landing point. At shared points
(Dornum, Emden EPT1) several TSOs publish the *same* physical flow, so identical same-day values
within one terminal are counted once.
Other routes (config.PIPE_ROUTES): one entry-side publication per point, UK->Continent netted.
All values GWh/d (GCV); mcm/d with ENTSOG's 10.57275 GWh/mcm.
"""
from __future__ import annotations

import collections
import datetime as dt

from ..cache import cache
from ..config import GWH_PER_MCM, NORWAY_POINTS, PIPE_ROUTES
from ..http import get_json

URL = "https://transparency.entsog.eu/api/v1/operationaldatas"


def point_direction(r: dict) -> str | None:
    if not (r.get("pointKey") and r.get("operatorKey") and r.get("directionKey")):
        return None
    return f"{r['operatorKey'].lower()}{r['pointKey'].lower()}{r['directionKey'].lower()}"


def dedupe_sum(values: list[float], rel_tol: float = 0.005) -> float:
    """Sum of distinct values; values within rel_tol of an already counted one are duplicates."""
    kept: list[float] = []
    for v in sorted(values, reverse=True):
        if any(abs(v - k) <= rel_tol * max(abs(k), 1e-9) for k in kept):
            continue
        kept.append(v)
    return sum(kept)


def aggregate(rows: list[dict]) -> dict:
    """Norwegian rows -> per-terminal daily GWh/d with duplicate removal."""
    by = collections.defaultdict(lambda: collections.defaultdict(list))  # day -> terminal -> [GWh]
    reporting = collections.Counter()
    for r in rows:
        meta = NORWAY_POINTS.get(point_direction(r) or "")
        day = (r.get("periodFrom") or "")[:10]
        v = r.get("value")
        if not meta or not day or v is None:
            continue
        by[day][meta["terminal"]].append(float(v) / 1e6)  # kWh/d -> GWh/d
        if float(v) > 0:
            reporting[day] += 1
    days = sorted(by)
    terminals = sorted({t for d in days for t in by[d]})
    series = {t: [round(dedupe_sum(by[d][t]), 1) if by[d].get(t) else None for d in days] for t in terminals}
    total = [round(sum(series[t][i] or 0 for t in terminals), 1) for i in range(len(days))]
    # A day counts as complete once at least 80 % of the usual number of non-zero publications exist.
    typical = sorted(reporting.values())[len(reporting) // 2] if reporting else 0
    complete = [d for d in days if typical and reporting[d] >= 0.8 * typical]
    through = complete[-1] if complete else None
    return {"days": days, "terminals": series, "total": total, "complete_through": through}


def _avg(xs: list[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def kpis(agg: dict, ly_total_avg30: float | None = None) -> dict:
    days, total, through = agg["days"], agg["total"], agg["complete_through"]
    if not through:
        return {}
    idx = days.index(through)
    hist = total[: idx + 1]
    d1 = hist[-1]
    a7, a30 = _avg(hist[-7:]), _avg(hist[-30:])
    last30 = hist[-30:]
    return {
        "day": through, "d1": d1, "avg7": a7, "avg30": a30,
        "min30": min(last30) if last30 else None, "max30": max(last30) if last30 else None,
        "d1_vs_30": (d1 - a30) if a30 else None, "avg7_vs_30": (a7 - a30) if (a7 and a30) else None,
        "d1_mcm": d1 / GWH_PER_MCM, "avg30_mcm": a30 / GWH_PER_MCM if a30 else None,
        "avg30_ly": ly_total_avg30,
        "avg30_vs_ly_pct": ((a30 / ly_total_avg30 - 1) * 100) if (a30 and ly_total_avg30) else None,
        "by_terminal_d1": {t: v[idx] for t, v in agg["terminals"].items()},
    }


def route_series(rows: list[dict]) -> dict[str, dict[str, float]]:
    """rows -> {route_id: {day: GWh/d}}; a day is only valid when every leg of the route reported."""
    vals: dict[str, dict[str, float]] = collections.defaultdict(dict)  # point -> day -> GWh
    for r in rows:
        pd, day, v = point_direction(r), (r.get("periodFrom") or "")[:10], r.get("value")
        if pd and day and v is not None:
            vals[pd][day] = float(v) / 1e6
    out: dict[str, dict[str, float]] = {}
    for rid, spec in PIPE_ROUTES.items():
        legs = [(p, 1) for p in spec["plus"]] + [(p, -1) for p in spec.get("minus", [])]
        days = set.intersection(*[set(vals.get(p, {})) for p, _ in legs]) if legs else set()
        out[rid] = {d: round(sum(sign * vals[p][d] for p, sign in legs), 1) for d in sorted(days)}
    return out


def route_kpis(series: dict[str, float], ly: dict[str, float], through: str | None) -> dict:
    if not series:
        return {}
    ref = through or max(series)
    past = [d for d in sorted(series) if d <= ref]
    if not past:
        return {}
    last = past[-1]
    s = [series[d] for d in past]
    a7, a30 = _avg(s[-7:]), _avg(s[-30:])
    ly_ref = (dt.date.fromisoformat(last) - dt.timedelta(days=364))
    ly_win = [ly.get((ly_ref - dt.timedelta(days=k)).isoformat()) for k in range(30)]
    a30_ly = _avg(ly_win) if sum(v is not None for v in ly_win) >= 20 else None
    return {"day": last, "d1": series[last], "avg7": a7, "avg30": a30, "avg30_ly": a30_ly,
            "d1_mcm": series[last] / GWH_PER_MCM,
            "avg30_vs_ly": (a30 - a30_ly) if (a30 is not None and a30_ly is not None) else None}


async def _fetch(points: list[str], start: dt.date, end: dt.date) -> list[dict]:
    data = await get_json(URL, params={
        "pointDirection": ",".join(points), "indicator": "Physical Flow", "periodType": "day",
        "from": start.isoformat(), "to": end.isoformat(), "timezone": "CET", "limit": -1,
    }, timeout=120)
    rows = data.get("operationaldatas") or []
    if not rows:
        raise ValueError(data.get("message") or "ENTSOG: keine Daten")
    return rows


def _route_points() -> list[str]:
    return sorted({p for r in PIPE_ROUTES.values() for p in r["plus"] + r.get("minus", [])})


async def supply(days: int = 120) -> tuple[dict, dict]:
    async def fetch():
        end = dt.date.today() + dt.timedelta(days=1)
        start = end - dt.timedelta(days=days)
        pts = list(NORWAY_POINTS) + _route_points()
        rows = await _fetch(pts, start, end)
        ly_end = end - dt.timedelta(days=364)
        ly_rows = await _fetch(pts, ly_end - dt.timedelta(days=45), ly_end)
        agg = aggregate(rows)
        ly_agg = aggregate(ly_rows)
        through = agg["complete_through"]
        ly_avg = None
        if through:
            ly_ref = dt.date.fromisoformat(through) - dt.timedelta(days=364)
            win = {(ly_ref - dt.timedelta(days=k)).isoformat() for k in range(30)}
            vals = [v for d, v in zip(ly_agg["days"], ly_agg["total"]) if d in win]
            ly_avg = sum(vals) / len(vals) if len(vals) >= 20 else None
        agg["kpi"] = kpis(agg, ly_avg)
        rs, rs_ly = route_series(rows), route_series(ly_rows)
        agg["routes"] = [{"id": rid, "name": spec["name"], **route_kpis(rs[rid], rs_ly[rid], through)}
                         for rid, spec in PIPE_ROUTES.items()]
        agg["route_series"] = {rid: [[d, v] for d, v in sorted(s.items())] for rid, s in rs.items()}
        return agg

    return await cache.get("entsog:supply", 2 * 3600, fetch)
