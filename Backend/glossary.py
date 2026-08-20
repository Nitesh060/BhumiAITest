"""
glossary.py
===========
Static glossary content served by GET /glossary. Plain data — no GEE
calls, so it's instant and always available even if Earth Engine is down.
"""


class _GlossaryTerms(list):
    """List-compatible glossary that also supports report lookups by term.

    The frontend/API historically consumes GLOSSARY_TERMS as a list of
    dictionaries. The PDF report also needs dictionary-style lookup by term.
    Supporting both interfaces keeps the API response backwards compatible
    while allowing the report generator to use ``term in terms`` and
    ``terms[term]`` safely.
    """

    def __contains__(self, item):
        if isinstance(item, str):
            return any(isinstance(row, dict) and row.get("term") == item for row in self)
        return super().__contains__(item)

    def __getitem__(self, key):
        if isinstance(key, str):
            for row in self:
                if isinstance(row, dict) and row.get("term") == key:
                    full_form = row.get("full_form")
                    explanation = row.get("explanation") or ""
                    return f"{full_form}. {explanation}" if full_form else explanation
            raise KeyError(key)
        return super().__getitem__(key)

    def items(self):
        """Provide dict-like iteration for report code while staying list-like."""
        for row in self:
            if isinstance(row, dict) and row.get("term"):
                full_form = row.get("full_form")
                explanation = row.get("explanation") or ""
                meaning = f"{full_form}. {explanation}" if full_form else explanation
                yield row["term"], meaning


GLOSSARY_TERMS = _GlossaryTerms([
    # ---- The 20 FarmScore parameters (Vegetation / Radar / Weather / Temperature) ----
    {"term": "NDVI", "full_form": "Normalized Difference Vegetation Index",
     "explanation": "Measures vegetation greenness/health from satellite bands. Higher = healthier, denser plant growth."},
    {"term": "EVI", "full_form": "Enhanced Vegetation Index",
     "explanation": "Similar to NDVI but corrects for atmospheric haze and soil background, so it stays accurate even in dense canopy where NDVI saturates. Higher = healthier vegetation."},
    {"term": "SAVI", "full_form": "Soil Adjusted Vegetation Index",
     "explanation": "A vegetation index that reduces the influence of visible soil in the background — useful for young or sparse crops where bare soil would otherwise skew NDVI. Higher = healthier vegetation."},
    {"term": "MSAVI", "full_form": "Modified Soil Adjusted Vegetation Index",
     "explanation": "A refined version of SAVI that self-adjusts for soil brightness without needing a manually tuned constant. Higher = healthier vegetation."},
    {"term": "NDRE", "full_form": "Normalized Difference Red Edge",
     "explanation": "Uses the red-edge band, which is more sensitive to canopy nitrogen/chlorophyll than plain NDVI. Used here as a crop-health / nitrogen-status signal. Higher = better nitrogen status."},
    {"term": "NDMI", "full_form": "Normalized Difference Moisture Index",
     "explanation": "Measures moisture content in plant canopy. Higher = more water in the crop/vegetation."},
    {"term": "NDWI", "full_form": "Normalized Difference Water Index",
     "explanation": "Detects surface water presence (ponds, flooding, waterlogging) from satellite imagery."},
    {"term": "CI_Green", "full_form": "Chlorophyll Index (Green)",
     "explanation": "Estimates leaf chlorophyll content using the green band — a proxy for how photosynthetically active the crop canopy is. Higher = more chlorophyll."},
    {"term": "CI_RedEdge", "full_form": "Chlorophyll Index (Red Edge)",
     "explanation": "Same idea as CI_Green but using the red-edge band, which is more sensitive at higher chlorophyll/biomass levels. Higher = more chlorophyll."},
    {"term": "VV", "full_form": "Radar Backscatter (Vertical-Vertical polarization)",
     "explanation": "Sentinel-1 radar signal strength (in dB) reflected back from the field. Sensitive to canopy structure and surface moisture; works even through cloud cover."},
    {"term": "VH", "full_form": "Cross-Polarized Radar Backscatter (Vertical-Horizontal)",
     "explanation": "Sentinel-1 radar signal (in dB) that is especially sensitive to crop volume/biomass and canopy structure. Works even through cloud cover."},
    {"term": "VH/VV Ratio", "full_form": None,
     "explanation": "The ratio of the VH and VV radar signals — helps separate vegetation structure from soil/surface effects better than either signal alone."},
    {"term": "RVI", "full_form": "Radar Vegetation Index",
     "explanation": "A vegetation-density index computed purely from Sentinel-1 radar (VV/VH), so it stays usable even when optical satellites are blocked by clouds."},
    {"term": "Rainfall", "full_form": None,
     "explanation": "Mean daily rainfall (mm/day) for the growing season from CHIRPS. Too little stresses the crop; too much risks waterlogging."},
    {"term": "Air Temperature", "full_form": None,
     "explanation": "Ambient air temperature (°C). In this app it is currently sourced from the same MODIS Land Surface Temperature signal as LST, so it is shown for reference but is not separately weighted in the FarmScore (to avoid double-counting one signal)."},
    {"term": "Solar Radiation", "full_form": None,
     "explanation": "Average daily incoming solar energy (MJ/m²/day) from ERA5-Land — a key driver of photosynthesis and crop growth rate."},
    {"term": "SPI", "full_form": "Standardized Precipitation Index",
     "explanation": "Compares current rainfall against the historical average for the same location, flagging drought (very negative) or unusually wet (very positive) conditions — context a single rainfall number can't give."},
    {"term": "SPEI", "full_form": "Standardized Precipitation-Evapotranspiration Index",
     "explanation": "Like SPI, but also accounts for temperature-driven water loss (evapotranspiration) — catches heat-driven moisture stress even when rainfall looks normal."},
    {"term": "GDD", "full_form": "Growing Degree Days",
     "explanation": "Accumulated heat units over the growing season, used to gauge whether crop development is on track, behind schedule, or heat-stressed."},
    {"term": "LST", "full_form": "Land Surface Temperature",
     "explanation": "The temperature of the land surface itself (not air temperature), measured from thermal satellite bands."},
    # ---- Other terms used elsewhere in the app ----
    {"term": "Cropping Intensity", "full_form": None,
     "explanation": "How many crop cycles a field goes through in a year — single (mono), double, or triple cropping."},
    {"term": "Agro-Ecological Zone (AEZ)", "full_form": None,
     "explanation": "A regional classification based on climate (rainfall, temperature) and soil that groups areas with similar farming potential."},
    {"term": "MSP", "full_form": "Minimum Support Price",
     "explanation": "The government-guaranteed floor price for certain crops, meant to protect farmers from price crashes."},
    {"term": "Groundwater Proxy (GLDAS)", "full_form": None,
     "explanation": "Soil moisture at 100-200 cm depth from NASA's GLDAS model, used here as an indirect proxy for groundwater availability — not a direct well-level measurement."},
    {"term": "CHIRPS", "full_form": "Climate Hazards Group InfraRed Precipitation with Station data",
     "explanation": "A satellite + rain-gauge rainfall dataset used for the rainfall figures in this app."},
    {"term": "Sentinel-1", "full_form": None,
     "explanation": "A European Space Agency radar satellite pair. Unlike optical satellites, radar sees through clouds — the source for the VV/VH/RVI parameters here."},
    {"term": "Sentinel-2", "full_form": None,
     "explanation": "A European Space Agency satellite pair providing 10 m resolution optical imagery, the core data source for NDVI/NDMI/EVI/SAVI/etc. here."},
    {"term": "Irrigation Signal", "full_form": None,
     "explanation": "An inference (not a direct measurement) of whether a field is irrigated, based on whether it stays green through the dry season."},
    {"term": "Nighttime Lights Proxy", "full_form": None,
     "explanation": "Satellite-measured night light intensity, used as an indirect proxy for local economic activity — not an official income or prosperity index."},
])
