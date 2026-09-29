"""Storage (AGSI) and LNG send-out (ALSI) analytics."""
from __future__ import annotations

import datetime as dt


def doy365(d: dt.date) -> int:
    if d.month == 2 and d.day == 29:
        d = d.replace(day=28)
    return (d.replace(year=2001) - dt.date(2001, 1, 1)).days


def _avg(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def storage_summary(rows: list[dict], target: float, today: dt.date) -> dict:
    rows = [r for r in rows if r.get("full") is not None]
    if not rows:
        return {}
    last = rows[-1]
    ld = dt.date.fromisoformat(last["d"])
    year = ld.year
    by_year: dict[int, list[float | None]] = {}
    for r in rows:
        d = dt.date.fromisoformat(r["d"])
        by_year.setdefault(d.year, [None] * 365)[doy365(d)] = r["full"]
    band_years = [y for y in range(year - 5, year) if y in by_year]
    band = {"min": [], "max": [], "avg": []}
    for i in range(365):
        vals = [by_year[y][i] for y in band_years if by_year[y][i] is not None]
        band["min"].append(round(min(vals), 2) if len(vals) >= 3 else None)
        band["max"].append(round(max(vals), 2) if len(vals) >= 3 else None)
        band["avg"].append(round(sum(vals) / len(vals), 2) if len(vals) >= 3 else None)

    i = doy365(ld)
    net = [(r["inj"] or 0) - (r["wd"] or 0) if (r.get("inj") is not None or r.get("wd") is not None) else None for r in rows]
    net7, net30 = _avg(net[-7:]), _avg(net[-30:])
    wgv, gis, full = last.get("wgv"), last.get("gis"), last["full"]
    avg5 = band["avg"][i]
    ly = by_year.get(year - 1, [None] * 365)[i]

    targets = []
    for label, tdate in (("1. Nov", dt.date(year, 11, 1)), ("1. Dez", dt.date(year, 12, 1))):
        days = (tdate - ld).days
        if days <= 0 or not wgv or gis is None:
            continue
        need_gwh = (target / 100 * wgv - gis) * 1000
        req = need_gwh / days
        proj_pace = min(100.0, (gis + (net7 or 0) * days / 1000) / wgv * 100) if net7 is not None else None
        a_now, a_t = band["avg"][i], band["avg"][doy365(tdate)]
        proj_5y = min(100.0, full + (a_t - a_now)) if (a_now is not None and a_t is not None) else None
        targets.append({"label": label, "date": tdate.isoformat(), "days": days, "required_gwh_d": req,
                        "projected_at_7d_pace": proj_pace, "projected_at_5y_pace": proj_5y})

    return {
        "date": last["d"], "status": last.get("status"), "full": full, "gis_twh": gis, "wgv_twh": wgv,
        "vs5y_pp": (full - avg5) if avg5 is not None else None,
        "vs5y_twh": ((full - avg5) / 100 * wgv) if (avg5 is not None and wgv) else None,
        "vsly_pp": (full - ly) if ly is not None else None,
        "net_d1": net[-1], "net7": net7, "net30": net30,
        "inj_d1": last.get("inj"), "wd_d1": last.get("wd"),
        "targets": targets, "target": target,
        "seasonal": {"year": year, "current": by_year.get(year), "last_year": by_year.get(year - 1),
                     "band": band, "band_years": band_years},
        "net_series": [[r["d"], n] for r, n in zip(rows[-400:], net[-400:])],
    }


def lng_summary(rows: list[dict], today: dt.date) -> dict:
    rows = [r for r in rows if r.get("sendout") is not None]
    if not rows:
        return {}
    last = rows[-1]
    ld = dt.date.fromisoformat(last["d"])
    m = {r["d"]: r["sendout"] for r in rows}

    def window(end: dt.date, n: int) -> float | None:
        return _avg([m.get((end - dt.timedelta(days=k)).isoformat()) for k in range(n)])

    ly_end = ld - dt.timedelta(days=364)  # same weekday last year
    a30, a30ly = window(ld, 30), window(ly_end, 30)
    series = [[r["d"], r["sendout"]] for r in rows if dt.date.fromisoformat(r["d"]) > ld - dt.timedelta(days=365)]
    ly_series = [[(dt.date.fromisoformat(d) + dt.timedelta(days=364)).isoformat(), v] for d, v in
                 ((r["d"], r["sendout"]) for r in rows)
                 if ld - dt.timedelta(days=729) < dt.date.fromisoformat(d) <= ld - dt.timedelta(days=364)]
    inv, dtmi = last.get("inv"), last.get("dtmi")
    return {
        "date": last["d"], "d1": last["sendout"], "avg7": window(ld, 7), "avg30": a30, "avg30_ly": a30ly,
        "avg30_vs_ly_pct": ((a30 / a30ly - 1) * 100) if (a30 and a30ly) else None,
        "inv_gwh": inv, "inv_pct": (inv / dtmi * 100) if (inv is not None and dtmi) else None,
        "dtrs": last.get("dtrs"), "series": series, "ly_series": ly_series,
    }
