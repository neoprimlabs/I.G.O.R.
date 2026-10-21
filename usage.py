"""How many Groq tokens IGOR has spent, per model, over the last 24 hours.

2026-10-16: the daily cap on gpt-oss-120b (200000, Direct and React together) ran
out mid-afternoon and nothing in IGOR could have said it was close. Groq reports
the cap only in a 429 body, after it is gone; the x-ratelimit headers describe the
per-minute bucket and look healthy throughout. Every response does carry its exact
usage, and until this module it was thrown away.

Hourly buckets in memory/token_ledger.json, so the file stays small and a restart
keeps the count. The 24h figure sums whole hours, so it can overstate by up to an
hour of use - the safe direction for a budget. It is IGOR's own count: anything
else spending on the same key (a test run from a shell, the Groq console) only
shows up here if it went through record().
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
    """Tokens left today on a model with a measured cap, or None if it has none."""
    cap = config.DAILY_TOKEN_CAPS.get(model)
    if cap is None:
        return None
    return cap - sum(last_24h(now).get(model, {}).values())


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
        against = f" of {cap:,} daily cap" if cap else " (daily cap not measured)"
        parts = ", ".join(f"{k} {v:,}" for k, v in sorted(labels.items(), key=lambda kv: -kv[1]))
        lines.append(f"  - {model}: {total:,}{against} - {parts}")
    return "\n".join(lines)
