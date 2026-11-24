from datetime import datetime

from app.core.clock import local_now


def test_local_now_is_timezone_free_like_raw_device_timestamps():
    value = local_now()
    assert isinstance(value, datetime)
    assert value.tzinfo is None and value.microsecond == 0
