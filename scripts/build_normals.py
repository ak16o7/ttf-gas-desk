"""Build daily temperature / HDD normals per basket city.

Primary source: Meteostat station data (bulk CSV, CC BY-NC 4.0) at the city's WMO station.
Fallback per city: NASA POWER daily T2M (MERRA-2 grid cell) - it runs ~1 C cold in winter
at city points, so it is only used when a station lacks coverage.

Run once (or yearly) and commit data/normals.json:
    python scripts/build_normals.py

Two reference periods:
  n30  = 1991-2020 (WMO climate normal)
  n10  = 2016-2025 (recent decade, captures the warming trend traders usually price)
Day-of-year means on a 365-day calendar (29 Feb folded into 28 Feb), smoothed with a
21-day circular moving average. HDD normals are averaged from daily HDD, not derived from
the mean temperature (HDD is non-linear in T).
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import ASIA_BASKET, EU_BASKET, hdd  # noqa: E402

URL = ("https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M&community=RE"
       "&longitude={lon:.2f}&latitude={lat:.2f}&start=19910101&end=20251231&format=JSON")
METEOSTAT = "https://bulk.meteostat.net/v2/daily/{station}.csv.gz"
STATIONS = {  # WMO ids as used by Meteostat
    "Berlin": "10384", "Hamburg": "10147", "München": "10865", "Köln": "10513", "Frankfurt": "10637",
    "London": "03772", "Birmingham": "03534", "Manchester": "03334", "Mailand": "16080", "Rom": "16239",
    "Paris": "07149", "Lyon": "07481", "Amsterdam": "06240", "Rotterdam": "06344", "Brüssel": "06447",
    "Wien": "11035", "Tokio": "47662", "Osaka": "47772", "Seoul": "47108", "Shanghai": "58362", "Peking": "54511",
}
MIN_COVERAGE = 0.6
PERIODS = {"n30": (1991, 2020), "n10": (2016, 2025)}
WINDOW = 21


def doy365(d: dt.date) -> int:
    """0..364; 29 Feb shares the slot of 28 Feb."""
    if d.month == 2 and d.day == 29:
        d = d.replace(day=28)
    return (d.replace(year=2001) - dt.date(2001, 1, 1)).days


def smooth(vals: list[float]) -> list[float]:
    n, h = len(vals), WINDOW // 2
    return [round(sum(vals[(i + k) % n] for k in range(-h, h + 1)) / WINDOW, 2) for i in range(n)]


def fetch_meteostat(station: str) -> dict[str, float]:
    with urllib.request.urlopen(METEOSTAT.format(station=station), timeout=120) as r:
        text = gzip.decompress(r.read()).decode()
    out: dict[str, float] = {}
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0][:4].isdigit():
            continue
        tavg, tmin, tmax = (float(x) if x else None for x in row[1:4])
        v = tavg if tavg is not None else ((tmin + tmax) / 2 if tmin is not None and tmax is not None else None)
        if v is not None:
            out[row[0].replace("-", "")] = v
    return out


def coverage(raw: dict[str, float]) -> float:
    worst = 1.0
    for y0, y1 in PERIODS.values():
        n = sum(1 for k in raw if y0 <= int(k[:4]) <= y1)
        days = (dt.date(y1, 12, 31) - dt.date(y0, 1, 1)).days + 1
        worst = min(worst, n / days)
    return worst


def fetch(lat: float, lon: float) -> dict[str, float]:
    for attempt in range(4):
        try:
            with urllib.request.urlopen(URL.format(lat=lat, lon=lon), timeout=120) as r:
                p = json.load(r)["properties"]["parameter"]["T2M"]
            return {k: v for k, v in p.items() if v is not None and v > -900}
        except Exception as e:  # noqa: BLE001
            print(f"  retry {attempt + 1}: {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"NASA POWER failed for {lat},{lon}")


def build_city(name: str, lat: float, lon: float) -> dict:
    raw, source = {}, None
    if name in STATIONS:
        try:
            raw = fetch_meteostat(STATIONS[name])
            source = f"meteostat:{STATIONS[name]}"
        except Exception as e:  # noqa: BLE001
            print("  meteostat failed:", e)
    if not raw or coverage(raw) < MIN_COVERAGE:
        raw, source = fetch(lat, lon), "nasa-power"
    print(f"  {source} coverage={coverage(raw):.2f}")
    out = {"lat": lat, "lon": lon, "source": source}
    for key, (y0, y1) in PERIODS.items():
        t_sum, h_sum, cnt = [0.0] * 365, [0.0] * 365, [0] * 365
        for k, v in raw.items():
            d = dt.date(int(k[:4]), int(k[4:6]), int(k[6:]))
            if y0 <= d.year <= y1:
                i = doy365(d)
                t_sum[i] += v
                h_sum[i] += hdd(v)
                cnt[i] += 1
        out[f"t_{key}"] = smooth([t_sum[i] / cnt[i] for i in range(365)])
        out[f"hdd_{key}"] = smooth([h_sum[i] / cnt[i] for i in range(365)])
    return out


def main() -> None:
    cities: dict[str, dict] = {}
    for basket in (EU_BASKET, ASIA_BASKET):
        for c in basket.values():
            for name, lat, lon, _w in c["cities"]:
                print("fetching", name)
                cities[name] = build_city(name, lat, lon)
    doc = {
        "source": "Meteostat station data (CC BY-NC 4.0), fallback NASA POWER T2M",
        "built": dt.date.today().isoformat(),
        "periods": {k: f"{a}-{b}" for k, (a, b) in PERIODS.items()},
        "calendar": "365-day, index 0 = 1 Jan, 29 Feb folded into 28 Feb",
        "smoothing": f"{WINDOW}-day circular moving average",
        "cities": cities,
    }
    path = ROOT / "data" / "normals.json"
    path.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    print("wrote", path, path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
