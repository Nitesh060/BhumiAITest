# WhatsApp Integration — Setup Guide

Your existing dashboard chatbot now also works over WhatsApp. Same
brain (`generate_chat_reply`), same FarmScore engine (`compute_farmscore`)
— just a different front door.

## What it does
1. Officer opens WhatsApp, sends your business number a **location pin**.
2. Bot replies "Calculating…", then sends back FarmScore, grade, key
   factors, top crop recommendation, climate risk.
3. Officer can then ask follow-up questions as plain text ("is this
   irrigated?", "what crop should I grow?") — answered by the same AI
   that powers the Dashboard chat, grounded in that farm's real data.
4. Sharing a new location switches to a new farm (session resets).

## Cost
- **API access itself: free**, no monthly fee from Meta.
- Conversations opened by the *user* (which is this whole flow — the
  officer messages first) are **free for the first 1,000/month per
  business**. For an internal team of field officers, this should stay
  free indefinitely. Full pricing: https://developers.facebook.com/docs/whatsapp/pricing

## Setup steps (browser only, no CLI)

1. Go to **https://developers.facebook.com/apps** → **Create App** →
   choose type **"Business"** → name it (e.g. "AFPL FarmScore").
2. On the app dashboard, find **WhatsApp** in the product list → **Set up**.
3. You land on **WhatsApp → API Setup**. Here you'll see:
   - A **temporary access token** (valid 24h — for testing only)
   - A **test phone number** already provided by Meta
   - A **Phone Number ID** (a long number, not the phone number itself)
4. Under **"To"**, click **Manage phone number list** and add your own
   WhatsApp number (for testing — up to 5 numbers allowed without
   business verification).
5. On your backend (Render), go to **Environment** tab and add:
   ```
   WHATSAPP_TOKEN         = <the access token from step 3>
   WHATSAPP_PHONE_ID      = <the Phone Number ID from step 3>
   WHATSAPP_VERIFY_TOKEN  = <make up any string, e.g. farmscore2026>
   ```
   Save — this triggers a redeploy.
6. Back on Meta: **WhatsApp → Configuration** → **Webhook** → **Edit**:
   - Callback URL: `https://<your-render-backend>.onrender.com/webhook/whatsapp`
   - Verify Token: the exact same string you put in `WHATSAPP_VERIFY_TOKEN`
   - Click **Verify and Save** (this calls your `GET /webhook/whatsapp` —
     if it fails, make sure your backend redeployed with the new code
     and env vars first)
7. Still in Configuration, under **Webhook fields**, click **Manage** →
   subscribe to **messages**.
8. Test: from your own WhatsApp (the number you added in step 4), open
   a chat with the test number shown on the API Setup page, send it
   your farm's location pin.

## Important limits (test mode)
- The **temporary access token expires in 24 hours** — for a permanent
  token, see the section below.
- Test mode only works with the up-to-5 numbers you manually added.
- To message ANY phone number (production), you need **Meta Business
  verification** (upload business documents, 2-10 business days) — this
  is the part that has a real wait, not the API access.

## Getting a permanent token (no more daily token updates)

This is free — same API, just a different way of generating the token
so it never expires.

1. Go to **https://business.facebook.com** (Meta Business Suite).
2. Left menu → **Business Settings**.
3. **Users → System Users**.
4. Click **Add** → name it (e.g. "FarmScore Bot") → role **Admin**.
5. On the new system user, click **Add Assets** → select your
   FarmScore app (under Apps) → toggle **Full Control** → **Save Changes**.
6. Click **Generate New Token**:
   - Select your FarmScore app
   - Check permissions: `whatsapp_business_messaging` and
     `whatsapp_business_management`
   - Click **Generate Token**
7. Copy the token shown (you only see it once — copy it now).
8. On Render → your backend service → **Environment** tab → update
   `WHATSAPP_TOKEN` to this new value → Save (redeploys automatically).

That's it — no more 24-hour expiry, no daily token swaps.

## Files added/changed
- `Backend/whatsapp_service.py` — new. Webhook handling, message sending, session management.
- `Backend/app.py` — added `/webhook/whatsapp` (GET verify + POST receive), and refactored `/calculate`'s logic into a reusable `compute_farmscore()` function so both the web app and WhatsApp use identical scoring.
