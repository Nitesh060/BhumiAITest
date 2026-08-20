"""
crop_recommendation.py
========================
Recommends the best-fit crop from the 4 supported (Rice, Wheat, Maize,
Groundnut) using satellite/weather signals. Extended to optionally use
EVI and NDRE (from the comprehensive 20-parameter model already
fetched in compute_farmscore) alongside the original 5 inputs — no new
satellite calls, just reusing what's already computed.

Backward compatible: evi/ndre are optional keyword args. If not
supplied, scoring works exactly as before (just without those two
bonus signals) — no caller is forced to change.
"""

from typing import Dict, List, Optional


def recommend_crop(
    ndvi: float,
    ndmi: float,
    rainfall: float,
    temperature: float,
    groundwater: float,
    evi: Optional[float] = None,
    ndre: Optional[float] = None,
) -> Dict:

    ndvi = ndvi or 0
    ndmi = ndmi or 0
    rainfall = rainfall or 0
    temperature = temperature or 0
    groundwater = groundwater or 0

    crops: List[Dict] = []

    # ---------------- Rice ----------------
    rice_score = 0

    if rainfall >= 6:
        rice_score += 20

    if ndvi >= 0.60:
        rice_score += 20

    if ndmi >= 0.20:
        rice_score += 15

    if 24 <= temperature <= 34:
        rice_score += 15

    if groundwater >= 150:
        rice_score += 10

    if evi is not None and evi >= 0.40:
        rice_score += 10
    if ndre is not None and ndre >= 0.25:
        rice_score += 10

    crops.append({
        "crop": "Rice",
        "score": rice_score
    })

    # ---------------- Wheat ----------------
    wheat_score = 0

    if rainfall <= 5:
        wheat_score += 15

    if ndvi >= 0.45:
        wheat_score += 20

    if ndmi >= 0:
        wheat_score += 15

    if 18 <= temperature <= 28:
        wheat_score += 20

    if groundwater >= 80:
        wheat_score += 10

    if evi is not None and evi >= 0.30:
        wheat_score += 10
    if ndre is not None and ndre >= 0.20:
        wheat_score += 10

    crops.append({
        "crop": "Wheat",
        "score": wheat_score
    })

    # ---------------- Maize ----------------
    maize_score = 0

    if 3 <= rainfall <= 7:
        maize_score += 20

    if ndvi >= 0.50:
        maize_score += 20

    if ndmi >= 0.10:
        maize_score += 15

    if 20 <= temperature <= 32:
        maize_score += 15

    if groundwater >= 100:
        maize_score += 10

    if evi is not None and evi >= 0.35:
        maize_score += 10
    if ndre is not None and ndre >= 0.22:
        maize_score += 10

    crops.append({
        "crop": "Maize",
        "score": maize_score
    })

    # ---------------- Groundnut ----------------
    groundnut_score = 0

    if rainfall <= 5:
        groundnut_score += 20

    if ndvi >= 0.40:
        groundnut_score += 20

    if ndmi >= 0:
        groundnut_score += 15

    if 22 <= temperature <= 35:
        groundnut_score += 15

    if groundwater >= 60:
        groundnut_score += 10

    if evi is not None and evi >= 0.28:
        groundnut_score += 10
    if ndre is not None and ndre >= 0.18:
        groundnut_score += 10

    crops.append({
        "crop": "Groundnut",
        "score": groundnut_score
    })

    crops.sort(key=lambda x: x["score"], reverse=True)

    return {
        "primary": crops[0],
        "secondary": crops[1],
        "all": crops,
        "signals_used": {
            "evi": evi is not None,
            "ndre": ndre is not None,
        },
    }
