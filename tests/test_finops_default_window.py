from datetime import datetime

from backend.finops.router import _window


def test_default_window_is_stable_within_a_five_minute_refresh_bucket():
    start_value, end_value = _window(None, None)

    start = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_value.replace("Z", "+00:00"))

    assert end.minute % 5 == 0
    assert end.second == 0
    assert end.microsecond == 0
    assert (end - start).days == 30


def test_explicit_window_remains_exact():
    assert _window(
        "2026-08-01T12:03:17Z",
        "2026-08-02T14:09:41Z",
    ) == (
        "2026-08-01T12:03:17Z",
        "2026-08-02T14:09:41Z",
    )
