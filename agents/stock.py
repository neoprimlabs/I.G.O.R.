"""Watching a product page for a restock, rather than waiting for a posted deal.

The price watch reads Slickdeals, which only knows about discounts somebody bothered
to post. It cannot see a vendor quietly putting stock back on the shelf, and for a
part that sells out that is the event worth knowing about. Measured 2026-10-13: the
AOOSTAR AG01 was in stock on the vendor's own store at $179, under target, and the
price watch had no way to know - the only Slickdeals posting for it was 18 months old
and correctly age-filtered.

Shopify stores expose /products/<handle>.js: the product, its variants, a price in
cents and an `available` boolean each. Structured data, no key, no HTML parsing, and
no model call - this module costs nothing per check.

aoostar.com refuses this server. Every request returns 429 TOO_MANY_REQUESTS within
150ms, including the bare storefront on the fourth request ever, and it still refused
after a 60 second wait: an IP-level block on the Oracle range, not a rate limit we
tripped. So the fetch tries direct first and falls back to r.jina.ai, a text relay
that reads the page for us. That is a third-party dependency on the critical path and
it is the weak point here - if it disappears, restock checks stop and the log says so.
Only public product URLs are sent to it, never anything of the user's.
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

_TIMEOUT = 45
_RELAY = "https://r.jina.ai/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_STATE = "stock_state.json"


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def _fetch(url: str) -> tuple[str, str]:
    """The product JSON, and how it was obtained. Direct first, then the relay."""
    try:
        return _get(url), "direct"
    except Exception as e:
        logger.info("Stock: direct fetch refused (%s: %s), trying the relay",
                    type(e).__name__, e)
    return _get(_RELAY + url), "relay"


def _json_url(url: str) -> str:
    """The .js endpoint for a Shopify product URL, however it was pasted."""
    clean = (url or "").strip().split("?")[0].split("#")[0].rstrip("/")
    if clean.endswith(".js") or clean.endswith(".json"):
        return clean
    return clean + ".js"


def _parse(payload: str) -> Optional[dict]:
    """The product, or None. The relay wraps JSON in markdown, so find the object."""
    if not payload:
        return None
    text = payload.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or "variants" not in data:
        return None
    return data


def _watchlist() -> list[dict]:
    """Items from memory/stock_watch.md: a heading, a url, optional variant and max."""
    try:
        text = (config.MEMORY_DIR / "stock_watch.md").read_text(encoding="utf-8")
    except OSError:
        return []

    items, current = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current = {"name": stripped[3:].strip(), "url": "", "variant": "",
                       "under": None}
            items.append(current)
        elif current is None:
            continue
        elif stripped.lower().startswith("url:"):
            current["url"] = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("variant:"):
            current["variant"] = stripped.split(":", 1)[1].strip().lower()
        elif stripped.lower().startswith("under:"):
            raw = stripped.split(":", 1)[1].strip().lstrip("$").replace(",", "")
            try:
                current["under"] = float(raw)
            except ValueError:
                logger.warning("stock_watch: unreadable price in %r", stripped)
    return [i for i in items if i["url"]]


def _state_path():
    return config.MEMORY_DIR / _STATE


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
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Could not write %s - %s: %s", _STATE, type(e).__name__, e)


def _variants(product: dict, wanted: str) -> list[dict]:
    out = []
    for v in product.get("variants") or []:
        title = str(v.get("title") or "")
        if wanted and wanted not in title.lower():
            continue
        price = v.get("price")
        try:
            # Shopify prices are integer cents.
            dollars = float(price) / 100.0 if price is not None else None
        except (TypeError, ValueError):
            dollars = None
        out.append({"title": title, "available": bool(v.get("available")),
                    "price": dollars})
    return out


def check(fetch=None, now: Optional[datetime] = None) -> list[dict]:
    """Variants that just became buyable. Never raises.

    Only transitions are reported. A variant that was available on the last check and
    still is says nothing, or an hourly job would send the same message all day.
    """
    now = now or datetime.now(timezone.utc)
    items = _watchlist()
    if not items:
        return []

    fetcher = fetch or (lambda u: _fetch(_json_url(u)))
    state = _load_state()
    # Two maps, deliberately. "available" is raw stock, which is what status reports.
    # Transitions are judged on "alertable" - available AND within the ceiling -
    # because recording a restock above target as available would suppress the alert
    # when the price later came down while it was still in stock, which is precisely
    # the moment worth knowing about.
    previous = state.get("alertable") or {}
    previous_raw = state.get("available") or {}
    current, current_raw, restocked = {}, {}, []

    for item in items:
        try:
            payload, how = fetcher(item["url"])
        except Exception as e:
            logger.warning("Stock: %s unreachable - %s: %s",
                           item["name"], type(e).__name__, e)
            # No data is not the same as out of stock. Carry the old state forward,
            # or a failed fetch would fake a restock on the next successful one.
            for key, was in previous.items():
                if key.startswith(item["name"] + "|"):
                    current[key] = was
            for key, was in previous_raw.items():
                if key.startswith(item["name"] + "|"):
                    current_raw[key] = was
            continue

        product = _parse(payload)
        if product is None:
            logger.error("Stock: %s returned nothing parseable (%d chars)",
                         item["name"], len(payload or ""))
            for key, was in previous.items():
                if key.startswith(item["name"] + "|"):
                    current[key] = was
            for key, was in previous_raw.items():
                if key.startswith(item["name"] + "|"):
                    current_raw[key] = was
            continue

        found = _variants(product, item["variant"])
        if not found:
            logger.warning("Stock: %s matched no variant for %r",
                           item["name"], item["variant"])
            continue

        for v in found:
            key = f"{item['name']}|{v['title']}"
            current_raw[key] = v["available"]

            over = (item["under"] is not None and v["price"] is not None
                    and v["price"] > item["under"])
            # The alertable state, not raw stock. Recording an over-target restock as
            # available would suppress the alert when the price later dropped while
            # it was still in stock - which is the moment most worth knowing about.
            current[key] = v["available"] and not over

            if not v["available"]:
                continue
            if over:
                logger.info("Stock: %s %s is in stock at $%.2f, over the $%.0f target",
                            item["name"], v["title"], v["price"], item["under"])
                continue
            if previous.get(key):
                continue
            restocked.append({
                "name": item["name"], "variant": v["title"],
                "price": v["price"], "under": item["under"],
                "url": item["url"], "product": product.get("title") or item["name"],
                "first_seen": key not in previous,
            })

    state["alertable"] = current
    state["available"] = current_raw
    state["last_checked"] = now.isoformat()
    _save_state(state)
    logger.info("Stock watch: %d item(s), %d variant(s) tracked, %d back in stock",
                len(items), len(current_raw), len(restocked))
    return restocked


def format_for_discord(hits: list[dict]) -> str:
    lines = ["**Back in stock**"]
    for hit in hits:
        price = f"${hit['price']:,.2f}" if hit.get("price") is not None else "price unknown"
        target = f", you wanted under ${hit['under']:,.0f}" if hit.get("under") else ""
        seen = " (first check, so it may have been in stock already)" if hit.get("first_seen") else ""
        lines.append(f"{hit['product']}\n{hit['variant']} at {price}{target}{seen}\n{hit['url']}")
    lines.append("Stock read from the vendor's own product data. Confirm shipping "
                 "terms at checkout before buying.")
    return "\n\n".join(lines)


def status(now: Optional[datetime] = None) -> str:
    """What the watch knows, for Monitor to answer from. Never raises."""
    items = _watchlist()
    if not items:
        return "Stock watch: nothing configured"
    state = _load_state()
    available = state.get("available") or {}
    lines = [f"Stock watch: {len(items)} item(s) watched, hourly"]
    checked = state.get("last_checked")
    if checked:
        try:
            when = datetime.fromisoformat(checked)
            lines.append("  last run: " + when.strftime("%a %H:%M") + " UTC")
        except ValueError:
            pass
    else:
        lines.append("  last run: no record of a check yet")
    for item in items:
        prefix = item["name"] + "|"
        mine = {k.split("|", 1)[1]: v for k, v in available.items() if k.startswith(prefix)}
        if not mine:
            lines.append(f"  - {item['name']}: not yet checked")
            continue
        up = [t for t, v in sorted(mine.items()) if v]
        lines.append(f"  - {item['name']}: {len(up)} of {len(mine)} variants in stock"
                     + (f" ({', '.join(up[:3])})" if up else ""))
    return "\n".join(lines)
