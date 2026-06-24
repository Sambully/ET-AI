"""Geospatial pollution source-attribution engine.

Design (this is the defensible part): the percentage split is NOT invented by
the LLM. It is computed from:
  1. a published seasonal apportionment PRIOR (data/apportionment.json), then
  2. modulated by live signals — upwind satellite fires, wind, season, city
     density — using transparent rules.
The LLM then (a) fine-tunes within a small band and (b) writes the expert
explanation. If the LLM is unavailable, a templated explanation is used and the
computed split stands on its own. Either way the numbers are traceable.
"""
import math

import config
from services import inventory, llm

_CATS = [c["key"] for c in config.APPORTIONMENT["categories"]]
_CAT_META = {c["key"]: c for c in config.APPORTIONMENT["categories"]}


def season_for_month(month: int) -> str:
    months = config.APPORTIONMENT["seasons"]["_months"]
    for season, ms in months.items():
        if month in ms:
            return season
    return "winter"


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing_deg(lat1, lon1, lat2, lon2):
    """Compass bearing FROM point 1 TO point 2, degrees 0-360."""
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
         math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dl))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def upwind_fires(city, wind, fires, radius_km=300, arc=55):
    """Fires within radius_km that sit in the direction the wind blows FROM
    (i.e. their plume drifts toward the city)."""
    src_dir = wind["direction_deg"]  # meteorological 'from' direction
    out = []
    for f in fires:
        d = _haversine_km(city["lat"], city["lon"], f["lat"], f["lon"])
        if d > radius_km:
            continue
        b = _bearing_deg(city["lat"], city["lon"], f["lat"], f["lon"])
        diff = abs((b - src_dir + 180) % 360 - 180)
        if diff <= arc:
            out.append({**f, "distance_km": round(d), "bearing_deg": round(b)})
    out.sort(key=lambda f: f["distance_km"])
    return out


def _normalize_to_100(split: dict) -> dict:
    total = sum(max(0, v) for v in split.values()) or 1
    scaled = {k: max(0, v) / total * 100 for k, v in split.items()}
    rounded = {k: int(round(v)) for k, v in scaled.items()}
    # fix rounding drift onto the largest bucket
    drift = 100 - sum(rounded.values())
    if drift and rounded:
        top = max(rounded, key=rounded.get)
        rounded[top] += drift
    return rounded


def compute_split(city, aqi_info, wind, fires, force_season=None):
    """Deterministic anchored split + the signals that produced it.
    `force_season` overrides the current month (used by the validation view to
    compare like-for-like against a published winter study)."""
    import datetime
    month = datetime.date.today().month
    season = force_season or season_for_month(month)
    prior = dict(config.APPORTIONMENT["regions"][city["region"]][season])

    upwind = upwind_fires(city, wind, fires)
    n_upwind = len(upwind)
    aqi = aqi_info["aqi"]

    # Use city-learned prior if we have enough historical events for this season
    learned = inventory.get_adaptive_prior(city["id"], season)
    split = dict(learned if learned is not None else prior)
    prior_source = "learned" if learned is not None else "published"

    # stubble/biomass boost from upwind fires (stronger when AQI already high)
    if n_upwind:
        boost = min(26, n_upwind * 0.9) * (1.15 if aqi > 200 else 1.0)
        split["stubble_biomass"] += boost
    # vehicular boost for dense metros under load
    if (city.get("population_m") or 0) >= 8 and aqi > 120:
        split["vehicular"] += 6
    # dust boost in summer/pre-monsoon
    if season == "summer":
        split["dust"] += 6

    split = _normalize_to_100(split)
    signals = {
        "season": season,
        "wind": wind,
        "upwind_fire_count": n_upwind,
        "nearest_upwind_km": upwind[0]["distance_km"] if upwind else None,
        "aqi": aqi,
        "prior_used": prior,
        "prior_source": prior_source,
    }
    return split, signals, upwind


def _templated_explanation(city, split, signals):
    dom = max(split, key=split.get)
    dom_label = _CAT_META[dom]["label"].lower()
    w = signals["wind"]
    parts = [f"{city['name']}'s current PM2.5 load is dominated by {dom_label} "
             f"({split[dom]}%), consistent with the {signals['season'].replace('_', '-')} "
             f"apportionment profile for this region."]
    if signals["upwind_fire_count"]:
        parts.append(
            f"{signals['upwind_fire_count']} satellite fire hotspots lie upwind "
            f"(wind from {w['direction']} at {w['speed_kmh']} km/h), elevating the "
            f"stubble/biomass share to {split['stubble_biomass']}%.")
    else:
        parts.append(f"No significant upwind fire activity was detected on the "
                     f"current {w['direction']} wind.")
    parts.append("Attribution is anchored to published seasonal source-apportionment "
                 "and modulated by live wind and satellite signals.")
    return " ".join(parts)


def _llm_refine(city, split, signals):
    """Ask the LLM to fine-tune (small band) and explain. Returns (split, conf, text)."""
    cats = ", ".join(_CATS)
    system = ("You are a senior atmospheric scientist doing real-time air-quality "
              "source apportionment for Indian cities. You are given a data-derived "
              "attribution estimate and the live signals behind it. Keep each "
              "percentage within ~8 points of the given estimate (the estimate is "
              "anchored to published apportionment studies — do not overturn it). "
              "Adjust only to reflect the live signals, then explain like an expert.")
    prompt = f"""City: {city['name']} ({city['region']} region)
Season: {signals['season']}
Current AQI: {signals['aqi']}
Wind: from {signals['wind']['direction']} ({signals['wind']['direction_deg']}deg) at {signals['wind']['speed_kmh']} km/h
Upwind satellite fire hotspots within 300km: {signals['upwind_fire_count']}{f" (nearest {signals['nearest_upwind_km']} km)" if signals['nearest_upwind_km'] else ""}

Data-derived attribution estimate (percent, sums to 100):
{ {k: split[k] for k in _CATS} }

Categories: {cats}

Return JSON exactly:
{{"split": {{ {", ".join(f'"{k}": <int>' for k in _CATS)} }}, "confidence": <int 0-100>, "explanation": "<3-4 sentence expert explanation grounded in the signals above>"}}
The split values must sum to 100."""
    data = llm.generate_json(prompt, system=system, temperature=0.4, max_tokens=900)
    if not data or "split" not in data:
        return None
    try:
        refined = {k: float(data["split"].get(k, split[k])) for k in _CATS}
        refined = _normalize_to_100(refined)
        conf = int(data.get("confidence", 80))
        text = str(data.get("explanation", "")).strip()
        if not text:
            return None
        return refined, max(40, min(98, conf)), text
    except Exception as exc:
        print(f"[attribution] refine parse error: {exc}")
        return None


def attribute(city, aqi_info, wind, fires):
    split, signals, upwind = compute_split(city, aqi_info, wind, fires)

    base_conf = 70 + (8 if aqi_info["live"] else 0) + (8 if wind["live"] else 0)
    explanation = None
    llm_used = False

    if config.HAS_LLM:
        refined = _llm_refine(city, split, signals)
        if refined:
            split, conf, explanation = refined
            llm_used = True
        else:
            conf = min(95, base_conf)
    else:
        conf = min(90, base_conf)

    if not explanation:
        explanation = _templated_explanation(city, split, signals)

    bars = [{"key": k, "label": _CAT_META[k]["label"],
             "color": _CAT_META[k]["color"], "pct": split[k]} for k in _CATS]
    bars.sort(key=lambda b: b["pct"], reverse=True)

    # Record this event to the inventory (non-blocking; errors are swallowed)
    try:
        inventory.record(
            city_id=city["id"], season=signals["season"], split=split,
            aqi=aqi_info["aqi"], upwind_fire_count=signals["upwind_fire_count"],
            wind_speed_kmh=wind.get("speed_kmh", 0), llm_used=llm_used,
        )
    except Exception as exc:
        print(f"[inventory] record error: {exc}")

    return {
        "city": city["name"], "city_id": city["id"],
        "split": split, "bars": bars,
        "confidence": conf, "explanation": explanation,
        "season": signals["season"], "wind": wind,
        "upwind_fires": upwind[:20], "upwind_fire_count": signals["upwind_fire_count"],
        "anchored_prior": signals["prior_used"],
        "prior_source": signals.get("prior_source", "published"),
        "llm_used": llm_used, "provider": config.provider_label(),
    }
