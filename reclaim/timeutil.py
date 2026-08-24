"""Every timestamp in this system is timezone-aware IST. Quiet hours and salary
windows are IST concepts; a naive datetime silently breaks both."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

QUIET_HOURS_START = time(20, 0)  # 20:00 IST — contact window closes
QUIET_HOURS_END = time(9, 0)     # 09:00 IST — contact window opens


def now() -> datetime:
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    """Naive input is assumed IST rather than UTC — everything upstream of this
    system is an Indian merchant's clock."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def is_quiet_hours(dt: datetime) -> bool:
    t = to_ist(dt).time()
    return t >= QUIET_HOURS_START or t < QUIET_HOURS_END


def next_contact_window(dt: datetime) -> datetime:
    """First moment at or after dt when customer contact is permitted."""
    dt = to_ist(dt)
    if not is_quiet_hours(dt):
        return dt
    candidate = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    if candidate <= dt:
        candidate += timedelta(days=1)
    return candidate


def next_salary_window(dt: datetime) -> datetime:
    """Next 1st-3rd of a month at 11:00 IST. Indian salary credits cluster there,
    which is the whole point of scheduling INSUFFICIENT_FUNDS retries onto it."""
    dt = to_ist(dt)
    at_eleven = dt.replace(hour=11, minute=0, second=0, microsecond=0)
    if 1 <= dt.day <= 3 and at_eleven > dt:
        return at_eleven
    year, month = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    return datetime(year, month, 1, 11, 0, tzinfo=IST)
