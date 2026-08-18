import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# TPM limits re-measured 2026-08-18 from the x-ratelimit headers, after Groq
# removed the Llama family. Every general-purpose model left is 8000 TPM: the
# 6000 and 12000 buckets this file used to describe no longer exist. Buckets are
# per-model, so roles sharing a model share its budget.
MODELS = {
    "router": "qwen/qwen3.6-27b",        # 8000 TPM, sole occupant, ~28 tokens a call
    "chat": "openai/gpt-oss-120b",       # 8000 TPM bucket, shared with react
    "react": "openai/gpt-oss-120b",      # shares chat's bucket, deliberately
    "research": "openai/gpt-oss-20b",    # 8000 TPM bucket, shared with evaluator and summary
    "evaluator": "openai/gpt-oss-20b",   # shares research's bucket
    "summary": "openai/gpt-oss-20b",     # shares research's bucket
}
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

DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
EXA_API_KEY: str = os.getenv("EXA_API_KEY", "")

_uid = os.getenv("AUTHORIZED_DISCORD_USER_ID", "")
AUTHORIZED_USER_ID: int = int(_uid) if _uid.isdigit() else 0

MEMORY_DIR = BASE_DIR / "memory"
LOG_FILE = BASE_DIR / "igor.log"

# Number of individual messages (user + assistant counted separately) kept in
# rolling session context and passed with every API call.
CONTEXT_WINDOW = 6

# Critic pass fires a second API call after every React turn to evaluate skill
# capture. On the Groq free 8k-TPM tier this doubles per-turn token pressure and
# reliably trips the rate limit once context fills. Off by default; re-enable on
# a higher tier or when the improvement loop (Phase 2.3) replaces it.
ENABLE_CRITIC = False
