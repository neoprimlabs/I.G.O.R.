import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

MODEL = "claude-sonnet-4-6"

DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
EXA_API_KEY: str = os.getenv("EXA_API_KEY", "")

_uid = os.getenv("AUTHORIZED_DISCORD_USER_ID", "")
AUTHORIZED_USER_ID: int = int(_uid) if _uid.isdigit() else 0

MEMORY_DIR = BASE_DIR / "memory"
LOG_FILE = BASE_DIR / "igor.log"

# Number of individual messages (user + assistant counted separately) kept in
# rolling session context and passed with every API call.
CONTEXT_WINDOW = 10
