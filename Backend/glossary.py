"""
glossary.py
===========
Static glossary content served by GET /glossary. Plain data — no GEE
calls, so it's instant and always available even if Earth Engine is down.
"""

GLOSSARY_TERMS = [
    {"term": "NDVI", "full_form": "Normalized Difference Vegetation Index",
     "explanation": "Measures vegetation greenness/health from satellite bands. Higher = healthier, denser plant growth."},
    {"term": "NDMI", "full_form": "Normalized Difference Moisture Index",
     "explanation": "Measures moisture content in plant canopy. Higher = more water in the crop/vegetation."},
    {"term": "NDWI", "full_form": "Normalized Difference Water Index",
     "explanation": "Detects surface water presence (ponds, flooding, waterlogging) from satellite imagery."},
    {"term": "Cropping Intensity", "full_form": None,
     "explanation": "How many crop cycles a field goes through in a year — single (mono), double, or triple cropping."},
    {"term": "Agro-Ecological Zone (AEZ)", "full_form": None,
     "explanation": "A regional classification based on climate (rainfall, temperature) and soil that groups areas with similar farming potential."},
    {"term": "MSP", "full_form": "Minimum Support Price",
     "explanation": "The government-guaranteed floor price for certain crops, meant to protect farmers from price crashes."},
    {"term": "Groundwater Proxy (GLDAS)", "full_form": None,
     "explanation": "Soil moisture at 100-200 cm depth from NASA's GLDAS model, used here as an indirect proxy for groundwater availability — not a direct well-level measurement."},
    {"term": "LST", "full_form": "Land Surface Temperature",
     "explanation": "The temperature of the land surface itself (not air temperature), measured from thermal satellite bands."},
    {"term": "CHIRPS", "full_form": "Climate Hazards Group InfraRed Precipitation with Station data",
     "explanation": "A satellite + rain-gauge rainfall dataset used for the rainfall figures in this app."},
    {"term": "Sentinel-2", "full_form": None,
     "explanation": "A European Space Agency satellite pair providing 10 m resolution optical imagery, the core data source for NDVI/NDMI here."},
    {"term": "Irrigation Signal", "full_form": None,
     "explanation": "An inference (not a direct measurement) of whether a field is irrigated, based on whether it stays green through the dry season."},
    {"term": "Nighttime Lights Proxy", "full_form": None,
     "explanation": "Satellite-measured night light intensity, used as an indirect proxy for local economic activity — not an official income or prosperity index."},
]
