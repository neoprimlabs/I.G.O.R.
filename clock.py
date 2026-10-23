"""The user's wall clock, in one place.

Every prompt that shows the time and every conversion from a time the user typed
goes through here, so there is one timezone and one format. The models copy this
line; they are never asked to convert zones or add durations themselves. The
weekday alone did not spare them calendar arithmetic: on 2026-10-22 and 10-23 both
Direct and React offered "Monday Oct 28", a Wednesday. week_line lists the next
seven dates, so a weekday is looked up, never counted to.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config

USER_TZ = ZoneInfo(config.USER_TZ)


def time_line(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(USER_TZ)
    return f"{local:%a %Y-%m-%d %H:%M %Z} ({now.astimezone(timezone.utc):%H:%M} UTC)"


def week_line(now: datetime | None = None) -> str:
    local = (now or datetime.now(timezone.utc)).astimezone(USER_TZ)
    days = (local + timedelta(days=n) for n in range(1, 8))
    return "The next seven days: " + ", ".join(f"{d:%a %Y-%m-%d}" for d in days)
