"""
gemini_service.py
==================
Two Gemini-powered features, both grounded strictly in real FarmScore
data — never inventing farm-specific numbers:

- generate_insight(context)   → short explanation of a /calculate result
- generate_chat_reply(...)    → chatbot replies (general + farm-specific)

If GEMINI_API_KEY is not set, or any call fails, these return None —
callers treat that as "unavailable this time", never as an error that
should block the rest of the response.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_FALLBACK_MODEL = "gemini-3.5-flash"
REQUEST_TIMEOUT_S = 8


def _gemini_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _call_gemini(model: str, api_key: str, contents: list, system_text: Optional[str] = None,
                  temperature: float = 0.3, max_tokens: int = 220) -> Optional[str]:
    """Single Gemini call. Raises requests exceptions on failure so the
    caller's retry/fallback loop can react to them."""
    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    response = requests.post(
        _gemini_url(model),
        params={"key": api_key},
        json=payload,
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    data = response.json()

    candidates = data.get("candidates") or []
    if not candidates:
        logger.warning("Gemini (%s) returned no candidates: %s", model, data)
        return None

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    return text or None


def _call_with_fallback(api_key: str, contents: list, **kwargs) -> Optional[str]:
    """Try the primary model, then the fallback model on a 404 (usually
    a deprecated/renamed model), never raising — returns None on failure."""
    for model in (GEMINI_MODEL, GEMINI_FALLBACK_MODEL):
        try:
            return _call_gemini(model, api_key, contents, **kwargs)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            logger.warning("Gemini (%s) HTTP error %s: %s", model, status, exc)
            if status == 404 and model != GEMINI_FALLBACK_MODEL:
                continue
            return None
        except requests.exceptions.RequestException as exc:
            logger.warning("Gemini (%s) request failed: %s", model, exc)
            return None
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("Gemini (%s) response parsing failed: %s", model, exc)
            return None
    return None


# ===========================================================================
# 1. AI Insight — one-shot explanation of a /calculate result
# ===========================================================================

def _build_insight_prompt(context: Dict[str, Any]) -> str:
    components = context.get("components", {})
    lines = []
    for key, c in components.items():
        if not c:
            continue
        lines.append(
            f"- {key}: raw value {c.get('raw_value')} {c.get('unit') or ''}, "
            f"sub-score {c.get('sub_score')}/100, weight {c.get('weight')}%, "
            f"source {c.get('source')}"
        )
    components_text = "\n".join(lines) or "No component data available."

    crop = context.get("recommended_crops") or {}
    primary_crop = crop.get("primary", {}).get("crop") if crop.get("primary") else None
    climate = context.get("climate_risk") or {}

    return f"""You are helping a bank loan officer read a farmland suitability report.
Below are the ACTUAL computed values from satellite data. Do not invent,
estimate, or restate any number that is not given below. Do not mention
a specific yield, rupee amount, or percentage that isn't listed here.
Write 3-4 short sentences, plain language, no headers or bullet points.

FarmScore: {context.get('score')}/900 ({context.get('grade')})
Component breakdown:
{components_text}

Top recommended crop: {primary_crop or "not available"}
Climate risk level: {climate.get('level', 'not available')}
Climate risk notes: {', '.join(climate.get('flags', [])) or 'none flagged'}
Surface water index (NDWI): {context.get('ndwi')}

Explain what this means for the land's suitability and what the officer
should keep in mind, using ONLY the numbers above."""


def generate_insight(context: Dict[str, Any]) -> Optional[str]:
    """Return a short grounded explanation of a /calculate result, or
    None if Gemini is unavailable."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("GEMINI_API_KEY not set — skipping AI insight")
        return None

    prompt = _build_insight_prompt(context)
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    return _call_with_fallback(api_key, contents, max_tokens=220)


# ===========================================================================
# 1b. AI Farm Advisor — a proactive, prioritized advisory synthesized from
# ALL available farm data (score, enrichment, crop intelligence, weather/
# soil/terrain), not just an explanation of the score number.
# ===========================================================================

def _build_advisor_prompt(context: Dict[str, Any]) -> str:
    import json
    trimmed = {k: v for k, v in context.items() if v is not None}
    return f"""You are an agriculture advisor. Below is real satellite/data-derived
information about one farm. Write a short, prioritized action list for the
farmer or field officer — grouped into "Immediate (this week)", "This
season", and "Longer-term" — using ONLY the data given. Do not invent any
number, crop name, or risk that isn't in the data. If a section has
nothing actionable, omit it rather than padding it. Keep it under 180
words total, plain language, no markdown headers — just short labeled
paragraphs.

Farm data:
{json.dumps(trimmed, indent=2, default=str)}
"""


def generate_farm_advisor(context: Dict[str, Any]) -> Optional[str]:
    """Synthesizes a prioritized advisory from the full farm data bundle
    (score + enrichment + crop intelligence + weather/soil/terrain, as
    much as the caller has available). Returns None if Gemini is
    unavailable — callers should show the raw data sections instead in
    that case, never fabricate an advisory.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("GEMINI_API_KEY not set — skipping AI farm advisor")
        return None

    prompt = _build_advisor_prompt(context)
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    return _call_with_fallback(api_key, contents, temperature=0.3, max_tokens=320)


# ===========================================================================
# 1c. AI Risk Analysis — synthesizes climate/flood/drought/crop-stress
# signals (each already computed elsewhere in this app) into one
# coherent risk narrative, instead of the caller reading several
# separate risk fields independently.
# ===========================================================================

def _build_risk_prompt(context: Dict[str, Any]) -> str:
    import json
    trimmed = {k: v for k, v in context.items() if v is not None}
    return f"""You are a risk analyst for agricultural lending. Below are several
independently-computed risk signals for one farm (climate risk, flood
risk, historical drought years, crop growth stage/stress). Write a 3-4
sentence risk summary combining them — state the overall risk level
(Low/Moderate/High) and the 1-2 biggest contributing factors. Use ONLY
the data given; do not invent a risk that isn't supported by it. If the
signals conflict, say so plainly rather than picking one arbitrarily.

Risk data:
{json.dumps(trimmed, indent=2, default=str)}
"""


def generate_risk_analysis(context: Dict[str, Any]) -> Optional[str]:
    """Returns None if Gemini is unavailable — callers should fall back
    to showing the individual risk_level fields directly (never fabricate
    a synthesized narrative without the model).
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("GEMINI_API_KEY not set — skipping AI risk analysis")
        return None

    prompt = _build_risk_prompt(context)
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    return _call_with_fallback(api_key, contents, temperature=0.2, max_tokens=180)


# ===========================================================================
# 2. Chatbot — general agriculture Q&A + questions about the current farm
# ===========================================================================

CHAT_SYSTEM_INSTRUCTIONS = """You are FarmScore Assistant, a helpful agriculture and land-suitability
advisor built into a satellite-based farmland assessment tool used by bank
loan officers in India.

You can answer two kinds of questions:
1. General agriculture/satellite questions (e.g. "what is NDVI?", "which
   crops need less water?", "what does groundwater depletion mean?") —
   answer these from general knowledge, plainly and briefly.
2. Questions about the specific farm currently being evaluated — answer
   these ONLY using the "Current farm data" block below, if provided.
   Never invent a score, crop name, distance, or any other number for
   this specific farm that isn't in that block. If the data needed to
   answer isn't in the block, say so plainly instead of guessing.

Keep answers short (2-5 sentences), plain language, no markdown headers."""


def _format_farm_context(farm_context: Optional[Dict[str, Any]]) -> str:
    if not farm_context:
        return "No farm has been calculated yet in this session."

    components = farm_context.get("components", {})
    comp_lines = "\n".join(
        f"- {k}: {v.get('raw_value')} {v.get('unit') or ''} "
        f"(sub-score {v.get('sub_score')}/100, weight {v.get('weight')}%, source {v.get('source')})"
        for k, v in components.items() if v
    )
    crop = (farm_context.get("recommended_crops") or {}).get("primary", {})
    climate = farm_context.get("climate_risk") or {}

    return f"""FarmScore: {farm_context.get('score')}/900 ({farm_context.get('grade')})
Components:
{comp_lines or "not available"}
Top recommended crop: {crop.get('crop', 'not available')} ({crop.get('score', '')}%)
Climate risk: {climate.get('level', 'not available')} — {', '.join(climate.get('flags', [])) or 'no flags'}
Surface water (NDWI): {farm_context.get('ndwi')}
Coordinates: {farm_context.get('coordinates')}"""


def generate_chat_reply(
    message: str,
    history: Optional[list] = None,
    farm_context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Return a grounded chat reply, or None if Gemini is unavailable."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("GEMINI_API_KEY not set — skipping chat reply")
        return None

    contents = []
    for turn in (history or [])[-10:]:  # cap history to keep prompts small
        role = "model" if turn.get("role") == "assistant" else "user"
        text = turn.get("text", "")
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    system_block = CHAT_SYSTEM_INSTRUCTIONS + "\n\nCurrent farm data:\n" + _format_farm_context(farm_context)
    return _call_with_fallback(api_key, contents, system_text=system_block, temperature=0.4, max_tokens=300)


# ===========================================================================
# 3. Crop disease diagnosis — real Gemini vision, not a fabricated model.
#    Always returns an explicit confidence + caveat; never states a
#    diagnosis as certain, and says so plainly when the image is unclear
#    or doesn't look like a plant/crop at all.
# ===========================================================================

DIAGNOSIS_PROMPT = """You are looking at a photo a farmer or bank loan officer uploaded to check
crop health. Examine the image and respond with ONLY a JSON object (no
markdown fences, no extra text) in exactly this shape:

{
  "is_plant": true or false,
  "crop_guess": "best guess at the crop/plant, or null if unclear",
  "category": "disease" | "pest" | "nutrient_deficiency" | "weed" | "healthy" | null,
  "diagnosis": "the disease/pest/deficiency you observe, or 'No obvious issue detected' if the plant looks healthy, or null if you cannot tell",
  "confidence": "High" | "Medium" | "Low",
  "symptoms_observed": ["short phrase", "short phrase"],
  "remedy_steps": ["short actionable step", "short actionable step"],
  "approx_cost_inr": "a rough cost range for the remedy in Indian Rupees (e.g. '₹300-500/acre'), or null if not applicable (e.g. for cultural practices with no direct cost, or if healthy)",
  "caveat": "one sentence reminding the user this is an AI estimate, not a substitute for a local agricultural extension officer or plant pathologist, especially before applying any chemical treatment"
}

Rules:
- If the image is not a plant/crop/leaf at all, set is_plant to false and
  leave the other diagnostic fields null/empty, but still explain briefly
  in "diagnosis" what the image actually shows.
- Never invent a confident diagnosis from a blurry, dark, or ambiguous
  photo — use "Low" confidence and say what would help (a clearer photo,
  a close-up of the affected leaf, etc.) in remedy_steps instead.
- Keep remedy_steps practical and low-cost where possible (cultural/
  mechanical control before chemical), and never recommend a specific
  banned or restricted-use pesticide."""


def _rule_based_spectral_advice(spectral: Dict[str, Any]) -> Dict[str, str]:
    """Deterministic fallback so the Spectral Intelligence module works
    fully even without a Gemini API key — derived only from the numbers
    already computed in spectral_service, never invented."""
    idx = spectral.get("indices", {})
    nitrogen_sc = (idx.get("nitrogen") or {}).get("sub_score", 50)
    moisture_sc = (idx.get("moisture_stress") or {}).get("sub_score", 50)
    stress_sc = (idx.get("stress_risk") or {}).get("sub_score", 50)
    chlorophyll_sc = (idx.get("chlorophyll") or {}).get("sub_score", 50)

    if moisture_sc < 40:
        irrigation = "Canopy moisture is below optimal — increase irrigation frequency and check for uneven water distribution across the plot."
    elif moisture_sc < 60:
        irrigation = "Moisture levels are adequate but not optimal — monitor before the next growth stage and irrigate if rainfall is delayed."
    else:
        irrigation = "Canopy moisture is healthy — maintain the current irrigation schedule."

    if nitrogen_sc < 40:
        fertilization = "Red-edge signal suggests nitrogen deficiency — consider a split nitrogen top-dressing and re-check canopy response in 2-3 weeks."
    elif nitrogen_sc < 60:
        fertilization = "Nitrogen status is moderate — a light top-dressing may help if the crop is entering a high-demand growth stage."
    else:
        fertilization = "Nitrogen status looks healthy from the red-edge signal — no immediate correction indicated."

    if stress_sc < 40 or chlorophyll_sc < 40:
        crop_mgmt = "Vigor/moisture/chlorophyll signals are inconsistent or low — a field visit is recommended to rule out pest, disease, or localized water stress before the next input decision."
    else:
        crop_mgmt = "No major stress inconsistency detected — continue routine scouting and re-run this analysis after the next clear-sky satellite pass."

    return {
        "irrigation_advice": irrigation,
        "fertilization_advice": fertilization,
        "crop_management_advice": crop_mgmt,
    }


def generate_spectral_insight(spectral: Dict[str, Any]) -> Dict[str, str]:
    """Return irrigation/fertilization/crop-management guidance grounded
    in the spectral_service output. Uses Gemini when available; always
    falls back to a deterministic rule-based summary otherwise, so this
    never returns None (the module stays fully functional offline)."""
    fallback = _rule_based_spectral_advice(spectral)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return fallback

    idx = spectral.get("indices", {})
    lines = [
        f"- {v.get('label')}: {v.get('index')}={v.get('raw_value')}, sub-score {v.get('sub_score')}/100 ({v.get('status')})"
        for v in idx.values() if v
    ]
    prompt = f"""You are an agronomy advisor reading a satellite-derived Spectral
Health report for a single farm plot (Sentinel-2 multispectral proxies,
not true hyperspectral). Using ONLY the numbers below, respond with ONLY
a JSON object (no markdown fences) shaped exactly:
{{"irrigation_advice": "...", "fertilization_advice": "...", "crop_management_advice": "..."}}
Each value: 1-2 short plain-language sentences. Do not invent numbers not listed below.

Spectral Health Score: {spectral.get('spectral_score')}/100 ({spectral.get('grade')})
{chr(10).join(lines)}
Flags: {', '.join(spectral.get('flags', [])) or 'none'}"""

    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    raw = _call_with_fallback(api_key, contents, temperature=0.3, max_tokens=260)
    if not raw:
        return fallback

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned[4:] if cleaned.lower().startswith("json") else cleaned
        parsed = json.loads(cleaned)
        return {
            "irrigation_advice": parsed.get("irrigation_advice") or fallback["irrigation_advice"],
            "fertilization_advice": parsed.get("fertilization_advice") or fallback["fertilization_advice"],
            "crop_management_advice": parsed.get("crop_management_advice") or fallback["crop_management_advice"],
        }
    except (json.JSONDecodeError, ValueError):
        logger.warning("Spectral insight: unparseable Gemini response, using rule-based fallback")
        return fallback


def diagnose_crop_image(image_bytes: bytes, mime_type: str) -> Optional[Dict[str, Any]]:
    """Return a structured diagnosis dict, or None if Gemini is unavailable
    or the response couldn't be parsed. Never fabricates confidence —
    the prompt explicitly requires Gemini to say when it's unsure.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("GEMINI_API_KEY not set — skipping crop diagnosis")
        return None

    b64_data = base64.b64encode(image_bytes).decode("ascii")
    contents = [{
        "role": "user",
        "parts": [
            {"text": DIAGNOSIS_PROMPT},
            {"inline_data": {"mime_type": mime_type, "data": b64_data}},
        ],
    }]

    for model in (GEMINI_MODEL, GEMINI_FALLBACK_MODEL):
        try:
            raw = _call_gemini(model, api_key, contents, temperature=0.2, max_tokens=500)
            if raw is None:
                continue
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                cleaned = cleaned[4:] if cleaned.lower().startswith("json") else cleaned
            return json.loads(cleaned)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            logger.warning("Gemini diagnosis (%s) HTTP error %s: %s", model, status, exc)
            if status == 404 and model != GEMINI_FALLBACK_MODEL:
                continue
            return None
        except requests.exceptions.RequestException as exc:
            logger.warning("Gemini diagnosis (%s) request failed: %s", model, exc)
            return None
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Gemini diagnosis (%s) returned unparseable JSON: %s", model, exc)
            return None

    return None
