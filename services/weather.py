"""Wind (current) and 72-hour AQI/PM2.5 forecast via Open-Meteo (no API key).

Open-Meteo is free and reliable. Both calls fail soft to a deterministic
synthetic series so the forecast chart and wind back-trajectory always render.
"""
import datetime
import hashlib
import math

import requests

_WIND_URL = "https://api.open-meteo.com/v1/forecast"
_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

_DIR_NAMES = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _dir_name(deg: float) -> str:
    return _DIR_NAMES[round(deg / 22.5) % 16]


def get_wind(city: dict) -> dict:
    """Current wind speed (km/h) + direction (deg, meteorological 'from')."""
    try:
        r = requests.get(_WIND_URL, params={
            "latitude": city["lat"], "longitude": city["lon"],
            "current": "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "kmh", "timezone": "Asia/Kolkata",
        }, timeout=6)
        r.raise_for_status()
        cur = r.json().get("current", {})
        spd = cur.get("wind_speed_10m")
        deg = cur.get("wind_direction_10m")
        if spd is not None and deg is not None:
            return {"speed_kmh": round(spd, 1), "direction_deg": deg,
                    "direction": _dir_name(deg), "live": True}
    except Exception as exc:
        print(f"[weather] wind error for {city['id']}: {exc}")
    # fallback: deterministic NW-ish wind (typical Indo-Gangetic winter flow)
    h = int(hashlib.md5(city["id"].encode()).hexdigest(), 16)
    deg = 290 + (h % 60) - 30
    return {"speed_kmh": round(8 + (h % 12), 1), "direction_deg": deg % 360,
            "direction": _dir_name(deg % 360), "live": False}


def get_forecast(city: dict, hours: int = 72) -> dict:
    """Hourly us_aqi + pm2_5 for the next `hours`. Returns parallel lists."""
    try:
        r = requests.get(_AQ_URL, params={
            "latitude": city["lat"], "longitude": city["lon"],
            "hourly": "pm2_5,us_aqi", "forecast_days": 4,
            "timezone": "Asia/Kolkata",
        }, timeout=8)
        r.raise_for_status()
        h = r.json().get("hourly", {})
        times = h.get("time", [])[:hours]
        aqi = h.get("us_aqi", [])[:hours]
        pm = h.get("pm2_5", [])[:hours]
        if times and any(a is not None for a in aqi):
            aqi = [a if a is not None else 0 for a in aqi]
            pm = [p if p is not None else 0 for p in pm]
            return {"time": times, "aqi": aqi, "pm25": pm, "live": True}
    except Exception as exc:
        print(f"[weather] forecast error for {city['id']}: {exc}")
    return _synthetic_forecast(city, hours)


def _destination(lat, lon, bearing_deg, dist_km):
    """Point reached from (lat,lon) heading `bearing_deg` for `dist_km`."""
    r = 6371.0
    br = math.radians(bearing_deg)
    lat1, lon1 = math.radians(lat), math.radians(lon)
    dr = dist_km / r
    lat2 = math.asin(math.sin(lat1) * math.cos(dr) +
                     math.cos(lat1) * math.sin(dr) * math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br) * math.sin(dr) * math.cos(lat1),
                             math.cos(dr) - math.sin(lat1) * math.sin(lat2))
    return [round(math.degrees(lat2), 4), round(math.degrees(lon2), 4)]


def back_trajectory(city: dict, wind: dict, steps: int = 6, step_km: float = 55):
    """Polyline tracing the air mass back upwind (toward the source). Wind
    `direction_deg` is the 'from' bearing, so we walk along it from the city."""
    bearing = wind["direction_deg"]
    pts = [[city["lat"], city["lon"]]]
    for k in range(1, steps + 1):
        pts.append(_destination(city["lat"], city["lon"], bearing, k * step_km))
    return {"points": pts, "from_direction": wind["direction"],
            "from_deg": bearing, "speed_kmh": wind["speed_kmh"],
            "reach_km": steps * step_km, "live": wind["live"]}


def _synthetic_forecast(city: dict, hours: int) -> dict:
    """Diurnal sine wave around the city's seeded level (worse pre-dawn)."""
    from services.aqi_data import _seeded_aqi
    day_key = datetime.date.today().isoformat()
    base = _seeded_aqi(city, day_key)
    now = datetime.datetime.now()
    times, aqi, pm = [], [], []
    for i in range(hours):
        t = now + datetime.timedelta(hours=i)
        hour = t.hour
        # peak ~6am (inversion), trough ~3pm
        diurnal = math.cos((hour - 6) / 24 * 2 * math.pi) * 0.22
        val = max(20, round(base * (1 + diurnal)))
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        aqi.append(val)
        pm.append(round(val * 0.45 + 6))
    return {"time": times, "aqi": aqi, "pm25": pm, "live": False}
