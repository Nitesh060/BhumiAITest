#!/usr/bin/env python3
"""Measure Bhumi's FarmScore against a reference report, row by row.

The structural bugs (phantom seasons, dropped seasons, the 400-point
rescale, flat-topped normalisers) are fixed in code. What remains is
CALIBRATION: how strict each parameter's ideal band in
`comprehensive_score_service.THRESHOLDS` should be. That cannot be guessed
from the reference spreadsheet alone, because the spreadsheet records only
the reference report's OUTPUT scores — not the satellite inputs behind
them. So this script fetches Bhumi's own inputs for each farm from the live
API and lines them up against the reference column.

Usage
-----
    # 1. Build a CSV with one row per farm from the comparison sheet:
    #    ref_id,lat,lng,base,kharif,rabi,total
    #    2322,21.050606,86.442146,75,278,200,553
    #
    # 2. Point it at a running backend:
    python tools/calibrate_thresholds.py farms.csv --api http://localhost:5000

Read the output as: if `kharif` is consistently over the reference, the
vegetation/radar bands are too generous — narrow `ideal_low`/`ideal_high`
in THRESHOLDS. If `base` is off, the fix is in
`seasonal_score_service.compute_base_score` instead. Change one group at a
time and re-run; the mean absolute error per component is the thing to
drive down.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import urllib.error
import urllib.request

COMPONENTS = ("base", "kharif", "rabi", "total")


def fetch_score(api: str, lat: float, lng: float, timeout: float) -> dict:
    payload = json.dumps({"lat": lat, "lng": lng}).encode()
    request = urllib.request.Request(
        f"{api.rstrip('/')}/calculate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", help="ref_id,lat,lng,base,kharif,rabi,total")
    parser.add_argument("--api", default="http://localhost:5000", help="Backend base URL")
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-farm timeout (the drought enrichment is slow)")
    args = parser.parse_args()

    with open(args.csv_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        print("No rows in CSV.", file=sys.stderr)
        return 1

    deltas: dict[str, list[float]] = {name: [] for name in COMPONENTS}

    header = f"{'ref_id':>12s} " + " ".join(f"{name:>22s}" for name in COMPONENTS)
    print(header)
    print(f"{'':>12s} " + " ".join(f"{'bhumi   ref   delta':>22s}" for _ in COMPONENTS))
    print("-" * len(header))

    for row in rows:
        ref_id = row.get("ref_id", "?")
        try:
            result = fetch_score(args.api, float(row["lat"]), float(row["lng"]), args.timeout)
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as exc:
            print(f"{ref_id:>12s}  FAILED: {type(exc).__name__}: {exc}")
            continue

        breakdown = (result.get("enrichment") or {}).get("farmscore_breakdown") or {}
        if not breakdown.get("available"):
            print(f"{ref_id:>12s}  no breakdown returned — {breakdown.get('reason', 'unknown reason')}")
            continue

        mine = {
            "base": breakdown["base"]["score"],
            "kharif": breakdown["kharif"]["score"],
            "rabi": breakdown["rabi"]["score"],
            "total": result.get("score"),
        }

        cells = []
        for name in COMPONENTS:
            got, want = mine[name], row.get(name)
            if got is None or not want:
                cells.append(f"{'-':>22s}")
                continue
            want = float(want)
            delta = got - want
            deltas[name].append(delta)
            cells.append(f"{got:>6.0f}{want:>6.0f}{delta:>+8.0f}  ")
        print(f"{ref_id:>12s} " + " ".join(cells))

    print()
    print("Summary — mean signed bias and mean absolute error per component:")
    for name in COMPONENTS:
        values = deltas[name]
        if not values:
            print(f"  {name:>7s}: no comparable rows")
            continue
        bias = statistics.fmean(values)
        mae = statistics.fmean(abs(v) for v in values)
        direction = "too generous" if bias > 0 else "too harsh"
        print(f"  {name:>7s}: bias {bias:+7.1f} ({direction}), MAE {mae:6.1f}, n={len(values)}")

    print()
    print("A positive `kharif`/`rabi` bias means the seasonal normalisers are")
    print("too generous — narrow the ideal bands in")
    print("comprehensive_score_service.THRESHOLDS and re-run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
