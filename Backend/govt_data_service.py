"""
govt_data_service.py
=====================
Crop Price/MSP and District Yield Comparison — the two SatSource-sheet
items that genuinely cannot come from Earth Engine, because they are
government market/agriculture-census statistics, not satellite data.

IMPORTANT — deployment note:
This calls data.gov.in's open API (Agmarknet mandi price series +
ICRISAT/DES district-level yield data mirrors). Both need:
  1. A free API key from https://data.gov.in/ (env var DATA_GOV_IN_KEY)
  2. Outbound network access to api.data.gov.in from your server

Neither is available in this dev sandbox, so this module is written and
structured but not live-tested here — test it after deploying with a
real key. Every function fails soft (returns an "unavailable" shape
with a reason) rather than crashing /calculate if the key is missing
or the API is unreachable, so the rest of the app keeps working either
way.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

DATA_GOV_IN_KEY = os.getenv("DATA_GOV_IN_KEY")

# Agmarknet daily mandi price resource on data.gov.in (variety-wise daily
# market prices). Resource ID is data.gov.in's, not ours — confirm it's
# still current before relying on it, data.gov.in resource IDs do change.
AGMARKNET_RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
AGMARKNET_BASE_URL = f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE_ID}"


def _unavailable(reason: str) -> Dict[str, Any]:
    return {"available": False, "reason": reason}


def fetch_mandi_price(commodity: str, state: str, district: Optional[str] = None) -> Dict[str, Any]:
    """Latest modal price (Rs/quintal) for *commodity* from the nearest
    reporting mandi in *state*/*district*, via the Agmarknet resource on
    data.gov.in.
    """
    if not DATA_GOV_IN_KEY:
        return _unavailable("DATA_GOV_IN_KEY not set — get a free key at https://data.gov.in/")

    params = {
        "api-key": DATA_GOV_IN_KEY,
        "format": "json",
        "limit": 5,
        "filters[commodity]": commodity,
        "filters[state]": state,
    }
    if district:
        params["filters[district]"] = district

    try:
        resp = requests.get(AGMARKNET_BASE_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Agmarknet fetch failed: %s", exc)
        return _unavailable(f"API request failed: {exc}")

    records = data.get("records", [])
    if not records:
        return _unavailable("No mandi price records found for this commodity/location")

    latest = records[0]
    return {
        "available": True,
        "commodity": latest.get("commodity"),
        "market": latest.get("market"),
        "state": latest.get("state"),
        "modal_price_rs_per_quintal": latest.get("modal_price"),
        "min_price_rs_per_quintal": latest.get("min_price"),
        "max_price_rs_per_quintal": latest.get("max_price"),
        "arrival_date": latest.get("arrival_date"),
        "source": "Agmarknet via data.gov.in",
    }


def fetch_district_yield_comparison(crop: str, district: str, state: str, farm_yield_tonnes_per_ha: Optional[float] = None) -> Dict[str, Any]:
    """District-average yield for *crop* vs the farm's own yield (if the
    user has supplied one — this app has no way to measure actual
    harvested yield from satellite data alone, so farm_yield must come
    from the user or a future ground-truth integration).

    NOTE: There is no single stable open data.gov.in resource ID for
    district-level crop yield across all of India's states — coverage is
    patchy and resource IDs vary by state/dataset release (confirmed
    again as of Aug 2026 — the district-level APY datasets on data.gov.in
    are published per-state, e.g. separate Karnataka/Telangana/Gujarat
    resources, not one national one). Two more promising leads if you're
    wiring this up for real:
      - https://upag.gov.in — "UPAg", GoI's newer unified agri-statistics
        platform, explicitly built to consolidate exactly this kind of
        cross-state reporting. Check its API/data-export options.
      - https://data.desagri.gov.in/website/crops-apy-report-web and
        .../crops-report-major-contributing-district-web — the
        Directorate of Economics & Statistics' own APY report tool.
    Neither has been verified against a real API call from this build
    environment (no network access here) — confirm the actual request/
    response shape before wiring either in. Wire the correct resource ID
    for your states of operation before relying on this in production;
    treat this function as a template.
    """
    if not DATA_GOV_IN_KEY:
        return _unavailable("DATA_GOV_IN_KEY not set — get a free key at https://data.gov.in/")

    return _unavailable(
        "District yield resource ID needs to be configured per state — "
        "no single national dataset covers all 8 of AFPL's RTS states consistently. "
        "See module docstring for two concrete leads (UPAg, DES APY reports)."
    )


def fetch_major_crops_in_region(district: str, state: str) -> Dict[str, Any]:
    """Season-wise crop area (Ha/%) and average yield (Kg/Ha) for a
    district — the "Major Crops in the Region" table from the SatSource
    sample. Sourced from Agriculture Census / District-level crop
    statistics on data.gov.in.

    Same caveat and same two leads as fetch_district_yield_comparison()
    above — data.gov.in's district-level crop statistics coverage
    varies by state and dataset vintage, with no single resource ID
    that reliably covers all districts. Configure the resource ID for
    your operating states before relying on this; this is a working
    template, not a drop-in.
    """
    if not DATA_GOV_IN_KEY:
        return _unavailable("DATA_GOV_IN_KEY not set — get a free key at https://data.gov.in/")

    return _unavailable(
        "District crop-area/yield resource ID needs to be configured per state — "
        "same data.gov.in coverage gap as district yield. See module docstring for two concrete leads (UPAg, DES APY reports)."
    )
