"""
whatsapp_service.py
====================
WhatsApp Cloud API integration for Bhumi AI.

The webhook handler validates Meta's X-Hub-Signature-256 when an
app secret is configured, deduplicates message IDs, and moves the
expensive farm calculation off the HTTP request path so Meta receives
a fast 200 response.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

import requests
from flask import request

logger = logging.getLogger(__name__)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")
GRAPH_API_VERSION = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v20.0")
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

SESSIONS: Dict[str, Dict[str, Any]] = {}
PROCESSED_MESSAGE_IDS: Dict[str, float] = {}
MAX_HISTORY_TURNS = 12
MAX_PROCESSED_IDS = 5000
EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("WHATSAPP_WORKERS", "2")))


def _get_session(wa_id: str) -> Dict[str, Any]:
    if wa_id not in SESSIONS:
        SESSIONS[wa_id] = {"farm_context": None, "history": []}
    return SESSIONS[wa_id]


_ENVIRONMENT = os.getenv("ENVIRONMENT", os.getenv("FLASK_ENV", "development")).lower()


def _signature_valid(raw_body: bytes) -> bool:
    """Validate Meta's signature.

    A missing WHATSAPP_APP_SECRET used to fail OPEN (return True
    unconditionally) — anyone could POST a forged webhook payload as if
    from Meta, with the WhatsApp Copilot processing it (and replying,
    consuming Gemini/Earth-Engine calls) as a genuine user message. In
    production this now fails CLOSED: no secret configured means no
    request is accepted, full stop. The lenient behavior is kept only for
    local/dev (same ENVIRONMENT-gated pattern auth_service.py already
    uses for JWT_SECRET), so a developer without the secret configured
    can still exercise the webhook locally.
    """
    if not WHATSAPP_APP_SECRET:
        if _ENVIRONMENT in {"production", "prod"}:
            logger.error("WHATSAPP_APP_SECRET not configured in production — rejecting webhook request")
            return False
        logger.warning("WHATSAPP_APP_SECRET not configured; webhook signature validation is disabled (non-production only)")
        return True
    supplied = request.headers.get("X-Hub-Signature-256", "")
    if not supplied.startswith("sha256="):
        return False
    expected = hmac.new(WHATSAPP_APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied[7:], expected)


def send_whatsapp_text(to: str, text: str) -> bool:
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        logger.error("WHATSAPP_TOKEN / WHATSAPP_PHONE_ID not configured")
        return False
    url = f"{GRAPH_URL}/{WHATSAPP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    if len(text) > 4000:
        text = text[:3980] + "\n\n…(truncated)"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code >= 300:
            logger.error("WhatsApp send failed [%s]: %s", resp.status_code, resp.text)
            return False
        return True
    except Exception:
        logger.exception("WhatsApp send request failed")
        return False


def verify_webhook(mode: Optional[str], token: Optional[str], challenge: Optional[str]) -> Optional[str]:
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN and WHATSAPP_VERIFY_TOKEN:
        return challenge
    return None


def _format_score_summary(result: Dict[str, Any]) -> str:
    score = result.get("score")
    grade = result.get("grade")
    confidence = result.get("score_confidence") or result.get("confidence")
    coords = result.get("coordinates", {})
    components = result.get("components", {})
    crops = result.get("recommended_crops") or []
    climate = result.get("climate_risk", {})
    lines = [f"🌱 *Bhumi AI Score: {score}/1000 — {grade}*"]
    if confidence:
        lines.append(f"Confidence: {confidence} (provisional until ground-truth calibration)")
    lines += [f"📍 {coords.get('lat')}° N, {coords.get('lng')}° E", "", "*Key factors:*"]
    for key, c in components.items():
        lines.append(f"• {key.upper()}: {c.get('raw_value')}{c.get('unit','')} → {c.get('sub_score')}/100")
    if crops and isinstance(crops, dict):
        primary = crops.get("primary") or {}
        if primary.get("crop"):
            lines.append(f"\n🌾 Recommended crop: {primary['crop']}")
    yield_pred = result.get("yield_prediction")
    if yield_pred:
        total = f", ~{yield_pred['estimated_total_yield_quintal']} quintal on {yield_pred['area_ha']} ha" if yield_pred.get("estimated_total_yield_quintal") is not None else ""
        lines.append(f"📦 Est. yield: {yield_pred['estimated_yield_kg_per_ha']} kg/ha{total} (formula proxy, not measured)")
    if climate.get("level"):
        lines.append(f"⚠️ Climate risk: {climate['level']}")
    lines.append("\nAsk me anything about this farm, or share a new location to switch farms.")
    return "\n".join(lines)


def _process_location(wa_id: str, lat: float, lng: float, compute_farmscore) -> None:
    send_whatsapp_text(wa_id, "📡 Calculating Bhumi AI Score from satellite data — this can take some time…")
    try:
        result = compute_farmscore(lat, lng, None)
        session = _get_session(wa_id)
        session["farm_context"] = result
        session["history"] = []
        send_whatsapp_text(wa_id, _format_score_summary(result))
    except Exception:
        logger.exception("compute_farmscore failed from WhatsApp")
        send_whatsapp_text(wa_id, "Sorry, couldn't calculate Bhumi AI Score for that location right now. Please try again shortly.")


def handle_incoming_message(payload: Dict[str, Any], compute_farmscore, generate_chat_reply) -> None:
    try:
        raw_body = request.get_data(cache=True)
        if not _signature_valid(raw_body):
            logger.warning("Rejected WhatsApp webhook: invalid X-Hub-Signature-256")
            return

        entry = (payload.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value = change.get("value", {})
        messages = value.get("messages")
        if not messages:
            return

        msg = messages[0]
        message_id = msg.get("id")
        if message_id:
            if message_id in PROCESSED_MESSAGE_IDS:
                return
            PROCESSED_MESSAGE_IDS[message_id] = __import__("time").time()
            if len(PROCESSED_MESSAGE_IDS) > MAX_PROCESSED_IDS:
                oldest = sorted(PROCESSED_MESSAGE_IDS, key=PROCESSED_MESSAGE_IDS.get)[:500]
                for key in oldest:
                    PROCESSED_MESSAGE_IDS.pop(key, None)

        wa_id = msg.get("from")
        if not wa_id:
            return
        msg_type = msg.get("type")
        session = _get_session(wa_id)

        if msg_type == "location":
            loc = msg.get("location", {})
            lat, lng = loc.get("latitude"), loc.get("longitude")
            if lat is None or lng is None:
                send_whatsapp_text(wa_id, "Couldn't read that location pin — please try sharing it again.")
                return
            EXECUTOR.submit(_process_location, wa_id, float(lat), float(lng), compute_farmscore)
            return

        if msg_type == "text":
            text = (msg.get("text") or {}).get("body", "").strip()
            if not text:
                return
            history = session.get("history", [])
            try:
                reply = generate_chat_reply(text, history=history, farm_context=session.get("farm_context"))
            except Exception:
                logger.exception("generate_chat_reply failed from WhatsApp")
                reply = None
            if not reply:
                send_whatsapp_text(wa_id, "Sorry, I couldn't generate a reply just now. Please try again.")
                return
            history.append({"role": "user", "text": text})
            history.append({"role": "assistant", "text": reply})
            session["history"] = history[-MAX_HISTORY_TURNS * 2:]
            send_whatsapp_text(wa_id, reply)
            return

        send_whatsapp_text(wa_id, "I can read text questions and location pins right now. Please send one of those.")
    except Exception:
        logger.exception("Unhandled error processing WhatsApp webhook payload")
