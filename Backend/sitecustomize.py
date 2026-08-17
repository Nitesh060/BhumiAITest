"""Runtime compatibility patch for rolling cropping-history years.

The application has multiple callers of enrichment_service.fetch_cropping_history().
Keep the underlying satellite logic unchanged, but make the default three-year
window use the three most recently completed crop years rather than the old
hard-coded 2021-2023 window.

For August 2026 this means 2023, 2024 and 2025. The current incomplete 2026
Kharif season is deliberately excluded so the comparison remains complete and
like-for-like.
"""

from datetime import datetime

try:
    import enrichment_service as _enrichment

    _original_fetch_cropping_history = _enrichment.fetch_cropping_history

    def fetch_cropping_history_current(lat, lng, polygon=None, years=None):
        if years is None:
            current_year = datetime.utcnow().year
            years = tuple(range(current_year - 3, current_year))
        return _original_fetch_cropping_history(lat, lng, polygon, years=years)

    _enrichment.fetch_cropping_history = fetch_cropping_history_current
except Exception:
    # Never prevent application startup if the optional patch cannot load.
    pass
