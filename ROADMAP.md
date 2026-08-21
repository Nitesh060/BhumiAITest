# Bhumi Platform Roadmap — Closing the Gap Analysis

This continues the codebase's existing phase numbering (Phase 1 = Farmer/Farm
management, Phase 2 = Historical Timeline, Phase 3 = Crop Intelligence,
Phase 4 = Weather/Soil/Terrain, Phase 6 = BCIS Credit Intelligence,
Phase 7 = Insurance Intelligence — see the module docstrings in `Backend/`).
Everything below is **not yet built**; it turns the Gap Analysis into
ordered, actionable phases so each gap has a home instead of sitting as a
single flat list.

Ordering follows the Gap Analysis's own argument: the highest-leverage next
step is unblocking data (ground truth + loan history), because every other
open item — trained models, the full BCIS blend, real crop verification —
is downstream of it. Partnerships/licensing tracks (insurer, IRDAI, input
companies) run in parallel since they're calendar-bound, not engineering-bound.

---

## Phase 8 — Ground Truth & Reference Data (Layer 1 gaps)
Unblocks Phase 11 (trained models) and Phase 12 (full BCIS). Highest priority.

- Wire MSP/mandi price + district crop-area lookups to a working
  `data.gov.in` resource ID per operating state (config-only effort).
- Replace the generic OpenLandMap soil texture layer with an India-specific
  NBSS&LUP soil taxonomy shapefile, uploaded as an Earth Engine asset.
- Design a minimal loan/repayment schema and get it exposed from
  Annapurna's own systems (needs Annapurna IT — the single highest-leverage
  integration in the whole roadmap).
- Build a basic field-officer data-capture flow (yield measurements,
  geo-tagged photos) so labeled farm-seasons start accumulating this season,
  not whenever Phase 11 formally kicks off.

## Phase 9 — Governance & Compliance Foundation
No formal dependency on Phase 8, but must land before Phase 6/Phase 7 move
from pilot to production lending/claims decisions — regardless of how good
the underlying scores are.

- Timestamped consent-recording system (WhatsApp opt-in + separate consent
  per data use: advisory, loan data, insurance data, photo storage).
- Confirm/enforce India-only data residency (current hosting is Render;
  the vision doc specifies AWS `ap-south-1` Mumbai — verify or migrate).
- Standardise the confidence-score/explainability wrapper across every
  model output (several functions already carry ad hoc `note`/`reason`
  fields — generalize the pattern instead of inventing a new one).
- Formal audit-trail + 7-year evidence-retention system for every scored
  loan and auto-freeze event (`AuditLog` already exists in `models.py` —
  this phase is about making its use complete and mandatory, not new schema).
- Start the IRDAI Corporate Agent licence and RBI/DPDP compliance
  sign-off process — legal/compliance track, run in parallel, not blocked
  on any of the above.

## Phase 10 — Copilot: Multilingual & Proactive (Layer 3 gaps)
Independent of Phase 8/9; can start anytime.

- Add a language-selection step + translation pass on WhatsApp replies
  (Hindi/Odia/Telugu/Kannada/Tamil/Marathi) — the existing AI chat backend
  can likely handle this without new infrastructure.
- Add a scheduled job that re-checks each opted-in farmer's latest
  satellite signal and proactively messages on meaningful change (the
  Daily/Weekly/Risk/Market advisory types from the vision doc — today the
  copilot is reactive-only).
- Voice-note support via a speech-to-text integration — scope this only
  once the proactive text advisory above is live.

## Phase 11 — Trained ML Models (Layer 2, replaces heuristics)
Depends on Phase 8 producing labeled farm-seasons. Per the vision doc's own
ICRISAT VDSA benchmark (2,000+ farm-seasons for <15% MAPE), this is a
multi-season effort — current formulas/heuristics (M1 crop ID, M3 yield)
are an honest bridge until then, not a placeholder to rush past.

- Once 200-500 labeled farm-seasons exist: train a real M1 crop-ID model
  (replacing the NDVI-shape heuristic) and a real M3 yield model
  (replacing the national/district-average scaling) — the Gap Analysis's
  own first candidates, in that order.
- M2 (crop health) and M4 (climate risk) are already live/complete signal
  sets and are lower priority for retraining.

## Phase 12 — Full BCIS Signal Blend (Layer 5 completion)
No new BCIS engineering — this phase is entirely "better inputs become
available," triggered automatically once Phase 8 (loan data) and Phase 11
(M1 confidence score) land.

- Add repayment history (16% weight) once Phase 8's loan schema is live.
- Add real crop-verification confidence (16% weight) once Phase 11's M1
  model replaces the heuristic.
- Formula itself (yield stress 28% / crop health 22% / climate risk 18%)
  is already correct and needs no change.

## Phase 13 — Insurance Platform Completion (Layer 6)
Partnerships/licensing-gated, not engineering-gated; the triage logic
already works today as a human-review aid.

- Pilot the existing fraud-triage + claim-assessment logic with a single
  insurer partner before building any marketplace or multi-insurer code.
- Automated payout/settlement — needs that insurer's banking/payment
  integration.
- Multi-insurer marketplace — needs the Phase 9 IRDAI licence plus
  additional insurer partnerships.
- Live in-season monitoring tied to actual issued policies (depends on
  having real policies to monitor, i.e. the pilot above).

## Phase 14 — Photo AI Treatment Marketplace (Layer 4 completion)
A partnerships-and-commerce gap, not a satellite/AI gap — diagnosis quality
via the current general-purpose vision model can be evaluated as-is.

- Sign input-company partners and define referral-margin commercial terms.
- Only then build the SKU catalogue, order flow, and fulfillment — no
  commerce code before the partnerships exist.
- Done: `Backend/plant_disease_model.py` (MobileNetV2, transfer-learned on
  the PlantVillage dataset, 38 classes, ~99.5% validation accuracy) is
  trained and is now the sole model behind `/diagnose` (`app.py`,
  `_diagnose_with_trained_model`) — Gemini is no longer called for photo
  diagnosis at all, by explicit choice. `Backend/train_plant_disease.py`
  remains the retraining path if the checkpoint ever needs updating
  (new classes, more data, a different base dataset) — run it in Colab
  or locally, never as part of a deployed request.
- Still open: remedy/treatment advice and cost estimates, which the
  classifier alone can't produce (it only outputs a disease label +
  confidence) — Gemini used to fill this in. A static disease->remedy
  lookup table would close this without reintroducing Gemini, if wanted.

---

### Suggested sequencing

Phase 8 and Phase 9 should start immediately and in parallel — neither
blocks the other, and both unblock or de-risk everything after them.
Phase 10 can run alongside them at any time. Phases 11-14 are each gated on
a specific upstream phase (11←8, 12←8+11, 13/14←partnerships) rather than
on each other, so they don't need to happen in strict numeric order beyond
that.
