"""Satellite fire hotspots via NASA FIRMS (VIIRS). Needs FIRMS_MAP_KEY.

Fallback: a deterministic cluster over the Punjab/Haryana stubble belt plus a
few scattered points, so the map shows orange fire markers and the wind
back-trajectory has something to connect to. Flagged `live: false`.
"""
import csv
import hashlib
import io

import requests

import config

# India bounding box (west, south, east, north)
_BBOX = "68,8,97,37"
_SOURCE = "VIIRS_SNPP_NRT"


def get_fires(max_points: int = 120) -> dict:
    if config.HAS_FIRMS:
        live = _live_firms(max_points)
        if live is not None:
            return {"fires": live, "live": True}
    return {"fires": _seeded_fires(), "live": False}


def _live_firms(max_points: int):
    try:
        url = (f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
               f"{config.FIRMS_MAP_KEY}/{_SOURCE}/{_BBOX}/2")
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        fires = []
        for row in rows:
            try:
                fires.append({
                    "lat": float(row["latitude"]),
                    "lon": float(row["longitude"]),
                    "confidence": row.get("confidence", "n"),
                    "frp": float(row.get("frp", 0) or 0),
                    "date": row.get("acq_date", ""),
                })
            except (ValueError, KeyError):
                continue
        # densest first (by FRP), capped for map performance
        fires.sort(key=lambda f: f["frp"], reverse=True)
        return fires[:max_points]
    except Exception as exc:
        print(f"[fires] FIRMS error: {exc}")
        return None


def _seeded_fires():
    """~50 deterministic points: a dense Punjab/Haryana cluster + scatter."""
    fires = []
    # Punjab/Haryana stubble belt
    for i in range(36):
        h = int(hashlib.md5(f"pb-{i}".encode()).hexdigest(), 16)
        lat = 29.6 + ((h % 1700) / 1000.0)          # 29.6 – 31.3
        lon = 74.6 + (((h // 7) % 2600) / 1000.0)    # 74.6 – 77.2
        fires.append({"lat": round(lat, 3), "lon": round(lon, 3),
                      "confidence": "h", "frp": 8 + (h % 40), "date": "seeded"})
    # scattered: central + east
    for i in range(14):
        h = int(hashlib.md5(f"sc-{i}".encode()).hexdigest(), 16)
        lat = 20 + ((h % 6000) / 1000.0)
        lon = 78 + (((h // 5) % 9000) / 1000.0)
        fires.append({"lat": round(lat, 3), "lon": round(lon, 3),
                      "confidence": "n", "frp": 4 + (h % 18), "date": "seeded"})
    return fires
