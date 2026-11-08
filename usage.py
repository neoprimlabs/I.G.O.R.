"""How many Groq tokens IGOR has spent, per model, over the last 24 hours.

2026-10-16: the daily cap on gpt-oss-120b (200000, Direct and React together) ran
out mid-afternoon and nothing in IGOR could have said it was close. Groq reports
the cap only in a 429 body, after it is gone; the x-ratelimit headers describe the
per-minute bucket and look healthy throughout. Every response does carry its exact
usage, and until this module it was thrown away.

Hourly buckets in memory/token_ledger.json, so the file stays small and a restart
keeps the count. It is IGOR's own count: anything else spending on the same key (a
test run from a shell, the Groq console) only shows up here if it went through record().

The cap is a bucket, not a 24-hour sum. Measured 2026-11-01 from real 429 bodies: "Limit
200000, Used 195831, Requested 4937. Please try again in 5m31.776s" is 768 over, and 768
tokens at 200000/86400 a second is exactly that wait; the per-minute limit fits 8000/60
the same way. So what headroom gives is what Groq would allow, and the 24-hour sum, which
read 206000 of 200000 that day while Groq kept answering, is only a record of who spent it.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

_KEEP = timedelta(hours=25)


def _path():
    return config.MEMORY_DIR / "token_ledger.json"


def _hour(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record(model: str, response, label: str = "", now: Optional[datetime] = None,
           ms: Optional[int] = None) -> None:
    """Add one response's usage to the ledger. Never raises: accounting must not
    cost the user a reply. The log line it writes is also the record
    scripts/usage_report.py reads back from the journal, so keep its shape."""
    try:
        used = getattr(response, "usage", None)
        total = int(getattr(used, "total_tokens", 0) or 0)
        if total <= 0:
            return
        now = now or datetime.now(timezone.utc)
        data = _load()
        cutoff = _hour(now - _KEEP)
        data = {h: v for h, v in data.items() if h >= cutoff and isinstance(v, dict)}
        by_label = data.setdefault(_hour(now), {}).setdefault(model, {})
        key = label or "other"
        by_label[key] = int(by_label.get(key, 0)) + total
        path = _path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        logger.info("%s used %d tokens (%d in, %d out) on %s%s", key, total,
                    int(getattr(used, "prompt_tokens", 0) or 0),
                    int(getattr(used, "completion_tokens", 0) or 0), model,
                    f" in {ms} ms" if ms is not None else "")
    except Exception as e:
        logger.warning("Token ledger not updated - %s: %s", type(e).__name__, e)
        return
    _count(key, total, now)


# The dashboard's agents: calls and tokens per label by the user's day, and each label's newest call.
_DAYS_SHOWN = 7
_DAYS_KEPT = 8


def _activity_path():
    return config.MEMORY_DIR / "agent_activity.json"


def _activity() -> dict:
    try:
        data = json.loads(_activity_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"days": {}, "last": {}}
    if not isinstance(data, dict):
        return {"days": {}, "last": {}}
    days, last = data.get("days"), data.get("last")
    return {"days": {d: v for d, v in days.items() if isinstance(v, dict)} if isinstance(days, dict) else {},
            "last": last if isinstance(last, dict) else {}}


def _count(label: str, total: int, now: datetime) -> None:
    try:
        import clock
        data = _activity()
        today = now.astimezone(clock.USER_TZ).date()
        cutoff = (today - timedelta(days=_DAYS_KEPT - 1)).isoformat()
        days = {d: v for d, v in data["days"].items() if d >= cutoff}
        row = days.setdefault(today.isoformat(), {})
        calls, tokens = (row.get(label) or [0, 0])[:2]
        row[label] = [int(calls) + 1, int(tokens) + total]
        data["last"][label] = now.astimezone(timezone.utc).isoformat(timespec="seconds")
        path = _activity_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"days": days, "last": data["last"]}, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        logger.warning("Agent activity not updated - %s: %s", type(e).__name__, e)


def week(now: Optional[datetime] = None) -> dict:
    """The last seven of the user's days, oldest first, and per label its calls and tokens on each and
    its newest call: {"days": [...], "labels": {label: {"calls": [...], "tokens": [...], "last": iso}}}."""
    import clock
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(clock.USER_TZ).date()
    days = [(today - timedelta(days=back)).isoformat() for back in range(_DAYS_SHOWN - 1, -1, -1)]
    data = _activity()
    labels: dict[str, dict] = {}
    for i, day in enumerate(days):
        for label, pair in data["days"].get(day, {}).items():
            if not isinstance(pair, list) or len(pair) < 2:
                continue
            entry = labels.setdefault(label, {"calls": [0] * _DAYS_SHOWN, "tokens": [0] * _DAYS_SHOWN, "last": None})
            entry["calls"][i] += int(pair[0])
            entry["tokens"][i] += int(pair[1])
    for label, when in data["last"].items():
        if isinstance(when, str):
            labels.setdefault(label, {"calls": [0] * _DAYS_SHOWN, "tokens": [0] * _DAYS_SHOWN, "last": None})["last"] = when
    return {"days": days, "labels": labels}


def last_24h(now: Optional[datetime] = None) -> dict[str, dict[str, int]]:
    """{model: {label: tokens}} over the last 24 whole hours."""
    now = now or datetime.now(timezone.utc)
    cutoff = _hour(now - timedelta(hours=23))
    out: dict[str, dict[str, int]] = {}
    for hour, models in _load().items():
        if hour < cutoff or not isinstance(models, dict):
            continue
        for model, labels in models.items():
            for label, n in (labels or {}).items():
                out.setdefault(model, {})
                out[model][label] = out[model].get(label, 0) + int(n)
    return out


def headroom(model: str, now: Optional[datetime] = None) -> Optional[int]:
    """Tokens Groq would allow now on a model with a measured cap, or None if it has none.
    To the hour: an hour's spend is counted whole until the next, then refills a full hour's worth."""
    cap = config.DAILY_TOKEN_CAPS.get(model)
    if cap is None:
        return None
    now = now or datetime.now(timezone.utc)
    data, used = _load(), 0.0
    for back in range(int(_KEEP.total_seconds() // 3600), -1, -1):
        hour = data.get(_hour(now - timedelta(hours=back)))
        labels = hour.get(model) if isinstance(hour, dict) else None
        spent = sum(int(n) for n in labels.values()) if isinstance(labels, dict) else 0
        used = min(cap, max(0.0, used - cap / 24) + spent)
    return round(cap - used)


def summary(now: Optional[datetime] = None) -> str:
    """For Monitor: what has been spent, against the cap where one is known."""
    spent = last_24h(now)
    if not spent:
        return "Groq tokens used in the last 24h: none recorded."
    lines = ["Groq tokens used in the last 24h (IGOR's own count):"]
    for model in sorted(spent):
        labels = spent[model]
        total = sum(labels.values())
        cap = config.DAILY_TOKEN_CAPS.get(model)
        # Rounded: it is right to the hour, and a phone reads it aloud.
        against = (f", about {round(headroom(model, now), -3):,} of the {cap:,} daily cap free now, since it refills steadily"
                   if cap else " (daily cap not measured)")
        parts = ", ".join(f"{k} {v:,}" for k, v in sorted(labels.items(), key=lambda kv: -kv[1]))
        lines.append(f"  - {model}: {total:,}{against} - {parts}")
    return "\n".join(lines)
