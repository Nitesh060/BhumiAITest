"""FarmScore scoring adapter.

The score uses the transparent 20-parameter comprehensive model. Groundwater
is intentionally not a score input. Air temperature is only accepted from a
real 2 m air-temperature field; MODIS LST remains separate.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, Optional
from comprehensive_score_service import compute_comprehensive_score, PARAMETER_LABELS, DEFAULT_GRADE
logger = logging.getLogger(__name__)
_UNITS = {"ndvi":"","evi":"","savi":"","msavi":"","ndre":"","ndmi":"","ndwi":"","ci_green":"","ci_rededge":"","vv":" dB","vh":" dB","vh_vv":"","rvi":"","rainfall":" mm/day","air_temp":"°C","solar_radiation":" MJ/m²/day","spi":"","spei":"","gdd":" GDD-units","lst":"°C"}
_SOURCES = {"ndvi":"Sentinel-2","evi":"Sentinel-2","savi":"Sentinel-2","msavi":"Sentinel-2","ndre":"Sentinel-2 Red Edge","ndmi":"Sentinel-2","ndwi":"Sentinel-2","ci_green":"Sentinel-2","ci_rededge":"Sentinel-2","vv":"Sentinel-1 SAR","vh":"Sentinel-1 SAR","vh_vv":"Sentinel-1 SAR","rvi":"Sentinel-1 SAR","rainfall":"CHIRPS","air_temp":"ERA5-Land 2m Air Temperature","solar_radiation":"ERA5-Land","spi":"CHIRPS","spei":"CHIRPS + MODIS (Thornthwaite proxy)","gdd":"MODIS LST","lst":"MODIS LST"}

def calculate_score(raw_values: Dict[str, Optional[float]], weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    normalized = dict(raw_values)
    normalized["air_temp"] = normalized.get("air_temperature")
    normalized.pop("air_temperature", None)
    comp_result = compute_comprehensive_score(normalized, weights=weights)
    if comp_result.get("score_0_100") is None:
        logger.warning("calculate_score: no usable parameters")
        return {"final_score": 300, "grade": DEFAULT_GRADE, "components": {}, "parameters_used": 0, "parameters_total": comp_result.get("parameters_total", 20), "confidence": "low"}
    components = {}
    for key, c in comp_result["components"].items():
        components[key] = {"raw_value": c["raw_value"], "sub_score": c["sub_score"], "weight": c["weight_pct"], "weighted_contribution": c.get("contribution"), "data_available": c["sub_score"] is not None, "unit": _UNITS.get(key, ""), "source": _SOURCES.get(key, ""), "label": PARAMETER_LABELS.get(key, key)}
    return {"final_score": comp_result["score_300_900"], "grade": comp_result["grade"], "components": components, "parameters_used": comp_result["parameters_used"], "parameters_total": comp_result["parameters_total"], "confidence": comp_result.get("confidence", "low"), "parameter_groups": comp_result.get("parameter_groups", {}), "validation_status": comp_result.get("validation_status")}
