import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# TPM limits verified 2026-07-09; Groq buckets are per-model, so roles sharing a
# model share its budget (noted below). Do not assume a role has a private bucket.
MODELS = {
    "router": "llama-3.1-8b-instant",        # 6000 TPM bucket, shared with summary
    "chat": "llama-3.3-70b-versatile",       # 12000 TPM bucket, shared with evaluator
    "react": "openai/gpt-oss-120b",          # 8000 TPM bucket, sole occupant
    "research": "openai/gpt-oss-20b",        # 8000 TPM bucket, sole occupant
    "evaluator": "llama-3.3-70b-versatile",  # shares chat's 12000 bucket
    "summary": "llama-3.1-8b-instant",       # shares router's 6000 bucket
}

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
