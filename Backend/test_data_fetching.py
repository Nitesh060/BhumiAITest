"""
Diagnostic script to test actual API responses for missing data parameters.
Run this to debug why Rainfall, Solar Radiation, SPI, and SPEI are returning null.

Usage:
    python test_data_fetching.py
"""
import os
import sys
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Test coordinates (Indian agricultural region)
TEST_LAT = 28.6139
TEST_LNG = 77.2090
TEST_LOCATION = f"Delhi ({TEST_LAT}, {TEST_LNG})"

def test_earth_engine_init():
    """Test if Earth Engine can be initialized."""
    logger.info("=" * 80)
    logger.info("TEST 1: Earth Engine Initialization")
    logger.info("=" * 80)
    
    try:
        from earth_engine_service import initialise_earth_engine
        initialise_earth_engine()
        logger.info("✅ Earth Engine initialized successfully")
        return True
    except FileNotFoundError as e:
        logger.error(f"❌ Credentials not found: {e}")
        logger.error("   Set GEE_KEY_FILE, GOOGLE_CREDENTIALS, or place key at Backend/credentials/gee-service-account.json")
        return False
    except Exception as e:
        logger.error(f"❌ Earth Engine init failed: {type(e).__name__}: {e}")
        return False


def test_rainfall_fetch():
    """Test rainfall data fetching."""
    logger.info("=" * 80)
    logger.info("TEST 2: Rainfall Data Fetch")
    logger.info("=" * 80)
    logger.info(f"Location: {TEST_LOCATION}")
    
    try:
        from earth_engine_service import _fetch_rainfall
        result = _fetch_rainfall(TEST_LAT, TEST_LNG, polygon=None)
        
        if result is None:
            logger.warning("⚠️  Rainfall returned None")
            logger.info("   Possible causes:")
            logger.info("   - CHIRPS dataset has no data for this location/date")
            logger.info("   - Earth Engine API timeout or rate limit")
            logger.info("   - Location is outside CHIRPS coverage")
            return None
        
        logger.info(f"✅ Rainfall fetched: {result} mm/day")
        return result
    except Exception as e:
        logger.error(f"❌ Rainfall fetch failed: {type(e).__name__}: {e}")
        return None


def test_solar_radiation_fetch():
    """Test solar radiation data fetching."""
    logger.info("=" * 80)
    logger.info("TEST 3: Solar Radiation Data Fetch")
    logger.info("=" * 80)
    logger.info(f"Location: {TEST_LOCATION}")
    
    try:
        from weather_indices_service import fetch_solar_radiation
        result = fetch_solar_radiation(TEST_LAT, TEST_LNG, polygon=None)
        
        if result.get("available") is False:
            logger.warning(f"⚠️  Solar radiation unavailable: {result.get('reason')}")
            return None
        
        solar_val = result.get("avg_daily_solar_radiation_mj_m2")
        logger.info(f"✅ Solar radiation fetched: {solar_val} MJ/m²/day")
        return solar_val
    except Exception as e:
        logger.error(f"❌ Solar radiation fetch failed: {type(e).__name__}: {e}")
        return None


def test_spi_fetch():
    """Test SPI (Standardized Precipitation Index) data fetching."""
    logger.info("=" * 80)
    logger.info("TEST 4: SPI (Standardized Precipitation Index) Fetch")
    logger.info("=" * 80)
    logger.info(f"Location: {TEST_LOCATION}")
    
    try:
        from weather_indices_service import fetch_spi
        result = fetch_spi(TEST_LAT, TEST_LNG, polygon=None)
        
        if result.get("available") is False:
            logger.warning(f"⚠️  SPI unavailable: {result.get('reason')}")
            return None
        
        spi_val = result.get("spi")
        category = result.get("category")
        logger.info(f"✅ SPI fetched: {spi_val} ({category})")
        return spi_val
    except Exception as e:
        logger.error(f"❌ SPI fetch failed: {type(e).__name__}: {e}")
        return None


def test_gdd_fetch():
    """Test GDD (Growing Degree Days) data fetching."""
    logger.info("=" * 80)
    logger.info("TEST 5: GDD (Growing Degree Days) Fetch")
    logger.info("=" * 80)
    logger.info(f"Location: {TEST_LOCATION}")
    
    try:
        from weather_indices_service import fetch_gdd
        result = fetch_gdd(TEST_LAT, TEST_LNG, polygon=None)
        
        if result.get("available") is False:
            logger.warning(f"⚠️  GDD unavailable: {result.get('reason')}")
            return None
        
        gdd_val = result.get("gdd")
        logger.info(f"✅ GDD fetched: {gdd_val} GDD-units")
        return gdd_val
    except Exception as e:
        logger.error(f"❌ GDD fetch failed: {type(e).__name__}: {e}")
        return None


def test_spei_fetch():
    """Test SPEI (Standardized Precipitation-Evapotranspiration Index) data fetching."""
    logger.info("=" * 80)
    logger.info("TEST 6: SPEI (Water Balance Proxy) Fetch")
    logger.info("=" * 80)
    logger.info(f"Location: {TEST_LOCATION}")
    
    try:
        from weather_indices_service import fetch_spei_proxy
        result = fetch_spei_proxy(TEST_LAT, TEST_LNG, polygon=None)
        
        if result.get("available") is False:
            logger.warning(f"⚠️  SPEI unavailable: {result.get('reason')}")
            return None
        
        spei_val = result.get("spei_proxy")
        category = result.get("category")
        logger.info(f"✅ SPEI fetched: {spei_val} ({category})")
        return spei_val
    except Exception as e:
        logger.error(f"❌ SPEI fetch failed: {type(e).__name__}: {e}")
        return None


def test_comprehensive_score():
    """Test comprehensive score with all parameters."""
    logger.info("=" * 80)
    logger.info("TEST 7: Comprehensive Score Calculation")
    logger.info("=" * 80)
    logger.info(f"Location: {TEST_LOCATION}")
    
    try:
        from earth_engine_service import fetch_farm_data
        from weather_indices_service import fetch_solar_radiation, fetch_spi, fetch_gdd, fetch_spei_proxy
        from spectral_indices import fetch_extended_indices
        from spectral_service import calculate_spectral_intelligence
        from comprehensive_score_service import compute_comprehensive_score
        
        logger.info("Fetching satellite data...")
        satellite_data = fetch_farm_data(TEST_LAT, TEST_LNG, polygon=None)
        logger.info(f"  - NDVI: {satellite_data.get('ndvi')}")
        logger.info(f"  - Rainfall: {satellite_data.get('rainfall')}")
        logger.info(f"  - Air Temperature: {satellite_data.get('air_temperature')}")
        logger.info(f"  - LST: {satellite_data.get('lst')}")
        
        logger.info("Fetching weather indices...")
        solar = fetch_solar_radiation(TEST_LAT, TEST_LNG, polygon=None)
        spi = fetch_spi(TEST_LAT, TEST_LNG, polygon=None)
        gdd = fetch_gdd(TEST_LAT, TEST_LNG, polygon=None)
        spei = fetch_spei_proxy(TEST_LAT, TEST_LNG, polygon=None)
        
        solar_val = solar.get("avg_daily_solar_radiation_mj_m2") if solar.get("available") else None
        spi_val = spi.get("spi") if spi.get("available") else None
        gdd_val = gdd.get("gdd") if gdd.get("available") else None
        spei_val = spei.get("spei_proxy") if spei.get("available") else None
        
        logger.info(f"  - Solar Radiation: {solar_val}")
        logger.info(f"  - SPI: {spi_val}")
        logger.info(f"  - GDD: {gdd_val}")
        logger.info(f"  - SPEI: {spei_val}")
        
        raw_values = {
            "ndvi": satellite_data.get("ndvi"),
            "ndmi": satellite_data.get("ndmi"),
            "rainfall": satellite_data.get("rainfall"),
            "air_temp": satellite_data.get("air_temperature"),
            "solar_radiation": solar_val,
            "spi": spi_val,
            "gdd": gdd_val,
            "spei": spei_val,
            "lst": satellite_data.get("lst"),
        }
        
        result = compute_comprehensive_score(raw_values)
        logger.info(f"✅ Comprehensive score computed: {result.get('score_0_100')}")
        logger.info(f"   Parameters used: {result.get('parameters_used')}")
        logger.info(f"   Confidence: {result.get('confidence')}")
        
        return result
    except Exception as e:
        logger.error(f"❌ Comprehensive score failed: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def main():
    """Run all diagnostic tests."""
    logger.info("\n")
    logger.info("╔" + "=" * 78 + "╗")
    logger.info("║" + " " * 78 + "║")
    logger.info("║" + "  BHUMI AI - DATA FETCHING DIAGNOSTIC TEST SUITE".center(78) + "║")
    logger.info("║" + f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".center(78) + "║")
    logger.info("║" + " " * 78 + "║")
    logger.info("╚" + "=" * 78 + "╝")
    
    results = {}
    
    # Test 1: Earth Engine init (required for all other tests)
    if not test_earth_engine_init():
        logger.error("\n❌ Earth Engine init failed. Cannot proceed with other tests.")
        logger.error("   Fix: Set up Google Earth Engine credentials")
        return results
    
    # Test 2-6: Individual parameter fetches
    results["rainfall"] = test_rainfall_fetch()
    results["solar_radiation"] = test_solar_radiation_fetch()
    results["spi"] = test_spi_fetch()
    results["gdd"] = test_gdd_fetch()
    results["spei"] = test_spei_fetch()
    
    # Test 7: Comprehensive score
    results["comprehensive_score"] = test_comprehensive_score()
    
    # Summary
    logger.info("\n")
    logger.info("╔" + "=" * 78 + "╗")
    logger.info("║" + " TEST SUMMARY ".center(78, "=") + "║")
    logger.info("║" + " " * 78 + "║")
    
    for test_name, result in results.items():
        status = "✅" if result is not None else "❌"
        logger.info(f"║  {status} {test_name:.<70} {str(result)[:5]:>5} ║")
    
    logger.info("║" + " " * 78 + "║")
    logger.info("╚" + "=" * 78 + "╝\n")
    
    return results


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n\nTest interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"\n\nFatal error: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
