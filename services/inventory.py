"""Proprietary Emission Inventory — the compounding data moat.

Every pollution attribution event is recorded here. Over time this builds a
city-specific emission inventory that:
  1. Replaces generic published priors with city-learned priors
  2. Tracks prediction accuracy improvement via RMSE-vs-prior benchmarks
  3. Makes AirGuard's model meaningfully more accurate than a new entrant's

Storage: data/inventory.json  (flat JSON, human-readable, no DB required)
Schema per city:
  {
    "city_id": {
      "events": [ { ts, season, split, aqi, upwind_fires, wind_speed, llm_used } ],
      "learned_priors": { "winter": {...}, "summer": {...}, ... },
      "rmse_history": [ { ts, n_events, rmse_vs_base, rmse_vs_learned } ],
      "first_event": "ISO-date",
      "last_event": "ISO-date"
    }
  }
"""
import datetime
import json
import math
import os
import threading

import config

_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "inventory.json")
_LOCK = threading.Lock()

# Minimum events before we trust the learned prior over the published one
_MIN_EVENTS_FOR_LEARNING = 8
# How many events to use for the rolling learned prior (most-recent window)
_WINDOW = 60


# ── I/O helpers ───────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict):
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Core: record an attribution event ────────────────────────────────────────

def record(city_id: str, season: str, split: dict, aqi: int,
           upwind_fire_count: int, wind_speed_kmh: float, llm_used: bool):
    """Append one attribution event and recompute learned priors + RMSE."""
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    event = {
        "ts": ts, "season": season, "split": split,
        "aqi": aqi, "upwind_fires": upwind_fire_count,
        "wind_speed": round(wind_speed_kmh, 1), "llm_used": llm_used,
    }
    with _LOCK:
        data = _load()
        city = data.setdefault(city_id, {
            "events": [], "learned_priors": {},
            "rmse_history": [], "first_event": ts, "last_event": ts,
        })
        city["events"].append(event)
        city["last_event"] = ts

        # Recompute learned priors for this season
        _recompute_priors(city, season)

        # Append RMSE snapshot
        base_prior = config.APPORTIONMENT["regions"].get(
            _city_region(city_id), {}
        ).get(season, {})
        if base_prior:
            rmse_base, rmse_learned = _compute_rmse(city, season, base_prior)
            if rmse_base is not None:
                city["rmse_history"].append({
                    "ts": ts, "season": season,
                    "n_events": len([e for e in city["events"] if e["season"] == season]),
                    "rmse_vs_base": rmse_base,
                    "rmse_vs_learned": rmse_learned,
                })
                # Keep last 200 RMSE snapshots
                if len(city["rmse_history"]) > 200:
                    city["rmse_history"] = city["rmse_history"][-200:]

        _save(data)


def _city_region(city_id: str) -> str:
    city = config.get_city(city_id)
    return city["region"] if city else "north_plains"


def _recompute_priors(city: dict, season: str):
    """Average the most-recent _WINDOW events for this season → learned prior."""
    season_events = [e for e in city["events"] if e["season"] == season]
    if len(season_events) < 2:
        return
    window = season_events[-_WINDOW:]
    cats = list(window[0]["split"].keys())
    learned = {}
    for cat in cats:
        vals = [e["split"].get(cat, 0) for e in window]
        learned[cat] = round(sum(vals) / len(vals), 1)
    # Normalize to 100
    total = sum(learned.values()) or 100
    learned = {k: round(v / total * 100, 1) for k, v in learned.items()}
    city["learned_priors"][season] = learned


def _rmse(a: dict, b: dict, cats: list) -> float:
    """Root mean squared error between two attribution splits."""
    sq = sum((a.get(c, 0) - b.get(c, 0)) ** 2 for c in cats)
    return round(math.sqrt(sq / len(cats)), 2)


def _compute_rmse(city: dict, season: str, base_prior: dict):
    """Compare recent events against base prior and learned prior."""
    season_events = [e for e in city["events"] if e["season"] == season]
    if len(season_events) < 3:
        return None, None
    # Use last 20 events as ground truth (the average IS the learned signal)
    recent = season_events[-20:]
    cats = list(base_prior.keys())
    # Mean of recent events = empirical centre
    empirical = {c: sum(e["split"].get(c, 0) for e in recent) / len(recent) for c in cats}
    rmse_base = _rmse(empirical, base_prior, cats)
    learned = city.get("learned_priors", {}).get(season, base_prior)
    rmse_learned = _rmse(empirical, learned, cats)
    return rmse_base, rmse_learned


# ── Query: get adaptive prior ─────────────────────────────────────────────────

def get_adaptive_prior(city_id: str, season: str) -> dict | None:
    """Return the learned prior if we have enough events, else None (use base)."""
    data = _load()
    city = data.get(city_id, {})
    season_events = [e for e in city.get("events", []) if e["season"] == season]
    if len(season_events) < _MIN_EVENTS_FOR_LEARNING:
        return None
    return city.get("learned_priors", {}).get(season)


# ── Query: stats for the moat dashboard ──────────────────────────────────────

def get_stats(city_id: str) -> dict:
    data = _load()
    city = data.get(city_id, {})
    events = city.get("events", [])
    if not events:
        return _empty_stats(city_id)

    total = len(events)
    by_season = {}
    for e in events:
        by_season.setdefault(e["season"], []).append(e)

    season_summary = []
    for season, evs in by_season.items():
        n = len(evs)
        base = config.APPORTIONMENT["regions"].get(_city_region(city_id), {}).get(season, {})
        learned = city.get("learned_priors", {}).get(season, {})
        rmse_b, rmse_l = _compute_rmse(city, season, base) if base else (None, None)
        season_summary.append({
            "season": season, "events": n,
            "learned": n >= _MIN_EVENTS_FOR_LEARNING,
            "learned_prior": learned,
            "base_prior": base,
            "rmse_vs_base": rmse_b,
            "rmse_vs_learned": rmse_l,
            "improvement_pct": round((rmse_b - rmse_l) / rmse_b * 100, 1)
                if rmse_b and rmse_l and rmse_b > 0 else None,
        })
    season_summary.sort(key=lambda s: s["events"], reverse=True)

    # Overall moat score (0-100): log-scale of total events, capped at 500 = 100
    moat_score = min(100, round(math.log1p(total) / math.log1p(500) * 100))

    # Days since first event
    days_active = 0
    if city.get("first_event"):
        try:
            first = datetime.datetime.fromisoformat(city["first_event"])
            days_active = (datetime.datetime.now() - first).days
        except Exception:
            pass

    # Drift: how much has the learned prior drifted from the published prior?
    drift_summary = []
    for season, evs in by_season.items():
        if len(evs) < _MIN_EVENTS_FOR_LEARNING:
            continue
        base = config.APPORTIONMENT["regions"].get(_city_region(city_id), {}).get(season, {})
        learned = city.get("learned_priors", {}).get(season, {})
        if base and learned:
            for cat in base:
                delta = round(learned.get(cat, 0) - base.get(cat, 0), 1)
                if abs(delta) >= 1.5:
                    drift_summary.append({
                        "season": season, "category": cat, "delta": delta,
                        "base": base.get(cat, 0), "learned": learned.get(cat, 0),
                    })
    drift_summary.sort(key=lambda d: abs(d["delta"]), reverse=True)

    # RMSE trend (last 20 snapshots for chart)
    rmse_trend = city.get("rmse_history", [])[-20:]

    return {
        "city_id": city_id, "total_events": total,
        "days_active": days_active, "moat_score": moat_score,
        "first_event": city.get("first_event", ""),
        "last_event": city.get("last_event", ""),
        "season_summary": season_summary,
        "drift": drift_summary[:8],
        "rmse_trend": rmse_trend,
        "learned_seasons": [s["season"] for s in season_summary
                            if s["events"] >= _MIN_EVENTS_FOR_LEARNING],
        "using_learned_prior": any(s["learned"] for s in season_summary),
    }


def _empty_stats(city_id: str) -> dict:
    return {
        "city_id": city_id, "total_events": 0, "days_active": 0,
        "moat_score": 0, "first_event": "", "last_event": "",
        "season_summary": [], "drift": [], "rmse_trend": [],
        "learned_seasons": [], "using_learned_prior": False,
    }


def get_national_summary() -> dict:
    """Overview across all cities — for the investor/judge dashboard."""
    data = _load()
    cities = []
    total_events = 0
    for city_id, city in data.items():
        n = len(city.get("events", []))
        total_events += n
        events_by_season = {}
        for e in city.get("events", []):
            events_by_season.setdefault(e["season"], 0)
            events_by_season[e["season"]] += 1
        learned = [s for s, cnt in events_by_season.items() if cnt >= _MIN_EVENTS_FOR_LEARNING]
        cities.append({
            "city_id": city_id,
            "name": (config.get_city(city_id) or {}).get("name", city_id),
            "events": n, "learned_seasons": learned,
            "moat_score": min(100, round(math.log1p(n) / math.log1p(500) * 100)),
            "last_event": city.get("last_event", ""),
        })
    cities.sort(key=lambda c: c["events"], reverse=True)
    return {
        "total_events": total_events, "cities_tracked": len(cities),
        "cities": cities,
        "moat_summary": _moat_summary_text(total_events, cities),
    }


def _moat_summary_text(total: int, cities: list) -> str:
    learned_count = sum(1 for c in cities if c["learned_seasons"])
    if total == 0:
        return ("No attribution events recorded yet. Every city analysis you run "
                "starts building your proprietary emission inventory.")
    return (
        f"{total:,} attribution events across {len(cities)} cities. "
        f"{learned_count} {'city has' if learned_count == 1 else 'cities have'} accumulated "
        f"enough data to use city-specific learned priors instead of published anchors. "
        f"Each event sharpens RMSE vs a new entrant using only generic data."
    )
