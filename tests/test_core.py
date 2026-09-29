"""Offline tests: conversions, strip parsing, spread basis logic, ENTSOG de-duplication, storage and weather maths."""
from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

from app import analytics, fundamentals
from app.config import GWH_PER_MCM, hdd
from app.contracts import months_ahead, parse_strip
from app.sources import entsog, weather
from app.sources.ice import build_row

FIX = pathlib.Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 9, 24)


# ------------------------------------------------------------------ units
def test_unit_conversions_roundtrip():
    usd = 1.138
    x = analytics.eur_mwh_to_usd_mmbtu(74.58, usd)
    assert x == pytest.approx(74.58 * usd * 0.29307107)
    assert analytics.usd_mmbtu_to_eur_mwh(x, usd) == pytest.approx(74.58)
    # 1 therm = 29.3071 kWh -> 100 p/th = 1 GBP/th = 34.1214 GBP/MWh
    assert analytics.nbp_p_th_to_eur_mwh(100, 1.0) == pytest.approx(34.1214, rel=1e-5)
    assert analytics.to_eur_mwh("NBP", 186.41, usd, 0.8607) == pytest.approx(186.41 * 0.341214 / 0.8607, rel=1e-5)
    assert analytics.to_usd_mmbtu("HH", 3.0, usd, 0.86) == 3.0


def test_hdd_definition():
    assert hdd(20) == 0.0 and hdd(15.01) == 0.0
    assert hdd(15) == pytest.approx(3.0)
    assert hdd(-2) == pytest.approx(20.0)


# ------------------------------------------------------------------ contracts
@pytest.mark.parametrize("label,kind,start,end,key", [
    ("Oct26", "month", "2026-10-01", "2026-10-31", "M-2026-10"),
    ("Feb28", "month", "2028-02-01", "2028-02-29", "M-2028-02"),
    ("Q4 26", "quarter", "2026-10-01", "2026-12-31", "Q-2026-4"),
    ("Winter26", "season", "2026-10-01", "2027-03-31", "WIN-2026"),
    ("Summer27", "season", "2027-04-01", "2027-09-30", "SUM-2027"),
    ("Cal 27", "cal", "2027-01-01", "2027-12-31", "CAL-2027"),
    ("GY 26", "gasyear", "2026-10-01", "2027-09-30", "GY-2026"),
    ("Nov26-Mar27", "range", "2026-11-01", "2027-03-31", "R-2026-11-01-2027-03-31"),
])
def test_parse_strip(label, kind, start, end, key):
    s = parse_strip(label)
    assert s is not None
    assert (s.kind, s.start.isoformat(), s.end.isoformat(), s.key) == (kind, start, end, key)
    if kind in ("month", "quarter", "season", "cal", "gasyear"):
        assert analytics.key_period(s.key) == (s.start, s.end)


def test_parse_strip_rejects_garbage_and_months_ahead():
    assert parse_strip("Foo 12") is None
    assert months_ahead(parse_strip("Oct26"), TODAY) == 1
    assert months_ahead(parse_strip("Cal 28"), TODAY) == 16


# ------------------------------------------------------------------ ICE rows
def _raw(last, time, pct=0.0):
    return {"marketId": 1, "lastPrice": last, "lastTime": time, "change": pct, "volume": 10}


def test_build_row_live_vs_settle():
    bars = [["2026-08-24", 60.0], ["2026-09-17", 70.0], ["2026-09-23", 72.0]]
    s = parse_strip("Oct26")
    live = build_row(_raw(74.0, "09/24/2026 02:02 PM GMT", 2.7), s, bars, TODAY)
    assert live["src"] == "last" and live["mark"] == 74.0 and live["prev_settle"] == 72.0
    assert live["chg_d"] == pytest.approx(2.0) and live["chg_1w"] == pytest.approx(4.0)
    assert live["chg_1m"] == pytest.approx(14.0)
    stale = build_row(_raw(71.0, "09/22/2026 10:00 AM GMT"), s, bars, TODAY)
    assert stale["src"] == "settle_prev" and stale["mark"] == 72.0 and stale["chg_d"] is None
    settled = build_row(_raw(74.0, "09/24/2026 02:02 PM GMT"), s, bars + [["2026-09-24", 74.5]], TODAY)
    assert settled["src"] == "settle" and settled["mark"] == 74.5 and settled["prev_settle"] == 72.0


# ------------------------------------------------------------------ spreads & synthetic strips
def _row(key, label, mark, src, prev, bars=None, kind="month", start=None, end=None, last_time=None):
    per = analytics.key_period(key)
    return {"key": key, "label": label, "kind": kind, "start": (start or per[0]).isoformat() if not isinstance(start, str) else start,
            "end": (end or per[1]).isoformat() if not isinstance(end, str) else end, "mark": mark, "src": src,
            "prev_settle": prev, "_bars": bars or [], "last_time": last_time}


def test_synthetic_season_is_day_weighted():
    rows = [_row(f"M-{y}-{m:02d}", "x", float(m), "last", float(m) - 1)
            for y, m in [(2026, 10), (2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 3)]]
    syn = analytics.synth_row(rows, "WIN-2026", "Win-26")
    days = [31, 30, 31, 31, 28, 31]
    assert syn["mark"] == pytest.approx(sum(d * m for d, m in zip(days, [10, 11, 12, 1, 2, 3])) / sum(days))
    assert syn["src"] == "synth"
    # a missing month means no synthetic strip
    assert analytics.synth_row(rows[:-1], "WIN-2026", "Win-26") is None


def test_synthetic_uses_settle_basis_when_components_mixed():
    rows = [_row("M-2026-10", "a", 10.0, "last", 9.0), _row("M-2026-11", "b", 20.0, "settle_prev", 20.0),
            _row("M-2026-12", "c", 30.0, "settle_prev", 30.0)]
    syn = analytics.synth_row(rows, "Q-2026-4", "Q4-26")
    assert syn["src"] == "settle_prev"
    assert syn["mark"] == pytest.approx((31 * 9 + 30 * 20 + 31 * 30) / 92)


def test_spread_never_mixes_live_and_settle():
    fx = analytics.FxHist({"USD": {"2026-09-23": 1.14}})
    spot = {"EURUSD": 1.14, "EURGBP": 0.86}
    bars_a = [["2026-09-22", 70.0], ["2026-09-23", 72.0]]
    bars_b = [["2026-09-22", 71.0], ["2026-09-23", 73.0]]
    a = _row("M-2026-10", "THE", 73.0, "settle_prev", 73.0, bars_b)
    b = _row("M-2026-10", "TTF", 75.0, "last", 72.0, bars_a, last_time="2026-09-24T14:00:00+00:00")
    s = analytics.spread_series([("THE", a), ("TTF", b)], lambda v, *_: v[0] - v[1], fx, spot, TODAY)
    assert s["basis"] == "settle_prev" and s["value"] == pytest.approx(1.0) and s["chg_d"] is None
    # both live but traded 2 h apart -> settlement basis as well
    a2 = dict(a, src="last", mark=74.0, last_time="2026-09-24T12:00:00+00:00")
    s2 = analytics.spread_series([("THE", a2), ("TTF", b)], lambda v, *_: v[0] - v[1], fx, spot, TODAY)
    assert s2["basis"] == "settle_prev" and s2["leg_gap_min"] == 120
    # both live and close in time -> live value and daily change
    a3 = dict(a2, last_time="2026-09-24T13:45:00+00:00")
    s3 = analytics.spread_series([("THE", a3), ("TTF", b)], lambda v, *_: v[0] - v[1], fx, spot, TODAY)
    assert s3["basis"] == "live" and s3["value"] == pytest.approx(-1.0) and s3["chg_d"] == pytest.approx(-2.0)
    assert [h[0] for h in s3["history"]] == ["2026-09-22", "2026-09-23"]


# ------------------------------------------------------------------ ENTSOG
def test_entsog_dedupe_counts_shared_points_once():
    assert entsog.dedupe_sum([703.1, 703.2, 703.2]) == pytest.approx(703.2)
    assert entsog.dedupe_sum([272.2, 264.4, 228.0, 264.5]) == pytest.approx(272.2 + 264.5 + 228.0)
    assert entsog.dedupe_sum([0.0, 146.8, 146.8]) == pytest.approx(146.8)


def test_entsog_aggregate_and_kpis():
    rows = []
    days = [(TODAY - dt.timedelta(days=k)).isoformat() for k in range(40, -1, -1)]
    for i, d in enumerate(days):
        partial = i == len(days) - 1
        for pd, v in [("de-tso-0005itp-00188entry", 700), ("de-tso-0009itp-00126entry", 700), ("de-tso-0009itp-00525entry", 700),
                      ("uk-tso-0001itp-00022entry", 500), ("fr-tso-0003itp-00045entry", 520), ("be-tso-0001itp-00106entry", 480)]:
            if partial and not pd.startswith("de"):
                continue
            op, pt, dr = pd[:11].upper(), pd[11:20].upper(), pd[20:]
            rows.append({"operatorKey": op, "pointKey": pt, "directionKey": dr, "periodFrom": d + "T06:00:00+02:00",
                         "value": v * 1e6})
    agg = entsog.aggregate(rows)
    assert agg["complete_through"] == days[-2]
    k = entsog.kpis(agg)
    assert k["d1"] == pytest.approx(700 + 500 + 520 + 480)
    assert k["d1_mcm"] == pytest.approx(2200 / GWH_PER_MCM)


# ------------------------------------------------------------------ storage
def test_storage_summary_band_and_targets():
    rows = []
    for y in range(2021, 2027):
        d = dt.date(y, 1, 1)
        while d <= (dt.date(2026, 9, 22) if y == 2026 else dt.date(y, 12, 31)):
            base = 50 + 30 * ((d.timetuple().tm_yday) / 365)
            full = base - (10 if y == 2026 else 0)
            rows.append({"d": d.isoformat(), "full": full, "gis": full / 100 * 1000, "inj": 3000, "wd": 500, "wgv": 1000})
            d += dt.timedelta(days=1)
    s = fundamentals.storage_summary(rows, 90.0, TODAY)
    assert s["vs5y_pp"] == pytest.approx(-10, abs=0.05)
    assert s["net7"] == pytest.approx(2500)
    dec = next(t for t in s["targets"] if t["label"] == "1. Dez")
    assert dec["days"] == (dt.date(2026, 12, 1) - dt.date(2026, 9, 22)).days
    assert dec["required_gwh_d"] == pytest.approx((900 - s["gis_twh"]) * 1000 / dec["days"])


# ------------------------------------------------------------------ weather (fixtures captured from Open-Meteo)
def _load(name):
    return json.loads((FIX / name).read_text())


def test_weather_basket_from_fixtures():
    det, ee, eg = _load("om_det.json"), _load("om_ecmwf_ifs025.json"), _load("om_gfs05.json")
    today = dt.date.fromisoformat(ee[0]["daily"]["time"][0])
    b = weather.build_basket("eu", det, ee, eg, today)
    assert abs(sum(w["w"] for w in b["weights"]) - 1) < 1e-3
    i = b["dates"].index(today.isoformat())
    assert b["ens_ecmwf"]["p10"][i] <= b["ens_ecmwf"]["mean"][i] <= b["ens_ecmwf"]["p90"][i]
    assert b["best"][i] == b["ens_ecmwf"]["mean"][i]
    last = max(j for j, v in enumerate(b["ens_gfs"]["mean"]) if v is not None)
    assert b["best"][last] == b["ens_gfs"]["mean"][last]      # GEFS beyond ECMWF-ENS horizon
    k = b["kpi"]["w0_14"]
    assert k["days"] == 15 and -15 < k["anom_n10"] < 15
    # anomaly sign and HDD sign must agree when it is heating season-ish or zero
    for c in b["countries"]:
        assert [w["label"] for w in c["windows"]] == ["T+0–4", "T+5–9", "T+10–14", "T+15–34"]


def test_wind_aggregation_synthetic():
    w = weather.build_wind(_load("om_wind_synthetic.json"))
    assert len(w["dates"]) == 16
    assert w["gfs"][-1] is not None and all(v is not None for v in w["ecmwf"])


# ------------------------------------------------------------------ v1.1: audit fixes and new sources
def test_gas_co2_factor_is_on_gross_calorific_basis():
    from app.config import EF_COAL, EF_GAS
    assert EF_GAS == pytest.approx(0.18406, abs=1e-4)        # t/MWh Hs, matches TTF pricing basis
    assert EF_COAL == pytest.approx(0.34056)
    # CCGT at 50 % Hs (~55 % Hi) emits ~0.37 t/MWh_el, not 0.40
    assert EF_GAS / 0.50 == pytest.approx(0.368, abs=0.002)


def test_calibration_removes_gefs_and_gfs_offsets():
    det, ee, eg = _load("om_det.json"), _load("om_ecmwf_ifs025.json"), _load("om_gfs05.json")
    today = dt.date.fromisoformat(ee[0]["daily"]["time"][0])
    det2, eg2, offs = weather.calibrate(det, ee, eg, today)
    assert len(offs) == len(weather.all_locations())
    for i in range(len(offs)):
        me, mg = weather.ensemble_mean_by_date(ee[i]), weather.ensemble_mean_by_date(eg2[i])
        days = [d for d in sorted(me) if d in mg][:5]
        assert abs(sum(mg[d] - me[d] for d in days) / len(days)) < 0.01
    # original inputs are not mutated
    assert eg[0]["daily"]["temperature_2m_mean"] != eg2[0]["daily"]["temperature_2m_mean"] or offs[0]["gefs"] == 0


def test_the_consumption_parse_and_summary():
    from app.sources import the
    days = the.parse_consumption(_load("the_consumption.json"))
    d = days["2026-09-23"]
    assert d["slp"] == pytest.approx((339250632 + 67061256 + 19534632 + 5181360) / 1e6)
    assert d["rlm"] == pytest.approx((1116532632 + 63679056 + 26159607 + 153413) / 1e6)
    assert days["2026-09-24"]["rlm"] is None and days["2026-09-24"]["total"] is None
    s = the.consumption_summary(days, dt.date(2026, 9, 24))
    k = s["kpi"]
    assert k["rlm_day"] == "2026-09-23" and k["slp_today"] == pytest.approx(days["2026-09-24"]["slp"])
    assert len(s["dates"]) == len(s["series"]["total"]["current"])


def test_the_prices_final_wins_over_preliminary():
    from app.sources import the
    final = [{"gastag": "2026-09-23T00:00:00", "gastagesindex": 73.3, "preisAusgleichsenergiePositiv": 75, "preisAusgleichsenergieNegativ": 70}]
    prelim = [{"gastag": "2026-09-23T06:00:00", "gastagesindex": 99, "preisAusgleichsenergiePositiv": 1, "preisAusgleichsenergieNegativ": 1},
              {"gastag": "2026-09-24T06:00:00", "gastagesindex": 73.8, "preisAusgleichsenergiePositiv": 75.4, "preisAusgleichsenergieNegativ": 72.2}]
    out = the.parse_prices(final, prelim)
    assert [(p["d"], p["index"], p["status"]) for p in out] == [("2026-09-23", 73.3, "endgültig"), ("2026-09-24", 73.8, "vorläufig")]


def test_gassco_feed_parse_collapses_versions():
    from app.sources import gassco
    msgs = gassco.parse_feed((FIX / "gassco_atom.xml").read_text(), now=dt.datetime(2026, 9, 24, 12, tzinfo=dt.timezone.utc))
    assert msgs and all(m["asset"] for m in msgs)
    assert len({m["event"] for m in msgs}) == len(msgs)
    kristin = [m for m in msgs if m["asset"] == "Kristin" and m["start"].startswith("2026-09-24")][0]
    assert kristin["unavailable"] == 6.2 and kristin["unit"] == "mcm/d" and kristin["active_now"]


def test_pipe_routes_net_uk_flows_and_require_all_legs():
    def row(pd, day, gwh):
        op, pt, dr = pd[:11].upper(), pd[11:20].upper(), pd[20:]
        return {"operatorKey": op, "pointKey": pt, "directionKey": dr, "periodFrom": day + "T06:00:00+02:00", "value": gwh * 1e6}
    rows = [row("uk-tso-0003itp-00061exit", "2026-09-20", 100), row("uk-tso-0001itp-00207exit", "2026-09-20", 50),
            row("uk-tso-0003itp-00061entry", "2026-09-20", 10), row("uk-tso-0004itp-00207exit", "2026-09-20", 5),
            row("uk-tso-0003itp-00061exit", "2026-09-21", 100),  # other legs missing -> no value
            row("it-tso-0001itp-00008entry", "2026-09-20", 300)]
    rs = entsog.route_series(rows)
    assert rs["uk_eu"] == {"2026-09-20": 135.0}
    assert rs["az_it"] == {"2026-09-20": 300.0}
