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
"""
import asyncio
import difflib
import hashlib
import logging
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


def main(argv: list[str]) -> None:
    react_only = argv[:1] == ["--react"]
    messages = argv[1:] if react_only else argv
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
        asyncio.run(_run(messages, react_only, config))
        changed = sorted(k for k, v in _hashes(root).items() if code_before.get(k) != v)
        print("\ncode files the run changed (in the copy only):", changed or "none")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _run(messages: list[str], react_only: bool, config) -> None:
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
        reply, _ = await o.process(config.AUTHORIZED_USER_ID, message)
        print(f"\nUSER: {message}\nIGOR: {reply}")

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
