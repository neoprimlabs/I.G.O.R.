"""IGOR deciding, on its own, whether to say something - and usually deciding not to.

The measured failure of proactive agents is not silence, it is noise. ProactiveBench
evaluated 22 models and found they largely help when help is wanted but "fail to stay
silent when the user does not require any assistance", and that hinting at restraint
in the prompt "yields only marginal gains". Two consequences shape this file:

- Every condition that can be decided without a model is decided without one. The
  gates below run first and return before any call is made. A prompt that asks for
  restraint is not a control; a return statement is.
- It runs on the summary model. The same work found proactiveness does not track
  model capacity, so the larger model would cost more and buy nothing.

The second risk is quieter. Anything sent here goes through record_outbound into
context.db, so an invented detail becomes an input to every later turn - the exact
mechanism behind the 2026-08-13 fabrication. The model is given a block of facts read
from files and the database, told it may use nothing else, and has no tools.

Off unless memory/presence_config.md says "state: on". Missing or unreadable means
off: a thing that messages the user unasked should not start talking because a file
went missing.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import clock
import config

logger = logging.getLogger(__name__)

# Quiet hours in local time. The user set these: they are regularly up past 3am, so
# a conventional evening cutoff would silence it exactly when they are around.
_QUIET_START = 3
_QUIET_END = 10

_MIN_GAP = timedelta(hours=4)
_MAX_PER_DAY = 2
_MAX_CALLS_PER_DAY = 6
_CONVERSATION_LIVE = timedelta(minutes=20)
_MAX_CHARS = 400

# A break, not an interruption and not an ambush hours later.
_LULL_MIN = timedelta(minutes=25)
_LULL_MAX = timedelta(minutes=90)

_SILENT = "SILENT"

# Presence has no tools and performs no work. So any first-person claim of having
# done something is false by construction, and can be caught in code rather than
# asked for in a prompt.
#
# 2026-09-21, from the eval, one edit away from shipping: "I've been working on the
# X/Twitter fetch module - just finished a solid prototype, ready when you're ready
# to look." None of that happened. The prompt already forbade inventing activity and
# the model did it anyway, which is the whole argument for controls in code. Worse
# than a bad message: record_outbound would have made it a permanent input, the same
# mechanism as the 2026-08-13 fabrication.
_INVENTED_ACTIVITY = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\bI(?:'ve| have| ve)?\s*(?:been\s+)?(?:working|built|build|finished|made|"
        r"completed|implemented|fixed|prepared|drafted|wrote|written|started|set up|"
        r"put together|looked into|investigated|researched)\b",
        r"\bI(?:'m| am)\s+(?:working|building|looking into|putting together)\b",
        r"\bI\s+(?:can|will|could)\s+(?:have|get)\s+(?:it|that|this)\s+(?:ready|done)\b",
        r"\bready (?:for you )?to (?:look|review|check)\b",
        r"\bmy (?:prototype|draft|progress|work)\b",
    )
]

# Triggers where the code has already decided there is something to say, so the
# model writes rather than judges. Asking it to re-decide is the "restraint in a
# prompt" this module's docstring rejects: on 2026-09-21 judging everything scored
# 2 misses out of 2, silent even about drafts 38 days old. lull is the only trigger
# that still judges, because there the code genuinely cannot know.
_COMPOSE_TRIGGERS = frozenset({"checkin"})

# What the user asked for, twice, in their own words: "Just check in on me", and
# "Will you message me at some random times tomorrow?". What they got instead was
# "The draft titled Universal Basic Income was sent 6 days ago" - a report about a
# file IGOR wrote and filed somewhere they have never seen. Their reply: "I asked
# for a message. Not a report I don't even know where is being saved."
#
# So there is a check-in, it happens once a day at a time that moves, and it is
# written as a message to a person. Pending work is context it may draw on, never
# the subject. The hours are wide because they are regularly up past 3am.
_CHECKIN_EARLIEST = 11
_CHECKIN_LATEST = 23

_SYSTEM_JUDGE = """You decide whether I.G.O.R. says something to the one person it talks to, right now, without being asked.

Reply with exactly SILENT, or with the message itself.

SILENT is the right answer most of the time, and it is a good answer. Say something only when the facts below contain something specific and current that is worth raising unprompted. Having nothing to say is normal.

Do not send:
- a greeting with nothing behind it
- a status report, or a list
- anything checking whether they are there
- anything you cannot point to in the facts
- anything listed under "Already sent to the user". They have read it. Repeating a digest, an alert or an earlier message back to them is never worth doing, however important it looks

If you do send something:
- One to three sentences, plain prose, like a person typing.
- Use ONLY the facts given. Never invent activity, progress, events, or anything the user said or did. Do not guess how something went or what they are working on.
- Refer to a specific thing from the facts, not to the fact that time has passed.
- Do not mention being scheduled, triggered, woken, or that you decided to message.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""

_SYSTEM_CHECKIN = """You are I.G.O.R., writing a short check-in to the one person you talk to. They asked you to check in on them.

This is a message to a person, not a status report. Write what someone would actually type.

- One or two sentences. Often one is enough.
- If the facts show something specific and current, you may mention it in passing, the way a person would. It is never the point of the message.
- Never name a file, a draft, a task list, a count of days, or anything internal to how you work. They did not ask about your filing.
- Do not ask for a status update, do not list anything, do not offer a menu of things you could do.
- Let the time of day shape it. Late at night is not the same as mid afternoon.
- Vary it. Do not open the same way every day.
- Never invent activity, progress, or anything they said or did.
- You have not been working on anything. You have no tools here and take no actions between messages. Never say you have built, finished, prepared, looked into or made progress on anything - none of it happened, and they will know.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""


def _system_for(reason: str) -> str:
    return _SYSTEM_CHECKIN if reason in _COMPOSE_TRIGGERS else _SYSTEM_JUDGE


def _config_path():
    return config.MEMORY_DIR / "presence_config.md"


def _state_path():
    return config.MEMORY_DIR / "presence_state.json"


def is_on() -> bool:
    try:
        text = _config_path().read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"^\s*state:\s*on\s*$", text, re.IGNORECASE | re.MULTILINE))


def _load_state() -> dict:
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        path = _state_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Could not write presence state - %s: %s", type(e).__name__, e)


def _today(state: dict, now: datetime) -> dict:
    """Counters are per local day, so a late night does not spend tomorrow's budget."""
    day = now.astimezone(clock.USER_TZ).strftime("%Y-%m-%d")
    if state.get("day") != day:
        state = dict(state)
        state.update({"day": day, "sent": 0, "calls": 0})
    return state


def _parse(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _last_user_at() -> Optional[datetime]:
    """The user's last message, not IGOR's.

    record_outbound stores the digest, so keying on "the last stored message" made
    the 13:00 digest look like the end of a conversation: on 2026-09-17 lull fired on
    every tick from 13:25 to 14:30 and spent three model calls on nobody.
    """
    try:
        import context_store
        return context_store.last_user_timestamp()
    except Exception:
        return None


def _blocked(state: dict, now: datetime, last_user_at: Optional[datetime]) -> Optional[str]:
    """Why it must not speak, or None. No model call happens if this returns a reason."""
    local_hour = now.astimezone(clock.USER_TZ).hour
    if _QUIET_START <= local_hour < _QUIET_END:
        return "quiet hours"
    if last_user_at is not None and now - last_user_at < _CONVERSATION_LIVE:
        return "a conversation is live"
    last_sent = _parse(state.get("last_sent"))
    if last_sent is not None and now - last_sent < _MIN_GAP:
        return "too soon after the last one"
    if state.get("sent", 0) >= _MAX_PER_DAY:
        return "daily message cap"
    if state.get("calls", 0) >= _MAX_CALLS_PER_DAY:
        return "daily call budget"
    return None


def _drafts() -> list[tuple[str, datetime]]:
    """Draft headings and their timestamps, newest first."""
    try:
        text = (config.MEMORY_DIR / "drafts.md").read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        if not line.startswith("## "):
            continue
        match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*UTC", line)
        if not match:
            continue
        try:
            when = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        found.append((line[3:].strip(), when.replace(tzinfo=timezone.utc)))
    return sorted(found, key=lambda pair: pair[1], reverse=True)


def _checkin_time(now: datetime, state: dict) -> Optional[datetime]:
    """When today's check-in is due, or None if it already happened.

    Chosen once per local day and stored, so it does not move every ten minutes when
    the scheduler looks again.
    """
    local = now.astimezone(clock.USER_TZ)
    today = local.strftime("%Y-%m-%d")
    planned = state.get("checkin") or {}
    if planned.get("day") == today:
        if planned.get("sent"):
            return None
        return _parse(planned.get("at"))

    import random
    hour = random.randint(_CHECKIN_EARLIEST, _CHECKIN_LATEST - 1)
    at = local.replace(hour=hour, minute=random.randint(0, 59), second=0, microsecond=0)
    state["checkin"] = {"day": today, "at": at.astimezone(timezone.utc).isoformat(), "sent": False}
    _save_state(state)
    logger.info("Check-in planned for %s", at.strftime("%a %H:%M %Z"))
    return at.astimezone(timezone.utc)


def _open_tasks() -> list[str]:
    try:
        text = (config.MEMORY_DIR / "tasks.md").read_text(encoding="utf-8")
    except OSError:
        return []
    from agents import monitor
    return monitor._parse_tasks(text)


def _facts(reason: str, now: datetime, last_user_at: Optional[datetime]) -> str:
    """Only what can be read from a file or the database. Nothing inferred."""
    lines = [
        f"Woken by: {reason}",
        f"Current time: {clock.time_line(now)}",
    ]
    if last_user_at is not None:
        minutes = int((now - last_user_at).total_seconds() // 60)
        lines.append(f"The user last said something: {minutes} minutes ago")
    else:
        lines.append("The user last said something: unknown")

    tasks = _open_tasks()
    lines.append(f"Open tasks ({len(tasks)}):" if tasks else "Open tasks: none")
    lines.extend(f"  {t}" for t in tasks[:5])

    drafts = _drafts()
    if drafts:
        lines.append(f"Drafts sent for review and not replied to ({len(drafts)}):")
        for title, when in drafts[:3]:
            age = (now - when).days
            lines.append(f"  {title} - sent {age} days ago")
    else:
        lines.append("Drafts awaiting review: none")

    try:
        import context_store
        recent = context_store.load(8)
    except Exception:
        recent = []

    # Split deliberately. On 2026-09-18 and 09-20 presence sent the user a resolved
    # "qwen3.6 is gone" alert, because record_outbound had stored it and this bundle
    # offered it back as conversation. What IGOR already said belongs here so it does
    # not repeat itself. It does not belong here as something to raise.
    said_by_user = [t for t in recent if t.get("role") == "user"]
    said_by_igor = [t for t in recent if t.get("role") != "user"]

    if said_by_user:
        lines.append("What the user said, oldest first:")
        for turn in said_by_user[-4:]:
            lines.append(f"  user: {(turn.get('content') or '')[:300]}")
    else:
        lines.append("What the user said: nothing stored.")

    if said_by_igor:
        lines.append("Already sent to the user unprompted. Do not repeat, re-raise, "
                     "follow up on, or comment on any of it:")
        for turn in said_by_igor[-4:]:
            text = (turn.get("content") or "").replace("[sent proactively]", "").strip()
            lines.append(f"  - {' '.join(text.split())[:110]}")
    return "\n".join(lines)


def _tidy(text: str) -> Optional[str]:
    """The style rules, applied in code. A prompt rule is not a control."""
    text = (text or "").strip()
    if not text or text.upper().startswith(_SILENT):
        return None
    text = text.replace("!", ".")
    text = re.sub(r"\.{2,}", ".", text)
    if len(text) > _MAX_CHARS:
        logger.warning("Presence reply was %d chars, dropping it", len(text))
        return None
    for pattern in _INVENTED_ACTIVITY:
        hit = pattern.search(text)
        if hit:
            # Silence beats a lie, and a lie here would be stored and read back later.
            logger.error("Presence claimed work it did not do (%r), dropping: %s",
                         hit.group(0), text[:120])
            return None
    return text


async def _ask_model(bundle: str, reason: str = "lull") -> str:
    import openai
    import llm

    client = openai.AsyncOpenAI(
        api_key=config.GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    return await llm.complete(
        client,
        config.MODELS["summary"],
        _system_for(reason),
        bundle,
        max_tokens=400,
        label="Presence",
    )


async def consider(reason: str, now: Optional[datetime] = None,
                   ask: Optional[Callable] = None,
                   last_user_at: Optional[datetime] = None) -> Optional[str]:
    """The message to send, or None for silence. Never raises."""
    now = now or datetime.now(timezone.utc)
    if not is_on():
        return None
    if last_user_at is None:
        last_user_at = _last_user_at()

    state = _today(_load_state(), now)
    blocked = _blocked(state, now, last_user_at)
    if blocked:
        logger.info("Presence (%s): not speaking - %s", reason, blocked)
        _save_state(state)
        return None

    state["calls"] = state.get("calls", 0) + 1
    state.setdefault("last_trigger", {})[reason] = now.isoformat()
    _save_state(state)

    async def _default_ask(bundle: str) -> str:
        return await _ask_model(bundle, reason)

    try:
        reply = await (ask or _default_ask)(_facts(reason, now, last_user_at))
    except Exception as e:
        logger.error("Presence (%s) model call failed - %s: %s", reason, type(e).__name__, e)
        return None

    message = _tidy(reply)
    if message is None:
        # In write mode nothing was being judged, so an empty answer is the model
        # failing the task, not choosing silence. Worth seeing in the log as such.
        level = logger.warning if reason in _COMPOSE_TRIGGERS else logger.info
        level("Presence (%s): %s", reason,
              "returned nothing in write mode" if reason in _COMPOSE_TRIGGERS else "silent")
        return None

    state["sent"] = state.get("sent", 0) + 1
    state["last_sent"] = now.isoformat()
    if reason == "checkin" and isinstance(state.get("checkin"), dict):
        state["checkin"]["sent"] = True
    _save_state(state)
    logger.info("Presence (%s): speaking - %s", reason, message[:80])
    return message


def due_triggers(now: Optional[datetime] = None,
                 last_user_at: Optional[datetime] = None) -> list[str]:
    """Reasons to consider speaking. Reasons, not schedules."""
    now = now or datetime.now(timezone.utc)
    if last_user_at is None:
        last_user_at = _last_user_at()
    state = _load_state()
    fired = state.get("last_trigger") or {}
    due = []

    # The check-in they asked for: once a day, at a time that moves. The code picks
    # the minute, as it does everywhere else, because "some random times" produced
    # one fixed number when the model was asked to choose.
    checkin_at = _checkin_time(now, state)
    if checkin_at is not None and now >= checkin_at:
        due.append("checkin")

    # Once per lull, not once per tick. The window is an hour wide and the job runs
    # every ten minutes, so without this it fires six times for one quiet spell.
    if last_user_at is not None and _LULL_MIN <= now - last_user_at <= _LULL_MAX:
        considered = _parse(fired.get("lull"))
        if considered is None or considered < last_user_at:
            due.append("lull")

    # A digest trigger lived here until 2026-09-21. It woke presence 90 minutes after
    # the digest, when the only fresh thing in context was IGOR's own digest - so the
    # only subject available was the one subject it must not repeat. Four decisions in
    # three days: two silences, and two echoes of an already-resolved model alert sent
    # back to the user. A trigger whose only material is its own output has none.

    # There were drafts and stale_task triggers here until 2026-09-21. Each produced
    # a standalone report about IGOR's own bookkeeping - "The draft titled Universal
    # Basic Income was sent 6 days ago" - about a file the user has never seen. Their
    # words: "I asked for a message. Not a report I don't even know where is being
    # saved." Pending work still reaches the model in the facts block, where the
    # check-in may mention it in passing. It is not a subject of its own.

    return due


async def tick(send_fn: Callable) -> None:
    """One pass. Considers at most one reason, so several coming due cannot stack."""
    if not is_on():
        return
    for reason in due_triggers():
        message = await consider(reason)
        if message:
            await send_fn(message)
        return
