# TTF Gas Desk

Web dashboard for European natural gas (TTF): forward curves, spreads, fundamentals and weather.
FastAPI backend, vanilla JS front end, Plotly bundled locally. UI language: German.

## Features

- **Forward curves:** TTF, THE, NBP, JKM, Henry Hub; months, quarters, seasons, calendar years; changes vs. prior day, 1W, 1M, 3M; EUR/MWh or USD/MMBtu
- **Spreads:** time spreads (M1–M2, Q+1–Q+2, winter–summer, storage spread, Cal), THE–TTF, NBP–TTF, JKM–TTF, TTF–HH, TTF in % of Brent; 1-year range, percentile and history
- **Fuel switch & clean spreads:** SRMC gas vs. coal incl. CO₂, clean spark/dark vs. German baseload power, switch price
- **LNG arbitrage:** US Gulf netbacks to NW Europe and NE Asia vs. Henry Hub feedgas cost
- **Storage:** EU and country fill levels vs. prior year and 5-year range, injection required for the 90 % target
- **Germany:** THE day index (spot) and balancing prices; consumption SLP/RLM vs. prior years
- **Pipeline supply:** Norway by landing point, Algeria, TAP, TurkStream, Libya, UK–Continent net; latest Gassco UMMs
- **LNG send-out:** EU and country regasification vs. prior year
- **Weather:** demand-weighted temperature baskets for NW Europe and NE Asia, ECMWF ENS / GEFS, HDD anomalies, wind
- **Global:** front months, US storage, Henry Hub spot

## Data sources

| Source | Data | Key |
|---|---|---|
| ICE (delayed website quotes) | Futures curves and settlements: TTF, THE, NBP, JKM, HH, EUA, API2, Brent, German power | – |
| GIE AGSI+ / ALSI | Storage, LNG send-out | `GIE_API_KEY` |
| ENTSOG | Physical pipeline flows | – |
| Trading Hub Europe | Day index, balancing prices, consumption | – |
| Gassco | REMIT UMM feed | – |
| Open-Meteo | Forecasts (ECMWF, GFS, ensembles) | optional `OPEN_METEO_API_KEY` |
| Meteostat | Temperature normals (prebuilt in `data/normals.json`) | – |
| EIA | Henry Hub spot, US storage | `EIA_API_KEY` |
| ECB, Yahoo Finance | FX, continuous front-month history | – |

## Configuration

| Variable | Required | Description |
|---|---|---|
| `EIA_API_KEY` | yes | EIA API v2 key |
| `GIE_API_KEY` | yes | GIE AGSI+/ALSI key |
| `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD` | no | Enables HTTP basic auth |
| `POWER_DESK_URL` | no | Link target in the header |
| `CACHE_DIR` | no | Cache directory, default `/tmp/gas-desk-cache` |
| `OPEN_METEO_API_KEY` | no | Uses the Open-Meteo customer API |

Instruments, weather baskets, weights and default assumptions are in `app/config.py`.

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export EIA_API_KEY=... GIE_API_KEY=...
uvicorn app.main:app --reload --port 8000
pytest
```

## Deploy (Render)

New → Blueprint → select this repository. `render.yaml` defines the web service; set the environment variables when prompted.

## Methodology

- **Contract mark:** today's settlement if published, else today's last trade, else prior settlement.
- **Spreads:** if a leg has not traded today or the legs' last trades are more than 60 min apart, the spread is valued on prior settlements. Missing strips are built day-weighted from complete component contracts (marked `*`).
- **Units:** 1 MMBtu = 0.29307107 MWh, 1 therm = 29.3071 kWh, 1 mcm = 10.57275 GWh.
- **Fuel switch:** SRMC gas = (TTF + EUA × 0.184) / η_gas (gross calorific basis); SRMC coal = (API2 / EURUSD / 6.978 + EUA × 0.341) / η_coal; EUA = December contract of the delivery year.
- **LNG arbitrage:** netback NWE = TTF − regas − freight; netback Asia = JKM − freight; margin = best netback − HH factor × HH. Freight, regas, toll and HH factor are user inputs.
- **Storage:** 5-year band per calendar day from the five prior years; required injection = (90 % × working gas volume − stock) / remaining days.
- **Norway:** entry-side ENTSOG flows; identical same-day values reported by several TSOs at one terminal are counted once; incomplete recent gas days are excluded.
- **Weather:** country weights ≈ residential/commercial gas demand, city weights ≈ population. HDD = 18 − T for T ≤ 15 °C, computed per city and ensemble member. GFS/GEFS are offset-corrected per city against ECMWF. Normals: 1991–2020 and 2016–2025, rebuild with `python scripts/build_normals.py`.

## Data notes

- ICE quotes are delayed public website data, not a licensed feed.
- The Gassco feed contains the latest ~10 messages only.
- THE day index and SLP values for the current and next gas day are preliminary.
- For non-commercial use. Not investment advice.
