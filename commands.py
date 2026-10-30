"""Commands answered in code, with no model call: Hermes' gateway slash commands.

Asking "how many tokens are left" cost a router call and a 120b call to read one file.
Each of these is the whole message, with or without a slash, so "help me find a desk" and
"status of my order" stay ordinary requests. The work is done in orchestrator.py, which
holds the turn they act on.
"""

import re
from typing import Optional

_COMMAND = re.compile(r"^\s*/?(stop|retry|undo|usage|status|help)\s*[.!?]*\s*$", re.IGNORECASE)

HELP = ("These take no model call. stop ends what I am working on. retry runs your last message "
        "again. undo removes your last message and my reply from the conversation; files stay as "
        "they are. usage shows the tokens spent today. status shows what is running and what is "
        "scheduled. rollback lists file changes I can put back. heartbeat every 1h, with a link, "
        "watches a page.")


def parse(text: str) -> Optional[str]:
    m = _COMMAND.match(text or "")
    return m.group(1).lower() if m else None
