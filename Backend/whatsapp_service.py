"""
whatsapp_service.py
====================
Lets the same chatbot experience that runs on the Dashboard work over
WhatsApp, using Meta's WhatsApp Cloud API (free API access; free for
the first 1,000 user-initiated "service conversations"/month — see
README note in this file's docstring for the current cost model).

Flow
----
1. Officer opens a WhatsApp chat with your business number and shares
   a location pin (WhatsApp's native "Location" attachment).
2. Webhook receives it, runs the EXACT same `compute_farmscore()` used
   by /calculate, and replies with a text summary (score, grade, top
   factors) — this becomes the "farm_context" for the session.
3. Any text message after that goes through the same
   `generate_chat_reply()` used by the web chatbot, grounded in that
   farm_context — so answers on WhatsApp match answers on the
   Dashboard for the same farm.

Session storage is in-memory (per phone number) and resets on server
restart — good enough for a field-officer tool where each
conversation is short-lived. If you need persistence across restarts,
swap SESSIONS for a Redis/DB-backed store later.

Setup (Meta side) — done once, in the browser, no CLI:
  1. https://developers.facebook.com/apps → Create App → type "Business"
  2. Add product: "WhatsApp"
  3. Under WhatsApp → API Setup you get a temporary access token + a
     test phone number + a "Phone Number ID" — good for testing with
     up to 5 numbers you add yourself, no business verification needed
  4. Set environment variables on your server:
       WHATSAPP_TOKEN         = the access token from that page
       WHATSAPP_PHONE_ID      = the Phone Number ID from that page
       WHATSAPP_VERIFY_TOKEN  = any string you make up (e.g. "farmscore123")
  5. Under WhatsApp → Configuration → Webhook, set:
       Callback URL   = https://<your-backend>/webhook/whatsapp
       Verify Token   = same string as WHATSAPP_VERIFY_TOKEN above
     Click "Verify and Save" — Meta calls the GET handler below to
     confirm you own the URL.
  6. Subscribe to the "messages" field.
  7. For PRODUCTION (talking to any phone number, not just 5 test
     numbers), you'll need Meta Business verification — that's the
     part that takes 2-10 business days, not the API access itself.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

GRAPH_API_VERSION = "v20.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# In-memory per-phone-number session — {wa_id: {"farm_context": dict|None, "history": [...]}}
SESSIONS: Dict[str, Dict[str, Any]] = {}
MAX_HISTORY_TURNS = 12  # keep sessions from growing unbounded


def _get_session(wa_id: str) -> Dict[str, Any]:
    if wa_id not in SESSIONS:
        SESSIONS[wa_id] = {"farm_context": None, "history": []}
    return SESSIONS[wa_id]


def send_whatsapp_text(to: str, text: str) -> bool:
    """Sends a plain text WhatsApp message. Returns True on success."""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        logger.error("WHATSAPP_TOKEN / WHATSAPP_PHONE_ID not configured — cannot send message")
        return False

    url = f"{GRAPH_URL}/{WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    # WhatsApp caps message length well above this, but keep replies
    # readable on a phone screen rather than a giant wall of text.
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
    """Handles Meta's GET verification handshake. Returns the challenge
    string to echo back if valid, else None (caller should 403).
    """
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN and WHATSAPP_VERIFY_TOKEN:
        return challenge
    return None


def _format_score_summary(result: Dict[str, Any]) -> str:
    score = result.get("score")
    grade = result.get("grade")
    coords = result.get("coordinates", {})
    components = result.get("components", {})
    crops = result.get("recommended_crops") or []
    climate = result.get("climate_risk", {})

    lines = [
        f"🌱 *Bhumi AI Score: {score}/900 — {grade}*",
        f"📍 {coords.get('lat')}° N, {coords.get('lng')}° E",
        "",
        "*Key factors:*",
    ]
    for key, c in components.items():
        lines.append(f"• {key.upper()}: {c.get('raw_value')}{c.get('unit','')} → {c.get('sub_score')}/100")

    if crops:
        top_crop = crops[0] if isinstance(crops, list) else None
        if isinstance(top_crop, dict):
            lines.append(f"\n🌾 Recommended crop: {top_crop.get('crop', top_crop.get('name','—'))}")

    yield_pred = result.get("yield_prediction")
    if yield_pred:
        total = f", ~{yield_pred['estimated_total_yield_quintal']} quintal on {yield_pred['area_ha']} ha" if yield_pred.get("estimated_total_yield_quintal") is not None else ""
        lines.append(f"📦 Est. yield: {yield_pred['estimated_yield_kg_per_ha']} kg/ha{total} (formula estimate, not measured)")

    if climate.get("level"):
        lines.append(f"⚠️ Climate risk: {climate['level']}")

    lines.append("\nAsk me anything about this farm, or share a new location to switch farms.")
    return "\n".join(lines)


def handle_incoming_message(payload: Dict[str, Any], compute_farmscore, generate_chat_reply) -> None:
    """Parses one WhatsApp webhook payload and replies. `compute_farmscore`
    and `generate_chat_reply` are passed in (from app.py / gemini_service.py)
    to avoid a circular import — this module doesn't import app.py.
    """
    try:
        entry = (payload.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value = change.get("value", {})
        messages = value.get("messages")
        if not messages:
            return  # status updates (delivered/read) etc. — nothing to do

        msg = messages[0]
        wa_id = msg.get("from")
        msg_type = msg.get("type")
        session = _get_session(wa_id)

        if msg_type == "location":
            loc = msg.get("location", {})
            lat, lng = loc.get("latitude"), loc.get("longitude")
            if lat is None or lng is None:
                send_whatsapp_text(wa_id, "Couldn't read that location pin — please try sharing it again.")
                return

            send_whatsapp_text(wa_id, "📡 Calculating Bhumi AI Score from satellite data — this can take 20-40 seconds…")
            try:
                result = compute_farmscore(float(lat), float(lng), None)
            except Exception:
                logger.exception("compute_farmscore failed from WhatsApp")
                send_whatsapp_text(wa_id, "Sorry, couldn't calculate Bhumi AI Score for that location right now. Please try again shortly.")
                return

            session["farm_context"] = result
            session["history"] = []
            send_whatsapp_text(wa_id, _format_score_summary(result))
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

        # Other message types (image, document, etc.) — not handled yet.
        send_whatsapp_text(wa_id, "I can read text questions and location pins right now. Please send one of those.")

    except Exception:
        logger.exception("Unhandled error processing WhatsApp webhook payload")
