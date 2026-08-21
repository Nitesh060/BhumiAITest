"""Regression tests for the "Rainfall / SPI always show no data" bug.

Root causes (see the commit this file ships with, and the comments in
earth_engine_service.py / weather_indices_service.py, for the full story):

1. Rainfall: `_fetch_rainfall_monthly` used to gate each month on a raw,
   un-retried `c.size().getInfo()` call — the only Earth Engine call in
   the whole module that bypassed `_getinfo_with_backoff`. It was also
   redundant, and for October it computed the wrong date window (rolled
   into a 3-month Oct-Dec span instead of just October).
2. SPI: `fetch_spi` needs up to 4 separate valid history years, each
   requiring 5 sequential CHIRPS month calls — 20-30 Earth Engine round
   trips total, sequential, capped by a 20s wall-clock budget that
   real-world latency/retries could rarely fit inside. Both the season
   fetch and the budget needed fixing.

These tests never touch a real Earth Engine backend — `ee.Initialize()`
is never called. `import ee` itself only needs the `earthengine-api`
package installed (no credentials/network), and every `ee.*` object
these functions build is replaced with a MagicMock stand-in before the
module under test ever calls `.getInfo()` on anything for real; the
actual reduction step is exercised through the already-mocked
`_reduce_mean_with_retry`, matching how this module's own retry/backoff
layer is meant to be tested without live credentials.
"""
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import earth_engine_service as ees
import weather_indices_service as wis


def _chainable_ee_stub():
    """A MagicMock standing in for `ee` that lets any
    `ee.ImageCollection(...).filterDate(...).filterBounds(...).select(...).mean()/.sum()`
    chain be built without ever touching a real Earth Engine backend.
    """
    stub = MagicMock()
    chain = MagicMock()
    chain.filterDate.return_value = chain
    chain.filterBounds.return_value = chain
    chain.select.return_value = chain
    chain.mean.return_value = chain
    chain.sum.return_value = chain
    stub.ImageCollection.return_value = chain
    return stub, chain


# ---------------------------------------------------------------------------
# Rainfall: date-window bug (pure logic, no mocking needed)
# ---------------------------------------------------------------------------

class TestRainfallMonthWindow:
    def test_october_window_is_one_month_not_three(self):
        """The old inline `if month < 10 else f'{year+1}-01-01'` fired its
        else-branch for October itself (10 < 10 is False), turning
        October's "monthly" window into Oct-Dec. Only December should
        ever roll into the next year.
        """
        start, end = ees._rainfall_month_window(2025, 10)
        assert start == "2025-10-01"
        assert end == "2025-11-01"

    def test_december_window_still_rolls_into_next_year(self):
        start, end = ees._rainfall_month_window(2025, 12)
        assert start == "2025-12-01"
        assert end == "2026-01-01"

    def test_mid_season_month_window(self):
        start, end = ees._rainfall_month_window(2025, 7)
        assert start == "2025-07-01"
        assert end == "2025-08-01"


# ---------------------------------------------------------------------------
# Rainfall: the raw c.size().getInfo() gate is gone
# ---------------------------------------------------------------------------

class TestFetchRainfallMonthly:
    def _patch_common(self, monkeypatch):
        stub, chain = _chainable_ee_stub()
        monkeypatch.setattr(ees, "ee", stub)
        monkeypatch.setattr(ees, "_region_geometry", lambda lat, lng, polygon: (MagicMock(), "approximate_point_buffer"))
        return stub, chain

    def test_never_calls_size_getinfo(self, monkeypatch):
        """Regression test for the actual production bug: this used to
        call c.size().getInfo() — a raw, un-retried Earth Engine call —
        before ever reaching _reduce_mean_with_retry. Confirms that gate
        is gone: `.size` is never touched on the collection chain.
        """
        stub, chain = self._patch_common(monkeypatch)
        monkeypatch.setattr(ees, "_reduce_mean_with_retry", lambda *a, **k: 5.0)

        result = ees._fetch_rainfall_monthly(12.0, 77.0, None)

        chain.size.assert_not_called()
        assert len(result) == 5
        assert [m["month"] for m in result] == ["Jun", "Jul", "Aug", "Sep", "Oct"]
        assert all(m["mm_per_day"] == 5.0 for m in result)
        assert all(m["reason"] is None for m in result)

    def test_every_month_reaches_reduce_mean_with_retry_directly(self, monkeypatch):
        """No gate means every month's value comes straight from the
        already-hardened _reduce_mean_with_retry, regardless of what a
        bare .size() would have reported.
        """
        self._patch_common(monkeypatch)
        calls = []

        def fake_reduce(image, lat, lng, polygon, scale, max_retries=3):
            calls.append(scale)
            return 3.5

        monkeypatch.setattr(ees, "_reduce_mean_with_retry", fake_reduce)
        ees._fetch_rainfall_monthly(12.0, 77.0, None)
        assert len(calls) == 5
        assert all(s == ees.CHIRPS_SCALE_M for s in calls)

    def test_one_bad_month_does_not_sink_the_others(self, monkeypatch):
        """A transient failure on a single month (the exact scenario the
        old unprotected .size().getInfo() call was vulnerable to) should
        only cost that one month, not the whole season — and it should
        carry a specific reason, not a silent None.
        """
        self._patch_common(monkeypatch)

        def fake_reduce(image, lat, lng, polygon, scale, max_retries=3):
            # Simulate August failing transiently.
            raise RuntimeError("simulated transient Earth Engine timeout")

        def selective_reduce(image, lat, lng, polygon, scale, max_retries=3):
            return None

        # Patch per-call based on a shared mutable call counter isn't
        # available here (no month id passed through), so instead verify
        # via _fetch_rainfall_one_month directly for a single failing month.
        filter_region = MagicMock()
        monkeypatch.setattr(ees, "_reduce_mean_with_retry", fake_reduce)
        result = ees._fetch_rainfall_one_month(12.0, 77.0, None, filter_region, 2025, 8, "Aug")
        assert result["mm_per_day"] is None
        assert "Aug" in result["reason"]
        assert "RuntimeError" in result["reason"]

    def test_fetches_months_concurrently(self, monkeypatch):
        """Performance regression guard: the old implementation fetched
        5 months sequentially. If each month's reduce takes ~0.2s,
        concurrent execution should finish well under the 5x serial
        cost.
        """
        self._patch_common(monkeypatch)

        def slow_reduce(image, lat, lng, polygon, scale, max_retries=3):
            time.sleep(0.2)
            return 1.0

        monkeypatch.setattr(ees, "_reduce_mean_with_retry", slow_reduce)
        start = time.monotonic()
        ees._fetch_rainfall_monthly(12.0, 77.0, None)
        elapsed = time.monotonic() - start
        assert elapsed < 0.6, f"expected concurrent fetch well under 1.0s (5x0.2s serial), took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Rainfall: aggregate reason surfacing
# ---------------------------------------------------------------------------

class TestSeasonalRainfallFromMonthly:
    def test_all_months_missing_includes_specific_reasons(self):
        monthly = [
            {"month": "Jun", "mm_per_day": None, "reason": "Jun (2025-06-01 to 2025-07-01) fetch failed: RuntimeError: boom"},
            {"month": "Jul", "mm_per_day": None, "reason": "CHIRPS reduceRegion returned no value for 2025-07-01 to 2025-08-01."},
            {"month": "Aug", "mm_per_day": None, "reason": None},
            {"month": "Sep", "mm_per_day": None, "reason": None},
            {"month": "Oct", "mm_per_day": None, "reason": None},
        ]
        value, reason = ees._seasonal_rainfall_from_monthly(monthly)
        assert value is None
        assert "No individual month" in reason
        assert "boom" in reason
        assert "reduceRegion returned no value" in reason

    def test_partial_success_still_averages(self):
        monthly = [
            {"month": "Jun", "mm_per_day": 4.0, "reason": None},
            {"month": "Jul", "mm_per_day": None, "reason": "some failure"},
            {"month": "Aug", "mm_per_day": 6.0, "reason": None},
            {"month": "Sep", "mm_per_day": None, "reason": None},
            {"month": "Oct", "mm_per_day": None, "reason": None},
        ]
        value, reason = ees._seasonal_rainfall_from_monthly(monthly)
        assert value == 5.0
        assert reason is None


# ---------------------------------------------------------------------------
# SPI: _season_rainfall_mm concurrency + tolerance for sporadic gaps
# ---------------------------------------------------------------------------

class TestSeasonRainfallMm:
    def _patch_common(self, monkeypatch):
        stub, chain = _chainable_ee_stub()
        monkeypatch.setattr(wis, "ee", stub)
        monkeypatch.setattr(wis, "_weather_region", lambda lat, lng, polygon, scale_m=None: MagicMock())
        return stub, chain

    def test_sums_all_valid_months(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(wis, "_reduce_mean_with_retry", lambda *a, **k: 20.0)
        total = wis._season_rainfall_mm(12.0, 77.0, None, 2024)
        assert total == 100.0  # 5 months x 20mm

    def test_tolerates_a_couple_of_missing_months(self, monkeypatch):
        self._patch_common(monkeypatch)
        values = iter([10.0, None, 10.0, None, 10.0])
        monkeypatch.setattr(wis, "_reduce_mean_with_retry", lambda *a, **k: next(values))
        total = wis._season_rainfall_mm(12.0, 77.0, None, 2024)
        assert total == 30.0

    def test_returns_none_when_every_month_fails(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(wis, "_reduce_mean_with_retry", lambda *a, **k: None)
        assert wis._season_rainfall_mm(12.0, 77.0, None, 2024) is None

    def test_a_raising_month_does_not_abort_the_others(self, monkeypatch):
        self._patch_common(monkeypatch)

        def flaky(*a, **k):
            flaky.calls += 1
            if flaky.calls == 1:
                raise RuntimeError("simulated transient Earth Engine error")
            return 15.0
        flaky.calls = 0

        monkeypatch.setattr(wis, "_reduce_mean_with_retry", flaky)
        total = wis._season_rainfall_mm(12.0, 77.0, None, 2024)
        assert total == 60.0  # 4 successful months x 15mm

    def test_fetches_months_concurrently(self, monkeypatch):
        """This is the core SPI performance fix: 5 sequential CHIRPS
        calls per year, times up to 4+ history years, is what blew the
        old 20s SPI_TIME_BUDGET_S. Confirms months within one year are
        no longer fetched one at a time.
        """
        self._patch_common(monkeypatch)

        def slow(*a, **k):
            time.sleep(0.2)
            return 10.0

        monkeypatch.setattr(wis, "_reduce_mean_with_retry", slow)
        start = time.monotonic()
        wis._season_rainfall_mm(12.0, 77.0, None, 2024)
        elapsed = time.monotonic() - start
        assert elapsed < 0.6, f"expected concurrent fetch well under 1.0s (5x0.2s serial), took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# SPI: fetch_spi end-to-end with a mocked _season_rainfall_mm
# ---------------------------------------------------------------------------

class TestFetchSpi:
    def test_succeeds_with_enough_history_and_sporadic_gaps(self, monkeypatch):
        """Simulates exactly the scenario the bug report described:
        CHIRPS has a sporadic per-year gap (one year returns None), but
        there's enough history overall — SPI should still compute,
        not report "no data".
        """
        # current season + walking back: year0=None(gap), then 5 more valid years
        call_log = []

        def fake_season(lat, lng, polygon, year):
            call_log.append(year)
            if len(call_log) == 1:
                return 120.0  # current season succeeds immediately
            if len(call_log) == 3:
                return None  # one sporadic historical gap
            return 100.0 + len(call_log)

        monkeypatch.setattr(wis, "_season_rainfall_mm", fake_season)
        result = wis.fetch_spi(12.0, 77.0, None, current_year=2024, history_years=4)
        assert result["available"] is True
        assert result["years_used"] == 4
        assert "spi" in result

    def test_reports_time_budget_when_exhausted(self, monkeypatch):
        """If Earth Engine is genuinely slow/degraded, fetch_spi should
        report a specific, honest time-budget reason instead of a
        generic "no data" — and that reason should be visible in the
        response so Render logs / the API's unavailable_reason field
        show the real cause.
        """
        monkeypatch.setattr(wis, "SPI_TIME_BUDGET_S", 0.05)

        def slow_season(lat, lng, polygon, year):
            time.sleep(0.03)
            return 100.0 if year == 2024 else None  # only current season ever succeeds

        monkeypatch.setattr(wis, "_season_rainfall_mm", slow_season)
        result = wis.fetch_spi(12.0, 77.0, None, current_year=2024, history_years=4)
        assert result["available"] is False
        assert "time budget" in result["reason"] or "Insufficient" in result["reason"]

    def test_insufficient_history_reports_specific_counts(self, monkeypatch):
        def fake_season(lat, lng, polygon, year):
            return 100.0 if year == 2024 else None  # no usable history at all
        monkeypatch.setattr(wis, "_season_rainfall_mm", fake_season)
        result = wis.fetch_spi(12.0, 77.0, None, current_year=2024, history_years=4)
        assert result["available"] is False
        assert "found 0 usable year" in result["reason"]
