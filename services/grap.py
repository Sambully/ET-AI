"""GRAP Trigger Automation — Graded Response Action Plan.

Detects when a city's AQI crosses CAQM / Supreme Court-mandated GRAP
thresholds and generates a ready-to-sign government order with a
source-attribution-aware enforcement checklist.

Stages:
    I   — Poor      : AQI 201–300
    II  — Very Poor : AQI 301–400
    III — Severe    : AQI 401–450
    IV  — Severe+   : AQI > 450
"""
import datetime
import config
from services import llm

STAGES = {
    1: {"name": "Stage I",   "label": "Poor",      "range": (201, 300), "color": "#f39c12"},
    2: {"name": "Stage II",  "label": "Very Poor",  "range": (301, 400), "color": "#e74c3c"},
    3: {"name": "Stage III", "label": "Severe",     "range": (401, 450), "color": "#c0392b"},
    4: {"name": "Stage IV",  "label": "Severe+",    "range": (451, 9999), "color": "#7e0023"},
}

# Stage-specific restrictions — each stage cumulates the ones below it
_RESTRICTIONS = {
    1: [
        "Ban on open burning of garbage, biomass and crop residue within city limits",
        "Mandatory dust suppression and water sprinkling at all construction sites >500 sqm",
        "Enhanced PUC certificate checks at major traffic intersections",
        "Closure of stone crushers and hot-mix plants operating without a valid NOC",
        "Anti-smog gun deployment at identified dusty road stretches",
        "Real-time stack-emission monitoring — non-compliant units to be served notice immediately",
    ],
    2: [
        "Prohibition on diesel generator sets except for emergency and hospital services",
        "Mechanised road sweeping on all arterial and sub-arterial roads (minimum 2 rounds/day)",
        "Coal- and firewood-based dhabas/eateries to switch to LPG or PNG with immediate effect",
        "Ban on C&D activities not using approved dust-control measures and wind barriers",
        "Only zigzag-technology brick kilns permitted; all others to shut down forthwith",
    ],
    3: [
        "Closure of all brick kilns, hot-mix plants and stone crushers irrespective of fuel type",
        "Ban on BS-III petrol and BS-IV diesel four-wheelers (exemptions: emergency and essential services)",
        "Suspension of all mining activities within and around the city boundary",
        "Immediate closure of industries using coal or biomass as primary fuel source",
        "Traffic Police to evaluate and implement odd-even scheme for private vehicles",
    ],
    4: [
        "Complete ban on entry of trucks into city limits (exemption: essential commodities only)",
        "Closure of all schools, colleges and educational institutions — switch to online mode",
        "Suspension of all non-essential construction including public infrastructure projects",
        "Ban on diesel-fuelled light commercial vehicles within city limits",
        "Minimum 50% work-from-home mandated for employees in government offices",
        "District Magistrates to hold daily emergency review meetings and submit nightly status report",
    ],
}


def detect_stage(aqi):
    """Return GRAP stage number (1–4) or 0 if below threshold."""
    if aqi > 450: return 4
    if aqi > 400: return 3
    if aqi > 300: return 2
    if aqi > 200: return 1
    return 0


def _all_restrictions(stage_num):
    out = []
    for s in range(1, stage_num + 1):
        out.extend(_RESTRICTIONS[s])
    return out


def _enforcement_checklist(stage_num, attribution):
    dom = max(attribution["split"], key=attribution["split"].get)
    checklist = [
        {"item": "Issue public notification via city website, social media and PA vans", "priority": "immediate"},
        {"item": "Activate Emergency Response Team at PCB headquarters", "priority": "immediate"},
        {"item": "Alert District Magistrates across all sub-districts", "priority": "immediate"},
        {"item": "Deploy inspection teams to top-priority emission sources per attribution", "priority": "today"},
        {"item": "Submit 24-hour field inspection report to State PCB by 18:00 hrs", "priority": "today"},
    ]
    source_items = {
        "vehicular": [
            {"item": "Set up PUC check-posts at 10 major traffic intersections", "priority": "today"},
            {"item": "Coordinate with Traffic Police for heavy-vehicle diversion routes", "priority": "today"},
        ],
        "stubble_biomass": [
            {"item": "Alert Revenue Dept / tehsildars for fire watch in upwind districts", "priority": "immediate"},
            {"item": "Deploy rapid-response teams to city-fringe open burning sites", "priority": "today"},
        ],
        "industrial": [
            {"item": "Issue stack-emission compliance notices to top 20 polluting units", "priority": "today"},
            {"item": "Night inspection of industrial clusters for after-hours violations", "priority": "tonight"},
        ],
        "dust": [
            {"item": "Order water tankers to top-10 dusty road stretches immediately", "priority": "today"},
            {"item": "Inspect top 15 construction sites for dust-barrier compliance", "priority": "today"},
        ],
        "other": [
            {"item": "Survey mixed-use zones for open burning and unregistered generator use", "priority": "today"},
        ],
    }
    checklist.extend(source_items.get(dom, []))
    if stage_num >= 2:
        checklist += [
            {"item": "Verify DG-set shutdown at commercial complexes and IT parks", "priority": "today"},
            {"item": "Confirm mechanised sweeper route coverage across arterial network", "priority": "today"},
        ]
    if stage_num >= 3:
        checklist += [
            {"item": "Deploy nakas for BS-III/IV vehicle enforcement on all arterial roads", "priority": "immediate"},
            {"item": "Coordinate brick kiln and hot-mix plant shutdowns with DGTD", "priority": "immediate"},
        ]
    if stage_num >= 4:
        checklist += [
            {"item": "Establish truck-entry checkpoints on all major entry corridors", "priority": "immediate"},
            {"item": "Issue school closure orders to all District Education Officers", "priority": "immediate"},
            {"item": "Notify Chief Secretary for work-from-home order for government offices", "priority": "immediate"},
        ]
    return checklist


def _llm_order(city, aqi_info, attribution, stage, restrictions, date_str, ref_no):
    dom = max(attribution["split"], key=attribution["split"].get)
    restr_text = "\n".join(f"{i+1}. {r}" for i, r in enumerate(restrictions))
    system = (
        "You are a senior official at an Indian State Pollution Control Board drafting "
        "an urgent enforcement order. Formal official English — crisp, authoritative, "
        "actionable. No markdown. This document will be printed and signed by a Commissioner."
    )
    prompt = f"""Draft the body of an official GRAP {stage['name']} ({stage['label']}) implementation order.

Ref No: {ref_no}
Date: {date_str}
City: {city['name']}
Current AQI: {aqi_info['aqi']} ({aqi_info['category']})
Dominant pollution source today: {dom.replace('_', '/')} ({attribution['split'][dom]}%)
Full source breakdown: {attribution['split']}

Restrictions being invoked:
{restr_text}

Write exactly 4 formal paragraphs (plain prose, no numbered lists or bullet points in the body):
1. Declaration that AQI {aqi_info['aqi']} has crossed the GRAP {stage['name']} threshold — emergency measures invoked
2. Priority sector targeting based on today's source attribution (mention the dominant source explicitly)
3. Directions to enforcement agencies: PCB, Traffic Police, Municipal Corporation, District Magistrate
4. Compliance timeline: daily field reports by 18:00 hrs; escalation clause if AQI does not fall within 48 hours

Close with this exact sentence: "Non-compliance will invite action under the Air (Prevention and Control of Pollution) Act, 1981 and Environment Protection Act, 1986."

Under 280 words total."""
    return llm.generate_text(prompt, system=system, temperature=0.3, max_tokens=700)


def _templated_order(city, aqi_info, stage, date_str):
    return (
        f"In exercise of the powers conferred under Section 19 of the Air (Prevention and "
        f"Control of Pollution) Act, 1981, read with the directions of the Hon'ble Supreme Court "
        f"of India, and in view of the Air Quality Index of {city['name']} having reached "
        f"{aqi_info['aqi']} ({aqi_info['category']}) on {date_str} — thereby crossing the GRAP "
        f"{stage['name']} ({stage['label']}) threshold — the following emergency measures are "
        f"invoked with immediate effect under the Graded Response Action Plan framework.\n\n"
        f"Source-attribution intelligence indicates the dominant contribution to today's "
        f"pollution load. Inspection and enforcement resources shall be prioritised accordingly, "
        f"with primary attention to the highest-contributing emission sectors. All field "
        f"commanders are directed to align deployment with the source-attribution briefing "
        f"issued alongside this order.\n\n"
        f"All enforcement agencies — including the Pollution Control Board, Traffic Police, "
        f"Municipal Corporation and the District Magistrate's office — are directed to ensure "
        f"strict compliance with the prescribed restrictions. Field inspections shall be "
        f"conducted on a daily basis and reports submitted to this office by 18:00 hrs each day. "
        f"These directions remain in force until the AQI of {city['name']} falls below the "
        f"{stage['name']} threshold for three consecutive days. Failure to comply will result "
        f"in immediate closure orders and financial penalties under applicable statutes.\n\n"
        f"Non-compliance will invite action under the Air (Prevention and Control of Pollution) "
        f"Act, 1981 and Environment Protection Act, 1986."
    )


def generate_order(city, aqi_info, attribution, stage_num):
    stage = STAGES[stage_num]
    restrictions = _all_restrictions(stage_num)
    date_str = datetime.date.today().strftime("%d %B %Y")
    ref_no = (f"SPCB/GRAP/{datetime.date.today().strftime('%Y-%m-%d')}"
              f"/{city['id'].upper()}/{stage_num}")
    checklist = _enforcement_checklist(stage_num, attribution)

    body = None
    if config.HAS_LLM:
        body = _llm_order(city, aqi_info, attribution, stage, restrictions, date_str, ref_no)

    return {
        "ref_no": ref_no,
        "date": date_str,
        "title": f"GRAP {stage['name']} Implementation Order — {city['name']}",
        "body": body or _templated_order(city, aqi_info, stage, date_str),
        "restrictions": restrictions,
        "checklist": checklist,
        "signatory": f"Member Secretary, {city['name']} Pollution Control Board",
        "llm_used": bool(body),
        "provider": config.provider_label(),
    }


def get_grap_status(city, aqi_info, attribution):
    """Main entry point — full GRAP status + order for a city."""
    stage_num = detect_stage(aqi_info["aqi"])
    if stage_num == 0:
        return {
            "triggered": False,
            "stage": 0,
            "stage_name": "No GRAP",
            "label": aqi_info["category"],
            "color": aqi_info.get("color", "#00b894"),
            "aqi": aqi_info["aqi"],
            "message": (
                f"{city['name']} AQI {aqi_info['aqi']} is below the GRAP Stage I threshold "
                f"(201). No emergency measures required at this time."
            ),
            "order": None,
            "checklist": [],
            "llm_used": False,
            "provider": config.provider_label(),
        }
    stage = STAGES[stage_num]
    order = generate_order(city, aqi_info, attribution, stage_num)
    return {
        "triggered": True,
        "stage": stage_num,
        "stage_name": stage["name"],
        "label": stage["label"],
        "color": stage["color"],
        "aqi": aqi_info["aqi"],
        "message": (
            f"GRAP {stage['name']} TRIGGERED — {city['name']} AQI "
            f"{aqi_info['aqi']} ({stage['label']})"
        ),
        "order": order,
        "checklist": order["checklist"],
        "llm_used": order["llm_used"],
        "provider": order["provider"],
    }
