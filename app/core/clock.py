from datetime import datetime
from zoneinfo import ZoneInfo
from app.core.config import get_settings


def local_timezone():
    return ZoneInfo(get_settings().local_timezone)


def local_now():
    """Instante actual en la hora local institucional (segundos, sin microsegundos)."""
    return datetime.now(local_timezone()).replace(microsecond=0)


def local_today():
    return local_now().date()
