"""Rehearse the overnight paper write: the last real revision run again with today's code and the
real models, on a throwaway copy of memory, so a change is seen on real text before a night runs it.

2026-11-06: each fix to the paper had been tested on fakes and then "seen tonight", and every night
found the next fault. One rehearsal found three in an hour: the shortlist labels read from text that
had not been cleaned, an older check labelling unsearched ideas "no income shown", and a summary
crediting income the proof check had not found.

Spends real tokens: one write on 120b (two if the first is rejected) and an evaluator call on 20b,
about 6000 and 2500. No searches. The spend is added to the real token ledger.

On the server, as igor:
    cd /opt/igor && venv/bin/python scripts/rehearse_paper.py [goal id, default g3] [--dry-run]
--dry-run names the inputs it would use and stops before any model call.
"""
import asyncio
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent


def _inputs(folder: Path) -> tuple[Path, Path]:
    """The paper and findings the last revision started from, or else today's."""
    moved = sorted(folder.glob("findings-*.md"), key=lambda p: p.stat().st_mtime)
    for findings in reversed(moved):
        before = folder / "versions" / findings.name.replace("findings-", "paper-", 1)
        if before.exists():
            return before, findings
    return folder / "paper.md", folder / "findings.md"


def main(argv: list[str]) -> None:
    sys.path.insert(0, str(SOURCE))
    sys.path.insert(0, str(SOURCE / "scripts"))
    logging.basicConfig(level=logging.INFO, format="  log: %(name)s: %(message)s")
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    import config

    gid = next((a for a in argv if not a.startswith("--")), "g3")
    real = config.MEMORY_DIR
    paper_in, findings_in = _inputs(real / "work" / gid)
    print(f"paper before: {paper_in}\nfindings: {findings_in}")
    if not (paper_in.exists() and findings_in.exists()):
        sys.exit("no paper and findings to rehearse")
    if "--dry-run" in argv:
        return
    tmp = Path(tempfile.mkdtemp(prefix="igor-rehearse-"))
    try:
        mem = tmp / "memory"
        mem.mkdir()
        for p in real.glob("*.md"):
            shutil.copy2(p, mem / p.name)
        shutil.copy2(real / "token_ledger.json", mem / "token_ledger.json")
        folder = mem / "work" / gid
        folder.mkdir(parents=True)
        shutil.copy2(paper_in, folder / "paper.md")
        shutil.copy2(findings_in, folder / "findings.md")
        if (real / "work" / gid / "proof.json").exists():
            shutil.copy2(real / "work" / gid / "proof.json", folder / "proof.json")
        config.MEMORY_DIR = mem

        import e2e_isolated
        import goals
        import proof
        from agents import overnight, research_loop

        goal = next(g for g in goals.load() if g["id"] == gid)
        before = json.loads((mem / "token_ledger.json").read_text(encoding="utf-8"))
        proof.next_idea = lambda names, ledger: None
        model = overnight._real_complete()

        async def complete(system, user, max_tokens, cap=None):
            return "" if system == research_loop._PLAN_SYSTEM else await model(system, user, max_tokens, cap)

        async def no_search(query, **kwargs):
            return []

        state = {"searches": 0, "files": [], "finished": [], "next_action": goal.get("next_action", "")}
        try:
            summary = asyncio.run(overnight._research_session(goal, state, no_search, complete, "rehearsal", 3, 60000))
        finally:
            e2e_isolated._count_in_the_real_ledger(
                before, json.loads((mem / "token_ledger.json").read_text(encoding="utf-8")))
        print(f"\nsession: {summary}")
        drafts = sorted(folder.glob("draft-*.md"))
        result = drafts[-1] if drafts and not state["finished"] else folder / "paper.md"
        print(f"\n{result.name}, {len(result.read_text(encoding='utf-8'))} characters:\n")
        print(result.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
