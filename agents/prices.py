"""Watching for a part to drop below a price the user set.

Direct retail scraping is not available and is not attempted. Measured from this
server on 2026-10-06: Best Buy refused the connection, Micro Center, camelcamelcamel
and PCPartPicker returned 403, Newegg redirected to a bot check. Best Buy's official
API stopped issuing keys to free email addresses, and Keepa is about $50 a month,
against a standing decision to pay for nothing.

Slickdeals publishes an RSS search that answers from here, free, keyless, with the
price in the title. It is deal-driven rather than a price feed: it sees a drop when
someone posts it. That is the shape of "tell me when it dips below X" anyway, and it
costs no tokens - this whole module makes one HTTP call per watched item and never
calls a model.

What it cannot do is tell a graphics card from a waterblock for one. Both carry the
same words and a low price, so the message quotes the posting title and links it,
and a human decides. Announcing "the 5070 Ti dropped to $89" would be worse than
useless.
"""

import json
import logging
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

_SEEN_DAYS = 30
_TIMEOUT = 20
_FEED = "https://slickdeals.net/newsearch.php?rss=1&q="
_PRICE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": "igor-pricewatch/1.0 (personal price alerts)",
        "Accept": "application/rss+xml, application/xml",
    })
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def _watchlist() -> list[dict]:
    """Items from memory/price_watch.md. A heading, a query and a target price."""
    try:
        text = (config.MEMORY_DIR / "price_watch.md").read_text(encoding="utf-8")
    except OSError:
        return []

    items, current = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current = {"name": stripped[3:].strip(), "query": "", "under": None}
            items.append(current)
        elif current is not None and stripped.lower().startswith("query:"):
            current["query"] = stripped.split(":", 1)[1].strip().lower()
        elif current is not None and stripped.lower().startswith("under:"):
            raw = stripped.split(":", 1)[1].strip().lstrip("$").replace(",", "")
            try:
                current["under"] = float(raw)
            except ValueError:
                # A target nobody can read is not a target. Skipping beats guessing.
                logger.warning("price_watch: could not read a target price from %r", stripped)
    return [i for i in items if i["query"] and i["under"] is not None]


def _lowest_price(title: str) -> Optional[float]:
    """The cheapest figure in a title. "Was $1,299 now $999" is a $999 deal."""
    found = []
    for raw in _PRICE.findall(title or ""):
        try:
            found.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return min(found) if found else None


def _from_feed(payload: str) -> list[dict]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    deals = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if title and url:
            deals.append({"title": title, "url": url})
    return deals


def _hits(item: dict, deals: list[dict], seen: set) -> list[dict]:
    """Deals for this item that are under its target and not already reported."""
    words = [w for w in item["query"].split() if w]
    out = []
    for deal in deals:
        if deal["url"] in seen:
            continue
        lowered = deal["title"].lower()
        if not all(word in lowered for word in words):
            continue
        price = _lowest_price(deal["title"])
        if price is None or price > item["under"]:
            continue
        out.append({"name": item["name"], "under": item["under"], "price": price,
                    "title": deal["title"], "url": deal["url"]})
    return out


def _seen_path():
    return config.MEMORY_DIR / "prices_seen.json"


def _load_seen() -> dict:
    try:
        data = json.loads(_seen_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(hits: list[dict], now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(days=_SEEN_DAYS)
    kept = {}
    for url, stamp in _load_seen().items():
        try:
            if datetime.fromisoformat(stamp) >= horizon:
                kept[url] = stamp
        except (TypeError, ValueError):
            continue
    for hit in hits:
        kept[hit["url"]] = now.isoformat()
    try:
        path = _seen_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(kept, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Could not write prices_seen.json - %s: %s", type(e).__name__, e)


_HEARTBEAT_DAYS = 7


def _state_path():
    return config.MEMORY_DIR / "prices_state.json"


def _load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        path = _state_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Could not write prices_state.json - %s: %s", type(e).__name__, e)


def _closest(item: dict, deals: list[dict]) -> Optional[dict]:
    """The cheapest thing matching this item, whatever the target."""
    words = [w for w in item["query"].split() if w]
    best = None
    for deal in deals:
        lowered = deal["title"].lower()
        if not all(word in lowered for word in words):
            continue
        price = _lowest_price(deal["title"])
        if price is None:
            continue
        if best is None or price < best["price"]:
            best = {"price": price, "title": deal["title"], "url": deal["url"]}
    return best


def heartbeat(now: Optional[datetime] = None) -> str:
    """A weekly line proving it is alive, or empty when one is not due yet.

    Silence is indistinguishable from being broken: the job watch sent nothing for
    five days and neither of us noticed until the user asked a question it could not
    answer. A watcher that only speaks on success cannot be trusted to be watching.
    """
    now = now or datetime.now(timezone.utc)
    state = _load_state()
    last = state.get("last_heartbeat")
    try:
        due = last is None or now - datetime.fromisoformat(last) >= timedelta(days=_HEARTBEAT_DAYS)
    except (TypeError, ValueError):
        due = True
    if not due:
        return ""

    seen = state.get("closest") or {}
    if not seen:
        return ""
    lines = ["**Still watching**"]
    for name, best in seen.items():
        target = best.get("under")
        lines.append(f"{name}: closest was ${best['price']:,.2f}"
                     + (f", you want under ${target:,.0f}" if target else ""))
    state["last_heartbeat"] = now.isoformat()
    _save_state(state)
    return "\n".join(lines)


def check(fetch=None) -> list[dict]:
    """Everything under target that has not been reported. Never raises."""
    items = _watchlist()
    if not items:
        return []
    seen = set(_load_seen())
    fetch = fetch or (lambda q: _from_feed(_get(_FEED + urllib.parse.quote_plus(q))))

    hits = []
    state = _load_state()
    closest = state.setdefault("closest", {})
    for item in items:
        try:
            deals = fetch(item["query"])
        except Exception as e:
            logger.warning("Price watch: %s unavailable - %s: %s",
                           item["name"], type(e).__name__, e)
            continue
        hits.extend(_hits(item, deals, seen))
        best = _closest(item, deals)
        if best is not None:
            best["under"] = item["under"]
            closest[item["name"]] = best
    _save_state(state)
    logger.info("Price watch: %d items watched, %d under target", len(items), len(hits))
    return hits


def format_for_discord(hits: list[dict]) -> str:
    lines = ["**Price drop**"]
    for hit in hits:
        lines.append(f"{hit['name']} at ${hit['price']:,.2f} - you wanted under "
                     f"${hit['under']:,.0f}\n{hit['title']}\n{hit['url']}")
    lines.append("Listing title is quoted as posted - check it is the right product "
                 "before buying.")
    return "\n\n".join(lines)
