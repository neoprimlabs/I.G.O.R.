"""The user's wall clock, in one place.

Every prompt that shows the time and every conversion from a time the user typed
goes through here, so there is one timezone and one format. The models copy this
line; they are never asked to convert zones or add durations themselves. The
weekday is included so "remind me Monday" does not need calendar arithmetic either.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config

USER_TZ = ZoneInfo(config.USER_TZ)


def time_line(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(USER_TZ)
    return f"{local:%a %Y-%m-%d %H:%M %Z} ({now.astimezone(timezone.utc):%H:%M} UTC)"
