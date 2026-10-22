import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# The user's timezone, as an IANA name so DST is handled. Every prompt shows local
# time from this, and scheduled messages convert from it. Confirmed by the user on
# 2026-09-12; three independent log comparisons had already put them at UTC-4.
USER_TZ = "America/New_York"

# TPM limits re-measured 2026-08-18 from the x-ratelimit headers, after Groq
# removed the Llama family. Every general-purpose model left is 8000 TPM: the
# 6000 and 12000 buckets this file used to describe no longer exist. Buckets are
# per-model, so roles sharing a model share its budget.
MODELS = {
    "router": "qwen/qwen3.8-27b",        # 8000 TPM, sole occupant, ~28 tokens a call
    "chat": "openai/gpt-oss-120b",       # 8000 TPM bucket, shared with react
    "react": "openai/gpt-oss-120b",      # shares chat's bucket, deliberately
    "research": "openai/gpt-oss-20b",    # 8000 TPM bucket, shared with evaluator and summary
    "evaluator": "openai/gpt-oss-20b",   # shares research's bucket
    "summary": "openai/gpt-oss-20b",     # shares research's bucket
    "worker": "openai/gpt-oss-20b",      # delegated workers; see below
    "screening": "openai/gpt-oss-120b",  # shares chat's bucket: one ~2000 token gig screen a day
    "vision": "qwen/qwen3.8-27b",        # shares router's bucket: 2048 tokens an image, only when one is sent
    "speech": "whisper-large-v3-turbo",  # voice notes; audio is limited in seconds, not tokens
}

# Delegated workers run on 20b, NOT on react's 120b. A worker starts while the
# parent is mid-turn, so putting both on one 8000 TPM bucket means the parent's
# next call contends with its own worker. 20b shares with research, evaluator and
# summary, none of which are on the hot path while the user is waiting. Workers are
# serialized too (react._delegation_gate), so at most one draws on it at a time.
#
# chat and react share a bucket on purpose: the router sends a message to one or
# the other and never both, so they alternate on the hot path instead of stacking.
#
# The router is alone on qwen for two reasons. It is the only model that still
# answers inside a 10-token budget - gpt-oss rejects reasoning_effort "none" with
# a 400 and spends the whole budget on hidden reasoning without it. And qwen is
# the newest model here, so it is the one most likely to be deprecated next: a
# router outage already falls back to React, while a chat outage reaches the user.
# If qwen goes, the router can move to gpt-oss-20b only by also raising its
# max_tokens in orchestrator._classify to at least 1024.

# Groq tokens-per-day caps, only where measured from a 429 body - the cap appears
# nowhere else. 120b read 2026-10-16. Never fill in a guess: usage.py reports a
# model without an entry as "not measured", which is the truth.
DAILY_TOKEN_CAPS = {
    "openai/gpt-oss-120b": 200000,
}

# Where a rate-limited model's calls go instead (llm.create). 120b carries Direct
# and React and is the one that ran dry on 2026-10-16; 20b is a separate bucket and
# already runs delegated workers through the same tool loop. 20b has no entry, so
# when it is limited too the call fails as before.
#
# Each entry is a chain tried in order, as in Hermes' fallback_providers. Cerebras
# serves the same gpt-oss-120b, so prompts and tools behave the same, on a free tier
# of about 1M tokens a day at 5 requests a minute (third-party figures, 2026-10-19).
# A provider whose key is not in the environment is skipped, so it sits dormant
# until CEREBRAS_API_KEY is added to .env.
FALLBACK_MODELS = {
    "openai/gpt-oss-120b": ["cerebras:gpt-oss-120b", "openai/gpt-oss-20b"],
}

# Providers other than Groq, by the prefix a fallback entry carries: base URL and the
# environment variable holding the key.
PROVIDERS = {
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
}

# Overnight loop budget per night, on gpt-oss-20b whose daily cap is unmeasured, so kept modest.
OVERNIGHT_MODEL = "openai/gpt-oss-20b"
# Only the paper write and revise call. Live 2026-10-21, 20b failed all three of the
# night's revisions: it replaced a 15-source paper (kept 1), cited 3 links that were
# not in the findings, and ran to 1087 words over a 7500-char cap. This shares chat's
# daily cap, at about 7000-13000 tokens a night; OVERNIGHT_TOKENS counts both models.
OVERNIGHT_WRITE_MODEL = "openai/gpt-oss-120b"
OVERNIGHT_TOKENS = 60000
OVERNIGHT_SEARCHES = 12

DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
EXA_API_KEY: str = os.getenv("EXA_API_KEY", "")

_uid = os.getenv("AUTHORIZED_DISCORD_USER_ID", "")
AUTHORIZED_USER_ID: int = int(_uid) if _uid.isdigit() else 0

MEMORY_DIR = BASE_DIR / "memory"
# memory/user.md goes into every Direct prompt, as Hermes puts USER.md into every
# session (its cap is 1375). A memory_write that would pass this is refused, so the
# file gets tidied instead of cut off.
USER_PROFILE_CAP = 1500
LOG_FILE = BASE_DIR / "igor.log"

# Number of individual messages (user + assistant counted separately) kept in
# rolling session context and passed with every API call.
CONTEXT_WINDOW = 6
# Unprompted posts (digest, job and gig lists) shown beside those six conversation
# messages. Counted apart since 2026-10-21: four morning posts used to fill the window
# and push out the user's own words.
CONTEXT_POSTS = 3
# Stored conversation is kept by age, as Hermes prunes sessions inactive 90 days.
# IGOR_SPEC: "no hoarding of conversation history" (ratified by the user 2026-10-17).
HISTORY_RETENTION_DAYS = 90

# Critic pass fires a second API call after every React turn to evaluate skill
# capture. On the Groq free 8k-TPM tier this doubles per-turn token pressure and
# reliably trips the rate limit once context fills. Off by default; re-enable on
# a higher tier or when the improvement loop (Phase 2.3) replaces it.
ENABLE_CRITIC = False
