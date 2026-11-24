from datetime import datetime
from zoneinfo import ZoneInfo
from app.core.config import get_settings


def local_timezone():
    return ZoneInfo(get_settings().local_timezone)


def local_now():
    """Hora institucional local compatible con timestamps RAW sin zona horaria."""
    return datetime.now(local_timezone()).replace(tzinfo=None, microsecond=0)


def local_today():
    return local_now().date()
