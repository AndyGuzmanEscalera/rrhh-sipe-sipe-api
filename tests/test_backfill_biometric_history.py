from datetime import date

from app.scripts.backfill_biometric_history import monthly_ranges


def ranges(start, end):
    return [(item.start, item.end) for item in monthly_ranges(start, end)]


def test_monthly_ranges_handle_calendar_boundaries_and_partial_months():
    assert ranges(date(2026, 1, 1), date(2026, 1, 31)) == [(date(2026, 1, 1), date(2026, 1, 31))]
    assert ranges(date(2026, 2, 1), date(2026, 2, 28)) == [(date(2026, 2, 1), date(2026, 2, 28))]
    assert ranges(date(2028, 2, 1), date(2028, 2, 29)) == [(date(2028, 2, 1), date(2028, 2, 29))]
    assert ranges(date(2026, 4, 1), date(2026, 4, 30)) == [(date(2026, 4, 1), date(2026, 4, 30))]
    assert ranges(date(2026, 1, 15), date(2026, 3, 12)) == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 12)),
    ]
