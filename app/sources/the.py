"""Trading Hub Europe (THE), German market area - free public data services.

Consumption: aggregated end-user allocations per gas day in kWh (SLP = standard load profile,
households/commerce; RLM = interval-metered, industry and gas-fired power plants). SLP for the current
and next gas day are day-ahead allocations (temperature-forecast based); RLM arrives with ~1 day lag.

Prices: the THE balancing prices and the "Gastagesindex" - the reference price for balancing energy
(volume-weighted EEX spot price for the gas day). Preliminary values exist for D and D+1.
"""
from __future__ import annotations

import datetime as dt

from ..cache import cache
from ..http import get_json

CONSUMPTION = "https://datenservice-api.tradinghub.eu/api/evoq/GetAggregierteVerbrauchsdatenTabelle"
PRICES_FINAL = "https://api.tradinghub.eu/api/website/evoq/GetAusgleichsenergieFinalTabelle"
PRICES_PRELIM = "https://api.tradinghub.eu/api/website/evoq/GetAusgleichsenergieVorlaeufigTabelle"

SLP_KEYS = ("slPsyn_H_Gas", "slPana_H_Gas", "slPsyn_L_Gas", "slPana_L_Gas")
RLM_KEYS = ("rlMmT_H_Gas", "rlMmT_L_Gas", "rlMoT_H_Gas", "rlMoT_L_Gas")


def _us(d: dt.date) -> str:
    return d.strftime("%m-%d-%Y")


def _sum_gwh(row: dict, keys: tuple[str, ...]) -> float | None:
    vals = [row.get(k) for k in keys]
    if any(v is None for v in vals):
        return None
    return sum(vals) / 1e6  # kWh -> GWh


def parse_consumption(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for r in rows:
        day = (r.get("gastag") or "")[:10]
        if not day:
            continue
        slp, rlm = _sum_gwh(r, SLP_KEYS), _sum_gwh(r, RLM_KEYS)
        out[day] = {"slp": slp, "rlm": rlm, "total": (slp + rlm) if (slp is not None and rlm is not None) else None,
                    "status": r.get("statusDE")}
    return out


def _avg(xs):
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def consumption_summary(days: dict[str, dict], today: dt.date) -> dict:
    if not days:
        return {}
    keys = sorted(days)

    def val(d: dt.date, f: str):
        x = days.get(d.isoformat())
        return x.get(f) if x else None

    def window(end: dt.date, n: int, f: str):
        return _avg([val(end - dt.timedelta(days=k), f) for k in range(n)])

    last_rlm = max((k for k in keys if days[k]["rlm"] is not None), default=None)
    last_slp = max((k for k in keys if days[k]["slp"] is not None), default=None)
    ref = dt.date.fromisoformat(last_rlm) if last_rlm else today
    ly = ref - dt.timedelta(days=364)
    kpi = {
        "rlm_day": last_rlm, "slp_last_day": last_slp,
        "slp_today": val(today, "slp"), "slp_tomorrow": val(today + dt.timedelta(days=1), "slp"),
        "slp_today_ly": val(today - dt.timedelta(days=364), "slp"),
        "rlm_d1": val(ref, "rlm"), "total_d1": val(ref, "total"),
    }
    for f in ("slp", "rlm", "total"):
        a, b = window(ref, 30, f), window(ly, 30, f)
        kpi[f"{f}_30"], kpi[f"{f}_30_ly"] = a, b
        kpi[f"{f}_30_vs_ly_pct"] = (a / b - 1) * 100 if (a and b) else None
    # year-to-date on complete days
    y0 = dt.date(ref.year, 1, 1)
    ytd = [val(y0 + dt.timedelta(days=k), "total") for k in range((ref - y0).days + 1)]
    ytd_ly = [val(y0 - dt.timedelta(days=364) + dt.timedelta(days=k), "total") for k in range((ref - y0).days + 1)]
    if ytd and all(v is not None for v in ytd) and all(v is not None for v in ytd_ly):
        kpi["total_ytd_twh"], kpi["total_ytd_ly_twh"] = sum(ytd) / 1000, sum(ytd_ly) / 1000
        kpi["total_ytd_vs_ly_pct"] = (sum(ytd) / sum(ytd_ly) - 1) * 100

    # chart: last 365 days + same weekday-aligned days of up to 4 prior years
    start = today - dt.timedelta(days=365)
    dates = [start + dt.timedelta(days=k) for k in range((today + dt.timedelta(days=1) - start).days + 1)]
    series = {}
    for f in ("slp", "rlm", "total"):
        cur = [val(d, f) for d in dates]
        prior = [[val(d - dt.timedelta(days=364 * y), f) for d in dates] for y in (1, 2, 3, 4)]
        band = [[p[i] for p in prior if p[i] is not None] for i in range(len(dates))]
        series[f] = {
            "current": cur, "last_year": prior[0],
            "min": [min(b) if len(b) >= 2 else None for b in band],
            "max": [max(b) if len(b) >= 2 else None for b in band],
        }
    first_year = keys[0][:4]
    return {"kpi": kpi, "dates": [d.isoformat() for d in dates], "series": series,
            "band_label": f"Spanne Vorjahre ab {first_year}"}


def parse_prices(final: list[dict], prelim: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for status, rows in (("endgültig", final), ("vorläufig", prelim)):
        for r in rows or []:
            day = (r.get("gastag") or "")[:10]
            if not day or r.get("gastagesindex") is None:
                continue
            if status == "vorläufig" and day in out:
                continue  # final wins
            out[day] = {"d": day, "index": r.get("gastagesindex"), "pos": r.get("preisAusgleichsenergiePositiv"),
                        "neg": r.get("preisAusgleichsenergieNegativ"), "status": status}
    return [out[k] for k in sorted(out)]


async def consumption() -> tuple[dict, dict]:
    async def fetch():
        today = dt.date.today()
        rows = await get_json(CONSUMPTION, params={
            "DatumStart": _us(dt.date(today.year - 4, 1, 1)), "DatumEnde": _us(today + dt.timedelta(days=2)),
            "GasXType_Id": "all"}, timeout=60)
        if not isinstance(rows, list) or not rows:
            raise ValueError("THE Verbrauchsdaten: keine Daten")
        return parse_consumption(rows)

    return await cache.get("the:consumption", 3600, fetch)


async def prices() -> tuple[list[dict], dict]:
    async def fetch():
        today = dt.date.today()
        final = await get_json(PRICES_FINAL, params={"DatumStart": _us(today - dt.timedelta(days=400)),
                                                     "DatumEnde": _us(today)}, timeout=45)
        prelim = await get_json(PRICES_PRELIM, timeout=45)
        out = parse_prices(final if isinstance(final, list) else [], prelim if isinstance(prelim, list) else [])
        if not out:
            raise ValueError("THE Preise: keine Daten")
        return out

    return await cache.get("the:prices", 30 * 60, fetch)
