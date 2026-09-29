"""TTF Gas Desk - FastAPI backend. Serves the static front end and aggregated JSON endpoints."""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import analytics, fundamentals
from .config import (AGSI_COUNTRIES, ALSI_COUNTRIES, DASHBOARD_PASSWORD, DASHBOARD_USERNAME, DEFAULTS,
                     EIA_API_KEY, GIE_API_KEY, ICE_PRODUCTS, POWER_DESK_URL, VERSION, YAHOO_FRONTS)
from .http import close as http_close
from .sources import eia, entsog, fx, gassco, gie, ice, the, weather, yahoo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gasdesk")
STATIC = Path(__file__).parent / "static"


def status(name: str, meta: dict | None = None, error: Exception | str | None = None, state: str | None = None) -> dict:
    if state is None:
        state = "error" if error else ("stale" if meta and meta.get("stale") else "ok")
    ts = (meta or {}).get("cached_at")
    return {"source": name, "state": state,
            "as_of": dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat() if ts else None,
            "error": str(error)[:300] if error else (meta or {}).get("error")}


# ------------------------------------------------------------------ builders
async def build_market() -> dict:
    spot = await fx.spot()
    syms = list(ICE_PRODUCTS)
    res = await asyncio.gather(*[ice.curve(s) for s in syms], return_exceptions=True)
    curves, sources = {}, []
    for s, r in zip(syms, res):
        if isinstance(r, Exception):
            curves[s] = []
            sources.append(status(f"ICE {s}", error=r))
        else:
            curves[s], meta = r
            sources.append(status(f"ICE {s}", meta))
    try:
        fxh, fxmeta = await fx.ecb_history()
        sources.append(status("EZB FX", fxmeta))
    except Exception as e:  # noqa: BLE001
        fxh = {}
        sources.append(status("EZB FX", error=e))
    today = ice.today_berlin()
    C = analytics.Curves(curves, today)
    spreads = analytics.build_spreads(C, analytics.FxHist(fxh), spot)
    public = {s: [{k: v for k, v in r.items() if k != "_bars"} for r in rows] for s, rows in curves.items()}
    return {
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(), "today": today.isoformat(),
        "fx": {k: spot[k] for k in ("EURUSD", "EURGBP", "source", "time")}, "fx_errors": spot.get("errors"),
        "units": {s: p["unit"] for s, p in ICE_PRODUCTS.items()},
        "names": {s: p["name"] for s, p in ICE_PRODUCTS.items()},
        "curves": public, "spreads": spreads,
        "switching": analytics.switching_inputs(C), "lng_arb": analytics.lng_inputs(C),
        "defaults": DEFAULTS, "sources": sources,
    }


async def build_storage() -> dict:
    if not GIE_API_KEY:
        return {"areas": {}, "sources": [status("GIE AGSI+", state="not_configured", error="GIE_API_KEY fehlt")]}
    res = await asyncio.gather(*[gie.storage(a) for a in AGSI_COUNTRIES], return_exceptions=True)
    today = dt.date.today()
    areas, sources = {}, []
    for a, r in zip(AGSI_COUNTRIES, res):
        if isinstance(r, Exception):
            sources.append(status(f"AGSI {a}", error=r))
            continue
        rows, meta = r
        areas[a] = fundamentals.storage_summary(rows, DEFAULTS["storage_target"], today)
        sources.append(status(f"AGSI {a}", meta))
    return {"areas": areas, "order": AGSI_COUNTRIES, "sources": sources}


async def build_lng() -> dict:
    if not GIE_API_KEY:
        return {"areas": {}, "sources": [status("GIE ALSI", state="not_configured", error="GIE_API_KEY fehlt")]}
    res = await asyncio.gather(*[gie.lng(a) for a in ALSI_COUNTRIES], return_exceptions=True)
    today = dt.date.today()
    areas, sources = {}, []
    for a, r in zip(ALSI_COUNTRIES, res):
        if isinstance(r, Exception):
            sources.append(status(f"ALSI {a}", error=r))
            continue
        rows, meta = r
        s = fundamentals.lng_summary(rows, today)
        if a != "EU":  # keep payload small: series only for EU
            s.pop("series", None)
            s.pop("ly_series", None)
        areas[a] = s
        sources.append(status(f"ALSI {a}", meta))
    return {"areas": areas, "order": ALSI_COUNTRIES, "sources": sources}


async def build_norway() -> dict:
    out, sources = {}, []
    try:
        data, meta = await entsog.supply()
        out.update(data)
        sources.append(status("ENTSOG", meta))
    except Exception as e:  # noqa: BLE001
        sources.append(status("ENTSOG", error=e))
    try:
        out["umm"], meta = await gassco.umm()
        sources.append(status("Gassco UMM", meta))
    except Exception as e:  # noqa: BLE001
        sources.append(status("Gassco UMM", error=e))
    out["sources"] = sources
    return out


async def build_germany() -> dict:
    out, sources = {}, []
    try:
        rows, meta = await the.consumption()
        out["consumption"] = the.consumption_summary(rows, ice.today_berlin())
        sources.append(status("THE Verbrauch", meta))
    except Exception as e:  # noqa: BLE001
        sources.append(status("THE Verbrauch", error=e))
    try:
        out["prices"], meta = await the.prices()
        sources.append(status("THE Preise", meta))
    except Exception as e:  # noqa: BLE001
        sources.append(status("THE Preise", error=e))
    out["sources"] = sources
    return out


async def build_weather() -> dict:
    names = ["Open-Meteo det", "Open-Meteo ECMWF ENS", "Open-Meteo GEFS", "Open-Meteo Wind"]
    res = await asyncio.gather(weather.raw_det(), weather.raw_ens_ecmwf(), weather.raw_ens_gfs(),
                               weather.raw_wind(), return_exceptions=True)
    raw, sources = [], []
    for n, r in zip(names, res):
        if isinstance(r, Exception):
            raw.append(None)
            sources.append(status(n, error=r))
        else:
            raw.append(r[0])
            sources.append(status(n, r[1]))
    det, ens_e, ens_g, wind = raw
    today = ice.today_berlin()
    out = {"sources": sources, "normals": {k: weather.normals()[k] for k in ("source", "periods", "built")}}
    try:
        det, ens_g, out["calibration"] = weather.calibrate(det, ens_e, ens_g, today)
    except Exception as e:  # noqa: BLE001
        log.exception("weather calibration")
        sources.append(status("Kalibrierung GFS/GEFS", error=e))
    if det or ens_e or ens_g:
        for b in ("eu", "asia"):
            try:
                out[b] = weather.build_basket(b, det, ens_e, ens_g, today)
            except Exception as e:  # noqa: BLE001
                log.exception("weather basket %s", b)
                sources.append(status(f"Wetter {b}", error=e))
    try:
        out["wind"] = weather.build_wind(wind)
    except Exception as e:  # noqa: BLE001
        sources.append(status("Wind", error=e))
    return out


async def build_global() -> dict:
    sources, out = [], {"fronts": {}}
    res = await asyncio.gather(*[yahoo.chart(v["symbol"]) for v in YAHOO_FRONTS.values()], return_exceptions=True)
    for (k, v), r in zip(YAHOO_FRONTS.items(), res):
        if isinstance(r, Exception):
            sources.append(status(f"Yahoo {v['symbol']}", error=r))
        else:
            out["fronts"][k] = {**r[0], "unit": v["unit"]}
            sources.append(status(f"Yahoo {v['symbol']}", r[1]))
    ttf = out["fronts"].get("TTF")
    if ttf:
        try:
            fxh, _ = await fx.ecb_history()
            fh = analytics.FxHist(fxh)
            ttf["series_usd"] = [[d, round(analytics.eur_mwh_to_usd_mmbtu(v, r), 4)]
                                 for d, v in ttf["series"] if (r := fh.on("USD", d))]
        except Exception as e:  # noqa: BLE001
            sources.append(status("EZB FX", error=e))
    if EIA_API_KEY:
        for name, fn, key in (("EIA Henry Hub", eia.henry_hub_spot, "hh_spot"), ("EIA Speicher US", eia.us_storage, "us_storage")):
            try:
                val, meta = await fn()
                out[key] = val
                sources.append(status(name, meta))
            except Exception as e:  # noqa: BLE001
                sources.append(status(name, error=e))
    else:
        sources.append(status("EIA", state="not_configured", error="EIA_API_KEY fehlt"))
    out["sources"] = sources
    return out


# ------------------------------------------------------------------ app
async def _warm() -> None:
    t0 = time.time()
    for fn in (build_market, build_germany, build_norway, build_storage, build_lng, build_weather, build_global):
        try:
            await fn()
        except Exception:  # noqa: BLE001
            log.exception("warm-up %s failed", fn.__name__)
    log.info("cache warm-up done in %.1fs", time.time() - t0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_warm())
    yield
    task.cancel()
    await http_close()


app = FastAPI(title="TTF Gas Desk", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; worker-src 'self' blob:; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


@app.middleware("http")
async def guard(request: Request, call_next):
    if DASHBOARD_USERNAME and DASHBOARD_PASSWORD and request.url.path != "/healthz":
        ok = False
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                ok = secrets.compare_digest(user, DASHBOARD_USERNAME) and secrets.compare_digest(pw, DASHBOARD_PASSWORD)
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="TTF Gas Desk"'})
    resp = await call_next(request)
    resp.headers.update({
        "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    })
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _json(fn):
    async def handler():
        try:
            return JSONResponse(await fn())
        except Exception as e:  # noqa: BLE001
            log.exception("endpoint failed")
            return JSONResponse({"error": str(e)[:300], "sources": [status(fn.__name__, error=e)]}, status_code=502)
    return handler


app.add_api_route("/api/market", _json(build_market), methods=["GET"])
app.add_api_route("/api/storage", _json(build_storage), methods=["GET"])
app.add_api_route("/api/lng", _json(build_lng), methods=["GET"])
app.add_api_route("/api/norway", _json(build_norway), methods=["GET"])
app.add_api_route("/api/germany", _json(build_germany), methods=["GET"])
app.add_api_route("/api/weather", _json(build_weather), methods=["GET"])
app.add_api_route("/api/global", _json(build_global), methods=["GET"])


@app.get("/api/meta")
async def meta():
    return {"version": VERSION, "auth": bool(DASHBOARD_USERNAME and DASHBOARD_PASSWORD),
            "eia": bool(EIA_API_KEY), "gie": bool(GIE_API_KEY), "power_desk_url": POWER_DESK_URL}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "version": VERSION}


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
