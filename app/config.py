"""Static configuration: instruments, weather baskets, conversion constants.

Everything a trader might want to tune lives here, not scattered in code.
"""
from __future__ import annotations

import os

VERSION = "1.1.0"
TZ = "Europe/Berlin"

EIA_API_KEY = os.getenv("EIA_API_KEY", "").strip()
GIE_API_KEY = os.getenv("GIE_API_KEY", "").strip()
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "").strip()
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
CACHE_DIR = os.getenv("CACHE_DIR", "/tmp/gas-desk-cache")
POWER_DESK_URL = os.getenv("POWER_DESK_URL", "https://de-power-desk.onrender.com/").strip()
USER_AGENT = os.getenv(
    "HTTP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
)

# ---------------------------------------------------------------- units
MWH_PER_MMBTU = 0.29307107          # 1 MMBtu = 293.07107 kWh
MWH_PER_THERM = 0.0293071           # 1 therm = 29.3071 kWh
GWH_PER_MCM = 10.57275              # ENTSOG standard GCV conversion (1 mcm = 10.57275 GWh)
MWH_PER_T_API2 = 6000 * 4.1868 / 3600  # API2 spec 6,000 kcal/kg NAR -> 6.978 MWh/t
# TTF is priced per MWh gross calorific value (Hs/HHV). The gas CO2 factor and the CCGT efficiency must
# be on the same basis: 0.053942 tCO2/MMBtu HHV (Platts, UK govt factors) = 0.1841 tCO2/MWh_Hs.
# (The often-quoted 0.202 is per MWh net calorific value and overstates CO2 cost by ~10 % on TTF prices.)
EF_GAS = 0.053942 * 3.412141        # tCO2 per MWh_th (Hs/HHV) = 0.18406
EF_COAL = 0.34056                   # tCO2 per MWh_th (NAR/NCV basis, same as API2 energy content)

# ---------------------------------------------------------------- ICE delayed futures
# productId/hubId are ICE's charting ids (not the product-page ids).
ICE_PRODUCTS: dict[str, dict] = {
    "TTF":   {"product": 4331, "hub": 7979, "name": "Dutch TTF", "unit": "EUR/MWh", "page": 27996665},
    "THE":   {"product": 4335, "hub": 7984, "name": "German THE", "unit": "EUR/MWh", "page": 27996810},
    "NBP":   {"product": 236,  "hub": 377,  "name": "UK NBP", "unit": "p/th", "page": 910},
    "JKM":   {"product": 4172, "hub": 7903, "name": "JKM LNG (Platts)", "unit": "USD/MMBtu", "page": 6753280},
    "HH":    {"product": 53,   "hub": 68,   "name": "Henry Hub LD1", "unit": "USD/MMBtu", "page": 6590258},
    "EUA":   {"product": 390,  "hub": 564,  "name": "EUA", "unit": "EUR/t", "page": 197},
    "API2":  {"product": 517,  "hub": 681,  "name": "API2 Rotterdam Coal", "unit": "USD/t", "page": 243},
    "BRENT": {"product": 254,  "hub": 403,  "name": "Brent", "unit": "USD/bbl", "page": 219},
    "DEPWR": {"product": 19032, "hub": 21971, "name": "Strom DE Base (ICE Endex)", "unit": "EUR/MWh", "page": 57609948},
}

# Which strips need settlement history (one HTTP call each): a strip is fetched when its delivery
# starts at most N months from today. Keep this lean - ICE is a public page endpoint, not a feed.
ICE_HISTORY_SCOPE: dict[str, dict] = {
    "TTF":   {"month": 30, "quarter": 24, "season": 36, "cal": 48},
    "THE":   {"month": 18, "quarter": 18, "season": 24, "cal": 36},
    "NBP":   {"month": 12, "quarter": 12, "season": 24, "cal": 24},
    "JKM":   {"month": 12},
    "HH":    {"month": 18, "cal": 24},
    "EUA":   {"month": 40},
    "API2":  {"month": 15, "quarter": 15, "cal": 36},
    "BRENT": {"month": 3},
    "DEPWR": {"month": 6, "quarter": 15, "season": 0, "cal": 36},
}

# Continuous front-month history (Yahoo chart API) for the 1y context chart.
YAHOO_FRONTS = {
    "TTF": {"symbol": "TTF=F", "unit": "EUR/MWh"},
    "HH": {"symbol": "NG=F", "unit": "USD/MMBtu"},
    "BRENT": {"symbol": "BZ=F", "unit": "USD/bbl"},
}

# ---------------------------------------------------------------- LNG / switching defaults
# Shown as editable assumptions in the UI; only defaults here.
DEFAULTS = {
    "eta_gas": 0.50,          # CCGT efficiency on Hs/HHV basis (= ~55 % Hi/LHV); Platts standard spark
    "eta_coal": 0.38,         # hard-coal fleet efficiency (NAR/NCV basis); Platts standard dark uses 0.35
    "hh_mult": 1.15,          # US SPA feedgas formula: 115 % HH
    "toll": 2.50,             # USD/MMBtu fixed liquefaction fee (sunk for cancel decision)
    "ship_nwe": 0.90,         # USD/MMBtu US Gulf -> NW Europe incl. boil-off
    "ship_asia": 2.40,        # USD/MMBtu US Gulf -> NE Asia (Cape routing)
    "regas_nwe": 0.40,        # USD/MMBtu regas + entry NW Europe
    "storage_target": 90.0,   # % EU target (anytime 1 Oct - 1 Dec, extended to 2027)
}

# ---------------------------------------------------------------- GIE
AGSI_COUNTRIES = ["EU", "DE", "NL", "FR", "IT", "AT", "BE"]
ALSI_COUNTRIES = ["EU", "NL", "BE", "FR", "DE", "IT", "ES", "PL"]

# ---------------------------------------------------------------- ENTSOG: Norwegian supply
# Entry side of every Gassco exit point (ENTSOG returns nothing for the NO exit side).
# Several receiving TSOs publish the same physical flow at shared points (Dornum, Emden EPT1);
# identical same-day values within one terminal are therefore counted once.
NORWAY_POINTS: dict[str, dict] = {
    "uk-tso-0001itp-00022entry": {"terminal": "St. Fergus", "country": "UK"},
    "uk-tso-0001itp-00091entry": {"terminal": "Easington", "country": "UK"},
    "fr-tso-0003itp-00045entry": {"terminal": "Dunkerque", "country": "FR"},
    "be-tso-0001itp-00106entry": {"terminal": "Zeebrugge", "country": "BE"},
    "de-tso-0005itp-00188entry": {"terminal": "Dornum", "country": "DE"},
    "de-tso-0009itp-00126entry": {"terminal": "Dornum", "country": "DE"},
    "de-tso-0009itp-00525entry": {"terminal": "Dornum", "country": "DE"},
    "de-tso-0002itp-00105entry": {"terminal": "Emden", "country": "DE"},
    "de-tso-0005itp-00081entry": {"terminal": "Emden", "country": "DE"},
    "de-tso-0009itp-00080entry": {"terminal": "Emden", "country": "DE"},
    "nl-tso-0001itp-00160entry": {"terminal": "Emden", "country": "NL"},
    "de-tso-0002itp-00075entry": {"terminal": "Emden NPT", "country": "DE"},
    "de-tso-0005itp-00086entry": {"terminal": "Emden NPT", "country": "DE"},
    "nl-tso-0001itp-00161entry": {"terminal": "Emden NPT", "country": "NL"},
    "dk-tso-0001itp-00630entry": {"terminal": "Baltic Pipe (North Sea Entry DK)", "country": "DK"},
}

# Other pipeline import routes into the EU (ENTSOG physical flow, GWh/d). Each receiving point is
# published once on the entry side, so no de-duplication is needed. UK->Continent is netted:
# IUK and BBL exports minus reverse flows into the UK.
PIPE_ROUTES: dict[str, dict] = {
    "dz_it": {"name": "Algerien → Italien (Mazara)", "plus": ["it-tso-0001itp-00093entry"]},
    "dz_es": {"name": "Algerien → Spanien (Medgaz)", "plus": ["es-tso-0006itp-00048entry"]},
    "az_it": {"name": "Aserbaidschan → Italien (TAP)", "plus": ["it-tso-0001itp-00008entry"]},
    "ru_bg": {"name": "Russland → EU (TurkStream)", "plus": ["bg-tso-0001itp-00549entry"]},
    "ly_it": {"name": "Libyen → Italien (Gela)", "plus": ["it-tso-0001itp-00074entry"]},
    "uk_eu": {"name": "UK → Kontinent netto (IUK+BBL)",
              "plus": ["uk-tso-0003itp-00061exit", "uk-tso-0001itp-00207exit"],
              "minus": ["uk-tso-0003itp-00061entry", "uk-tso-0004itp-00207exit"]},
}

# ---------------------------------------------------------------- Weather baskets
# Country weights ~ heating-sensitive gas demand (households + services, rounded TWh/yr);
# city weights ~ population share within the country. Approximate by design - tune here.
EU_BASKET: dict[str, dict] = {
    "DE": {"name": "Deutschland", "weight": 330, "cities": [
        ("Berlin", 52.52, 13.40, 0.24), ("Hamburg", 53.55, 9.99, 0.16), ("München", 48.14, 11.58, 0.20),
        ("Köln", 50.94, 6.96, 0.22), ("Frankfurt", 50.11, 8.68, 0.18)]},
    "UK": {"name": "Vereinigtes Königreich", "weight": 360, "cities": [
        ("London", 51.51, -0.13, 0.50), ("Birmingham", 52.49, -1.89, 0.25), ("Manchester", 53.48, -2.24, 0.25)]},
    "IT": {"name": "Italien", "weight": 250, "cities": [("Mailand", 45.46, 9.19, 0.60), ("Rom", 41.90, 12.50, 0.40)]},
    "FR": {"name": "Frankreich", "weight": 170, "cities": [("Paris", 48.86, 2.35, 0.60), ("Lyon", 45.76, 4.84, 0.40)]},
    "NL": {"name": "Niederlande", "weight": 110, "cities": [("Amsterdam", 52.37, 4.90, 0.50), ("Rotterdam", 51.92, 4.48, 0.50)]},
    "BE": {"name": "Belgien", "weight": 70, "cities": [("Brüssel", 50.85, 4.35, 1.0)]},
    "AT": {"name": "Österreich", "weight": 35, "cities": [("Wien", 48.21, 16.37, 1.0)]},
}
ASIA_BASKET: dict[str, dict] = {
    "JP": {"name": "Japan", "weight": 35, "cities": [("Tokio", 35.68, 139.69, 0.7), ("Osaka", 34.69, 135.50, 0.3)]},
    "KR": {"name": "Südkorea", "weight": 25, "cities": [("Seoul", 37.57, 126.98, 1.0)]},
    "CN": {"name": "China (Ost/Nord)", "weight": 40, "cities": [("Shanghai", 31.23, 121.47, 0.5), ("Peking", 39.90, 116.40, 0.5)]},
}
# Gas-for-power wind proxy: onshore/offshore-heavy points in N-Germany and NL.
WIND_POINTS = [("Husum", 54.48, 9.05), ("Emden", 53.37, 7.21), ("Rostock", 54.09, 12.10),
               ("Magdeburg", 52.13, 11.63), ("Den Helder", 52.96, 4.76)]
HDD_BASE = 18.0      # Eurostat: HDD = 18 - Tm if Tm <= 15 C, else 0
HDD_THRESHOLD = 15.0


def hdd(t: float | None) -> float | None:
    if t is None:
        return None
    return HDD_BASE - t if t <= HDD_THRESHOLD else 0.0
