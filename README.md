<div align="center">

# 🛰️ AirGuard AI

### Urban Air Quality Intelligence for Smart-City Intervention

**Not just *what* the air is — *why*, what it'll be, and what to do about it.**

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)
![MapLibre](https://img.shields.io/badge/MapLibre%20GL-4.7-1A2B4A?logo=maplibre&logoColor=white)
![Gemini](https://img.shields.io/badge/Google%20Gemini-2.5%20Flash-4285F4?logo=googlegemini&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-2fe6cf)

*Built for the ET AI Hackathon — Problem #5 · Smart Cities / Environmental Intelligence.*

</div>

---

## The problem

India deployed **900+ Continuous Ambient Air Quality Monitoring Stations**, yet a 2024 CAG audit found only **31%** of cities with that data have any action protocols linked to it. Air pollution drives an estimated **1.67M premature deaths/year**. The data exists; the **intelligence layer to act on it does not**.

AirGuard AI is that layer — fusing monitoring stations, satellite fire data, meteorology and land-use priors into source attribution, hyperlocal forecasts, and ready-to-act enforcement & citizen guidance.

## What it does

| | Feature | Detail |
|---|---|---|
| 🗺️ | **Live command-centre map** | MapLibre GL vector map of 20 cities, AQI-graded glowing markers, pollution heat cloud, live NASA fire hotspots |
| 🧠 | **Anchored source attribution** | % split by source (vehicular / stubble / industrial / dust) — *anchored to published seasonal apportionment* and modulated by live wind + upwind satellite fires. The LLM **explains**, it doesn't invent the numbers |
| 📈 | **72-hour forecast + skill metric** | Open-Meteo forecast with a real **RMSE-vs-persistence** backtest — the accuracy metric the brief asks for |
| 🚨 | **GRAP trigger automation** | Detects GRAP Stage I–IV threshold crossings and auto-generates a **ready-to-sign government order** — formal prose, statutory citations, source-attribution-targeted restrictions, and an enforcement checklist with *immediate / today / tonight* priorities. Closes the action-protocol gap: from data → signed order in one click |
| 🚓 | **Enforcement intelligence** | Prioritised inspector-deployment plan derived from the attribution |
| 🗣️ | **Multilingual citizen alerts** | WhatsApp-ready health advisories in Hindi, English, Tamil, Kannada, Marathi, Telugu |
| 📋 | **National morning brief** | One-page AI summary of the top-5 polluted cities, CPCB-director style |
| 🔬 | **Ground-truth validation** | Side-by-side: AirGuard's Delhi-winter split vs a published study (deterministic, no LLM variance) |
| 📍 | **Hyperlocal ~1 km grid** | IDW interpolation over a Delhi station network — ward-level resolution |
| 🌬️ | **Wind back-trajectory** | Animated trail tracing the air mass back to upwind fire sources |

## Screenshots

> Drop your demo captures into `docs/` and reference them here, e.g.
> `docs/hero.png` (map), `docs/panel.png` (city intelligence), `docs/validation.png`.

## Tech stack

- **Backend:** Python · Flask · in-memory cache + startup pre-warm
- **Frontend:** MapLibre GL JS · Chart.js · vanilla JS (no build step)
- **Intelligence:** Google Gemini (provider-agnostic — Claude/​templated fallbacks)
- **Data:** OpenAQ v3 · Open-Meteo · NASA FIRMS (VIIRS)

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # then add your free Gemini key
python app.py               # → http://127.0.0.1:5000
```

Get a free Gemini key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
**Every key is optional** — without them the app seeds season-aware, deterministic data
(each flagged `seeded` in the UI) so the demo never hard-fails.

## Configuration (`.env`)

| Variable | Purpose | Required |
|---|---|---|
| `GEMINI_API_KEY` | LLM intelligence (attribution, enforcement, alerts, brief) | recommended |
| `GEMINI_MODEL` | default `gemini-2.5-flash` | no |
| `ANTHROPIC_API_KEY` | fallback LLM (used only if Gemini key absent) | no |
| `OPENAQ_API_KEY` | live ground-station AQI | no |
| `FIRMS_MAP_KEY` | live NASA satellite fires | no |

The active provider and which layers are live vs. seeded are shown as pills in the UI.

## Architecture

```
Flask (app.py) ── JSON API + startup cache pre-warm
 ├─ services/llm.py          provider-agnostic LLM (Gemini → Claude → templated)
 ├─ services/aqi_data.py     OpenAQ (median-of-stations) + seasonal fallback
 ├─ services/weather.py      Open-Meteo wind, 72h forecast, back-trajectory
 ├─ services/fires.py        NASA FIRMS hotspots + seeded stubble belt
 ├─ services/attribution.py  anchored prior → live-signal modulation → LLM explain
 ├─ services/advisories.py   enforcement / citizen alerts / national brief
 ├─ services/grap.py         GRAP stage detection → LLM order → checklist
 ├─ services/forecast.py     RMSE-vs-persistence backtest
 ├─ services/grid.py         Delhi ~1 km IDW grid
 └─ data/*.json              cities + seasonal apportionment anchors
Frontend (static/, templates/): MapLibre GL + Chart.js command centre
```

## GRAP Stages

| Stage | Threshold | Restrictions invoked |
|---|---|---|
| **Stage I** | AQI 201–300 (Poor) | Open burning ban, dust suppression, PUC checks, anti-smog guns |
| **Stage II** | AQI 301–400 (Very Poor) | + DG-set ban, mechanised sweeping, coal-based dhabas shutdown |
| **Stage III** | AQI 401–450 (Severe) | + Brick kilns / hot-mix plants closed, BS-III/IV vehicles banned |
| **Stage IV** | AQI > 450 (Severe+) | + Truck entry ban, school closure, construction halt, WFH mandate |

Each order is LLM-generated with formal statutory language, cites the Air Act 1981 and EPA 1986, and names the dominant pollution source explicitly. Without a key the system falls back to a deterministic template — the order is still complete and printable.

## A note on the apportionment anchors

`data/apportionment.json` holds **illustrative** seasonal source-apportionment priors per region.
For a production / submission build, replace them with the **exact figures and citation** from the
specific published study you reference (e.g. SAFAR Delhi, TERI-ARAI) — that turns the attribution
from "plausible" into "validated against ground truth".

## License

[MIT](LICENSE) © 2026 AirGuard AI
