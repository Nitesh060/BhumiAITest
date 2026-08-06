# SatSource Parity — What Was Added

## Already existed (SatSource sheet undersold the current app)
- Farm Boundary / Polygon Drawing / Farm Centroid — Leaflet.draw + turf.js were already wired in `app.js`
- Nearest Road, Nearest Mandi — already fetched live via OSM Overpass API

## New — works out of the box (real Earth Engine data, no extra keys needed)
| Feature | File | Notes |
|---|---|---|
| Soil Type | `enrichment_service.py::fetch_soil_type` | OpenLandMap USDA texture class |
| Adjacent Land Details | `fetch_adjacent_land_cover` | ESA WorldCover, 1km ring around farm |
| Cropping Intensity | `fetch_cropping_intensity` | NDVI seasonality peak-counting, approximate |
| Irrigation Detection | `fetch_irrigation_signal` | Dry-season NDVI greenness signal |
| Temperature Annual Range | `fetch_temperature_annual_range` | Full-year MODIS LST min/max |
| Regional Prosperity | `fetch_prosperity_proxy` | VIIRS nightlights — **proxy only**, not an official index |
| Nearest Water Body (satellite) | `fetch_nearest_water_body_signal` | JRC Global Surface Water, 2km |
| Cropping History (3yr) | `fetch_cropping_history` | Kharif/Rabi NDVI cropped-vs-fallow, NOT crop-species ID |
| Agro-Ecological Zone | `estimate_agro_ecological_zone` | Rule-based from rainfall+temp — **not** the official ICAR shapefile lookup |
| Topography | `fetch_topography` | SRTM 30m elevation + slope, classified into terrain description |
| Village Population (proxy) | `fetch_village_population` | WorldPop gridded estimate, ~1.5km radius — **not** exact Census |
| Drought Instances | `fetch_drought_instances` | CHIRPS annual rainfall since 2000, 25km "district-scale" buffer, years <75% of local long-term average |
| Nearest Water Body (map) | `Frontend/app.js` | Added `natural=water` / `waterway=river` to OSM POI search |
| Glossary | `glossary.py` + `/glossary` endpoint + dedicated Glossary page | Static content |
| Professional PDF Report | `pdf_report.py` + `/report/pdf` | Branded header/footer, real satellite thumbnail, all enrichment data |
| WhatsApp bot | `whatsapp_service.py` + `/webhook/whatsapp` | Same chatbot, over WhatsApp — see `WHATSAPP_SETUP.md` |

All of the above are wired into `compute_farmscore()` (shared by `/calculate` and the WhatsApp webhook) under `"enrichment": {...}`.

## New — needs YOUR configuration before it works
| Feature | File | What's needed |
|---|---|---|
| Crop Price / MSP | `govt_data_service.py::fetch_mandi_price` | Free API key from data.gov.in → set `DATA_GOV_IN_KEY` env var. Exposed at `GET /mandi-price?commodity=X&state=Y` |
| District Yield Comparison | `govt_data_service.py::fetch_district_yield_comparison` | Currently a stub — data.gov.in doesn't have one stable resource ID covering all 8 AFPL RTS states consistently. You'll need to pick/verify a resource ID per state, or a different source (e.g. state agri department portals) |
| Major Crops in Region | `govt_data_service.py::fetch_major_crops_in_region` | Same data.gov.in coverage gap as District Yield — stub for now. Exposed at `GET /major-crops?district=X&state=Y` once configured |

These could NOT be tested in the build environment (no internet access to api.data.gov.in from there) — test them after you deploy with a real key.

## Deferred (needs a decision, not just code)
- **Crop Performance** — would need either the above yield data working, or a trained crop-yield model; there's no satellite dataset that gives this directly.
- **Risk Classification "Excellent" tier** — `scoring.py` grading logic untouched; once mandi price + yield data are live, the risk model can be re-weighted to fold them in.

## A note on speed
Drought Instances runs ~25 separate rainfall queries (one per year since 2000) inside the parallel enrichment pool — it's the slowest single enrichment call. It won't block the rest of the response (runs in its own thread), but expect `/calculate` to take a bit longer than before on the first call to a new location.
