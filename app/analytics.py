"""Curve analytics: unit conversion, trader spreads with 1y history, fuel-switch and LNG-arb inputs."""
from __future__ import annotations

import bisect
import calendar
import datetime as dt
from typing import Callable

from .config import MWH_PER_MMBTU, MWH_PER_THERM

# ------------------------------------------------------------------ units
P_TH_TO_GBP_MWH = 1 / 100 / MWH_PER_THERM  # 0.341214...


def eur_mwh_to_usd_mmbtu(x: float, eurusd: float) -> float:
    return x * eurusd * MWH_PER_MMBTU


def usd_mmbtu_to_eur_mwh(x: float, eurusd: float) -> float:
    return x / MWH_PER_MMBTU / eurusd


def nbp_p_th_to_eur_mwh(x: float, eurgbp: float) -> float:
    return x * P_TH_TO_GBP_MWH / eurgbp


def to_eur_mwh(sym: str, x: float | None, eurusd: float | None, eurgbp: float | None) -> float | None:
    if x is None:
        return None
    if sym in ("TTF", "THE"):
        return x
    if sym == "NBP":
        return nbp_p_th_to_eur_mwh(x, eurgbp) if eurgbp else None
    if sym in ("JKM", "HH"):
        return usd_mmbtu_to_eur_mwh(x, eurusd) if eurusd else None
    return None


def to_usd_mmbtu(sym: str, x: float | None, eurusd: float | None, eurgbp: float | None) -> float | None:
    if x is None:
        return None
    if sym in ("JKM", "HH"):
        return x
    e = to_eur_mwh(sym, x, eurusd, eurgbp)
    return eur_mwh_to_usd_mmbtu(e, eurusd) if (e is not None and eurusd) else None


# ------------------------------------------------------------------ fx history lookup
class FxHist:
    def __init__(self, hist: dict[str, dict[str, float]] | None):
        self._d = {}
        for cur, series in (hist or {}).items():
            keys = sorted(series)
            self._d[cur] = (keys, [series[k] for k in keys])

    def on(self, cur: str, day: str) -> float | None:
        if cur not in self._d:
            return None
        keys, vals = self._d[cur]
        i = bisect.bisect_right(keys, day) - 1
        return vals[i] if i >= 0 else None


# ------------------------------------------------------------------ curve helpers
def key_period(key: str) -> tuple[dt.date, dt.date] | None:
    """Inverse of Strip.key for the standard kinds."""
    parts = key.split("-")
    try:
        if parts[0] == "M":
            y, m = int(parts[1]), int(parts[2])
            return dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])
        if parts[0] == "Q":
            y, q = int(parts[1]), int(parts[2])
            m0 = 3 * (q - 1) + 1
            return dt.date(y, m0, 1), dt.date(y, m0 + 2, calendar.monthrange(y, m0 + 2)[1])
        if parts[0] == "WIN":
            y = int(parts[1])
            return dt.date(y, 10, 1), dt.date(y + 1, 3, 31)
        if parts[0] == "SUM":
            y = int(parts[1])
            return dt.date(y, 4, 1), dt.date(y, 9, 30)
        if parts[0] == "CAL":
            y = int(parts[1])
            return dt.date(y, 1, 1), dt.date(y, 12, 31)
        if parts[0] == "GY":
            y = int(parts[1])
            return dt.date(y, 10, 1), dt.date(y + 1, 9, 30)
    except (IndexError, ValueError):
        return None
    return None


def _days(r: dict) -> int:
    return (dt.date.fromisoformat(r["end"]) - dt.date.fromisoformat(r["start"])).days + 1


def synth_row(rows: list[dict], key: str, label: str) -> dict | None:
    """Day-weighted average of listed component strips that exactly tile the period of `key`.

    Tries months first, then quarters, then seasons. Returns None unless the tiling is complete
    and every component has a mark.
    """
    per = key_period(key)
    if per is None:
        return None
    a, b = per[0].isoformat(), per[1].isoformat()
    for kind in ("month", "quarter", "season"):
        comps = sorted((r for r in rows if r["kind"] == kind and a <= r["start"] and r["end"] <= b),
                       key=lambda r: r["start"])
        if not comps or comps[0]["start"] != a or comps[-1]["end"] != b:
            continue
        if sum(_days(r) for r in comps) != (per[1] - per[0]).days + 1:
            continue
        if any(r["mark"] is None for r in comps):
            continue
        w = [_days(r) for r in comps]
        W = sum(w)
        prevs = [r.get("prev_settle") for r in comps]
        prev = sum(p * wi for p, wi in zip(prevs, w)) / W if all(p is not None for p in prevs) else None
        live = all(r["src"] in ("last", "settle") for r in comps)
        if live:
            mark, src = sum(r["mark"] * wi for r, wi in zip(comps, w)) / W, "synth"
        elif all(r["src"] == "settle_prev" for r in comps) or prev is None:
            # consistent basis: yesterday's settlements (or whatever mix exists if prev is unknown)
            mark, src = sum(r["mark"] * wi for r, wi in zip(comps, w)) / W, "settle_prev"
        else:
            mark, src = prev, "settle_prev"
        maps = [{d: v for d, v in r.get("_bars") or []} for r in comps]
        bars = []
        if all(maps):
            for d in sorted(set.intersection(*[set(m) for m in maps])):
                bars.append([d, sum(m[d] * wi for m, wi in zip(maps, w)) / W])
        return {"key": key, "label": label + "*", "kind": "synthetic", "start": a, "end": b, "mark": mark,
                "src": src, "prev_settle": prev, "_bars": bars,
                "components": [r["label"] for r in comps]}
    return None


class Curves:
    def __init__(self, curves: dict[str, list[dict]], today: dt.date):
        self.c = curves
        self.today = today
        self.by_key = {s: {r["key"]: r for r in rows} for s, rows in curves.items()}

    def row(self, sym: str, key: str | None) -> dict | None:
        return self.by_key.get(sym, {}).get(key) if key else None

    def get(self, sym: str, key: str | None, label: str | None = None) -> dict | None:
        """Listed strip with a mark, else a synthetic strip built from its components."""
        if not key:
            return None
        r = self.row(sym, key)
        if r is not None and r["mark"] is not None and (r.get("_bars") or r["src"] != "settle_prev"):
            return r
        syn = synth_row(self.c.get(sym, []), key, label or (r["label"] if r else key))
        return syn or (r if r is not None and r["mark"] is not None else None)

    def front(self, sym: str, kind: str, n: int = 0, start_after_today: bool = True) -> dict | None:
        rows = [r for r in self.c.get(sym, []) if r["kind"] == kind and r["mark"] is not None
                and (not start_after_today or r["start"] > self.today.isoformat())]
        return rows[n] if len(rows) > n else None

    def season(self, sym: str, which: str, n: int = 0) -> dict | None:
        rows = [r for r in self.c.get(sym, []) if r["kind"] == "season" and r["key"].startswith(which)
                and r["start"] > self.today.isoformat() and r["mark"] is not None]
        return rows[n] if len(rows) > n else None


def _pctile(values: list[float], x: float) -> float | None:
    if not values:
        return None
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return 100.0 * (below + 0.5 * equal) / len(values)


MAX_LEG_GAP_MIN = 60  # live legs traded further apart than this are not combined


def spread_series(legs: list[tuple[str, dict]], fn: Callable, fx: FxHist, spot: dict, today: dt.date) -> dict:
    """legs: [(sym, row)]; fn(values_by_leg, eurusd, eurgbp) -> float."""
    # Never mix live prices with yesterday's settlements: if any leg has not traded today, value the
    # whole spread on the previous-settlement basis (desk convention: untraded spread = unchanged).
    # Same for live legs whose last trades are far apart (illiquid THE/NBP strips vs. liquid TTF).
    live = {"last", "settle", "synth"}
    mixed = any(r["src"] == "settle_prev" for _, r in legs) and any(r["src"] in live for _, r in legs)
    times = [dt.datetime.fromisoformat(r["last_time"]) for _, r in legs if r["src"] == "last" and r.get("last_time")]
    async_gap = (max(times) - min(times)).total_seconds() / 60 if len(times) >= 2 else 0.0
    if async_gap > MAX_LEG_GAP_MIN and all(r.get("prev_settle") is not None for _, r in legs):
        mixed = True
    basis = "settle_prev" if (mixed or all(r["src"] == "settle_prev" for _, r in legs)) else "live"
    cur_vals = [(r.get("prev_settle") if mixed else r["mark"]) for _, r in legs]
    current = None
    if all(v is not None for v in cur_vals):
        try:
            current = fn(cur_vals, spot.get("EURUSD"), spot.get("EURGBP"))
        except (TypeError, ZeroDivisionError):
            current = None
    maps = [{d: v for d, v in r.get("_bars") or []} for _, r in legs]
    common = sorted(set.intersection(*[set(m) for m in maps])) if maps and all(maps) else []
    hist = []
    for d in common:
        try:
            v = fn([m[d] for m in maps], fx.on("USD", d), fx.on("GBP", d))
        except (TypeError, ZeroDivisionError):
            continue
        if v is not None:
            hist.append([d, round(v, 4)])
    t_iso = today.isoformat()
    past = [h for h in hist if h[0] < t_iso]

    def ref(days: int):
        lim = (today - dt.timedelta(days=days)).isoformat()
        c = [h for h in past if h[0] <= lim]
        return c[-1][1] if c else None

    prev = past[-1][1] if past else None
    vals = [h[1] for h in hist]
    out = {
        "value": current, "basis": basis, "leg_gap_min": round(async_gap) if times else None,
        "prev": prev,
        "chg_d": (current - prev) if (current is not None and prev is not None and basis == "live") else None,
        "chg_1w": (current - ref(7)) if (current is not None and ref(7) is not None) else None,
        "chg_1m": (current - ref(30)) if (current is not None and ref(30) is not None) else None,
        "min_1y": min(vals) if vals else None, "max_1y": max(vals) if vals else None,
        "pctile_1y": _pctile(vals, current) if (vals and current is not None) else None,
        "history": hist,
        "legs": [{"sym": s, "label": r["label"], "mark": r["mark"], "src": r["src"]} for s, r in legs],
    }
    return out


def build_spreads(C: Curves, fx: FxHist, spot: dict) -> list[dict]:
    today = C.today
    specs: list[dict] = []

    def add(sid, group, name, unit, legs, fn, note="", op=" − "):
        if not legs or any(r is None for _, r in legs):
            specs.append({"id": sid, "group": group, "name": name, "unit": unit, "missing": True, "note": note})
            return
        s = spread_series(legs, fn, fx, spot, today)
        same = len({sym for sym, _ in legs}) == 1
        s.update({"id": sid, "group": group, "name": name, "unit": unit, "note": note,
                  "legs_label": op.join(r["label"] if same else f"{sym} {r['label']}" for sym, r in legs)})
        specs.append(s)

    diff = lambda v, *_: v[0] - v[1]  # noqa: E731

    m1, m2 = C.front("TTF", "month", 0), C.front("TTF", "month", 1)
    q1, q2 = C.front("TTF", "quarter", 0), C.front("TTF", "quarter", 1)
    win0 = C.season("TTF", "WIN", 0)
    sum0 = C.season("TTF", "SUM", 0)
    # next injection season: the first summer, and the winter directly after it
    win_after = C.get("TTF", f"WIN-{sum0['start'][:4]}") if sum0 else None
    cal1, cal2 = C.front("TTF", "cal", 0), C.front("TTF", "cal", 1)

    g = "Zeitspreads TTF"
    add("ttf_m1m2", g, "M1 − M2", "EUR/MWh", [("TTF", m1), ("TTF", m2)], diff, "Prompt-Struktur; negativ = Contango")
    add("ttf_q1q2", g, "Q+1 − Q+2", "EUR/MWh", [("TTF", q1), ("TTF", q2)], diff)
    add("ttf_winsum", g, "Winter − folgender Sommer", "EUR/MWh", [("TTF", win0), ("TTF", sum0)], diff,
        "Winterprämie der laufenden Saisonkurve")
    add("ttf_storage", g, "Sommer → Winter (Einspeicher-Spread)", "EUR/MWh", [("TTF", win_after), ("TTF", sum0)], diff,
        "Intrinsischer Speicherwert der nächsten Einspeichersaison (vor Kosten)")
    add("ttf_cal", g, "Cal+1 − Cal+2", "EUR/MWh", [("TTF", cal1), ("TTF", cal2)], diff)

    g = "Lokationsspreads"
    for label, key_row in (("M1", m1), ("Q+1", q1), ("Winter", win0), ("Cal+1", cal1)):
        if key_row is None:
            continue
        the = C.get("THE", key_row["key"], key_row["label"])
        add(f"the_ttf_{label}", g, f"THE − TTF {label}", "EUR/MWh", [("THE", the), ("TTF", key_row)], diff,
            "Deutscher Aufschlag; Konvertierungs-/Transportsignal")
    for label, key_row in (("M1", m1), ("Winter", win0)):
        if key_row is None:
            continue
        nbp = C.get("NBP", key_row["key"], key_row["label"])
        add(f"nbp_ttf_{label}", g, f"NBP − TTF {label}", "EUR/MWh", [("NBP", nbp), ("TTF", key_row)],
            lambda v, usd, gbp: nbp_p_th_to_eur_mwh(v[0], gbp) - v[1], "NBP in p/th → EUR/MWh (EURGBP)")

    g = "Interkontinental (USD/MMBtu)"
    jkm_rows = [r for r in C.c.get("JKM", []) if r["kind"] == "month" and r["mark"] is not None and r["start"] > today.isoformat()]
    if jkm_rows:
        j = jkm_rows[0]
        add("jkm_ttf", g, f"JKM − TTF {j['label']}", "USD/MMBtu", [("JKM", j), ("TTF", C.get("TTF", j["key"]))],
            lambda v, usd, gbp: v[0] - eur_mwh_to_usd_mmbtu(v[1], usd), "Asien-Prämie; Cargo-Wettbewerb")
    if m1 is not None:
        add("ttf_hh", g, f"TTF − HH {m1['label']}", "USD/MMBtu", [("TTF", m1), ("HH", C.get("HH", m1["key"]))],
            lambda v, usd, gbp: eur_mwh_to_usd_mmbtu(v[0], usd) - v[1], "Brutto-Arbitragefenster US-LNG")

    g = "Cross-Commodity"
    brent = C.front("BRENT", "month", 0, start_after_today=False)
    if m1 is not None and brent is not None:
        add("ttf_brent", g, "TTF in % Brent", "%", [("TTF", m1), ("BRENT", brent)],
            lambda v, usd, gbp: eur_mwh_to_usd_mmbtu(v[0], usd) / v[1] * 100,
            "Öl-Parität: TTF (USD/MMBtu) ÷ Brent (USD/bbl); klassische Ölindexierung ≈ 11–13 %", op=" ÷ ")
    return specs


def eua_for(C: Curves, strip_start: str) -> dict | None:
    """Dec EUA of the delivery year (desk convention); falls back to the first later Dec."""
    y = int(strip_start[:4])
    for yy in range(y, y + 3):
        r = C.row("EUA", f"M-{yy}-12")
        if r and r["mark"] is not None:
            return r
    return None


def api2_for(C: Curves, row: dict) -> tuple[float | None, str | None]:
    r = C.get("API2", row["key"], row["label"])
    return (r["mark"], r["label"]) if r else (None, None)


def switching_inputs(C: Curves) -> list[dict]:
    picks = [("M1", C.front("TTF", "month", 0)), ("M2", C.front("TTF", "month", 1)),
             ("Q+1", C.front("TTF", "quarter", 0)), ("Q+2", C.front("TTF", "quarter", 1)),
             ("Winter", C.season("TTF", "WIN", 0)), ("Sommer", C.season("TTF", "SUM", 0)),
             ("Cal+1", C.front("TTF", "cal", 0)), ("Cal+2", C.front("TTF", "cal", 1))]
    out = []
    for tag, r in picks:
        if r is None:
            continue
        api2, api2_lbl = api2_for(C, r)
        eua = eua_for(C, r["start"])
        pw = C.get("DEPWR", r["key"], r["label"])
        out.append({"tag": tag, "label": r["label"], "key": r["key"], "ttf": r["mark"], "ttf_src": r["src"],
                    "api2": api2, "api2_label": api2_lbl, "eua": eua["mark"] if eua else None,
                    "eua_label": eua["label"] if eua else None,
                    "power": pw["mark"] if pw else None, "power_label": pw["label"] if pw else None,
                    "power_src": pw["src"] if pw else None})
    return out


def lng_inputs(C: Curves) -> list[dict]:
    today = C.today.isoformat()
    out = []
    for r in C.c.get("TTF", []):
        if r["kind"] != "month" or r["start"] <= today or r["mark"] is None:
            continue
        j, h = C.get("JKM", r["key"]), C.get("HH", r["key"])
        if j is None and len(out) >= 6:
            break
        out.append({"key": r["key"], "label": r["label"], "ttf": r["mark"],
                    "jkm": j["mark"] if j else None, "jkm_src": j["src"] if j else None,
                    "hh": h["mark"] if h else None})
        if len(out) >= 12:
            break
    return out
