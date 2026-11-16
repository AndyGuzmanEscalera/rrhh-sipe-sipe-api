from datetime import datetime
from zoneinfo import ZoneInfo
from app.core.config import get_settings


def local_today():
    return datetime.now(ZoneInfo(get_settings().local_timezone)).date()
