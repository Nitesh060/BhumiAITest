from typing import Dict, List


def recommend_crop(
    ndvi: float,
    ndmi: float,
    rainfall: float,
    temperature: float,
    groundwater: float,
) -> Dict:

    crops: List[Dict] = []

    # ---------------- Rice ----------------
    rice_score = 0

    if rainfall >= 6:
        rice_score += 30

    if ndvi >= 0.60:
        rice_score += 25

    if ndmi >= 0.20:
        rice_score += 20

    if 24 <= temperature <= 34:
        rice_score += 15

    if groundwater >= 150:
        rice_score += 10

    crops.append({
        "crop": "Rice",
        "score": rice_score
    })

    # ---------------- Wheat ----------------
    wheat_score = 0

    if rainfall <= 5:
        wheat_score += 20

    if ndvi >= 0.45:
        wheat_score += 25

    if ndmi >= 0:
        wheat_score += 20

    if 18 <= temperature <= 28:
        wheat_score += 25

    if groundwater >= 80:
        wheat_score += 10

    crops.append({
        "crop": "Wheat",
        "score": wheat_score
    })

    # ---------------- Maize ----------------
    maize_score = 0

    if 3 <= rainfall <= 7:
        maize_score += 25

    if ndvi >= 0.50:
        maize_score += 25

    if ndmi >= 0.10:
        maize_score += 20

    if 20 <= temperature <= 32:
        maize_score += 20

    if groundwater >= 100:
        maize_score += 10

    crops.append({
        "crop": "Maize",
        "score": maize_score
    })

    # ---------------- Groundnut ----------------
    groundnut_score = 0

    if rainfall <= 5:
        groundnut_score += 25

    if ndvi >= 0.40:
        groundnut_score += 25

    if ndmi >= 0:
        groundnut_score += 20

    if 22 <= temperature <= 35:
        groundnut_score += 20

    if groundwater >= 60:
        groundnut_score += 10

    crops.append({
        "crop": "Groundnut",
        "score": groundnut_score
    })

    crops.sort(key=lambda x: x["score"], reverse=True)

    return {
        "primary": crops[0],
        "secondary": crops[1],
        "all": crops
    }
