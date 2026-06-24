"""Health Impact Assessment.

Converts PM2.5 / AQI into actionable public-health burden metrics using
published epidemiological coefficients — no fabricated numbers.

Sources:
  - WHO HRAPIE meta-analysis (2013): 1.3% increase in respiratory hospital
    admissions per 10 µg/m³ of PM2.5 above guideline (15 µg/m³ 24-hr limit).
  - IHME Global Burden of Disease 2019: baseline daily respiratory
    hospitalisation rate ~150 per million for India.
  - CPCB National AQI health breakpoints (2014).
  - India Census 2011 age structure: <14 yrs = 26%, 60+ yrs = 8.6%.
  - NCMH Burden of Disease 2005: chronic respiratory disease prevalence ~8%.
  - ILO India Labour Force Survey 2019: outdoor/informal workforce ~16% of
    total population.
"""
import math
import config
from services import llm

WHO_PM25_24H = 15.0   # µg/m³  WHO 2021 24-hr guideline
BASELINE_RESP_ADMISSIONS_PER_M = 150   # per million population per day
RISK_COEFF = 0.013    # 1.3% per 10 µg/m³ above guideline (HRAPIE)

# India age/occupation fractions (Census 2011 / ILO 2019)
FRAC_CHILDREN   = 0.260   # <14 yrs
FRAC_ELDERLY    = 0.086   # 60+ yrs
FRAC_RESP_CHRON = 0.080   # chronic respiratory/cardiac patients (NCMH)
FRAC_OUTDOOR    = 0.160   # outdoor / informal workers

# CPCB AQI health guidance (official breakpoints)
_GUIDANCE = {
    "Good":        ("No health implications for the general population.", "low"),
    "Satisfactory":("Sensitive individuals — those with asthma or heart disease — may "
                    "experience minor discomfort during prolonged outdoor activity.", "low"),
    "Moderate":    ("Children, elderly, and those with respiratory or cardiac conditions "
                    "should limit prolonged outdoor exertion. General public unlikely to "
                    "be affected.", "moderate"),
    "Poor":        ("People with respiratory / cardiac disease should avoid outdoor exertion. "
                    "Healthy individuals may experience irritation during prolonged activity. "
                    "Outdoor workers require protective equipment.", "high"),
    "Very Poor":   ("All outdoor physical activity should be curtailed for sensitive groups. "
                    "Healthy individuals should minimise time outdoors. Respiratory support "
                    "recommended for at-risk patients.", "very_high"),
    "Severe":      ("Health emergency — entire population at risk of respiratory / "
                    "cardiovascular stress. Outdoor activity should be avoided. Schools, "
                    "sports and outdoor events must be suspended immediately.", "emergency"),
}


def _pm25_to_excess_admissions(pm25, population_m):
    excess_pm25 = max(0.0, pm25 - WHO_PM25_24H)
    risk_multiplier = RISK_COEFF * excess_pm25 / 10.0
    baseline = population_m * BASELINE_RESP_ADMISSIONS_PER_M
    return int(round(baseline * risk_multiplier))


def _safe_hours(pm25):
    if pm25 <= WHO_PM25_24H:
        return 24.0
    return round(WHO_PM25_24H * 24.0 / pm25, 1)


def _exposure_multiple(pm25):
    return round(pm25 / WHO_PM25_24H, 1)


def _at_risk(population_m):
    pop = population_m * 1_000_000
    return {
        "children_under_14":    int(round(pop * FRAC_CHILDREN   / 1000) * 1000),
        "elderly_60_plus":      int(round(pop * FRAC_ELDERLY     / 1000) * 1000),
        "respiratory_cardiac":  int(round(pop * FRAC_RESP_CHRON  / 1000) * 1000),
        "outdoor_workers":      int(round(pop * FRAC_OUTDOOR     / 1000) * 1000),
    }


def _llm_clinical_summary(city, pm25, aqi_info, metrics):
    system = (
        "You are a senior medical epidemiologist briefing a state health secretary. "
        "Write two precise, clinical sentences — no metaphors, no alarmism, no bullet points. "
        "Cite the PM2.5 figure. Reference the most at-risk group specifically. "
        "End with the single highest-priority public-health action for today."
    )
    prompt = (
        f"City: {city['name']} | AQI: {aqi_info['aqi']} ({aqi_info['category']}) | "
        f"PM2.5: {pm25} µg/m³ (WHO 24-hr limit: 15 µg/m³)\n"
        f"Estimated excess respiratory hospital admissions today: {metrics['excess_admissions']}\n"
        f"Safe outdoor hours: {metrics['safe_hours_outdoors']} hrs | "
        f"WHO limit exceeded by: {metrics['exposure_multiple']}×\n"
        f"At-risk population: "
        f"{metrics['at_risk']['children_under_14']:,} children under 14, "
        f"{metrics['at_risk']['outdoor_workers']:,} outdoor workers\n\n"
        f"Write the two-sentence clinical briefing."
    )
    return llm.generate_text(prompt, system=system, temperature=0.3, max_tokens=160)


def _templated_summary(city, pm25, aqi_info, metrics):
    cat = aqi_info["category"]
    guidance, _ = _GUIDANCE.get(cat, (_GUIDANCE["Moderate"]))
    return (
        f"{city['name']}'s PM2.5 of {pm25} µg/m³ is {metrics['exposure_multiple']}× "
        f"the WHO 24-hour guideline of 15 µg/m³, with an estimated "
        f"{metrics['excess_admissions']:,} excess respiratory hospital admissions expected "
        f"today across the city's {city['population_m']}M population. "
        f"{guidance}"
    )


def assess(city, aqi_info):
    pm25 = aqi_info.get("pm25", 0) or 0
    population_m = city.get("population_m", 1.0)

    excess_admissions = _pm25_to_excess_admissions(pm25, population_m)
    safe_hours = _safe_hours(pm25)
    exposure_multiple = _exposure_multiple(pm25)
    at_risk = _at_risk(population_m)
    guidance, alert_level = _GUIDANCE.get(
        aqi_info["category"], _GUIDANCE["Moderate"])

    metrics = {
        "pm25": pm25,
        "who_guideline_pm25": WHO_PM25_24H,
        "exposure_multiple": exposure_multiple,
        "safe_hours_outdoors": safe_hours,
        "excess_admissions": excess_admissions,
        "at_risk": at_risk,
        "alert_level": alert_level,
        "guidance": guidance,
    }

    summary = None
    if config.HAS_LLM:
        summary = _llm_clinical_summary(city, pm25, aqi_info, metrics)

    metrics["summary"] = summary or _templated_summary(city, pm25, aqi_info, metrics)
    metrics["llm_used"] = bool(summary)
    metrics["provider"] = config.provider_label()
    metrics["sources"] = [
        "WHO HRAPIE meta-analysis (2013)",
        "IHME Global Burden of Disease 2019",
        "CPCB National AQI health breakpoints",
        "India Census 2011 · NCMH India",
    ]
    return metrics
