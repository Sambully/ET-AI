"""Action layer: enforcement intelligence, citizen alerts, national brief.

All three use the LLM when available and fall back to deterministic templates
so the demo always produces a usable artefact.
"""
import config
from services import llm
from services.aqi_data import aqi_category

LANGS = {"en": "English", "hi": "Hindi", "ta": "Tamil", "kn": "Kannada",
         "mr": "Marathi", "te": "Telugu", "bn": "Bengali"}

# Dominant-source → enforcement archetype (used for the templated fallback)
_ENFORCE = {
    "vehicular": ("traffic chokepoints & freight depots",
                  "idling trucks, visibly polluting / overage diesel vehicles, PUC non-compliance"),
    "stubble_biomass": ("city fringe & upwind waste-burning sites",
                        "open biomass/garbage burning, landfill fires"),
    "industrial": ("notified industrial clusters",
                   "stack emissions, units running without controls, after-hours operation"),
    "dust": ("active construction & road-works sites",
             "uncovered C&D material, missing wind barriers, no water-spraying"),
    "other": ("mixed-use hotspots", "open burning, generator sets, unpaved roads"),
}


# ── Enforcement ───────────────────────────────────────────────────────────
def enforcement(city, attribution, aqi_info):
    dom = max(attribution["split"], key=attribution["split"].get)
    if config.HAS_LLM:
        data = _llm_enforcement(city, attribution, aqi_info, dom)
        if data:
            return data
    return _templated_enforcement(city, attribution, aqi_info, dom)


def _llm_enforcement(city, attribution, aqi_info, dom):
    system = ("You are an enforcement-prioritisation analyst for an Indian state "
              "pollution control board. Turn source-attribution into a concrete, "
              "evidence-backed inspector deployment plan. Be specific and realistic; "
              "frame as prioritisation intelligence for human officers.")
    prompt = f"""City: {city['name']}
Current AQI: {aqi_info['aqi']} ({aqi_info['category']})
Source attribution: {attribution['split']}
Dominant source: {dom}
Upwind fire hotspots: {attribution['upwind_fire_count']}

Produce a prioritised enforcement plan. Return JSON exactly:
{{"summary": "<1 sentence on where the highest-ROI action is today>",
 "actions": [
   {{"priority": 1, "zone": "<area/type of location>", "inspectors": <int>, "target": "<what to inspect>", "rationale": "<why, tied to the attribution>"}}
 ]}}
Give 3-4 actions, ordered by priority, totalling a realistic number of inspectors for one city-day."""
    data = llm.generate_json(prompt, system=system, temperature=0.5, max_tokens=1100)
    if data and data.get("actions"):
        data["llm_used"] = True
        data["provider"] = config.provider_label()
        return data
    return None


def _templated_enforcement(city, attribution, aqi_info, dom):
    split = attribution["split"]
    loc, look = _ENFORCE[dom]
    insp = 6 if aqi_info["aqi"] > 250 else 4
    actions = [{
        "priority": 1, "zone": loc.title(), "inspectors": insp,
        "target": look,
        "rationale": f"{dom.replace('_', '/')} is the largest contributor "
                     f"({split[dom]}%) — highest-ROI intervention today.",
    }]
    # second action from the runner-up source
    second = sorted(split, key=split.get, reverse=True)[1]
    loc2, look2 = _ENFORCE[second]
    actions.append({
        "priority": 2, "zone": loc2.title(), "inspectors": 2,
        "target": look2,
        "rationale": f"Second contributor ({split[second]}%): {second.replace('_', '/')}.",
    })
    if attribution["upwind_fire_count"]:
        actions.append({
            "priority": 3, "zone": "Upwind district coordination cell",
            "inspectors": 2,
            "target": "flag upwind biomass fires to neighbouring district authorities",
            "rationale": f"{attribution['upwind_fire_count']} satellite fire hotspots "
                         f"detected upwind — cross-jurisdiction action needed.",
        })
    return {
        "summary": f"Focus {city['name']} enforcement on {loc} — "
                   f"{dom.replace('_', '/')} drives {split[dom]}% of today's load.",
        "actions": actions, "llm_used": False, "provider": config.provider_label(),
    }


# ── Citizen alerts ──────────────────────────────────────────────────────────
def citizen_alert(city, aqi_info, lang="hi"):
    lang = lang if lang in LANGS else "hi"
    if config.HAS_LLM:
        text = _llm_alert(city, aqi_info, lang)
        if text:
            return {"lang": lang, "language": LANGS[lang], "text": text,
                    "llm_used": True, "provider": config.provider_label()}
    return {"lang": lang, "language": LANGS[lang],
            "text": _templated_alert(city, aqi_info, lang),
            "llm_used": False, "provider": config.provider_label()}


def _llm_alert(city, aqi_info, lang):
    system = ("You write short, calm, actionable public-health advisories about air "
              "quality for Indian citizens. Plain language a ward officer can send on "
              "WhatsApp. Mention the city, the AQI level in words, who is most at risk "
              "(children, elderly, those with heart/lung conditions, outdoor workers), "
              "and 2-3 concrete precautions.")
    prompt = (f"Write a WhatsApp-ready air-quality health advisory for {city['name']}. "
              f"Current AQI is {aqi_info['aqi']} ({aqi_info['category']}). "
              f"Write ENTIRELY in {LANGS[lang]}. Keep it under 60 words. "
              f"Start with a clear header line. No markdown, no emojis except one warning sign.")
    return llm.generate_text(prompt, system=system, temperature=0.5, max_tokens=400)


def _templated_alert(city, aqi_info, lang):
    aqi, cat = aqi_info["aqi"], aqi_info["category"]
    if lang == "hi":
        return (f"⚠ वायु गुणवत्ता चेतावनी — {city['name']}\n"
                f"आज AQI {aqi} ({_cat_hi(cat)}) है। बुज़ुर्ग, बच्चे और "
                f"सांस/दिल के रोगी घर के अंदर रहें। "
                f"बाहर निकलने पर N95 मास्क पहनें, "
                f"दोपहर में बाहरी काम टालें।")
    # English default
    return (f"⚠ AIR QUALITY ALERT — {city['name']}\n"
            f"Today's AQI is {aqi} ({cat}). Elderly, children and those with "
            f"heart/lung conditions should stay indoors. Wear an N95 mask outdoors, "
            f"keep windows shut, and avoid outdoor work during the afternoon.")


def _cat_hi(cat):
    return {"Good": "अच्छी", "Satisfactory": "संतोषजनक",
            "Moderate": "मध्यम", "Poor": "खराब",
            "Very Poor": "बहुत खराब", "Severe": "गंभीर"}.get(cat, cat)


# ── National brief ──────────────────────────────────────────────────────────
def national_brief(all_aqi):
    ranked = sorted(all_aqi, key=lambda c: c["aqi"], reverse=True)
    top = ranked[:5]
    national_avg = round(sum(c["aqi"] for c in all_aqi) / len(all_aqi))
    if config.HAS_LLM:
        text = _llm_brief(top, national_avg)
        if text:
            return {"text": text, "top": top, "national_avg": national_avg,
                    "llm_used": True, "provider": config.provider_label()}
    return {"text": _templated_brief(top, national_avg), "top": top,
            "national_avg": national_avg, "llm_used": False,
            "provider": config.provider_label()}


def _llm_brief(top, national_avg):
    system = ("You are an analyst preparing the CPCB Director's morning air-quality "
              "brief. Crisp, factual, bureaucratic tone. One short paragraph.")
    lines = "\n".join(f"- {c['name']}: AQI {c['aqi']} ({c['category']})" for c in top)
    prompt = (f"National average AQI across monitored cities: {national_avg}.\n"
              f"Top 5 most polluted right now:\n{lines}\n\n"
              f"Write a 4-6 sentence morning intelligence brief a CPCB Director would "
              f"read: lead with the worst city, note the national average, and flag the "
              f"single priority action for today. No markdown headers.")
    return llm.generate_text(prompt, system=system, temperature=0.5, max_tokens=500)


def _templated_brief(top, national_avg):
    worst = top[0]
    cities = ", ".join(f"{c['name']} ({c['aqi']})" for c in top)
    return (f"National air-quality brief. The monitored-city average AQI stands at "
            f"{national_avg}. {worst['name']} is the most polluted urban centre today "
            f"at AQI {worst['aqi']} ({worst['category']}). Today's five priority cities: "
            f"{cities}. Recommended focus: deploy source-targeted enforcement in the top "
            f"three cities and issue ward-level citizen advisories where AQI exceeds 200.")
