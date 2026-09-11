#!/usr/bin/env python3
"""Load SAM-detected farm parcels into Neon PostGIS.

OFFLINE TOOL. Not imported by the web service and not covered by
Backend/requirements.txt on purpose — it needs geopandas, which pulls in
GDAL and is far too heavy for the Render dyno. Install separately:

    pip install geopandas shapely psycopg2-binary sqlalchemy

Pipeline position
-----------------
    village shapefile (vb_soi_or)
      -> per-village AOI
      -> Apple Basemap Base Map Download.py      (tiles -> GeoTIFF)
      -> Apple_SAM_Test3_final_without_Overlap.py (GeoTIFF -> parcels.shp)
      -> THIS SCRIPT                              (parcels.shp -> Neon)
      -> Bhumi resolves boundaries at request time via parcel_service.py

Usage
-----
    export DATABASE_URL='postgresql://...neon.tech/...?sslmode=require'

    # create the table and index (safe to re-run)
    python tools/load_parcels.py --init

    # load a run, tagging which village it covers
    python tools/load_parcels.py parcels.shp \\
        --source "mobile_sam110 z18 2026-09" \\
        --village-name Kusupur --village-code 21_12_045 --district Bhadrak

    # replace a previous load for the same village instead of duplicating
    python tools/load_parcels.py parcels.shp --source "..." \\
        --village-code 21_12_045 --replace

Notes on Neon specifically
--------------------------
* PostGIS is available but NOT enabled on a new project — `--init` runs
  CREATE EXTENSION for you. If your role lacks permission, enable it from
  the Neon console first.
* Neon's free tier has a storage cap. Smallholder parcels run roughly
  1-2 KB each once stored with the index, so a district with ~250k parcels
  is in the hundreds of MB. Check your plan before loading a whole district;
  this script prints an estimate before it commits.
* Neon compute autosuspends when idle. The first query after a pause pays a
  cold start — that hits the app's parcel lookup, not this loader.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

try:
    import geopandas as gpd
    from shapely.geometry import MultiPolygon
except ImportError:
    sys.exit(
        "geopandas is required for this offline tool:\n"
        "    pip install geopandas shapely\n"
        "(it is deliberately absent from Backend/requirements.txt — GDAL is "
        "too heavy for the deployed service)"
    )

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from parcel_service import MAX_PLAUSIBLE_PARCEL_HA, SCHEMA_SQL  # noqa: E402

TARGET_CRS = "EPSG:4326"
# Anything smaller than this is segmentation noise — a sliver along a bund,
# not a field.
MIN_PARCEL_HA = 0.02


def _engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set. Copy it from the Neon console.")
    if url.startswith("postgres") and "sslmode" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return create_engine(url, pool_pre_ping=True)


def _prepare(path: str):
    frame = gpd.read_file(path)
    if frame.empty:
        sys.exit(f"{path} contains no features.")

    if frame.crs is None:
        sys.exit(
            f"{path} has no CRS. The SAM script writes a .prj — if it is "
            "missing, the polygons cannot be placed on the globe and loading "
            "them would silently put every farm in the wrong location."
        )

    original_crs = frame.crs
    if frame.crs.to_string() != TARGET_CRS:
        # The SAM output is in the basemap's UTM zone (EPSG:32642 by
        # default in the download script). Storing that as 4326 without
        # reprojecting would place Odisha parcels somewhere off the coast
        # of Africa.
        frame = frame.to_crs(TARGET_CRS)

    # Geodesic area, via an equal-area projection rather than the geographic
    # CRS — area in degrees is not an area.
    frame["area_ha"] = frame.to_crs("EPSG:6933").geometry.area / 10_000.0

    before = len(frame)
    frame = frame[(frame.area_ha >= MIN_PARCEL_HA) & (frame.area_ha <= MAX_PLAUSIBLE_PARCEL_HA)]
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty]

    # The table column is MultiPolygon; a mixed Polygon/MultiPolygon frame
    # would be rejected on insert.
    frame["geometry"] = frame.geometry.apply(
        lambda g: g if g.geom_type == "MultiPolygon" else MultiPolygon([g])
    )

    dropped = before - len(frame)
    print(f"  read {before:,} features from {os.path.basename(path)} ({original_crs.to_string()})")
    if dropped:
        print(f"  dropped {dropped:,} outside {MIN_PARCEL_HA}-{MAX_PLAUSIBLE_PARCEL_HA} ha or invalid")
    print(f"  keeping {len(frame):,} parcels, median {frame.area_ha.median():.2f} ha, "
          f"total {frame.area_ha.sum():,.0f} ha")
    print(f"  estimated storage: ~{len(frame) * 1.5 / 1024:.0f} MB")
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("shapefile", nargs="?", help="SAM output .shp (or .geojson/.gpkg)")
    parser.add_argument("--init", action="store_true", help="Create the extension, table and indexes, then exit")
    parser.add_argument("--source", help="Provenance tag, e.g. 'mobile_sam110 z18 2026-09'")
    parser.add_argument("--village-name")
    parser.add_argument("--village-code")
    parser.add_argument("--district")
    parser.add_argument("--replace", action="store_true",
                        help="Delete existing parcels with the same village_code first")
    parser.add_argument("--dry-run", action="store_true", help="Report what would load, write nothing")
    args = parser.parse_args()

    engine = _engine()

    if args.init:
        with engine.begin() as conn:
            conn.execute(text(SCHEMA_SQL))
        print("Schema ready (postgis extension, farm_parcels table, GiST index).")
        return 0

    if not args.shapefile:
        parser.error("a shapefile is required unless --init is given")
    if not args.source:
        parser.error("--source is required so a bad run can be identified and removed later")
    if args.replace and not args.village_code:
        parser.error("--replace needs --village-code to know what to replace")

    frame = _prepare(args.shapefile)

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    rows = [
        {
            "wkt": geom.wkt,
            # Stable across runs: same geometry + same village -> same uid.
            "parcel_uid": hashlib.sha1(
                f"{args.village_code or ''}|{geom.wkb_hex}".encode()
            ).hexdigest(),
            "village_name": args.village_name,
            "village_code": args.village_code,
            "district": args.district,
            "source": args.source,
        }
        for geom in frame.geometry
    ]

    with engine.begin() as conn:
        if args.replace:
            if not args.village_code:
                # `WHERE village_code = NULL` is never true in SQL, so this
                # would delete nothing and then load a duplicate set while
                # printing a reassuring "replaced" line.
                raise SystemExit(
                    "--replace needs --village-code: without it the DELETE matches no rows "
                    "and you would end up with duplicates. Re-running without --replace is "
                    "safe now anyway — the load upserts on parcel_uid."
                )
            deleted = conn.execute(
                text("DELETE FROM farm_parcels WHERE village_code = :code"),
                {"code": args.village_code},
            ).rowcount
            print(f"  replaced: deleted {deleted:,} existing parcels for {args.village_code}")

        conn.execute(
            text(
                """
                INSERT INTO farm_parcels
                    (parcel_uid, geom, village_name, village_code, district, source)
                VALUES (
                    :parcel_uid,
                    ST_Multi(ST_GeomFromText(:wkt, 4326)),
                    :village_name, :village_code, :district, :source
                )
                -- Makes a re-run idempotent rather than duplicative.
                ON CONFLICT (parcel_uid) DO UPDATE SET
                    geom         = EXCLUDED.geom,
                    village_name = EXCLUDED.village_name,
                    district     = EXCLUDED.district,
                    source       = EXCLUDED.source,
                    detected_at  = now()
                """
            ),
            rows,
        )

    print(f"\nLoaded {len(rows):,} parcels.")
    print("Verify with:  SELECT count(*), district FROM farm_parcels GROUP BY district;")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
