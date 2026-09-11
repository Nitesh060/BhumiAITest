"""Regression tests for hardcoded satellite date windows.

Every window in `enrichment_service` was a literal: cropping intensity was
pinned to calendar 2023, irrigation to Feb-Apr 2024, the temperature range
and nightlights proxy to 2023, imagery to "2025-01-01" onward. Nothing
failed when they went stale — the app just kept answering confidently from
years-old data. Two of them feed the Base Score.
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from enrichment_service import last_dry_season, latest_complete_year, recent_imagery_window

SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "enrichment_service.py")


def test_no_literal_dates_remain_in_filterdate_calls():
    with open(SOURCE, encoding="utf-8") as handle:
        body = handle.read()
    offenders = re.findall(r'filterDate\(\s*["\']\d{4}-\d{2}-\d{2}', body)
    assert not offenders, f"hardcoded filterDate literals: {offenders}"


def test_latest_complete_year_lags_the_current_year():
    assert latest_complete_year(date(2026, 9, 9)) == 2025
    # In January the previous year's data may not have fully published yet.
    assert latest_complete_year(date(2026, 1, 15)) == 2024


def test_dry_season_window_is_always_in_the_past():
    for day in (date(2026, 3, 1), date(2026, 9, 9), date(2027, 1, 20)):
        start, end = last_dry_season(day)
        assert date.fromisoformat(end) <= day, f"{day}: dry season ends {end}"
        assert date.fromisoformat(start) < date.fromisoformat(end)


def test_dry_season_rolls_forward_with_the_calendar():
    assert last_dry_season(date(2026, 9, 9))[0].startswith("2026")
    assert last_dry_season(date(2028, 9, 9))[0].startswith("2028")


def test_imagery_window_ends_today_and_looks_back():
    start, end = recent_imagery_window(18, date(2026, 9, 9))
    assert end == "2026-09-09"
    assert date.fromisoformat(start) < date(2025, 9, 9)
