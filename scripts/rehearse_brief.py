"""Rehearse a role brief: one real posting run through agents/briefs.py with the real search and models,
on a throwaway copy of memory, so the brief is read before a night writes one.

Spends real tokens: about 4 calls on 20b and 3 Exa searches. The spend is added to the real token ledger.

On the server, as igor, from a checkout of the code to rehearse (the deployed tree, or a clone of a commit
not yet deployed):
    venv/bin/python scripts/rehearse_brief.py <posting url> [--memory /opt/igor/memory]
The posting is looked up in the job watch's live feeds, so it must still be listed.
"""
import asyncio
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> None:
    sys.path.insert(0, str(SOURCE))
    sys.path.insert(0, str(SOURCE / "scripts"))
    logging.basicConfig(level=logging.INFO, format="  log: %(name)s: %(message)s")
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    import config

    args = [a for a in argv if not a.startswith("--")]
    real = Path(argv[argv.index("--memory") + 1]) if "--memory" in argv else config.MEMORY_DIR
    args = [a for a in args if a != str(real)]
    if not args:
        sys.exit("usage: rehearse_brief.py <posting url> [--memory DIR]")
    url = args[0]

    from agents import jobs
    posting = None
    for name, fetch in jobs._live_fetchers():
        try:
            posting = next((j for j in fetch() if j.get("url") == url), None)
        except Exception as e:
            print(f"feed {name} unavailable: {e}")
        if posting:
            break
    if not posting:
        sys.exit("that posting is not in the live feeds")
    if not posting.get("description") and posting.get("source", "").startswith("greenhouse:"):
        posting = jobs._with_content(posting)
    print(f"posting: {posting['title']} - {posting.get('company')}")

    tmp = Path(tempfile.mkdtemp(prefix="igor-brief-"))
    try:
        mem = tmp / "memory"
        (mem / "private").mkdir(parents=True)
        shutil.copy2(real / "private" / "profile.md", mem / "private" / "profile.md")
        for name in ("token_ledger.json", "history.db"):
            if (real / name).exists():
                shutil.copy2(real / name, mem / name)
        config.MEMORY_DIR = mem

        import e2e_isolated
        from agents import briefs, overnight, research
        # Run from a clone, the real ledger is the one beside the real memory, not beside this script.
        e2e_isolated.SOURCE = real.parent

        before = json.loads((mem / "token_ledger.json").read_text(encoding="utf-8"))
        briefs.queue([posting], "1 APPLY", None)
        item = briefs.next_due("rehearsal")
        try:
            summary, searches, files = asyncio.run(
                briefs.run(item, research._run_search, overnight._real_complete(), "rehearsal"))
        finally:
            after = json.loads((mem / "token_ledger.json").read_text(encoding="utf-8"))
            e2e_isolated._count_in_the_real_ledger(before, after)
        print(f"\n{summary}, {searches} searches\n")
        for rel in files:
            print((mem / rel).read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
