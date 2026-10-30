"""Send messages through IGOR against a throwaway copy of its code and memory.

For a live check without Discord. The first version of this copied only memory; the
model ran with the real code folder writable, so a write_file in a test turn would
have changed IGOR itself (2026-10-26; nothing was written, but nothing stopped it).
Now the copy holds both, the keys are loaded into this process only, the copy is
deleted afterwards, and every code file the run touched is reported.

Spends the real daily token budget: one message is several model calls.

On the server, as igor:
    cd /opt/igor && venv/bin/python scripts/e2e_isolated.py "message" ["follow-up" ...]
    venv/bin/python scripts/e2e_isolated.py --react "message"   (React alone, no history)
    venv/bin/python scripts/e2e_isolated.py --attach /path/file "message"   (as if attached)
"""
import asyncio
import difflib
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent
_CODE_SKIP = ("venv", ".venv", ".git", "memory", ".cache", "research", "__pycache__", "*.log", ".env")


def _hashes(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not set(p.relative_to(root).parts) & {
                "memory", "venv", ".venv", ".git", ".cache", "research", "__pycache__"}}


def _md_snapshot(memory: Path) -> dict:
    return {p.name: p.read_text(encoding="utf-8") for p in memory.glob("*.md")}


def _read_ledger(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _count_in_the_real_ledger(before: dict, after: dict) -> None:
    """The copy's memory is thrown away, its spend is not. On 2026-10-30 the ledger read
    47000 of 120b's 200000 with about 116000 more spent in runs like this, so the budget
    checks and Monitor's report were all reading low."""
    real = SOURCE / "memory" / "token_ledger.json"
    data = _read_ledger(real)
    added = 0
    for hour, models in after.items():
        for model, labels in models.items():
            spent = sum(labels.values()) - sum(before.get(hour, {}).get(model, {}).values())
            if spent > 0:
                row = data.setdefault(hour, {}).setdefault(model, {})
                row["Isolated run"] = row.get("Isolated run", 0) + spent
                added += spent
    if added:
        tmp = real.with_name(real.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, real)
        print(f"\ntokens this run spent, added to the real ledger: {added}")


def main(argv: list[str]) -> None:
    react_only = argv[:1] == ["--react"]
    attach = argv[1] if argv[:1] == ["--attach"] else None
    messages = argv[1:] if react_only else argv[2:] if attach else argv
    if not messages:
        raise SystemExit(__doc__)

    tmp = Path(tempfile.mkdtemp(prefix="igor-e2e-"))
    root = tmp / "igor"
    try:
        shutil.copytree(SOURCE, root, ignore=shutil.ignore_patterns(*_CODE_SKIP))
        shutil.copytree(SOURCE / "memory", root / "memory",
                        ignore=shutil.ignore_patterns(".checkpoints", "work", "*.bak*", "*.log"))
        from dotenv import load_dotenv
        load_dotenv(SOURCE / ".env")
        sys.path.insert(0, str(root))
        logging.basicConfig(level=logging.INFO, format="  log: %(name)s: %(message)s")
        for noisy in ("httpx", "openai", "apscheduler"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        import config
        assert config.BASE_DIR.resolve() == root.resolve(), config.BASE_DIR
        code_before = _hashes(root)
        ledger = config.MEMORY_DIR / "token_ledger.json"
        spent_before = _read_ledger(ledger)
        try:
            asyncio.run(_run(messages, react_only, config, attach))
        finally:
            _count_in_the_real_ledger(spent_before, _read_ledger(ledger))
        changed = sorted(k for k, v in _hashes(root).items() if code_before.get(k) != v)
        print("\ncode files the run changed (in the copy only):", changed or "none")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class _Attachment:
    """What inbound.read uses of a discord.Attachment."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.filename, self.content_type, self.url = self.path.name, None, ""
        self.size = self.path.stat().st_size

    async def read(self) -> bytes:
        return self.path.read_bytes()


async def _run(messages: list[str], react_only: bool, config, attach: str | None = None) -> None:
    async def notify(_):
        pass

    if react_only:
        from agents import react
        react.set_notify(notify)
        for message in messages:
            before = _md_snapshot(config.MEMORY_DIR)
            print(f"\nUSER: {message}\nIGOR: {await react.handle(message, [])}")
            _print_changes(before, _md_snapshot(config.MEMORY_DIR))
        return

    import orchestrator
    o = orchestrator.Orchestrator(notify)
    for message in messages:
        before = _md_snapshot(config.MEMORY_DIR)
        content, prefer_tools = message, False
        if attach:
            from agents import inbound
            parts, prefer_tools = await inbound.read([_Attachment(attach)], ask=message)
            content, attach = inbound.combine(message, parts), None
        reply, _ = await o.process(config.AUTHORIZED_USER_ID, content, prefer_tools=prefer_tools)
        print(f"\nUSER: {content}\nIGOR: {reply}")

        async def send(text):
            print(f"\nIGOR (followed through): {text}")
        await o.follow_through(send)
        _print_changes(before, _md_snapshot(config.MEMORY_DIR))


def _print_changes(before: dict, after: dict) -> None:
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            print(f"  memory changed (copy): {name}")
            for line in difflib.unified_diff((before.get(name) or "").splitlines(),
                                             (after.get(name) or "").splitlines(), lineterm="", n=0):
                if line[:1] in "+-" and line[:3] not in ("+++", "---"):
                    print("   ", line)


if __name__ == "__main__":
    main(sys.argv[1:])
