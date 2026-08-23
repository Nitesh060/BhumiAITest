"""
app.py
======
Flask REST API for the FarmScore agricultural-suitability platform.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import logging
import os
import sys
import time
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, request, Response
from flask_cors import CORS

import io

from earth_engine_service import fetch_farm_data, initialise_earth_engine, fetch_farm_location_thumbnail
from scoring import calculate_score
from crop_recommendation import recommend_crop
from gemini_service import generate_insight, generate_chat_reply, generate_spectral_insight, generate_farm_advisor, generate_risk_analysis

try:
    # Real trained-model cross-check for /diagnose, alongside Gemini's
    # general-purpose vision call — see ROADMAP.md Phase 14 and
    # plant_disease_model.py. Guarded because torch/torchvision are a
    # real deploy-size/memory cost (this app has hit a Render free-tier
    # OOM from oversized deps once before — see requirements.txt) —
    # if the import ever fails in some environment, /diagnose should
    # still work with Gemini alone rather than the whole app failing to
    # start.
    import plant_disease_model
    from PIL import Image as PILImage
except Exception:
    plant_disease_model = None
    PILImage = None
    logging.getLogger(__name__).warning(
        "plant_disease_model unavailable (torch/torchvision/Pillow not installed?) "
        "— trained-model diagnosis cross-check disabled, Gemini diagnosis still works"
    )
from spectral_service import calculate_spectral_intelligence
from enrichment_service import (
    fetch_soil_type,
    fetch_adjacent_land_cover,
    fetch_cropping_intensity,
    fetch_irrigation_signal,
    fetch_temperature_annual_range,
    fetch_prosperity_proxy,
    fetch_nearest_water_body_signal,
    estimate_agro_ecological_zone,
    fetch_cropping_history,
    fetch_drought_instances,
    fetch_village_population,
    fetch_topography,
    fetch_ndvi_heatmap,
)
from govt_data_service import fetch_mandi_price, fetch_district_yield_comparison, fetch_major_crops_in_region
from glossary import GLOSSARY_TERMS
from pdf_report import generate_pdf_report
import whatsapp_service
from yield_prediction import estimate_yield, compute_polygon_area_ha
import db as db_module
import farm_management_service as fms
import auth_service
import governance_service
import ground_truth_service
from spectral_indices import fetch_extended_indices, fetch_sar_moisture
from historical_timeline_service import fetch_ndvi_historical_timeline, fetch_before_after_comparison
from enrichment_service import fetch_vegetation_heatmap
from crop_intelligence_service import (
    identify_crop_heuristic,
    identify_crop_history,
    detect_growth_stage,
    estimate_sowing_harvest,
    detect_crop_rotation,
    CROP_CALENDAR,
)
from seasonal_score_service import compute_farmscore as compute_farmscore_from_seasons
from enrichment_service import fetch_cropping_intensity as _fetch_cropping_intensity_for_ci
from enrichment_service import fetch_cropping_history as _fetch_cropping_history_for_ci
from weather_soil_terrain_service import (
    fetch_historical_weather,
    fetch_soil_health,
    fetch_soil_moisture,
    fetch_flood_risk,
)
from enrichment_service import fetch_topography as _fetch_topography_for_flood
from spectral_indices import fetch_sar_moisture as _fetch_sar_for_flood
from spectral_indices import fetch_extended_indices
from weather_indices_service import fetch_solar_radiation, fetch_spi, fetch_gdd, fetch_spei
from comprehensive_score_service import compute_comprehensive_score, DEFAULT_WEIGHTS
from credit_intelligence_service import (
    estimate_income,
    compute_bcis_score,
    recommend_loan_ceiling,
    auto_freeze_check,
)
from insurance_intelligence_service import (
    verify_acreage,
    verify_crop,
    estimate_loss,
    detect_fraud_signals,
    assess_claim,
)
from historical_timeline_service import fetch_before_after_comparison as _fetch_before_after_for_claim
from crop_intelligence_service import identify_crop_heuristic as _identify_crop_for_claim
from seasonal_data_service import fetch_seasonal_comprehensive_data

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
PORT = int(os.getenv("PORT", 5000))
HOST = os.getenv("HOST", "0.0.0.0")
DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Restricted to a real allowlist (was `origins: "*"`, letting any site on the
# internet trigger the satellite/Gemini-backed endpoints below from a
# browser). ALLOWED_ORIGIN accepts one or more comma-separated origins — the
# same env var wsgi.py's security middleware already reads, so there is one
# source of truth instead of two independently-configured CORS policies.
# Falls back to the known production frontend origin (not "*") if unset, so
# a missing env var narrows access instead of silently opening it wide.
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGIN", "").split(",") if o.strip()]
if not _allowed_origins:
    _allowed_origins = ["https://bhumiaitest-1.onrender.com"]
    logger.warning(
        "ALLOWED_ORIGIN not set — defaulting CORS to %s. Set ALLOWED_ORIGIN "
        "(comma-separated for multiple) to override.", _allowed_origins[0],
    )
CORS(app, resources={r"/*": {"origins": _allowed_origins}})

try:
    db_module.init_db()
except Exception:
    logger.exception("init_db() failed at startup — Farm Management endpoints will report 'not configured'")


@app.before_request
def _ensure_ee_init():
    """Best-effort Earth Engine init before each request.

    This used to `raise` here for EVERY request except /health, which meant
    ANY Earth Engine problem (bad/missing credentials, quota, a slow cold
    start) took down the entire API — including endpoints that never touch
    satellite data at all: /report/pdf (lays out an already-computed JSON
    payload), /glossary, /mandi-price, /major-crops, /auth/*, all farm
    management CRUD, /insurance-claim, /credit-intelligence, /admin/*,
    /portfolio/summary, /audit-log, consent and loan endpoints. That's why
    "PDF report generate nahi ho pa raha" could happen even though PDF
    generation itself never calls Earth Engine.

    The actual satellite-backed endpoints (/calculate, /spectral,
    /crop-intelligence, /historical-timeline, etc.) already call Earth
    Engine functions inside their own try/except and return a clear
    502/503 JSON error if that fails — same fail-soft pattern used
    throughout this codebase (see the per-parameter _safe()/_safe_score_fetch()
    wrappers in compute_farmscore). So we no longer need a hard app-wide
    gate here: log the failure and let the request proceed; only routes
    that genuinely need Earth Engine will be affected, and they already
    report that clearly to their caller.
    """
    try:
        initialise_earth_engine()
    except Exception as exc:
        logger.error("Earth Engine init failed (non-fatal for this request): %s", exc)


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "service": "FarmScore API"}), 200


# /credit-intelligence trusts the `score` field of whatever /calculate-shaped
# object the client sends it (by design — it never recomputes score/yield/
# climate_risk, only combines what's already there), and feeds it straight
# into compute_bcis_score/recommend_loan_ceiling/auto_freeze_check. Nothing
# stopped a caller from just inventing a high score to inflate their own
# recommended loan ceiling. Rather than recomputing the score server-side
# (an expensive Earth-Engine round trip on every credit-intelligence call —
# exactly what this design was built to avoid), /calculate now signs its own
# score with a server-held secret; /credit-intelligence requires and verifies
# that signature before trusting the score at all. The Frontend already
# spreads the entire /calculate response verbatim into its /credit-intelligence
# request body, so the signature travels through with no Frontend change.
SCORE_SIGNATURE_MAX_AGE_S = int(os.getenv("SCORE_SIGNATURE_MAX_AGE_S", str(24 * 3600)))


def _sign_score(score, lat, lng, issued_at: float) -> str:
    msg = f"{score}:{round(float(lat), 5)}:{round(float(lng), 5)}:{int(issued_at)}"
    return hmac_lib.new(auth_service.JWT_SECRET.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_score_signature(body: dict) -> bool:
    sig = body.get("_score_sig")
    issued_at = body.get("_score_sig_ts")
    score = body.get("score")
    coords = body.get("coordinates") or {}
    lat, lng = coords.get("lat"), coords.get("lng")
    if not sig or issued_at is None or score is None or lat is None or lng is None:
        return False
    try:
        if time.time() - float(issued_at) > SCORE_SIGNATURE_MAX_AGE_S:
            return False
        expected = _sign_score(score, lat, lng, issued_at)
    except (TypeError, ValueError):
        return False
    return hmac_lib.compare_digest(sig, expected)


# ---------------------------------------------------------------------------
# Seasonal (Kharif + Rabi) data pipeline — the real, live data source for
# most of the parameters below.
# ---------------------------------------------------------------------------
# This used to live in wsgi.py, which reassigned these exact names on
# THIS module's own globals from the outside, after import — invisible
# to anyone reading this file top-to-bottom, or reading
# weather_indices_service.py/spectral_service.py/spectral_indices.py on
# their own and reasonably assuming those are what run in production.
# That hiddenness is exactly why an earlier fix to
# weather_indices_service.py's SPI/solar-radiation logic had zero
# effect on the live site: those functions were never actually being
# called by /calculate or /comprehensive-score, only their seasonal
# replacements below were. Moved here, explicit, so the real call graph
# matches what this file appears to do.
#
# Every parameter below is recomputed as an average of the latest
# available Kharif and Rabi seasons (see seasonal_data_service.py)
# rather than the single rolling window the "real" per-parameter
# functions imported above use. This blended vector now feeds only the
# non-score uses (crop recommendation, climate risk, data-availability
# reasons) — the FarmScore itself is computed from Kharif and Rabi
# separately (see compute_farmscore's Base+Kharif+Rabi combination,
# below, via seasonal_score_service.compute_farmscore).
#
# SPEI is a deliberate exception: it keeps using the real
# weather_indices_service.fetch_spei (CSIC SPEIbase, falling back to a
# Thornthwaite proxy) rather than a seasonal average, because that's a
# strictly better implementation (proper FAO-56 Penman-Monteith PET)
# than the seasonal pipeline's Thornthwaite-only version. wsgi.py used
# to also define a seasonal SPEI wrapper, but bound it to the wrong
# module attribute name (fetch_spei_proxy instead of the fetch_spei this
# file actually imports and calls) — that mismatch meant it silently
# never ran, which is *why* SPEI kept working correctly throughout all
# of this, by accident rather than by design. Not repeating that
# function here at all, so there is no dead code standing in for a
# decision that was never really made on purpose.
from collections import OrderedDict as _OrderedDict

_season_cache: "_OrderedDict" = _OrderedDict()
_SEASON_CACHE_MAX = 50
_original_fetch_farm_data = fetch_farm_data


def _season_key(lat, lng, polygon):
    return (round(float(lat), 5), round(float(lng), 5), str(polygon) if polygon else "")


def _get_seasonal(lat, lng, polygon=None):
    key = _season_key(lat, lng, polygon)
    if key not in _season_cache:
        _season_cache[key] = fetch_seasonal_comprehensive_data(lat, lng, polygon)
        if len(_season_cache) > _SEASON_CACHE_MAX:
            _season_cache.popitem(last=False)  # evict oldest entry
    else:
        _season_cache.move_to_end(key)  # keep recently-used entries alive longest
    return _season_cache[key]


def _seasonal_farm_data(lat, lng, polygon=None):
    base = _original_fetch_farm_data(lat, lng, polygon)
    seasonal = _get_seasonal(lat, lng, polygon)
    raw = seasonal["raw_values"]
    rainfall = raw.get("rainfall")
    lst = raw.get("lst")
    base.update({
        "ndvi": raw.get("ndvi"), "ndmi": raw.get("ndmi"), "ndwi": raw.get("ndwi"),
        "rainfall": rainfall,
        # `rainfall_reason` used to be left over from `base`'s own real,
        # non-seasonal rainfall fetch above — if that one succeeded (reason
        # correctly None) but the seasonal rainfall we're overwriting it
        # with here failed, the stale "no error" reason was left standing
        # next to a null value (this is literally the mechanism behind a
        # live "rainfall_reason field itself was None/missing" log seen
        # earlier this session). Recomputed against the value actually
        # being kept, every time.
        "rainfall_reason": None if rainfall is not None else (
            "Kharif/Rabi seasonal rainfall unavailable for this location — see seasonal_analysis for the per-season detail."
        ),
        # `temperature` and `lst` are the same MODIS LST value under two
        # historical field names (see earth_engine_service.fetch_farm_data).
        # Only `temperature` used to be overwritten here, so `lst` silently
        # kept the non-seasonal value — compute_farmscore reads `lst`
        # specifically (10% of the comprehensive score), so that 10% was
        # being computed from the wrong data window. Both updated together
        # now.
        "temperature": lst, "lst": lst,
        "seasonal_analysis": seasonal["seasons"],
        "season_method": seasonal["season_method"],
        "season_count_used": seasonal["season_count_used"],
    })
    return base


def _seasonal_extended_indices(lat, lng, polygon=None):
    raw = _get_seasonal(lat, lng, polygon)["raw_values"]
    return {
        "evi": raw.get("evi"), "evi_label": None,
        "savi": raw.get("savi"), "savi_label": None,
        "msavi": raw.get("msavi"), "bsi": None, "bsi_label": None,
        "ci_green": raw.get("ci_green"), "ci_rededge": raw.get("ci_rededge"),
        "ndwi": raw.get("ndwi"),
        "source": "Sentinel-2 SR Harmonized — latest Kharif + latest Rabi seasonal composite",
    }


def _seasonal_spectral(lat, lng, polygon=None):
    raw = _get_seasonal(lat, lng, polygon)["raw_values"]
    ndvi, ndre, ndmi = raw.get("ndvi") or 0.0, raw.get("ndre") or 0.0, raw.get("ndmi") or 0.0
    chlorophyll = max(0.0, min(100.0, ndvi * 100.0))
    nitrogen = max(0.0, min(100.0, (ndre + 0.1) / 0.6 * 100.0))
    msi = ((1 - ndmi) / (1 + ndmi)) if ndmi > -1 else 2.0
    moisture = max(0.0, min(100.0, (2.0 - msi) / 1.6 * 100.0))
    stress = max(0.0, min(100.0, 100.0 - abs(chlorophyll - moisture)))
    score = int(round(0.30 * chlorophyll + 0.25 * nitrogen + 0.25 * moisture + 0.20 * stress))
    grade = "Excellent" if score >= 85 else "Good" if score >= 70 else "Moderate" if score >= 50 else "Fair" if score >= 30 else "Poor"
    return {
        "spectral_score": score, "grade": grade,
        "method": "Seasonal Sentinel-2 multispectral proxy using latest Kharif and Rabi composites.",
        "indices": {
            "chlorophyll": {"label": "Chlorophyll & Canopy Health", "raw_value": round(ndvi, 4), "index": "NDVI", "sub_score": round(chlorophyll, 1), "weight": 30},
            "nitrogen": {"label": "Nitrogen / Red-Edge Health", "raw_value": round(ndre, 4), "index": "NDRE", "sub_score": round(nitrogen, 1), "weight": 25},
            "moisture_stress": {"label": "Moisture Status", "raw_value": round(ndmi, 4), "index": "NDMI", "sub_score": round(moisture, 1), "weight": 25},
            "stress_risk": {"label": "Signal Consistency / Stress Risk", "raw_value": round(stress, 1), "index": "Composite", "sub_score": round(stress, 1), "weight": 20},
        },
        "flags": [], "source": "Sentinel-2 SR Harmonized",
    }


def _seasonal_sar(lat, lng, polygon=None):
    raw = _get_seasonal(lat, lng, polygon)["raw_values"]
    vv, vh = raw.get("vv"), raw.get("vh")
    return {
        "available": vv is not None and vh is not None,
        "vv_db": vv, "vh_db": vh, "vh_vv_ratio": raw.get("vh_vv"), "rvi": raw.get("rvi"),
        "flood_signal": bool(vv is not None and vv < -17) if vv is not None else None,
        "source": "Sentinel-1 GRD — latest Kharif + latest Rabi seasonal composite",
    }


def _seasonal_solar(lat, lng, polygon=None):
    v = _get_seasonal(lat, lng, polygon)["raw_values"].get("solar_radiation")
    return {"available": v is not None, "avg_daily_solar_radiation_mj_m2": v, "source": "ECMWF ERA5-Land Daily Aggregate"}


def _seasonal_spi(lat, lng, polygon=None):
    v = _get_seasonal(lat, lng, polygon)["raw_values"].get("spi")
    return {"available": v is not None, "spi": v, "source": "CHIRPS — latest Kharif + latest Rabi seasonal history"}


def _seasonal_gdd(lat, lng, polygon=None):
    v = _get_seasonal(lat, lng, polygon)["raw_values"].get("gdd")
    return {"available": v is not None, "gdd": v, "source": "MODIS LST"}


# Rebind the exact module-level names this file's routes call —
# everywhere fetch_farm_data(...), fetch_spi(...), etc. appear below
# (including inside compute_farmscore and /comprehensive-score), this
# is what actually runs. fetch_spei is deliberately left un-rebound —
# see the module note above.
fetch_farm_data = _seasonal_farm_data
fetch_extended_indices = _seasonal_extended_indices
calculate_spectral_intelligence = _seasonal_spectral
_fetch_sar_for_flood = _seasonal_sar
fetch_solar_radiation = _seasonal_solar
fetch_spi = _seasonal_spi
fetch_gdd = _seasonal_gdd


def compute_farmscore(lat: float, lng: float, polygon: Optional[dict] = None) -> dict:
    """Core FarmScore computation — satellite fetch, scoring, crop
    recommendation, climate risk, enrichment modules, AI insight.
    Used by both /calculate (web) and the WhatsApp webhook, so the two
    channels always return identical numbers for the same coordinates.
    Raises on hard failures (satellite fetch / scoring); callers decide
    how to surface that (HTTP error vs a WhatsApp text reply).

    FarmScore (final_score, 400-1000) is Base + Average Kharif Score +
    Average Rabi Score — see seasonal_score_service.compute_farmscore.
    Kharif/Rabi each run the full 20-parameter comprehensive model
    (Vegetation + Radar + Weather + Temperature — see
    comprehensive_score_service.py) scoped to that season's own
    satellite/weather values; Base comes from irrigation + cropping
    intensity. There is deliberately only this one FarmScore — no
    separate "seasonal" score alongside it.
    Groundwater is fetched here only for crop_recommendation.py (which
    still uses the original 5-input signature) — it does NOT feed the
    score anymore, by explicit design choice.
    """
    t0 = time.time()
    logger.info("compute_farmscore lat=%.5f lng=%.5f", lat, lng)

    satellite_data = fetch_farm_data(lat=lat, lng=lng, polygon=polygon)

    # ---- Gather the other 15 comprehensive-score parameters in
    # parallel (NDVI/NDMI/rainfall/temperature already came from
    # satellite_data above) — same pattern as /comprehensive-score. ----
    from concurrent.futures import ThreadPoolExecutor as _TPE_SCORE

    def _safe_score_fetch(name, fn, *args):
        try:
            return name, fn(*args)
        except Exception as exc:
            # Previously this discarded the exception entirely (returned
            # None), which is how solar/spi/spei could come back with
            # data_available=False but an EMPTY data_reasons — the actual
            # error (e.g. an Earth Engine exception thrown outside that
            # function's own try/except) never reached the response or the
            # per-component tooltip, only a full traceback in server logs.
            # Returning a reason-shaped dict here guarantees the frontend
            # always has *something* to show, regardless of where inside
            # the fetch function the failure happened.
            logger.exception("compute_farmscore sub-fetch '%s' failed (non-fatal)", name)
            return name, {"available": False, "reason": f"{name} fetch raised {type(exc).__name__}: {exc}"}

    with _TPE_SCORE(max_workers=3) as score_pool:
        score_futures = [
            score_pool.submit(_safe_score_fetch, "extended_indices", fetch_extended_indices, lat, lng, polygon),
            score_pool.submit(_safe_score_fetch, "spectral", calculate_spectral_intelligence, lat, lng, polygon),
            score_pool.submit(_safe_score_fetch, "sar", _fetch_sar_for_flood, lat, lng, polygon),
            score_pool.submit(_safe_score_fetch, "solar", fetch_solar_radiation, lat, lng, polygon),
            score_pool.submit(_safe_score_fetch, "spi", fetch_spi, lat, lng, polygon),
            score_pool.submit(_safe_score_fetch, "gdd", fetch_gdd, lat, lng, polygon),
            score_pool.submit(_safe_score_fetch, "spei", fetch_spei, lat, lng, polygon),
            # Fetched here (not just in the enrichment pool below) because
            # the Base Score component of the new Base+Kharif+Rabi
            # FarmScore needs them before the score itself is computed.
            score_pool.submit(_safe_score_fetch, "irrigation", fetch_irrigation_signal, lat, lng, polygon),
            score_pool.submit(_safe_score_fetch, "cropping_intensity", fetch_cropping_intensity, lat, lng, polygon),
        ]
        score_results = {name: val for name, val in (f.result() for f in score_futures)}

    extended = score_results.get("extended_indices") or {}
    spectral_for_score = score_results.get("spectral") or {}
    sar = score_results.get("sar") or {}
    solar = score_results.get("solar") or {}
    spi = score_results.get("spi") or {}
    gdd = score_results.get("gdd") or {}
    spei = score_results.get("spei") or {}
    irrigation_for_base = score_results.get("irrigation") or {}
    cropping_intensity_for_base = score_results.get("cropping_intensity") or {}

    ndre_val = None
    if spectral_for_score.get("indices", {}).get("nitrogen"):
        ndre_val = spectral_for_score["indices"]["nitrogen"].get("raw_value")

    comprehensive_raw_values = {
        "ndvi": satellite_data.get("ndvi"),
        "evi": extended.get("evi"),
        "savi": extended.get("savi"),
        "msavi": extended.get("msavi"),
        "ndre": ndre_val,
        "ndmi": satellite_data.get("ndmi"),
        "ndwi": extended.get("ndwi"),
        "ci_green": extended.get("ci_green"),
        "ci_rededge": extended.get("ci_rededge"),
        "vv": sar.get("vv_db") if sar.get("available") else None,
        "vh": sar.get("vh_db") if sar.get("available") else None,
        "vh_vv": sar.get("vh_vv_ratio") if sar.get("available") else None,
        "rvi": sar.get("rvi") if sar.get("available") else None,
        "rainfall": satellite_data.get("rainfall"),
        "air_temp": satellite_data.get("air_temperature"),
        "solar_radiation": solar.get("avg_daily_solar_radiation_mj_m2") if solar.get("available") else None,
        "spi": spi.get("spi") if spi.get("available") else None,
        "spei": spei.get("spei_proxy") if spei.get("available") else None,
        "gdd": gdd.get("gdd") if gdd.get("available") else None,
        "lst": satellite_data.get("lst"),
    }

    # ---- Base + Kharif + Rabi FarmScore. Kharif/Rabi each run the same
    # 20-parameter formula above, scoped to just that season's own
    # values (already computed by fetch_farm_data's seasonal pipeline —
    # satellite_data["seasonal_analysis"], no extra Earth Engine call).
    # air_temp has no per-season fetch, so both seasons share the one
    # value already in comprehensive_raw_values. ----
    seasonal_analysis = satellite_data.get("seasonal_analysis") or {}
    kharif_raw_values = {**(seasonal_analysis.get("kharif") or {}), "air_temp": comprehensive_raw_values.get("air_temp")}
    rabi_raw_values = {**(seasonal_analysis.get("rabi") or {}), "air_temp": comprehensive_raw_values.get("air_temp")}
    result = compute_farmscore_from_seasons(irrigation_for_base, cropping_intensity_for_base, kharif_raw_values, rabi_raw_values)

    # ---- Surface WHY a parameter came back unavailable (debug aid). Each
    # weather-index fetch already computes a human-readable reason when it
    # fails; previously that reason was only logged server-side and the
    # frontend just saw "no data" with no explanation. Attach it to the
    # matching component so the frontend can show it (e.g. as a tooltip).
    #
    # IMPORTANT: this always writes an entry for every unavailable param,
    # never only when a "reason" string happens to exist. A None/None case
    # (component unavailable but no reason string) is itself a bug signal —
    # showing that explicitly (instead of silently skipping it, which is
    # what produced an empty {} in an earlier version of this patch) is
    # what lets us actually find where the reason is getting lost. ----
    def _reason_for(available: bool, reason: Optional[str], raw_dump: Any) -> Optional[str]:
        if available:
            return None
        return reason or f"Unavailable, no error captured by the fetch function itself — raw service response was: {raw_dump!r}"

    data_reasons = {}
    if satellite_data.get("rainfall") is None:
        data_reasons["rainfall"] = satellite_data.get("rainfall_reason") or (
            f"Unavailable, no error captured — rainfall_reason field itself was None/missing."
        )
    r = _reason_for(bool(solar.get("available")), solar.get("reason"), solar)
    if r:
        data_reasons["solar_radiation"] = r
    r = _reason_for(bool(spi.get("available")), spi.get("reason"), spi)
    if r:
        data_reasons["spi"] = r
    r = _reason_for(bool(spei.get("available")), spei.get("reason"), spei)
    if r:
        data_reasons["spei"] = r
    r = _reason_for(bool(gdd.get("available")), gdd.get("reason"), gdd)
    if r:
        data_reasons["gdd"] = r
    for key, reason in data_reasons.items():
        if key in result.get("components", {}):
            result["components"][key]["unavailable_reason"] = reason
    if data_reasons:
        logger.warning("compute_farmscore data_reasons lat=%.5f lng=%.5f: %s", lat, lng, data_reasons)


    crop_result = recommend_crop(
        satellite_data.get("ndvi"),
        satellite_data.get("ndmi"),
        satellite_data.get("rainfall"),
        satellite_data.get("air_temperature"),
        satellite_data.get("groundwater"),
        evi=comprehensive_raw_values.get("evi"),
        ndre=comprehensive_raw_values.get("ndre"),
    )

    elapsed = round(time.time() - t0, 2)
    logger.info("Score=%d Grade=%s elapsed=%.2fs", result["final_score"], result["grade"], elapsed)

    # ---- Climate risk assessment — rule-based on the REAL rainfall/temperature
    # values just fetched, not a model prediction. Thresholds are simple and
    # transparent so the "why" is always visible. ----
    def _assess_climate_risk(rainfall_mm_day, temp_c, spi_val=None, gdd_val=None, spei_val=None):
        flags = []
        if rainfall_mm_day is not None:
            if rainfall_mm_day < 2:
                flags.append("Low rainfall for the growing season")
            elif rainfall_mm_day > 15:
                flags.append("Very high rainfall — waterlogging risk")
        if temp_c is not None:
            if temp_c > 35:
                flags.append("High temperature — heat stress risk")
            elif temp_c < 15:
                flags.append("Low temperature for most kharif crops")

        # SPI (drought/excess-rain anomaly vs historical years) — a
        # signal rainfall_mm_day alone can't give, since that's just
        # the current growing-season average with no historical context.
        if spi_val is not None:
            if spi_val <= -1.5:
                flags.append(f"SPI {spi_val} — severe drought vs historical years")
            elif spi_val <= -1:
                flags.append(f"SPI {spi_val} — moderate drought vs historical years")
            elif spi_val >= 1.5:
                flags.append(f"SPI {spi_val} — unusually wet vs historical years")

        # SPEI (Thornthwaite water-balance proxy) — catches heat-driven
        # moisture stress that rainfall alone misses (high temp can
        # deplete effective moisture even with normal rainfall).
        if spei_val is not None and spei_val <= -1.5:
            flags.append(f"SPEI {spei_val} — water-balance deficit (evapotranspiration proxy)")

        # GDD — very low accumulated heat units can indicate a stalled
        # growing season; very high can indicate accelerated/stressed
        # crop cycling. Thresholds are indicative, not crop-calibrated.
        if gdd_val is not None:
            if gdd_val < 400:
                flags.append(f"GDD {gdd_val} — low heat accumulation, growth may be behind schedule")
            elif gdd_val > 2200:
                flags.append(f"GDD {gdd_val} — very high heat accumulation, possible heat stress")

        if not flags:
            level = "Low"
        elif len(flags) <= 2:
            level = "Moderate"
        else:
            level = "High"

        return {"level": level, "flags": flags}

    climate_risk = _assess_climate_risk(
        satellite_data.get("rainfall"), satellite_data.get("air_temperature"),
        spi_val=comprehensive_raw_values.get("spi"),
        gdd_val=comprehensive_raw_values.get("gdd"),
        spei_val=comprehensive_raw_values.get("spei"),
    )

    # ---- Enrichment modules (SatSource parity) — run concurrently, each
    # fails soft so one bad dataset never breaks the whole response. ----
    from concurrent.futures import ThreadPoolExecutor as _TPE

    enrichment: dict = {}

    def _safe(name, fn, *args):
        try:
            return name, fn(*args)
        except Exception:
            logger.exception("Enrichment '%s' failed (non-fatal)", name)
            return name, None

    with _TPE(max_workers=2) as pool:
        futures = [
            pool.submit(_safe, "soil_type", fetch_soil_type, lat, lng, polygon),
            pool.submit(_safe, "adjacent_land_cover", fetch_adjacent_land_cover, lat, lng, polygon),
            pool.submit(_safe, "temperature_annual_range", fetch_temperature_annual_range, lat, lng, polygon),
            pool.submit(_safe, "regional_prosperity", fetch_prosperity_proxy, lat, lng, polygon),
            pool.submit(_safe, "nearest_water_body", fetch_nearest_water_body_signal, lat, lng, polygon),
            pool.submit(_safe, "cropping_history", fetch_cropping_history, lat, lng, polygon),
            pool.submit(_safe, "topography", fetch_topography, lat, lng, polygon),
            pool.submit(_safe, "village_population", fetch_village_population, lat, lng),
            pool.submit(_safe, "drought_instances", fetch_drought_instances, lat, lng),
        ]
        for f in futures:
            key, val = f.result()
            enrichment[key] = val

    # Already fetched earlier (score_pool) for the Base Score component —
    # reused here instead of a second Earth Engine call for the same signal.
    enrichment["irrigation"] = irrigation_for_base
    enrichment["cropping_intensity"] = cropping_intensity_for_base

    # AEZ is cheap (no GEE call) — compute directly from data already fetched
    enrichment["agro_ecological_zone"] = estimate_agro_ecological_zone(
        satellite_data.get("rainfall"), satellite_data.get("air_temperature")
    )

    # ---- Per-season crop identification (Rice/Wheat/Maize/Groundnut guess
    # for each season in the 3-year cropping_history window) — needs
    # cropping_history to already be in `enrichment`, so this runs after
    # the pool above, not inside it. One extra small Earth Engine call
    # per cropped Kharif season (a flood-signature check), so it's cheap. ----
    try:
        enrichment["crop_history_identified"] = identify_crop_history(
            lat, lng, polygon, cropping_history=enrichment.get("cropping_history")
        )
    except Exception:
        logger.exception("Per-season crop identification failed (non-fatal)")
        enrichment["crop_history_identified"] = None

    # FarmScore's own Base+Kharif+Rabi breakdown (already computed above,
    # into `result["breakdown"]`) — surfaced here for the frontend's
    # breakdown panel. Not a second score: same final_score as `result`.
    enrichment["farmscore_breakdown"] = result.get("breakdown")

    # ---- Yield Prediction (formula-based proxy, see yield_prediction.py) ----
    # Uses the top-recommended crop + this farm's own NDVI. Area comes from
    # the drawn polygon if one exists; without it, only per-hectare yield
    # is estimated (no total tonnage).
    try:
        top_crop_name = crop_result["primary"]["crop"] if crop_result.get("primary") else None
        area_ha = compute_polygon_area_ha(polygon) if polygon else None
        yield_prediction = estimate_yield(
            top_crop_name, satellite_data.get("ndvi"), area_ha,
            evi=comprehensive_raw_values.get("evi"), ndre=comprehensive_raw_values.get("ndre"),
        )
    except Exception:
        logger.exception("Yield prediction failed (non-fatal)")
        yield_prediction = None

    response_payload = {
        "score": result["final_score"],
        "grade": result["grade"],
        "components": result["components"],
        "recommended_crops": crop_result,
        "satellite_meta": satellite_data.get("satellite_meta"),
        "ndvi_trend": satellite_data.get("ndvi_trend"),
        "ndwi": satellite_data.get("ndwi"),
        "rainfall_monthly": satellite_data.get("rainfall_monthly"),
        "groundwater_trend": satellite_data.get("groundwater_trend"),
        "climate_risk": climate_risk,
        "coordinates": {"lat": lat, "lng": lng},
        "data_reasons": data_reasons,
        "enrichment": enrichment,
        "yield_prediction": yield_prediction,
        "elapsed_seconds": elapsed,
    }

    # AI insight is generated from the payload above ONLY — grounded in
    # real, already-computed numbers. If it fails or no key is set, the
    # rest of the response is returned unaffected.
    try:
        ai_insight = generate_insight({**response_payload, "climate_risk": climate_risk})
    except Exception:
        logger.exception("AI insight generation failed (non-fatal)")
        ai_insight = None

    response_payload["ai_insight"] = ai_insight

    issued_at = time.time()
    response_payload["_score_sig"] = _sign_score(result["final_score"], lat, lng, issued_at)
    response_payload["_score_sig_ts"] = issued_at
    return response_payload


@app.route("/calculate", methods=["POST"])
def calculate():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    lat = body.get("lat")
    lng = body.get("lng")
    polygon = body.get("polygon")

    if lat is None or lng is None:
        return jsonify({"error": "Both 'lat' and 'lng' are required"}), 400

    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return jsonify({"error": "'lat' and 'lng' must be numbers"}), 400

    if not (-90 <= lat <= 90):
        return jsonify({"error": f"Latitude out of range: {lat}"}), 400
    if not (-180 <= lng <= 180):
        return jsonify({"error": f"Longitude out of range: {lng}"}), 400

    try:
        response_payload = compute_farmscore(lat, lng, polygon)
    except Exception as exc:
        logger.exception("compute_farmscore failed")
        return jsonify({"error": "Failed to compute FarmScore", "detail": str(exc)}), 502

    return jsonify(response_payload), 200


@app.route("/spectral", methods=["POST"])
def spectral():
    """Hyperspectral-style crop intelligence — real Sentinel-2 multispectral
    proxy indices (NDVI/NDRE/GNDVI/NDMI/MSI), a 0-100 Spectral Health
    Score, rule-based flags, and grounded AI (or rule-based fallback)
    irrigation/fertilization/crop-management recommendations. See
    spectral_service.py for the honesty note on why this uses
    multispectral proxies rather than claiming true hyperspectral data.
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    lat = body.get("lat")
    lng = body.get("lng")
    polygon = body.get("polygon")

    if lat is None or lng is None:
        return jsonify({"error": "Both 'lat' and 'lng' are required"}), 400

    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return jsonify({"error": "'lat' and 'lng' must be numbers"}), 400

    if not (-90 <= lat <= 90):
        return jsonify({"error": f"Latitude out of range: {lat}"}), 400
    if not (-180 <= lng <= 180):
        return jsonify({"error": f"Longitude out of range: {lng}"}), 400

    t0 = time.time()
    logger.info("spectral lat=%.5f lng=%.5f", lat, lng)

    try:
        spectral_result = calculate_spectral_intelligence(lat=lat, lng=lng, polygon=polygon)
    except Exception as exc:
        logger.exception("Spectral intelligence computation failed")
        return jsonify({"error": "Failed to compute spectral intelligence", "detail": str(exc)}), 502

    try:
        spectral_result["recommendations"] = generate_spectral_insight(spectral_result)
    except Exception:
        logger.exception("Spectral AI recommendation failed (non-fatal, using fallback)")
        spectral_result["recommendations"] = {
            "irrigation_advice": "Unavailable — recommendation service failed.",
            "fertilization_advice": "Unavailable — recommendation service failed.",
            "crop_management_advice": "Unavailable — recommendation service failed.",
        }

    spectral_result["elapsed_seconds"] = round(time.time() - t0, 2)
    return jsonify(spectral_result), 200


@app.route("/chat", methods=["POST"])
def chat():
    """Chatbot endpoint — answers general agriculture questions and
    questions about the currently-calculated farm. Grounded strictly in
    the farm_context the frontend sends (the last /calculate response);
    never invents farm-specific numbers not present in that context.

    Request body:
        {"message": str, "history": [{"role": "user"|"assistant", "text": str}], "farm_context": {...} | null}
    """
    body = request.get_json(silent=True)
    if not body or not body.get("message"):
        return jsonify({"error": "'message' is required"}), 400

    message = str(body["message"]).strip()
    if not message:
        return jsonify({"error": "'message' cannot be empty"}), 400
    if len(message) > 1000:
        return jsonify({"error": "Message too long (max 1000 characters)"}), 400

    history = body.get("history") or []
    farm_context = body.get("farm_context")

    try:
        reply = generate_chat_reply(message, history=history, farm_context=farm_context)
    except Exception:
        logger.exception("Chat reply generation failed")
        reply = None

    if reply is None:
        return jsonify({
            "error": "AI assistant is currently unavailable. Check that GEMINI_API_KEY is configured."
        }), 503

    return jsonify({"reply": reply}), 200


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 6 * 1024 * 1024  # 6 MB


def _diagnose_with_trained_model(image_bytes: bytes) -> Optional[dict]:
    """Builds a full /diagnose response using our own trained MobileNetV2
    classifier (plant_disease_model.py, trained on PlantVillage — see
    ROADMAP.md Phase 14) — the only model /diagnose uses; no external
    AI call is made. Returns None if the trained model is unavailable
    (missing checkpoint, torch not installed, a corrupt image, or an
    inference-time error), which /diagnose turns into a 503 rather than
    ever raising out of this function.
    """
    if plant_disease_model is None or PILImage is None:
        return None
    try:
        image = PILImage.open(io.BytesIO(image_bytes))
        prediction = plant_disease_model.classify_image(image)
    except Exception:
        logger.exception("Trained plant-disease model inference failed")
        return None
    if prediction is None:
        return None

    crop, _, condition = prediction["label"].partition("___")
    is_healthy = condition.lower() == "healthy"
    confidence_pct = prediction["confidence"]
    confidence_bucket = "High" if confidence_pct >= 80 else "Medium" if confidence_pct >= 50 else "Low"

    return {
        "is_plant": True,
        "crop_guess": crop.replace("_", " "),
        "category": "healthy" if is_healthy else "disease",
        "diagnosis": "No obvious issue detected" if is_healthy else condition.replace("_", " "),
        "confidence": confidence_bucket,
        "symptoms_observed": [],
        "remedy_steps": [],
        "approx_cost_inr": None,
        "caveat": (
            "This is an AI estimate from our own trained model, not a substitute "
            "for a local agricultural extension officer or plant pathologist."
        ),
        "trained_model_prediction": prediction,
    }


@app.route("/diagnose", methods=["POST"])
def diagnose():
    """Crop disease diagnosis from an uploaded photo (multipart/form-data,
    field name 'image'). Uses our own trained MobileNetV2 classifier
    (see _diagnose_with_trained_model / plant_disease_model.py) — no
    external AI (Gemini) call. Always includes an explicit confidence
    level and a caveat that this isn't a substitute for expert advice.
    """
    if "image" not in request.files:
        return jsonify({"error": "No 'image' file in request"}), 400

    file = request.files["image"]
    if not file or file.filename == "":
        return jsonify({"error": "Empty file"}), 400

    mime_type = file.mimetype
    if mime_type not in ALLOWED_IMAGE_TYPES:
        return jsonify({
            "error": f"Unsupported image type '{mime_type}'. Use JPEG, PNG, or WEBP."
        }), 400

    image_bytes = file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return jsonify({"error": "Image too large (max 6 MB)"}), 400
    if len(image_bytes) == 0:
        return jsonify({"error": "Empty file"}), 400

    result = _diagnose_with_trained_model(image_bytes)

    if result is None:
        return jsonify({
            "error": "AI diagnosis is currently unavailable. Check that the trained model "
                     "checkpoint is present (plant_disease_model.pt / plant_disease_classes.json)."
        }), 503

    return jsonify(result), 200


@app.route("/report/pdf", methods=["POST"])
def report_pdf():
    """Generates the SatSource-style PDF report from a /calculate response.
    Frontend sends the exact result object it already has (score, components,
    enrichment, trends, etc.) — this endpoint lays out what was already
    calculated and does not recompute any SCORE or DATA VALUE.

    One deliberate exception: the farm-location satellite thumbnail (a
    visual aid, not a data value) is generated here, on demand, rather
    than on every /calculate call — most calculate calls never lead to
    a PDF download, so doing it here avoids adding Earth Engine load to
    the dashboard's hot path for the common case. It fails soft: if
    generation or the coordinates are missing, the PDF still renders
    fine without the image (see pdf_report.py's _location_page).
    """
    body = request.get_json(silent=True)
    if not body or "score" not in body:
        return jsonify({"error": "Request body must be a /calculate response (must include 'score')"}), 400

    coords = body.get("coordinates") or {}
    lat, lng = coords.get("lat"), coords.get("lng")
    if lat is not None and lng is not None and "satellite_thumbnail" not in body:
        try:
            body["satellite_thumbnail"] = fetch_farm_location_thumbnail(float(lat), float(lng), body.get("polygon"))
        except Exception:
            logger.exception("Farm location thumbnail generation failed (non-fatal for PDF)")
            body["satellite_thumbnail"] = {"available": False, "reason": "Thumbnail generation failed."}
    if lat is not None and lng is not None and "rainfall_trend" not in body:
        try:
            body["rainfall_trend"] = fetch_historical_weather(float(lat), float(lng), body.get("polygon"), start_year=2016)
        except Exception:
            logger.exception("Rainfall trend fetch failed (non-fatal for PDF)")
            body["rainfall_trend"] = None

    try:
        pdf_bytes = generate_pdf_report(body)
    except Exception as exc:
        logger.exception("PDF report generation failed")
        return jsonify({"error": "Failed to generate PDF report", "detail": str(exc)}), 500

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=FarmScore_Report.pdf"},
    )


@app.route("/webhook/whatsapp", methods=["GET"])
def whatsapp_verify():
    """Meta calls this once, when you click 'Verify and Save' on the
    WhatsApp webhook config page — confirms you control this URL.
    """
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    result = whatsapp_service.verify_webhook(mode, token, challenge)
    if result is not None:
        return result, 200
    return "Verification failed", 403


@app.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_incoming():
    """Meta calls this for every incoming message/status update. Always
    return 200 quickly — Meta retries aggressively on non-200 responses.
    """
    payload = request.get_json(silent=True) or {}
    try:
        whatsapp_service.handle_incoming_message(payload, compute_farmscore, generate_chat_reply)
    except Exception:
        logger.exception("WhatsApp webhook processing failed")
    return jsonify({"status": "ok"}), 200


@app.route("/ndvi-heatmap", methods=["POST"])
def ndvi_heatmap():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    if lat is None or lng is None:
        return jsonify({"error": "Both 'lat' and 'lng' are required"}), 400

    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return jsonify({"error": "'lat' and 'lng' must be numbers"}), 400

    result = fetch_ndvi_heatmap(lat, lng, polygon)
    if result is None:
        return jsonify({"error": "Heatmap generation failed"}), 502
    return jsonify(result), 200


def _db_unavailable_response():
    return jsonify({"error": "Database not configured. Set DATABASE_URL (Neon Postgres) on the server to enable Farm Management."}), 503


@app.route("/farmers", methods=["POST"])
@auth_service.require_auth()
def create_farmer_route():
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    body = request.get_json(silent=True) or {}
    if not body.get("name"):
        return jsonify({"error": "'name' is required"}), 400

    session = db_module.get_session()
    try:
        farmer = fms.create_farmer(
            session, name=body["name"], phone=body.get("phone"),
            village=body.get("village"), district=body.get("district"), state=body.get("state"),
        )
        return jsonify(farmer.to_dict()), 201
    finally:
        session.close()


@app.route("/farmers", methods=["GET"])
@auth_service.require_auth()
def list_farmers_route():
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        farmers = fms.list_farmers(session, search=request.args.get("search"))
        return jsonify({"farmers": [f.to_dict() for f in farmers]}), 200
    finally:
        session.close()


@app.route("/farmers/<farmer_id>", methods=["GET"])
@auth_service.require_auth()
def get_farmer_route(farmer_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        farmer = fms.get_farmer(session, farmer_id)
        if not farmer:
            return jsonify({"error": "Farmer not found"}), 404
        return jsonify(farmer.to_dict(include_farms=True)), 200
    finally:
        session.close()


@app.route("/farmers/<farmer_id>", methods=["PUT"])
@auth_service.require_auth()
def update_farmer_route(farmer_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    body = request.get_json(silent=True) or {}
    session = db_module.get_session()
    try:
        farmer = fms.update_farmer(session, farmer_id, **body)
        if not farmer:
            return jsonify({"error": "Farmer not found"}), 404
        return jsonify(farmer.to_dict()), 200
    finally:
        session.close()


@app.route("/farmers/<farmer_id>", methods=["DELETE"])
@auth_service.require_auth()
def delete_farmer_route(farmer_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        ok = fms.delete_farmer(session, farmer_id)
        if not ok:
            return jsonify({"error": "Farmer not found"}), 404
        return jsonify({"status": "deleted"}), 200
    finally:
        session.close()


@app.route("/farmers/<farmer_id>/farms", methods=["POST"])
@auth_service.require_auth()
def create_farm_route(farmer_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    body = request.get_json(silent=True) or {}
    lat, lng = body.get("lat"), body.get("lng")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400

    session = db_module.get_session()
    try:
        farm = fms.create_farm(
            session, farmer_id=farmer_id, lat=float(lat), lng=float(lng),
            label=body.get("label"), polygon=body.get("polygon"),
            survey_method=body.get("survey_method", "point_only"),
            land_use_type=body.get("land_use_type"), survey_number=body.get("survey_number"),
        )
        if not farm:
            return jsonify({"error": "Farmer not found"}), 404
        return jsonify(farm.to_dict()), 201
    finally:
        session.close()


@app.route("/farmers/<farmer_id>/farms", methods=["GET"])
@auth_service.require_auth()
def list_farms_route(farmer_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        farms = fms.list_farms_for_farmer(session, farmer_id)
        return jsonify({"farms": [f.to_dict() for f in farms]}), 200
    finally:
        session.close()


@app.route("/farms/<farm_id>", methods=["GET"])
@auth_service.require_auth()
def get_farm_route(farm_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        farm = fms.get_farm(session, farm_id)
        if not farm:
            return jsonify({"error": "Farm not found"}), 404
        return jsonify(farm.to_dict()), 200
    finally:
        session.close()


@app.route("/farms/<farm_id>", methods=["PUT"])
@auth_service.require_auth()
def update_farm_route(farm_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    body = request.get_json(silent=True) or {}
    session = db_module.get_session()
    try:
        farm = fms.update_farm(session, farm_id, **body)
        if not farm:
            return jsonify({"error": "Farm not found"}), 404
        return jsonify(farm.to_dict()), 200
    finally:
        session.close()


@app.route("/farms/<farm_id>", methods=["DELETE"])
@auth_service.require_auth()
def delete_farm_route(farm_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        ok = fms.delete_farm(session, farm_id)
        if not ok:
            return jsonify({"error": "Farm not found"}), 404
        return jsonify({"status": "deleted"}), 200
    finally:
        session.close()


@app.route("/farms/<farm_id>/ground-truth", methods=["POST"])
@auth_service.require_auth()
def create_ground_truth_route(farm_id):
    """Records a field officer's real, observed crop identity and yield
    for this farm-season, optionally with a photo — see
    ground_truth_service.py / ROADMAP.md Phase 8. multipart/form-data:
    crop (required), season, sowing_date, harvest_date (YYYY-MM-DD),
    observed_yield_kg_per_acre, notes, image (optional photo file).
    """
    if not db_module.is_db_configured():
        return _db_unavailable_response()

    crop = (request.form.get("crop") or "").strip()
    if not crop:
        return jsonify({"error": "'crop' is required"}), 400

    photo_bytes = None
    photo_mime_type = None
    file = request.files.get("image")
    if file and file.filename:
        if file.mimetype not in ALLOWED_IMAGE_TYPES:
            return jsonify({
                "error": f"Unsupported image type '{file.mimetype}'. Use JPEG, PNG, or WEBP."
            }), 400
        photo_bytes = file.read()
        photo_mime_type = file.mimetype

    yield_raw = request.form.get("observed_yield_kg_per_acre")
    try:
        observed_yield = float(yield_raw) if yield_raw else None
    except ValueError:
        return jsonify({"error": "'observed_yield_kg_per_acre' must be a number"}), 400

    session = db_module.get_session()
    try:
        farm = fms.get_farm(session, farm_id)
        if not farm:
            return jsonify({"error": "Farm not found"}), 404

        try:
            obs = ground_truth_service.create_observation(
                session, farm_id=farm_id, crop=crop,
                season=request.form.get("season") or None,
                sowing_date=request.form.get("sowing_date") or None,
                harvest_date=request.form.get("harvest_date") or None,
                observed_yield_kg_per_acre=observed_yield,
                notes=request.form.get("notes") or None,
                photo_bytes=photo_bytes, photo_mime_type=photo_mime_type,
                recorded_by_user_id=request.user.get("user_id"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        governance_service.log_event(
            session, event_type="ground_truth_recorded",
            summary=f"Ground truth recorded for farm {farm_id}: {crop}",
            detail=obs.to_dict(), user_id=request.user.get("user_id"), farm_id=farm_id,
        )
        return jsonify(obs.to_dict()), 201
    finally:
        session.close()


@app.route("/farms/<farm_id>/ground-truth", methods=["GET"])
@auth_service.require_auth()
def list_ground_truth_route(farm_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        observations = ground_truth_service.list_observations_for_farm(session, farm_id)
        return jsonify({"observations": [o.to_dict() for o in observations]}), 200
    finally:
        session.close()


@app.route("/farms/import", methods=["POST"])
@auth_service.require_auth()
def import_farm_boundary_route():
    """Accepts a multipart file upload (.kml or .geojson/.json) and
    returns the extracted polygon — does NOT save a farm; the frontend
    takes this polygon and either shows it for confirmation or calls
    /farmers/<id>/farms with it.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded (field name must be 'file')"}), 400

    f = request.files["file"]
    filename = (f.filename or "").lower()
    file_bytes = f.read()

    if filename.endswith(".kml"):
        polygon = fms.parse_kml_polygon(file_bytes)
    elif filename.endswith(".geojson") or filename.endswith(".json"):
        polygon = fms.parse_geojson_polygon(file_bytes)
    else:
        return jsonify({"error": "Unsupported file type — use .kml, .geojson, or .json"}), 400

    if not polygon:
        return jsonify({"error": "Could not extract a polygon from this file"}), 422

    return jsonify({"polygon": polygon}), 200


@app.route("/farms/auto-detect-boundary", methods=["POST"])
@auth_service.require_auth()
def auto_detect_boundary_route():
    body = request.get_json(silent=True) or {}
    lat, lng = body.get("lat"), body.get("lng")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400
    return jsonify(fms.auto_detect_boundary(float(lat), float(lng))), 200


@app.route("/spectral-indices", methods=["POST"])
def spectral_indices_route():
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400
    try:
        result = fetch_extended_indices(float(lat), float(lng), polygon)
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("spectral-indices failed")
        return jsonify({"error": "Failed to compute indices", "detail": str(exc)}), 502


@app.route("/sar-moisture", methods=["POST"])
def sar_moisture_route():
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400
    try:
        result = fetch_sar_moisture(float(lat), float(lng), polygon)
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("sar-moisture failed")
        return jsonify({"error": "Failed to fetch SAR data", "detail": str(exc)}), 502


@app.route("/historical-timeline", methods=["POST"])
def historical_timeline_route():
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400
    try:
        result = fetch_ndvi_historical_timeline(
            float(lat), float(lng), polygon,
            start_year=int(body.get("start_year", 2018)),
            end_year=body.get("end_year"),
        )
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("historical-timeline failed")
        return jsonify({"error": "Failed to fetch timeline", "detail": str(exc)}), 502


@app.route("/before-after", methods=["POST"])
def before_after_route():
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    date1, date2 = body.get("date1"), body.get("date2")
    if lat is None or lng is None or not date1 or not date2:
        return jsonify({"error": "'lat', 'lng', 'date1', and 'date2' are required"}), 400
    try:
        result = fetch_before_after_comparison(float(lat), float(lng), date1, date2, polygon)
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("before-after failed")
        return jsonify({"error": "Failed to fetch comparison", "detail": str(exc)}), 502


@app.route("/vegetation-heatmap", methods=["POST"])
def vegetation_heatmap_route():
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    index = body.get("index", "ndvi")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400
    result = fetch_vegetation_heatmap(float(lat), float(lng), polygon, index=index)
    if result is None:
        return jsonify({"error": "Heatmap generation failed"}), 502
    return jsonify(result), 200


@app.route("/crop-intelligence", methods=["POST"])
def crop_intelligence_route():
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400

    try:
        lat, lng = float(lat), float(lng)
        import datetime
        current_month = datetime.datetime.utcnow().month

        # Reuse the same 12-month NDVI curve cropping_intensity already computes
        intensity = _fetch_cropping_intensity_for_ci(lat, lng, polygon)
        monthly_ndvi = intensity.get("monthly_ndvi", [])

        identification = identify_crop_heuristic(lat, lng, polygon, monthly_ndvi=monthly_ndvi)
        growth_stage = detect_growth_stage(monthly_ndvi, current_month)

        season = "kharif" if 6 <= current_month <= 11 else "rabi"
        sowing_harvest = estimate_sowing_harvest(monthly_ndvi, identification.get("identified_crop"), season)

        history = _fetch_cropping_history_for_ci(lat, lng, polygon)
        rotation = detect_crop_rotation(history)

        calendar_ref = CROP_CALENDAR.get(identification.get("identified_crop"), {})

        return jsonify({
            "identification": identification,
            "growth_stage": growth_stage,
            "sowing_harvest_prediction": sowing_harvest,
            "crop_rotation": rotation,
            "crop_calendar": calendar_ref,
            "cropping_intensity": {"label": intensity.get("label"), "estimated_cycles": intensity.get("estimated_cycles")},
        }), 200
    except Exception as exc:
        logger.exception("crop-intelligence failed")
        return jsonify({"error": "Failed to compute crop intelligence", "detail": str(exc)}), 502


@app.route("/historical-weather", methods=["POST"])
def historical_weather_route():
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400
    try:
        result = fetch_historical_weather(
            float(lat), float(lng), polygon,
            start_year=int(body.get("start_year", 2015)),
            end_year=body.get("end_year"),
        )
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("historical-weather failed")
        return jsonify({"error": "Failed to fetch historical weather", "detail": str(exc)}), 502


@app.route("/soil-health", methods=["POST"])
def soil_health_route():
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400
    try:
        result = fetch_soil_health(float(lat), float(lng), polygon)
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("soil-health failed")
        return jsonify({"error": "Failed to fetch soil health", "detail": str(exc)}), 502


@app.route("/soil-moisture", methods=["POST"])
def soil_moisture_route():
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400
    try:
        result = fetch_soil_moisture(float(lat), float(lng), polygon)
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("soil-moisture failed")
        return jsonify({"error": "Failed to fetch soil moisture", "detail": str(exc)}), 502


@app.route("/flood-risk", methods=["POST"])
def flood_risk_route():
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400
    try:
        lat, lng = float(lat), float(lng)
        # Reuse topography (slope) and SAR flood signal already built in
        # earlier phases instead of recomputing them.
        topo = _fetch_topography_for_flood(lat, lng, polygon)
        sar = _fetch_sar_for_flood(lat, lng, polygon)
        result = fetch_flood_risk(
            lat, lng, polygon,
            slope_degrees=topo.get("slope_degrees"),
            sar_flood_signal=sar.get("flood_signal") if sar.get("available") else None,
        )
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("flood-risk failed")
        return jsonify({"error": "Failed to compute flood risk", "detail": str(exc)}), 502


@app.route("/farm-advisor", methods=["POST"])
def farm_advisor_route():
    """Accepts whatever farm data the frontend already has (score,
    enrichment, crop intelligence, weather/soil, etc.) and returns a
    synthesized, prioritized advisory. Never recomputes anything —
    grounded only in what's passed in.
    """
    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"error": "Request body must include farm data"}), 400

    advisory = generate_farm_advisor(body)
    if advisory is None:
        return jsonify({
            "advisory": None,
            "reason": "AI advisor unavailable (GEMINI_API_KEY not set or request failed). Review the individual data sections directly.",
        }), 200
    return jsonify({"advisory": advisory}), 200


@app.route("/risk-analysis", methods=["POST"])
def risk_analysis_route():
    """Same pattern as /farm-advisor — accepts already-computed risk
    signals (climate_risk, flood_risk, drought_instances, growth_stage,
    etc.) and returns one synthesized narrative.
    """
    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"error": "Request body must include risk data"}), 400

    analysis = generate_risk_analysis(body)
    if analysis is None:
        return jsonify({
            "analysis": None,
            "reason": "AI risk analysis unavailable (GEMINI_API_KEY not set or request failed). Review the individual risk_level fields directly.",
        }), 200
    return jsonify({"analysis": analysis}), 200


@app.route("/credit-intelligence", methods=["POST"])
@auth_service.require_auth()
def credit_intelligence_route():
    """Accepts the farm's already-computed result (score, yield_prediction,
    climate_risk, enrichment.drought_instances, coordinates) — the exact
    same object cached from /calculate — plus an optional pre-fetched
    flood_risk. Computes it fresh from coordinates if not supplied.
    Never recomputes score/yield/climate_risk — only combines what's
    already there.
    """
    body = request.get_json(silent=True) or {}
    if not body or "score" not in body:
        return jsonify({"error": "Request body must be a /calculate response (must include 'score')"}), 400
    if not _verify_score_signature(body):
        return jsonify({"error": "Score signature missing, invalid, or stale — resubmit the exact, unmodified object /calculate returned."}), 400

    try:
        coords = body.get("coordinates", {})
        lat, lng, polygon = coords.get("lat"), coords.get("lng"), body.get("polygon")

        flood_risk = body.get("flood_risk")
        if flood_risk is None and lat is not None and lng is not None:
            try:
                topo = _fetch_topography_for_flood(lat, lng, polygon)
                sar = _fetch_sar_for_flood(lat, lng, polygon)
                flood_risk = fetch_flood_risk(
                    lat, lng, polygon,
                    slope_degrees=topo.get("slope_degrees"),
                    sar_flood_signal=sar.get("flood_signal") if sar.get("available") else None,
                )
            except Exception:
                logger.exception("Flood risk fetch failed inside credit-intelligence (non-fatal)")
                flood_risk = None

        climate_risk = body.get("climate_risk", {})
        drought = (body.get("enrichment") or {}).get("drought_instances") or {}

        income = estimate_income(body.get("yield_prediction"), live_price=body.get("live_price"))
        bcis = compute_bcis_score(
            farmscore=body.get("score"),
            climate_risk_level=climate_risk.get("level"),
            flood_risk_level=flood_risk.get("risk_level") if flood_risk else None,
            drought_years=drought.get("drought_years"),
        )
        loan_ceiling = recommend_loan_ceiling(income, bcis, policy_max_rs=body.get("policy_max_rs"))
        freeze = auto_freeze_check(bcis)

        result = {
            "income_estimate": income,
            "bcis": bcis,
            "loan_ceiling": loan_ceiling,
            "auto_freeze": freeze,
            "flood_risk_used": flood_risk,
        }

        # Audit trail — every BCIS/loan-ceiling/auto-freeze decision gets
        # logged (RBI evidence-pack requirement). Never lets a logging
        # failure break the actual response.
        if db_module.is_db_configured():
            session = db_module.get_session()
            try:
                governance_service.log_event(
                    session, event_type="bcis_score",
                    summary=f"BCIS {bcis['score']}/100 ({bcis['tier']}), loan ceiling {loan_ceiling.get('loan_ceiling_rs')}",
                    detail=result, user_id=getattr(request, "user", {}).get("user_id"),
                    farmer_id=body.get("farmer_id"), farm_id=body.get("farm_id"), lat=lat, lng=lng,
                )
                if freeze["frozen"]:
                    governance_service.log_event(
                        session, event_type="auto_freeze", summary=freeze["reason"],
                        detail=freeze, user_id=getattr(request, "user", {}).get("user_id"),
                        farmer_id=body.get("farmer_id"), farm_id=body.get("farm_id"), lat=lat, lng=lng,
                    )
            finally:
                session.close()

        return jsonify(result), 200
    except Exception as exc:
        logger.exception("credit-intelligence failed")
        return jsonify({"error": "Failed to compute credit intelligence", "detail": str(exc)}), 502


@app.route("/insurance-claim", methods=["POST"])
@auth_service.require_auth()
def insurance_claim_route():
    """Full claim assessment: verifies declared acreage/crop against
    satellite, estimates loss from before/after imagery, flags fraud
    signals, and returns a triage recommendation. All inputs are the
    farmer/insurer's DECLARED values — this endpoint checks them
    against what the satellite actually shows.
    """
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    declared_area_ha = body.get("declared_area_ha")
    declared_crop = body.get("declared_crop")
    date1, date2 = body.get("date1"), body.get("date2")  # pre-event, post-event

    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400
    if not date1 or not date2:
        return jsonify({"error": "'date1' (pre-event) and 'date2' (post-event) are required"}), 400

    try:
        lat, lng = float(lat), float(lng)

        measured_area_ha = compute_polygon_area_ha(polygon) if polygon else None
        acreage_check = verify_acreage(declared_area_ha, measured_area_ha)

        identification = _identify_crop_for_claim(lat, lng, polygon)
        crop_check = verify_crop(declared_crop, identification)

        before_after = _fetch_before_after_for_claim(lat, lng, date1, date2, polygon)
        loss = estimate_loss(before_after)

        fraud = detect_fraud_signals(acreage_check, crop_check, identification.get("peak_ndvi"))
        claim = assess_claim(acreage_check, crop_check, loss, fraud)

        if db_module.is_db_configured():
            session = db_module.get_session()
            try:
                governance_service.log_event(
                    session, event_type="insurance_claim",
                    summary=f"Claim recommendation: {claim['recommendation']} — {claim['reason']}",
                    detail=claim, user_id=getattr(request, "user", {}).get("user_id"),
                    farmer_id=body.get("farmer_id"), farm_id=body.get("farm_id"), lat=lat, lng=lng,
                )
            finally:
                session.close()

        return jsonify(claim), 200
    except Exception as exc:
        logger.exception("insurance-claim failed")
        return jsonify({"error": "Failed to assess claim", "detail": str(exc)}), 502


@app.route("/auth/register", methods=["POST"])
def auth_register_route():
    """The FIRST registration ever becomes an admin automatically
    (bootstrap). After that, only an existing admin can register new
    users — pass their token in the Authorization header.
    """
    if not db_module.is_db_configured():
        return _db_unavailable_response()

    body = request.get_json(silent=True) or {}
    username, password, name = body.get("username"), body.get("password"), body.get("name")
    if not username or not password or not name:
        return jsonify({"error": "'username', 'password', and 'name' are required"}), 400

    session = db_module.get_session()
    try:
        is_first_user = session.query(auth_service.User).count() == 0
        if not is_first_user:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Only an admin can register additional users — log in as admin first"}), 401
            payload = auth_service.decode_token(auth_header[len("Bearer "):])
            if not payload or payload.get("role") != "admin":
                return jsonify({"error": "Only an admin can register additional users"}), 403

        existing = session.query(auth_service.User).filter(auth_service.User.username == username).first()
        if existing:
            return jsonify({"error": "Username already taken"}), 409

        role = body.get("role", "field_officer")
        user = auth_service.register_user(session, username, password, name, role)
        token = auth_service.generate_token(user)
        return jsonify({"user": user.to_dict(), "token": token}), 201
    finally:
        session.close()


@app.route("/auth/login", methods=["POST"])
def auth_login_route():
    if not db_module.is_db_configured():
        return _db_unavailable_response()

    body = request.get_json(silent=True) or {}
    username, password = body.get("username"), body.get("password")
    if not username or not password:
        return jsonify({"error": "'username' and 'password' are required"}), 400

    session = db_module.get_session()
    try:
        user = auth_service.authenticate_user(session, username, password)
        if not user:
            return jsonify({"error": "Invalid username or password"}), 401
        token = auth_service.generate_token(user)
        return jsonify({"user": user.to_dict(), "token": token}), 200
    finally:
        session.close()


@app.route("/auth/me", methods=["GET"])
@auth_service.require_auth()
def auth_me_route():
    return jsonify({"user": request.user}), 200


@app.route("/auth/logout", methods=["POST"])
@auth_service.require_auth()
def auth_logout_route():
    """Bumps this user's token_version, immediately invalidating THIS
    token and every other still-unexpired token issued to them —
    previously there was no way to invalidate a token before its
    natural expiry at all (see auth_service.bump_token_version).
    """
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        user = session.query(auth_service.User).filter(auth_service.User.id == request.user.get("user_id")).first()
        if user:
            auth_service.bump_token_version(session, user)
        return jsonify({"message": "Logged out."}), 200
    finally:
        session.close()


@app.route("/admin/users", methods=["GET"])
@auth_service.require_auth(["admin"])
def admin_list_users_route():
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        users = session.query(auth_service.User).order_by(auth_service.User.created_at.desc()).all()
        return jsonify({"users": [u.to_dict() for u in users]}), 200
    finally:
        session.close()


@app.route("/admin/users", methods=["POST"])
@auth_service.require_auth(["admin"])
def admin_create_user_route():
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    body = request.get_json(silent=True) or {}
    username, password, name = body.get("username"), body.get("password"), body.get("name")
    role = body.get("role", "field_officer")
    if not username or not password or not name:
        return jsonify({"error": "'username', 'password', and 'name' are required"}), 400
    if role not in ("admin", "field_officer"):
        return jsonify({"error": "'role' must be 'admin' or 'field_officer'"}), 400

    session = db_module.get_session()
    try:
        existing = session.query(auth_service.User).filter(auth_service.User.username == username).first()
        if existing:
            return jsonify({"error": "Username already taken"}), 409
        user = auth_service.register_user(session, username, password, name, role)
        return jsonify({"user": user.to_dict()}), 201
    finally:
        session.close()


@app.route("/admin/users/<user_id>", methods=["DELETE"])
@auth_service.require_auth(["admin"])
def admin_delete_user_route(user_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    if user_id == request.user.get("user_id"):
        return jsonify({"error": "Cannot delete your own account while logged in as it"}), 400

    session = db_module.get_session()
    try:
        user = session.query(auth_service.User).filter(auth_service.User.id == user_id).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
        session.delete(user)
        session.commit()
        return jsonify({"status": "deleted"}), 200
    finally:
        session.close()


@app.route("/portfolio/summary", methods=["GET"])
@auth_service.require_auth()
def portfolio_summary_route():
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        farmers = fms.list_farmers(session)
        all_farms = []
        for f in farmers:
            all_farms.extend(fms.list_farms_for_farmer(session, f.id))

        total_area = sum(f.area_ha for f in all_farms if f.area_ha)
        farms_with_area = sum(1 for f in all_farms if f.area_ha)

        survey_method_counts: dict = {}
        for f in all_farms:
            key = f.survey_method or "unknown"
            survey_method_counts[key] = survey_method_counts.get(key, 0) + 1

        district_counts: dict = {}
        for farmer in farmers:
            key = farmer.district or "Unspecified"
            district_counts[key] = district_counts.get(key, 0) + 1

        return jsonify({
            "total_farmers": len(farmers),
            "total_farms": len(all_farms),
            "total_area_ha": round(total_area, 2),
            "farms_with_measured_area": farms_with_area,
            "survey_method_breakdown": survey_method_counts,
            "farmers_by_district": district_counts,
        }), 200
    finally:
        session.close()


@app.route("/audit-log", methods=["GET"])
@auth_service.require_auth(["admin"])
def audit_log_route():
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        event_type = request.args.get("event_type")
        farmer_id = request.args.get("farmer_id")
        limit = min(int(request.args.get("limit", 100)), 500)
        events = governance_service.list_events(session, event_type=event_type, farmer_id=farmer_id, limit=limit)
        include_detail = request.args.get("include_detail") == "true"
        return jsonify({"events": [e.to_dict(include_detail=include_detail) for e in events]}), 200
    finally:
        session.close()


@app.route("/farmers/<farmer_id>/consent", methods=["GET"])
@auth_service.require_auth()
def get_consent_route(farmer_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        records = fms.get_consents_for_farmer(session, farmer_id)
        return jsonify({"consents": [r.to_dict() for r in records]}), 200
    finally:
        session.close()


@app.route("/farmers/<farmer_id>/consent", methods=["POST"])
@auth_service.require_auth()
def set_consent_route(farmer_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    body = request.get_json(silent=True) or {}
    consent_type, granted = body.get("consent_type"), body.get("granted")
    if not consent_type or granted is None:
        return jsonify({"error": "'consent_type' and 'granted' (true/false) are required"}), 400

    session = db_module.get_session()
    try:
        record = fms.set_consent(session, farmer_id, consent_type, bool(granted), notes=body.get("notes"))
        governance_service.log_event(
            session, event_type="consent_change",
            summary=f"Consent '{consent_type}' set to {'granted' if granted else 'revoked'} for farmer {farmer_id}",
            detail=record.to_dict(), user_id=getattr(request, "user", {}).get("user_id"), farmer_id=farmer_id,
        )
        return jsonify(record.to_dict()), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        session.close()


@app.route("/farmers/<farmer_id>/request-deletion", methods=["POST"])
@auth_service.require_auth()
def request_deletion_route(farmer_id):
    """DPDP-style deletion request — flags the farmer's consent records
    with a timestamp (the '72 hours' clock start). Does NOT delete data
    automatically; an admin/ops person must complete the actual
    deletion as a deliberate, irreversible step.
    """
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    body = request.get_json(silent=True) or {}
    session = db_module.get_session()
    try:
        result = fms.request_deletion(session, farmer_id, notes=body.get("notes"))
        governance_service.log_event(
            session, event_type="deletion_request", summary=f"Deletion requested for farmer {farmer_id}",
            detail=result, user_id=getattr(request, "user", {}).get("user_id"), farmer_id=farmer_id,
        )
        return jsonify(result), 200
    finally:
        session.close()


@app.route("/farmers/<farmer_id>/loans", methods=["POST"])
@auth_service.require_auth()
def create_loan_route(farmer_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    body = request.get_json(silent=True) or {}
    session = db_module.get_session()
    try:
        loan = governance_service.create_loan(
            session, farmer_id=farmer_id, farm_id=body.get("farm_id"),
            requested_amount_rs=body.get("requested_amount_rs"),
            crop=body.get("crop"), season=body.get("season"),
        )
        governance_service.log_event(
            session, event_type="loan_stage_change",
            summary=f"Loan created for farmer {farmer_id} — stage 'Application'",
            detail=loan.to_dict(), user_id=getattr(request, "user", {}).get("user_id"),
            farmer_id=farmer_id, farm_id=body.get("farm_id"),
        )
        return jsonify(loan.to_dict()), 201
    finally:
        session.close()


@app.route("/farmers/<farmer_id>/loans", methods=["GET"])
@auth_service.require_auth()
def list_loans_route(farmer_id):
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    session = db_module.get_session()
    try:
        loans = governance_service.list_loans_for_farmer(session, farmer_id)
        return jsonify({"loans": [l.to_dict() for l in loans]}), 200
    finally:
        session.close()


@app.route("/loans/<loan_id>/advance", methods=["POST"])
@auth_service.require_auth()
def advance_loan_route(loan_id):
    """Moves a loan to the next stage: Application -> Disbursement ->
    In-Season -> Pre-Harvest -> Renewal (matches the Bhumi doc's
    5-stage loan lifecycle). Cannot move backward.
    """
    if not db_module.is_db_configured():
        return _db_unavailable_response()
    body = request.get_json(silent=True) or {}
    new_stage = body.get("stage")
    if not new_stage:
        return jsonify({"error": "'stage' is required"}), 400

    session = db_module.get_session()
    try:
        result = governance_service.advance_loan_stage(
            session, loan_id, new_stage,
            approved_ceiling_rs=body.get("approved_ceiling_rs"),
            bcis_tier_at_approval=body.get("bcis_tier_at_approval"),
        )
        if result is None:
            return jsonify({"error": "Loan not found"}), 404
        if "error" in result:
            return jsonify(result), 400

        loan = result["loan"]
        governance_service.log_event(
            session, event_type="loan_stage_change",
            summary=f"Loan {loan_id} moved from '{result['old_stage']}' to '{result['new_stage']}'",
            detail=loan, user_id=getattr(request, "user", {}).get("user_id"),
            farmer_id=loan.get("farmer_id"), farm_id=loan.get("farm_id"), loan_id=loan_id,
        )
        return jsonify(loan), 200
    finally:
        session.close()


@app.route("/comprehensive-score", methods=["POST"])
def comprehensive_score_route():
    """Computes the 20-parameter weighted-average score (Vegetation +
    Radar + Weather + Temperature). Fetches every parameter in
    parallel from the existing modules that already compute them —
    nothing here is a duplicate satellite call for indices already
    available elsewhere in this app.
    """
    body = request.get_json(silent=True) or {}
    lat, lng, polygon = body.get("lat"), body.get("lng"), body.get("polygon")
    custom_weights = body.get("weights")  # optional override

    if lat is None or lng is None:
        return jsonify({"error": "'lat' and 'lng' are required"}), 400

    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return jsonify({"error": "'lat' and 'lng' must be numbers"}), 400

    from concurrent.futures import ThreadPoolExecutor as _TPE

    def _safe(name, fn, *args):
        try:
            return name, fn(*args)
        except Exception:
            logger.exception("comprehensive-score sub-fetch '%s' failed (non-fatal)", name)
            return name, None

    with _TPE(max_workers=3) as pool:
        futures = [
            pool.submit(_safe, "satellite", fetch_farm_data, lat, lng, polygon),
            pool.submit(_safe, "extended_indices", fetch_extended_indices, lat, lng, polygon),
            pool.submit(_safe, "spectral", calculate_spectral_intelligence, lat, lng, polygon),
            pool.submit(_safe, "sar", _fetch_sar_for_flood, lat, lng, polygon),
            pool.submit(_safe, "solar", fetch_solar_radiation, lat, lng, polygon),
            pool.submit(_safe, "spi", fetch_spi, lat, lng, polygon),
            pool.submit(_safe, "gdd", fetch_gdd, lat, lng, polygon),
            pool.submit(_safe, "spei", fetch_spei, lat, lng, polygon),
        ]
        results = {name: val for name, val in (f.result() for f in futures)}

    satellite = results.get("satellite") or {}
    extended = results.get("extended_indices") or {}
    spectral = results.get("spectral") or {}
    sar = results.get("sar") or {}
    solar = results.get("solar") or {}
    spi = results.get("spi") or {}
    gdd = results.get("gdd") or {}
    spei = results.get("spei") or {}

    ndre_val = None
    if spectral.get("indices", {}).get("nitrogen"):
        ndre_val = spectral["indices"]["nitrogen"].get("raw_value")

    raw_values = {
        "ndvi": satellite.get("ndvi"),
        "evi": extended.get("evi"),
        "savi": extended.get("savi"),
        "msavi": extended.get("msavi"),
        "ndre": ndre_val,
        "ndmi": satellite.get("ndmi"),
        "ndwi": extended.get("ndwi"),
        "ci_green": extended.get("ci_green"),
        "ci_rededge": extended.get("ci_rededge"),
        "vv": sar.get("vv_db") if sar.get("available") else None,
        "vh": sar.get("vh_db") if sar.get("available") else None,
        "vh_vv": sar.get("vh_vv_ratio") if sar.get("available") else None,
        "rvi": sar.get("rvi") if sar.get("available") else None,
        "rainfall": satellite.get("rainfall"),
        # FIX: this was reading satellite.get("temperature") -- the MODIS LST
        # value, same field used for "lst" below -- instead of the separate
        # ERA5-Land air_temperature field fetch_farm_data() actually computes.
        # That made air_temp == lst every time, which the safety check in
        # comprehensive_score_service.py correctly detected and nulled out,
        # showing as "Air Temperature -- no data" in the UI.
        "air_temp": satellite.get("air_temperature"),
        "solar_radiation": solar.get("avg_daily_solar_radiation_mj_m2") if solar.get("available") else None,
        "spi": spi.get("spi") if spi.get("available") else None,
        "spei": spei.get("spei_proxy") if spei.get("available") else None,
        "gdd": gdd.get("gdd") if gdd.get("available") else None,
        "lst": satellite.get("temperature"),
    }

    weights = custom_weights if custom_weights else DEFAULT_WEIGHTS
    result = compute_comprehensive_score(raw_values, weights=weights)
    result["coordinates"] = {"lat": lat, "lng": lng}
    result["raw_fetch_detail"] = {"spi": spi, "spei": spei, "gdd": gdd, "solar_radiation": solar, "sar": sar}

    return jsonify(result), 200


@app.route("/glossary", methods=["GET"])
def glossary():
    return jsonify({"terms": GLOSSARY_TERMS}), 200


@app.route("/mandi-price", methods=["GET"])
def mandi_price():
    """Query params: commodity, state, district (optional).
    Requires DATA_GOV_IN_KEY to be configured — see govt_data_service.py.
    """
    commodity = request.args.get("commodity")
    state = request.args.get("state")
    district = request.args.get("district")

    if not commodity or not state:
        return jsonify({"error": "'commodity' and 'state' query params are required"}), 400

    result = fetch_mandi_price(commodity, state, district)
    return jsonify(result), 200


@app.route("/major-crops", methods=["GET"])
def major_crops():
    """Query params: district, state.
    Requires DATA_GOV_IN_KEY to be configured — see govt_data_service.py.
    """
    district = request.args.get("district")
    state = request.args.get("state")

    if not district or not state:
        return jsonify({"error": "'district' and 'state' query params are required"}), 400

    result = fetch_major_crops_in_region(district, state)
    return jsonify(result), 200


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify({"error": "Method not allowed"}), 405


@app.errorhandler(500)
def internal_error(_):
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    logger.info("Starting FarmScore API on %s:%d", HOST, PORT)
    app.run(host=HOST, port=PORT, debug=DEBUG)
