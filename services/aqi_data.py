"""Current AQI per city.

Live source: OpenAQ v3 (needs OPENAQ_API_KEY). Fallback: a deterministic,
season-aware seed so the map always renders with believable values. Seeded
values are flagged (`live: false`) so the UI can label them honestly.
"""
import datetime
import hashlib

import requests

import config

# CPCB AQI bands (India) — (upper_bound, label, color)
_BANDS = [
    (50, "Good", "#009865"),
    (100, "Satisfactory", "#a3c853"),
    (200, "Moderate", "#f4d03f"),
    (300, "Poor", "#f39c12"),
    (400, "Very Poor", "#e74c3c"),
    (10_000, "Severe", "#7e0023"),
]

# Typical winter-peak AQI per city (used to seed the fallback).
_WINTER_PEAK = {
    "delhi": 320, "ghaziabad": 340, "noida": 300, "gurugram": 290,
    "lucknow": 280, "kanpur": 300, "varanasi": 260, "patna": 290,
    "muzaffarpur": 270, "amritsar": 240, "ludhiana": 250, "jaipur": 230,
    "ahmedabad": 200, "pune": 160, "mumbai": 190, "nagpur": 180,
    "kolkata": 230, "hyderabad": 150, "bengaluru": 130, "chennai": 130,
}


def aqi_category(aqi: float):
    for upper, label, color in _BANDS:
        if aqi <= upper:
            return label, color
    return "Severe", "#7e0023"


def _season_factor(month: int) -> float:
    if month in (12, 1, 2):
        return 1.0          # winter peak
    if month in (10, 11):
        return 1.05         # post-monsoon (stubble)
    if month in (7, 8, 9):
        return 0.40         # monsoon washout
    return 0.62             # summer / pre-monsoon (dust)


def _seeded_aqi(city: dict, day_key: str) -> int:
    peak = _WINTER_PEAK.get(city["id"], 160)
    month = int(day_key[5:7])
    base = peak * _season_factor(month)
    # deterministic jitter from city+day so markers are stable within a day
    h = int(hashlib.md5(f"{city['id']}-{day_key}".encode()).hexdigest(), 16)
    jitter = ((h % 1000) / 1000.0 - 0.5) * 0.24  # ±12%
    return max(20, round(base * (1 + jitter)))


def _live_openaq(city: dict):
    """Median PM2.5 from nearby OpenAQ v3 stations -> AQI. Robust to a single
    bad sensor via median + plausibility filter (3-900 ug/m3). None if no
    usable reading (caller then seeds the value)."""
    if not config.HAS_OPENAQ:
        return None
    try:
        headers = {"X-API-Key": config.OPENAQ_API_KEY}
        r = requests.get(
            "https://api.openaq.org/v3/locations",
            params={"coordinates": f"{city['lat']},{city['lon']}",
                    "radius": 25000, "limit": 8, "parameters_id": 2},  # 2 = pm25
            headers=headers, timeout=6,
        )
        r.raise_for_status()
        locs = r.json().get("results", [])
        vals = []
        for loc in locs[:4]:
            lr = requests.get(
                f"https://api.openaq.org/v3/locations/{loc['id']}/latest",
                headers=headers, timeout=6,
            )
            if lr.ok:
                for m in lr.json().get("results", []):
                    v = m.get("value")
                    if v is not None and 3 <= v <= 900:  # drop dead/spiking sensors
                        vals.append(v)
            if len(vals) >= 3:
                break
        if vals:
            vals.sort()
            return pm25_to_aqi(vals[len(vals) // 2])  # median
    except Exception as exc:
        print(f"[aqi] OpenAQ error for {city['id']}: {exc}")
    return None


def pm25_to_aqi(pm: float) -> int:
    """US-EPA style PM2.5 → AQI piecewise conversion (good enough for display)."""
    bp = [(0, 12, 0, 50), (12.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
          (55.5, 150.4, 151, 200), (150.5, 250.4, 201, 300),
          (250.5, 350.4, 301, 400), (350.5, 500.4, 401, 500)]
    for clo, chi, ilo, ihi in bp:
        if clo <= pm <= chi:
            return round((ihi - ilo) / (chi - clo) * (pm - clo) + ilo)
    return 500


def get_city_aqi(city: dict) -> dict:
    day_key = datetime.date.today().isoformat()
    live_aqi = _live_openaq(city)
    if live_aqi is not None:
        aqi, live = live_aqi, True
    else:
        aqi, live = _seeded_aqi(city, day_key), False
    label, color = aqi_category(aqi)
    pm25 = round(aqi * 0.45 + 6)  # display estimate
    return {
        "id": city["id"], "name": city["name"], "lat": city["lat"],
        "lon": city["lon"], "region": city["region"],
        "population_m": city.get("population_m"),
        "aqi": aqi, "pm25": pm25, "category": label, "color": color,
        "live": live,
    }


def get_all_aqi() -> list:
    # Fetch all cities concurrently so the first map paint stays fast even
    # when every city does a live OpenAQ lookup.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(get_city_aqi, config.CITIES))
