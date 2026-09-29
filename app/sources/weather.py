"""Open-Meteo weather for gas demand: demand-weighted basket temperatures, HDD and normals.

Calls per refresh (multi-location, one request each):
  det   : ECMWF IFS 0.25 + GFS deterministic, 14 past days + 16 forecast days
  ensE  : ECMWF ENS (51 members), 15 days
  ensG  : GEFS 0.5 (31 members), 35 days
  wind  : ECMWF + GFS hourly 100 m wind at N-German/Dutch points (gas-for-power proxy)
Normals come from data/normals.json (station-based, built offline), so no archive calls at runtime.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
from typing import Iterable

from ..cache import cache
from ..config import ASIA_BASKET, EU_BASKET, WIND_POINTS, hdd
from ..http import get_json

KEY = os.getenv("OPEN_METEO_API_KEY", "").strip()
FORECAST = "https://customer-api.open-meteo.com/v1/forecast" if KEY else "https://api.open-meteo.com/v1/forecast"
ENSEMBLE = ("https://customer-ensemble-api.open-meteo.com/v1/ensemble" if KEY
            else "https://ensemble-api.open-meteo.com/v1/ensemble")
NORMALS_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "normals.json"
BASKETS = {"eu": EU_BASKET, "asia": ASIA_BASKET}
WINDOWS = [("T+0–4", 0, 4), ("T+5–9", 5, 9), ("T+10–14", 10, 14), ("T+15–34", 15, 34)]
_normals: dict | None = None


def normals() -> dict:
    global _normals
    if _normals is None:
        _normals = json.loads(NORMALS_PATH.read_text(encoding="utf-8"))
    return _normals


def doy365(d: dt.date) -> int:
    if d.month == 2 and d.day == 29:
        d = d.replace(day=28)
    return (d.replace(year=2001) - dt.date(2001, 1, 1)).days


def locations(basket: dict) -> list[dict]:
    """Flatten a basket into locations with total weights summing to 1."""
    tot = sum(c["weight"] for c in basket.values())
    out = []
    for code, c in basket.items():
        for name, lat, lon, share in c["cities"]:
            out.append({"country": code, "city": name, "lat": lat, "lon": lon,
                        "w": c["weight"] / tot * share, "wc": share})
    return out


def all_locations() -> list[dict]:
    return [dict(loc, basket=b) for b, bk in BASKETS.items() for loc in locations(bk)]


def _params(locs: list[dict]) -> dict:
    return {"latitude": ",".join(f"{x['lat']:.2f}" for x in locs),
            "longitude": ",".join(f"{x['lon']:.2f}" for x in locs), "timezone": "auto"}


def _as_list(resp) -> list[dict]:
    if isinstance(resp, dict):
        if resp.get("error"):
            raise ValueError(resp.get("reason") or "Open-Meteo error")
        return [resp]
    return resp


def series_by_key(loc_resp: dict, key: str) -> dict[str, float]:
    daily = loc_resp.get("daily") or {}
    return {t: v for t, v in zip(daily.get("time") or [], daily.get(key) or []) if v is not None}


def member_keys(loc_resp: dict, var: str = "temperature_2m_mean") -> list[str]:
    return [k for k in (loc_resp.get("daily") or {}) if k == var or k.startswith(var + "_member")]


def wavg(values: Iterable[tuple[float | None, float]]) -> float | None:
    num = den = 0.0
    for v, w in values:
        if v is None:
            continue
        num += v * w
        den += w
    return num / den if den > 0 else None


def pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def ensemble_stats(resps: list[dict], idx: list[int], weights: list[float]) -> dict[str, dict]:
    """Per date: weighted-basket value for every member -> mean / p10 / p50 / p90, plus mean HDD."""
    if not idx:
        return {}
    keys = member_keys(resps[idx[0]])
    per_loc = [{k: series_by_key(resps[i], k) for k in keys} for i in idx]
    dates = sorted({d for loc in per_loc for s in loc.values() for d in s})
    out = {}
    for d in dates:
        temps, hdds = [], []
        for k in keys:
            t = wavg((loc[k].get(d), w) for loc, w in zip(per_loc, weights))
            h = wavg((hdd(loc[k].get(d)), w) for loc, w in zip(per_loc, weights))
            if t is not None:
                temps.append(t)
            if h is not None:
                hdds.append(h)
        if len(temps) < max(3, len(keys) // 2):
            continue
        temps.sort()
        out[d] = {"mean": sum(temps) / len(temps), "p10": pct(temps, 0.1), "p50": pct(temps, 0.5),
                  "p90": pct(temps, 0.9), "hdd": sum(hdds) / len(hdds) if hdds else None, "n": len(temps)}
    return out


def ensemble_mean_by_date(loc_resp: dict) -> dict[str, float]:
    keys = member_keys(loc_resp)
    per = [series_by_key(loc_resp, k) for k in keys]
    out = {}
    for d in sorted(set().union(*per)) if per else []:
        vals = [s[d] for s in per if d in s]
        if len(vals) >= max(3, len(keys) // 2):
            out[d] = sum(vals) / len(vals)
    return out


def _shift(loc_resp: dict, keys: list[str], off: float) -> dict:
    daily = dict(loc_resp.get("daily") or {})
    for k in keys:
        daily[k] = [None if v is None else round(v - off, 3) for v in daily.get(k) or []]
    return dict(loc_resp, daily=daily)


CAL_WINDOW_DAYS = 5   # GEFS vs ECMWF-ENS offset: mean over the first 5 forecast days
CAL_PAST_DAYS = 14    # GFS det vs ECMWF det offset: mean over the look-back days


def calibrate(det: list[dict] | None, ens_e: list[dict] | None, ens_g: list[dict] | None,
              today: dt.date) -> tuple[list[dict] | None, list[dict] | None, list[dict]]:
    """Remove per-city systematic offsets of GFS/GEFS against ECMWF.

    GEFS (0.5 deg) and GFS carry grid/elevation biases of up to +-2 C at single cities, which would
    otherwise show up as fake anomalies against station normals (checked against DWD station
    observations: ECMWF short-range is within +-0.2 C at most German cities, GFS up to +1.7 C).
    Offsets: GEFS mean - ECMWF-ENS mean over T+0..4; GFS det - ECMWF det over the look-back days.
    """
    offsets = []
    locs = all_locations()
    t0 = today.isoformat()
    ens_g2 = list(ens_g) if ens_g else ens_g
    det2 = list(det) if det else det
    for i, loc in enumerate(locs):
        rec = {"city": loc["city"], "gefs": None, "gfs": None}
        if ens_e and ens_g and i < len(ens_e) and i < len(ens_g):
            me, mg = ensemble_mean_by_date(ens_e[i]), ensemble_mean_by_date(ens_g[i])
            days = [d for d in sorted(me) if d >= t0 and d in mg][:CAL_WINDOW_DAYS]
            if len(days) >= 3:
                off = sum(mg[d] - me[d] for d in days) / len(days)
                ens_g2[i] = _shift(ens_g[i], member_keys(ens_g[i]), off)
                rec["gefs"] = round(off, 2)
        if det and i < len(det):
            e = series_by_key(det[i], "temperature_2m_mean_ecmwf_ifs025")
            g = series_by_key(det[i], "temperature_2m_mean_gfs_seamless")
            days = [d for d in sorted(e) if d < t0 and d in g][-CAL_PAST_DAYS:]
            if len(days) >= 5:
                off = sum(g[d] - e[d] for d in days) / len(days)
                det2[i] = _shift(det[i], ["temperature_2m_mean_gfs_seamless"], off)
                rec["gfs"] = round(off, 2)
        offsets.append(rec)
    return det2, ens_g2, offsets


def normal_for(city: str, d: dt.date, which: str, var: str = "t") -> float | None:
    c = normals()["cities"].get(city)
    return c[f"{var}_{which}"][doy365(d)] if c else None


def basket_normal(locs: list[dict], d: dt.date, which: str, var: str = "t", key: str = "w") -> float | None:
    return wavg((normal_for(x["city"], d, which, var), x[key]) for x in locs)


def build_basket(name: str, det: list[dict] | None, ens_e: list[dict] | None, ens_g: list[dict] | None,
                 today: dt.date) -> dict:
    basket = BASKETS[name]
    allloc = all_locations()
    idx = [i for i, x in enumerate(allloc) if x["basket"] == name]
    locs = [allloc[i] for i in idx]
    w = [x["w"] for x in locs]

    def det_series(key: str) -> dict[str, float]:
        if not det:
            return {}
        per = [series_by_key(det[i], key) for i in idx]
        dates = sorted({d for s in per for d in s})
        return {d: wavg((s.get(d), wi) for s, wi in zip(per, w)) for d in dates}

    de, dg = det_series("temperature_2m_mean_ecmwf_ifs025"), det_series("temperature_2m_mean_gfs_seamless")
    se = ensemble_stats(ens_e, idx, w) if ens_e else {}
    sg = ensemble_stats(ens_g, idx, w) if ens_g else {}
    dates = sorted(set(de) | set(dg) | set(se) | set(sg))
    D = [dt.date.fromisoformat(x) for x in dates]

    def col(src: dict, field: str | None = None) -> list:
        vals = []
        for d in dates:
            v = src.get(d)
            vals.append(None if v is None else round(v[field] if field else v, 2))
        return vals

    normal = {k: [round(basket_normal(locs, d, k), 2) for d in D] for k in ("n30", "n10")}
    normal_hdd = {k: [round(basket_normal(locs, d, k, "hdd"), 2) for d in D] for k in ("n30", "n10")}
    best = [se.get(d, sg.get(d, {})).get("mean") if (d in se or d in sg) else None for d in dates]
    best_hdd = [se.get(d, sg.get(d, {})).get("hdd") if (d in se or d in sg) else None for d in dates]

    # Country windows from the blended ensemble mean (ECMWF ENS to T+14, GEFS beyond).
    countries = []
    for code, c in basket.items():
        ci = [i for i in idx if allloc[i]["country"] == code]
        cw = [allloc[i]["wc"] for i in ci]
        cse = ensemble_stats(ens_e, ci, cw) if ens_e else {}
        csg = ensemble_stats(ens_g, ci, cw) if ens_g else {}
        clocs = [allloc[i] for i in ci]
        wins = []
        for label, a, b in WINDOWS:
            ts, hs, n30, n10, h30, h10 = [], [], [], [], [], []
            for off in range(a, b + 1):
                d = today + dt.timedelta(days=off)
                v = cse.get(d.isoformat()) or csg.get(d.isoformat())
                if not v:
                    continue
                ts.append(v["mean"])
                hs.append(v["hdd"] or 0)
                n30.append(basket_normal(clocs, d, "n30", key="wc"))
                n10.append(basket_normal(clocs, d, "n10", key="wc"))
                h30.append(basket_normal(clocs, d, "n30", "hdd", key="wc"))
                h10.append(basket_normal(clocs, d, "n10", "hdd", key="wc"))
            if ts:
                m = sum(ts) / len(ts)
                wins.append({"label": label, "days": len(ts), "t": m,
                             "anom_n30": m - sum(n30) / len(n30), "anom_n10": m - sum(n10) / len(n10),
                             "hdd": sum(hs), "hdd_anom_n30": sum(hs) - sum(h30), "hdd_anom_n10": sum(hs) - sum(h10)})
            else:
                wins.append({"label": label, "days": 0})
        countries.append({"code": code, "name": c["name"], "weight": c["weight"], "windows": wins})

    def window_kpi(a: int, b: int) -> dict:
        sel = [i for i, d in enumerate(D) if a <= (d - today).days <= b and best[i] is not None]
        if not sel:
            return {}
        m = sum(best[i] for i in sel) / len(sel)
        h = sum(best_hdd[i] or 0 for i in sel)
        return {"days": len(sel), "t": m,
                "anom_n30": m - sum(normal["n30"][i] for i in sel) / len(sel),
                "anom_n10": m - sum(normal["n10"][i] for i in sel) / len(sel),
                "hdd": h, "hdd_anom_n30": h - sum(normal_hdd["n30"][i] for i in sel),
                "hdd_anom_n10": h - sum(normal_hdd["n10"][i] for i in sel)}

    # ECMWF vs GFS deterministic disagreement over T+5..14 (a quick model-risk gauge).
    diff = [de[d] - dg[d] for d in dates if d in de and d in dg and 5 <= (dt.date.fromisoformat(d) - today).days <= 14]

    return {
        "dates": dates, "today": today.isoformat(),
        "normal": normal, "normal_hdd": normal_hdd,
        "det_ecmwf": col(de), "det_gfs": col(dg),
        "ens_ecmwf": {f: col(se, f) for f in ("mean", "p10", "p90", "hdd")},
        "ens_gfs": {f: col(sg, f) for f in ("mean", "p10", "p90", "hdd")},
        "best": [None if v is None else round(v, 2) for v in best],
        "best_hdd": [None if v is None else round(v, 2) for v in best_hdd],
        "countries": countries,
        "kpi": {"w0_14": window_kpi(0, 14), "w0_4": window_kpi(0, 4), "w5_14": window_kpi(5, 14),
                "w15_34": window_kpi(15, 34),
                "ecmwf_minus_gfs_5_14": sum(diff) / len(diff) if diff else None},
        "weights": [{"country": x["country"], "city": x["city"], "w": round(x["w"], 4)} for x in locs],
    }


def build_wind(resp: list[dict] | None) -> dict:
    if not resp:
        return {}
    out = {}
    for model in ("ecmwf_ifs025", "gfs_seamless"):
        key = f"wind_speed_100m_{model}"
        acc: dict[str, list[float]] = {}
        for loc in resp:
            h = loc.get("hourly") or {}
            for t, v in zip(h.get("time") or [], h.get(key) or []):
                if v is not None:
                    acc.setdefault(t[:10], []).append(v)
        # keep days with (almost) full hourly coverage across all points
        need = 6 * len(resp)  # tolerate 3-/6-hourly steps at longer lead times
        out[model] = {d: round(sum(v) / len(v), 2) for d, v in sorted(acc.items()) if len(v) >= need}
    dates = sorted(set(out["ecmwf_ifs025"]) | set(out["gfs_seamless"]))
    return {"dates": dates, "ecmwf": [out["ecmwf_ifs025"].get(d) for d in dates],
            "gfs": [out["gfs_seamless"].get(d) for d in dates], "unit": "m/s",
            "points": [p[0] for p in WIND_POINTS]}


# ------------------------------------------------------------------ fetchers
async def _fetch_det():
    locs = all_locations()
    p = _params(locs) | {"daily": "temperature_2m_mean", "models": "ecmwf_ifs025,gfs_seamless",
                         "past_days": 14, "forecast_days": 16}
    if KEY:
        p["apikey"] = KEY
    return _as_list(await get_json(FORECAST, params=p, timeout=60, retries=1))


async def _fetch_ens(model: str, days: int):
    locs = all_locations()
    p = _params(locs) | {"daily": "temperature_2m_mean", "models": model, "forecast_days": days}
    if KEY:
        p["apikey"] = KEY
    return _as_list(await get_json(ENSEMBLE, params=p, timeout=90, retries=1))


async def _fetch_wind():
    p = {"latitude": ",".join(f"{x[1]:.2f}" for x in WIND_POINTS),
         "longitude": ",".join(f"{x[2]:.2f}" for x in WIND_POINTS), "timezone": "Europe/Berlin",
         "hourly": "wind_speed_100m", "models": "ecmwf_ifs025,gfs_seamless", "forecast_days": 16,
         "wind_speed_unit": "ms"}
    if KEY:
        p["apikey"] = KEY
    return _as_list(await get_json(FORECAST, params=p, timeout=60, retries=1))


async def raw_det():
    return await cache.get("om:det", 60 * 60, _fetch_det)


async def raw_ens_ecmwf():
    return await cache.get("om:ens:ecmwf", 2 * 3600, lambda: _fetch_ens("ecmwf_ifs025", 15))


async def raw_ens_gfs():
    return await cache.get("om:ens:gfs", 2 * 3600, lambda: _fetch_ens("gfs05", 35))


async def raw_wind():
    return await cache.get("om:wind", 2 * 3600, _fetch_wind)
