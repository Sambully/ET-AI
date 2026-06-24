"""AirGuard AI — Flask backend.

Serves the single-page command-centre UI and a small JSON API. Results are
cached in-memory (10 min) so repeat clicks during a live demo are instant and
the LLM free-tier quota is conserved.
"""
import os
import threading
import time

from flask import Flask, jsonify, render_template, request

import config
from services import (advisories, aqi_data, attribution, fires, forecast,
                      grap, grid, health, inventory, stubble_prediction, weather)

app = Flask(__name__)

# ── tiny in-memory cache ────────────────────────────────────────────────────
_cache = {}
_TTL = 600  # seconds


def cached(key, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def _all_aqi():
    return cached("aqi:all", aqi_data.get_all_aqi)


def _fires():
    return cached("fires", fires.get_fires)


def _aqi_for(city_id):
    for c in _all_aqi():
        if c["id"] == city_id:
            return c
    return None


def _attribution(city):
    def build():
        aqi_info = _aqi_for(city["id"])
        wind = cached(f"wind:{city['id']}", lambda: weather.get_wind(city))
        return attribution.attribute(city, aqi_info, wind, _fires()["fires"])
    return cached(f"attr:{city['id']}", build)


# ── pages ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ── API ─────────────────────────────────────────────────────────────────────
@app.route("/api/meta")
def api_meta():
    return jsonify({
        "provider": config.provider_label(),
        "llm_provider": config.LLM_PROVIDER,
        "has_llm": config.HAS_LLM,
        "has_openaq": config.HAS_OPENAQ,
        "has_firms": config.HAS_FIRMS,
        "categories": config.APPORTIONMENT["categories"],
        "validation_reference": config.APPORTIONMENT["validation_reference"],
        "n_cities": len(config.CITIES),
    })


@app.route("/api/cities")
def api_cities():
    data = _all_aqi()
    worst = max(data, key=lambda c: c["aqi"])
    avg = round(sum(c["aqi"] for c in data) / len(data))
    any_live = any(c["live"] for c in data)
    return jsonify({"cities": data, "worst": worst, "national_avg": avg,
                    "live": any_live})


@app.route("/api/fires")
def api_fires():
    return jsonify(_fires())


@app.route("/api/city/<city_id>/analysis")
def api_analysis(city_id):
    city = config.get_city(city_id)
    if not city:
        return jsonify({"error": "unknown city"}), 404
    return jsonify(_attribution(city))


@app.route("/api/city/<city_id>/forecast")
def api_forecast(city_id):
    city = config.get_city(city_id)
    if not city:
        return jsonify({"error": "unknown city"}), 404
    fc = cached(f"fc:{city_id}", lambda: forecast.get_forecast(city))
    skill = cached(f"skill:{city_id}", lambda: forecast.skill_vs_persistence(city))
    return jsonify({"forecast": fc, "skill": skill})


@app.route("/api/city/<city_id>/enforcement")
def api_enforcement(city_id):
    city = config.get_city(city_id)
    if not city:
        return jsonify({"error": "unknown city"}), 404
    attr = _attribution(city)
    aqi_info = _aqi_for(city_id)
    return jsonify(cached(f"enf:{city_id}",
                          lambda: advisories.enforcement(city, attr, aqi_info)))


@app.route("/api/city/<city_id>/citizen")
def api_citizen(city_id):
    city = config.get_city(city_id)
    if not city:
        return jsonify({"error": "unknown city"}), 404
    lang = request.args.get("lang", "hi")
    aqi_info = _aqi_for(city_id)
    return jsonify(cached(f"cit:{city_id}:{lang}",
                          lambda: advisories.citizen_alert(city, aqi_info, lang)))


@app.route("/api/city/<city_id>/backtrajectory")
def api_backtrajectory(city_id):
    city = config.get_city(city_id)
    if not city:
        return jsonify({"error": "unknown city"}), 404
    wind = cached(f"wind:{city_id}", lambda: weather.get_wind(city))
    return jsonify(weather.back_trajectory(city, wind))


@app.route("/api/national-brief")
def api_national_brief():
    return jsonify(cached("brief", lambda: advisories.national_brief(_all_aqi())))


@app.route("/api/grid/delhi")
def api_grid_delhi():
    return jsonify(cached("grid:delhi", grid.delhi_grid))


@app.route("/api/validation")
def api_validation():
    """Side-by-side: AirGuard's Delhi WINTER attribution vs a published winter
    study — like-for-like, so the deviation is a real validation signal."""
    def build():
        city = config.get_city("delhi")
        aqi_info = _aqi_for("delhi")
        wind = cached("wind:delhi", lambda: weather.get_wind(city))
        split, _signals, _u = attribution.compute_split(
            city, aqi_info, wind, _fires()["fires"], force_season="winter")
        ref = config.APPORTIONMENT["validation_reference"]
        cats = config.APPORTIONMENT["categories"]
        dev = {c["key"]: abs(split[c["key"]] - ref["split"][c["key"]]) for c in cats}
        mad = round(sum(dev.values()) / len(dev), 1)
        return {"city": "Delhi", "season": "winter", "airguard": split,
                "reference": ref["split"], "deviation": dev, "mean_abs_dev": mad,
                "source": ref["source"], "categories": cats}
    return jsonify(cached("validation", build))


@app.route("/api/city/<city_id>/health")
def api_health_impact(city_id):
    city = config.get_city(city_id)
    if not city:
        return jsonify({"error": "unknown city"}), 404
    aqi_info = _aqi_for(city_id)
    return jsonify(cached(f"health:{city_id}",
                          lambda: health.assess(city, aqi_info)))


@app.route("/api/city/<city_id>/grap")
def api_grap(city_id):
    city = config.get_city(city_id)
    if not city:
        return jsonify({"error": "unknown city"}), 404
    aqi_info = _aqi_for(city_id)
    attr = _attribution(city)
    return jsonify(cached(f"grap:{city_id}",
                          lambda: grap.get_grap_status(city, aqi_info, attr)))


@app.route("/api/grap/national")
def api_grap_national():
    def build():
        all_aqi = _all_aqi()
        results = []
        for c in all_aqi:
            stage_num = grap.detect_stage(c["aqi"])
            if stage_num > 0:
                stage = grap.STAGES[stage_num]
                results.append({
                    "id": c["id"], "name": c["name"],
                    "aqi": c["aqi"], "color": c.get("color", stage["color"]),
                    "lat": c["lat"], "lon": c["lon"],
                    "stage": stage_num, "stage_name": stage["name"],
                    "label": stage["label"], "stage_color": stage["color"],
                })
        results.sort(key=lambda x: x["aqi"], reverse=True)
        return {"triggered": results, "count": len(results),
                "total_cities": len(all_aqi)}
    return jsonify(cached("grap:national", build))


@app.route("/api/inventory")
def api_inventory_national():
    return jsonify(inventory.get_national_summary())


@app.route("/api/inventory/<city_id>")
def api_inventory_city(city_id):
    city = config.get_city(city_id)
    if not city:
        return jsonify({"error": "unknown city"}), 404
    return jsonify(inventory.get_stats(city_id))


@app.route("/api/stubble/predictions")
def api_stubble_predictions():
    fires_list = _fires()["fires"]
    return jsonify(cached("stubble:predictions",
                          lambda: stubble_prediction.predict_burn_events(fires_list)))


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "provider": config.LLM_PROVIDER})


def _prewarm():
    """Warm the cache in the background so the first page load is instant and
    the demo's key cities are already LLM-backed."""
    try:
        _all_aqi(); _fires()
        for cid in ("delhi", "mumbai", "kolkata"):
            city = config.get_city(cid)
            _attribution(city)
            cached(f"fc:{cid}", lambda c=city: forecast.get_forecast(c))
        print("[warm] cache pre-warmed")
    except Exception as exc:
        print(f"[warm] error: {exc}")


if __name__ == "__main__":
    print(f"AirGuard AI — LLM provider: {config.provider_label()}")
    print(f"  OpenAQ live: {config.HAS_OPENAQ} | FIRMS live: {config.HAS_FIRMS}")
    # warm only in the request-serving process (avoid the reloader's parent)
    if not config.FLASK_DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=_prewarm, daemon=True).start()
    app.run(host="127.0.0.1", port=config.PORT, debug=config.FLASK_DEBUG)
