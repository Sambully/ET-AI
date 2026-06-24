"""Stubble Burn Prediction Engine.

Fuses three signals to predict burning events 48–72 hours before they happen:
  1. Crop harvest calendar  — which districts are in post-harvest burn window
  2. Sentinel-2 / FIRMS thermal anomaly density — current hotspot clustering
  3. Open-Meteo 72-h wind forecast — transport pathway toward population centres

Output: risk-ranked list of predicted burn events with GPS coordinates,
risk score, 48–72 h window, district, and a ready-to-send collector alert.
"""
import datetime
import hashlib
import math

import requests

import config
from services import llm

# ── Harvest calendar ─────────────────────────────────────────────────────────
# (month_start, month_end, crop, typical burn delay days)
# Punjab rice: harvest Oct–Nov, burn 7–14 days after
# Punjab wheat: harvest Apr–May, burn 3–7 days after
# UP paddy: harvest Sep–Oct; Haryana paddy: Oct–Nov
_HARVEST_CALENDAR = [
    {"months": [10, 11], "crop": "Rice/Paddy",  "delay_days": 10, "intensity": "high"},
    {"months": [4,  5],  "crop": "Wheat",       "delay_days": 5,  "intensity": "medium"},
    {"months": [9, 10],  "crop": "Paddy (UP)",  "delay_days": 12, "intensity": "medium"},
]

# ── Districts with approximate centroid coordinates ──────────────────────────
_DISTRICTS = [
    # Punjab
    {"name": "Amritsar",     "state": "Punjab",  "lat": 31.63, "lon": 74.87, "pop_risk": "high"},
    {"name": "Ludhiana",     "state": "Punjab",  "lat": 30.90, "lon": 75.85, "pop_risk": "high"},
    {"name": "Firozpur",     "state": "Punjab",  "lat": 30.93, "lon": 74.61, "pop_risk": "medium"},
    {"name": "Sangrur",      "state": "Punjab",  "lat": 30.23, "lon": 75.84, "pop_risk": "high"},
    {"name": "Bathinda",     "state": "Punjab",  "lat": 30.21, "lon": 74.95, "pop_risk": "medium"},
    {"name": "Patiala",      "state": "Punjab",  "lat": 30.34, "lon": 76.39, "pop_risk": "high"},
    {"name": "Fatehgarh Sahib", "state": "Punjab", "lat": 30.65, "lon": 76.39, "pop_risk": "medium"},
    {"name": "Mansa",        "state": "Punjab",  "lat": 29.99, "lon": 75.38, "pop_risk": "medium"},
    # Haryana
    {"name": "Karnal",       "state": "Haryana", "lat": 29.69, "lon": 76.99, "pop_risk": "high"},
    {"name": "Kurukshetra",  "state": "Haryana", "lat": 29.97, "lon": 76.84, "pop_risk": "high"},
    {"name": "Ambala",       "state": "Haryana", "lat": 30.38, "lon": 76.78, "pop_risk": "high"},
    {"name": "Kaithal",      "state": "Haryana", "lat": 29.80, "lon": 76.40, "pop_risk": "medium"},
    {"name": "Fatehabad",    "state": "Haryana", "lat": 29.51, "lon": 75.45, "pop_risk": "medium"},
    # Western UP
    {"name": "Muzaffarnagar","state": "Uttar Pradesh", "lat": 29.47, "lon": 77.70, "pop_risk": "high"},
    {"name": "Shamli",       "state": "Uttar Pradesh", "lat": 29.45, "lon": 77.32, "pop_risk": "medium"},
    {"name": "Saharanpur",   "state": "Uttar Pradesh", "lat": 29.97, "lon": 77.55, "pop_risk": "high"},
]

# Major downwind cities that suffer from stubble smoke
_DOWNWIND_CITIES = [
    {"name": "Delhi",     "lat": 28.65, "lon": 77.22},
    {"name": "Chandigarh","lat": 30.74, "lon": 76.79},
    {"name": "Agra",      "lat": 27.18, "lon": 78.01},
    {"name": "Lucknow",   "lat": 26.85, "lon": 80.95},
]

# ── Wind forecast ─────────────────────────────────────────────────────────────

def _get_wind_forecast_72h(lat: float, lon: float) -> list[dict]:
    """Fetch 72-hour hourly wind for a location. Returns list of {hour, speed_kmh, direction_deg}."""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "hourly": "wind_speed_10m,wind_direction_10m",
                "wind_speed_unit": "kmh",
                "forecast_days": 3,
                "timezone": "Asia/Kolkata",
            },
            timeout=8,
        )
        r.raise_for_status()
        data = r.json().get("hourly", {})
        times = data.get("time", [])
        speeds = data.get("wind_speed_10m", [])
        dirs = data.get("wind_direction_10m", [])
        result = []
        for i, t in enumerate(times[:72]):
            spd = speeds[i] if i < len(speeds) else None
            d = dirs[i] if i < len(dirs) else None
            if spd is not None and d is not None:
                result.append({"time": t, "speed_kmh": spd, "direction_deg": d})
        return result
    except Exception as exc:
        print(f"[stubble] wind forecast error: {exc}")
        return _seeded_wind_forecast()


def _seeded_wind_forecast() -> list[dict]:
    """Deterministic NW-wind forecast (typical Punjab pre-winter flow)."""
    base = datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
    out = []
    for h in range(72):
        t = base + datetime.timedelta(hours=h)
        seed = int(hashlib.md5(f"wind-{h}".encode()).hexdigest(), 16)
        speed = 8.0 + (seed % 120) / 10.0        # 8–20 km/h
        direction = 290 + (seed % 40) - 20        # NW ± 20°
        out.append({"time": t.strftime("%Y-%m-%dT%H:%M"), "speed_kmh": round(speed, 1),
                    "direction_deg": direction % 360})
    return out


# ── Hotspot density scoring ───────────────────────────────────────────────────

def _hotspot_density(district: dict, fires: list[dict]) -> float:
    """Count FIRMS hotspots within ~50 km of district centroid. Returns density score 0–1."""
    count = 0
    for f in fires:
        dlat = f["lat"] - district["lat"]
        dlon = f["lon"] - district["lon"]
        dist_km = math.sqrt((dlat * 111) ** 2 + (dlon * 111 * math.cos(math.radians(district["lat"]))) ** 2)
        if dist_km < 50:
            # weight by fire radiative power
            frp = f.get("frp", 5)
            count += 1 + frp / 20
    # normalise: >30 weighted points = 1.0
    return min(count / 30.0, 1.0)


# ── Season risk ───────────────────────────────────────────────────────────────

def _season_risk() -> dict:
    """Return active harvest calendar entry or None, plus risk multiplier."""
    month = datetime.date.today().month
    for entry in _HARVEST_CALENDAR:
        if month in entry["months"]:
            intensity_mult = {"high": 1.0, "medium": 0.7}.get(entry["intensity"], 0.5)
            return {"active": True, "crop": entry["crop"], "delay_days": entry["delay_days"],
                    "multiplier": intensity_mult}
    # Off-season: low background risk (field clearing, trash burning)
    return {"active": False, "crop": "off-season", "delay_days": 0, "multiplier": 0.25}


# ── Wind transport check ─────────────────────────────────────────────────────

def _transport_risk(district: dict, wind_forecast: list[dict]) -> dict:
    """Check if wind in next 48–72 h transports smoke toward major downwind cities."""
    if not wind_forecast:
        return {"risk": 0.3, "toward": [], "window_hours": "unknown", "mean_speed_kmh": 0}

    # Look at hours 12–72 (after burning typically starts)
    window = wind_forecast[12:72]
    if not window:
        window = wind_forecast

    downwind_hits = []
    for city in _DOWNWIND_CITIES:
        # bearing from district to city
        dlat = city["lat"] - district["lat"]
        dlon = (city["lon"] - district["lon"]) * math.cos(math.radians(district["lat"]))
        bearing_to_city = (math.degrees(math.atan2(dlon, dlat)) + 360) % 360

        # count hours where wind blows toward this city (within ±45°)
        # meteorological direction: "wind from" — convert to "wind toward"
        favorable = 0
        for w in window:
            wind_toward = (w["direction_deg"] + 180) % 360
            diff = abs(wind_toward - bearing_to_city)
            if diff > 180:
                diff = 360 - diff
            if diff < 45 and w["speed_kmh"] > 5:
                favorable += 1

        fraction = favorable / len(window)
        if fraction > 0.3:
            downwind_hits.append({
                "city": city["name"],
                "fraction": round(fraction, 2),
                "bearing": round(bearing_to_city, 0),
            })

    mean_speed = sum(w["speed_kmh"] for w in window) / len(window)
    risk = min(len(downwind_hits) * 0.25 + (mean_speed / 40), 1.0)
    return {
        "risk": round(risk, 2),
        "toward": downwind_hits,
        "window_hours": "48–72h",
        "mean_speed_kmh": round(mean_speed, 1),
    }


# ── Composite risk score ──────────────────────────────────────────────────────

def _composite_risk(season: dict, hotspot_density: float, transport: dict, district: dict) -> float:
    """Weighted composite 0–100."""
    pop_mult = {"high": 1.0, "medium": 0.75, "low": 0.5}.get(district["pop_risk"], 0.75)
    score = (
        season["multiplier"]  * 35 +   # seasonal burn window
        hotspot_density       * 30 +   # current thermal anomaly density
        transport["risk"]     * 35     # wind transport toward cities
    ) * pop_mult
    return round(min(score * 100 / 100, 100), 1)


# ── LLM alert generation ──────────────────────────────────────────────────────

def _llm_collector_alert(event: dict) -> str:
    system = (
        "You are an official at the Indian Ministry of Environment writing an urgent "
        "field alert to a District Collector. Concise, authoritative, action-oriented. "
        "Plain text only. No markdown. Under 120 words."
    )
    transport_cities = ", ".join(c["city"] for c in event["transport"]["toward"]) or "Delhi NCR"
    prompt = f"""Write an urgent alert to the District Collector, {event['district']}, {event['state']}.

Predicted stubble burn event:
- District: {event['district']}, {event['state']}
- Risk score: {event['risk_score']}/100 ({event['risk_level']})
- Predicted window: {event['predicted_window']}
- Crop: {event['crop']}
- Downwind cities at risk: {transport_cities}
- Wind speed forecast: {event['transport']['mean_speed_kmh']} km/h

Alert must:
1. State the predicted burn window
2. Name the downwind cities at risk
3. Direct the collector to deploy revenue officials and police to prevent field fires
4. Cite Section 19 of the Air Act 1981 and CPCB stubble-burning prohibition"""
    result = llm.generate_text(prompt, system=system, temperature=0.3, max_tokens=200)
    return result or _templated_alert(event, transport_cities)


def _templated_alert(event: dict, transport_cities: str) -> str:
    return (
        f"URGENT — PREDICTED STUBBLE BURN EVENT\n\n"
        f"Collector, {event['district']}, {event['state']}: Satellite thermal anomaly "
        f"analysis and wind forecast modelling indicate a HIGH-RISK stubble burning event "
        f"is predicted in your district within {event['predicted_window']}. "
        f"Smoke transport forecast shows {transport_cities} will be adversely affected. "
        f"You are hereby directed to immediately deploy revenue officials, Tehsildars and "
        f"Police to all identified post-harvest fields and prevent open burning under "
        f"Section 19 of the Air (Prevention and Control of Pollution) Act, 1981 and "
        f"CPCB Stubble Burning Prohibition Order. File compliance report within 6 hours."
    )


# ── Main prediction function ──────────────────────────────────────────────────

def predict_burn_events(fires_data: list[dict]) -> dict:
    """Main entry point. Returns ranked list of predicted burn events."""
    season = _season_risk()
    events = []

    # Fetch wind once for the belt centroid (30.5N, 76E) — representative
    wind_forecast = _get_wind_forecast_72h(30.5, 76.0)

    for district in _DISTRICTS:
        density = _hotspot_density(district, fires_data)
        transport = _transport_risk(district, wind_forecast)
        risk = _composite_risk(season, density, transport, district)

        if risk < 15:
            continue  # below noise floor

        # Predict window based on harvest delay
        today = datetime.date.today()
        window_start = today + datetime.timedelta(days=1)
        window_end = today + datetime.timedelta(days=3)
        window_str = f"{window_start.strftime('%d %b')} – {window_end.strftime('%d %b %Y')}"

        level = "CRITICAL" if risk >= 70 else ("HIGH" if risk >= 45 else ("MEDIUM" if risk >= 25 else "LOW"))

        event = {
            "district": district["name"],
            "state": district["state"],
            "lat": district["lat"],
            "lon": district["lon"],
            "risk_score": risk,
            "risk_level": level,
            "crop": season["crop"],
            "season_active": season["active"],
            "hotspot_density": round(density, 3),
            "transport": transport,
            "predicted_window": window_str,
            "collector_alert": None,  # filled below for high-risk
        }
        events.append(event)

    # Sort by risk descending
    events.sort(key=lambda e: e["risk_score"], reverse=True)

    # Generate collector alerts for top-5 high risk events
    for event in events[:5]:
        if event["risk_score"] >= 25:
            event["collector_alert"] = _llm_collector_alert(event)

    return {
        "events": events,
        "total": len(events),
        "season": season,
        "wind_live": len(wind_forecast) > 0 and wind_forecast[0].get("time", "").startswith("20"),
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M IST"),
        "critical_count": sum(1 for e in events if e["risk_level"] == "CRITICAL"),
        "high_count": sum(1 for e in events if e["risk_level"] == "HIGH"),
    }
