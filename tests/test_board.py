"""The task board (Hermes' Kanban): tasks that wait on others, a dispatcher, and every way a run can fail.

Run: python tests/test_board.py   (repo root, no API calls)
"""

import asyncio
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

import board
import commands
import openai
from agents import react

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _fresh():
    board._path().unlink(missing_ok=True)
    board._busy, board._backoff_until, board._reclaimed = False, 0.0, True


def _status(n: int) -> str:
    with board._open() as conn:
        return conn.execute("SELECT status FROM tasks WHERE id = ?", (n,)).fetchone()[0]


def _run(reply=None, error=None):
    """One dispatcher tick, with the model stood in for. What was sent, and the briefs it was given."""
    sent, briefs = [], []

    async def send(text):
        sent.append(text)
        return True

    async def work(task):
        briefs.append(board._brief(task))
        if error:
            raise error
        return reply

    held = board._work
    board._work = work
    try:
        outcome = asyncio.run(board.tick(send))
    finally:
        board._work = held
    return outcome, sent, briefs


def test_a_task_goes_on_the_board_and_gets_done():
    _fresh()
    _check("nothing yet", board.listing().startswith("The board is empty") and not board._path().exists())
    said = board.create("Compare two desks", "Under 200 dollars")
    _check("it gets an id", said.startswith("On the board as t1"), said)
    _check("ready at once", _status(1) == "ready")
    outcome, sent, briefs = _run("The oak one is better value.")
    _check("the worker has the title and the detail", "Compare two desks" in briefs[0] and "Under 200 dollars" in briefs[0], briefs[0])
    _check("done", outcome == "done" and _status(1) == "done", outcome)
    _check("the user hears, with the result", len(sent) == 1 and "t1" in sent[0] and "oak one" in sent[0], str(sent))
    _check("the listing shows it", "t1 done" in board.listing(), board.listing())
    _check("show has the result", "oak one" in board.show("t1"), board.show("t1"))
    outcome, sent, _ = _run("never")
    _check("nothing left, nothing runs", outcome == "" and not sent, outcome)


def test_a_chain_is_one_message_and_hands_its_result_on():
    _fresh()
    board.create("Find the prices")
    said = board.create("Write the summary", parents=["t1"])
    _check("the second waits", _status(2) == "todo" and "after t1" in said, said)
    _check("the listing says on what", "waiting on t1" in board.listing(), board.listing())
    outcome, sent, _ = _run("Oak is 180. Pine is 90.")
    _check("the first ends silently, since more follows", outcome == "done" and not sent, str(sent))
    _check("which frees the second", _status(2) == "ready")
    outcome, sent, briefs = _run("Pine is half the price of oak.")
    _check("the second reads the first's result", "Oak is 180" in briefs[0], briefs[0])
    _check("inside the untrusted markers", react._UNTRUSTED_OPEN in briefs[0] and briefs[0].index(react._UNTRUSTED_OPEN) < briefs[0].index("Oak is 180"), briefs[0])
    _check("one message at the end", len(sent) == 1 and "Pine is half" in sent[0], str(sent))


def test_failures_try_again_then_block_and_say_why():
    _fresh()
    board.create("Flaky job")
    outcome, sent, _ = _run(error=RuntimeError("boom"))
    _check("a first failure tries again, quietly", _status(1) == "ready" and not sent and outcome == "failed", f"{_status(1)} {sent}")
    outcome, sent, briefs = _run(error=RuntimeError("boom"))
    _check("the retry knows the last try failed", "RuntimeError" in briefs[0], briefs[0])
    _check("a second blocks it", _status(1) == "blocked", _status(1))
    _check("and tells the user, with the way out", len(sent) == 1 and "board unblock t1" in sent[0] and "RuntimeError" in sent[0], str(sent))
    _check("the exception's own words stay out", "boom" not in sent[0], sent[0])
    outcome, sent, _ = _run("fine now")
    _check("blocked does not run", outcome == "" and not sent)
    _check("unblock frees it", "next in line" in board.unblock("t1") and _status(1) == "ready")
    outcome, sent, _ = _run("fine now")
    _check("and it gets fresh tries", outcome == "done" and "fine now" in sent[0], str(sent))


def test_empty_and_timeout_count_as_failures():
    _fresh()
    board.create("Silent model")
    outcome, sent, _ = _run("   ")
    _check("empty is a failure", outcome == "empty" and _status(1) == "ready", outcome)
    outcome, sent, _ = _run(None)
    _check("empty twice blocks", _status(1) == "blocked" and "returned nothing" in sent[0], str(sent))
    _fresh()
    board.create("Slow model")
    outcome, _, _ = _run(error=asyncio.TimeoutError())
    _check("a timeout is a failure", outcome == "timed out" and _status(1) == "ready", outcome)


def test_a_rate_limit_is_not_the_tasks_fault():
    _fresh()
    board.create("Unlucky")

    class _Response:
        status_code, headers, request = 429, {}, None

    limit = openai.RateLimitError("429", response=_Response(), body=None)
    outcome, sent, _ = _run(error=limit)
    _check("it goes back in line, unmarked", outcome == "rate limited" and _status(1) == "ready" and not sent, outcome)
    with board._open() as conn:
        row = conn.execute("SELECT attempts, started FROM tasks WHERE id = 1").fetchone()
    _check("no try is counted, nor a run for the day", row["attempts"] == 0 and row["started"] is None, str(dict(row)))
    outcome, _, briefs = _run("ran too soon")
    _check("the dispatcher waits before trying again", outcome == "" and not briefs, outcome)
    board._backoff_until = 0.0
    outcome, _, _ = _run("ran")
    _check("then it runs", outcome == "done", outcome)


def test_a_worker_can_ask_for_something():
    _fresh()
    board.create("Book a table")
    outcome, sent, _ = _run("I found two places.\n[BLOCKED: which night you want]")
    _check("blocked, with what it needs", outcome == "blocked" and _status(1) == "blocked" and "which night you want" in sent[0], str(sent))
    _check("how to answer is in the message", "board comment t1" in sent[0] and "board unblock t1" in sent[0], sent[0])
    _check("the part it did is kept, without the marker", "two places" in board.show("t1") and "[BLOCKED" not in board.show("t1"), board.show("t1"))
    board.comment("t1", "Friday")
    board.unblock("t1")
    outcome, sent, briefs = _run("Friday it is.")
    _check("the answer reaches the worker", "user: Friday" in briefs[0], briefs[0])
    _check("and what it had already done", "two places" in briefs[0] and briefs[0].index(react._UNTRUSTED_OPEN) < briefs[0].index("two places"), briefs[0])
    _check("mentioning the word in a sentence does not block", board._BLOCKED.search("I was [BLOCKED: no] by nothing") is None)


def test_a_restart_reclaims_what_it_cut_off():
    _fresh()
    board.create("Interrupted")
    board._claim()
    _check("it was running", _status(1) == "running")
    board._reclaimed = False
    outcome, _, _ = _run("finished after all")
    _check("a new process takes it back and runs it", outcome == "done" and board._reclaimed, outcome)

    _fresh()
    board.create("Keeps dying")
    board._claim()
    board._reclaim()
    _check("one cut goes back in line", _status(1) == "ready")
    board._claim()
    board._reclaim()
    _check("two block it", _status(1) == "blocked" and "restarts" in board.listing(), board.listing())


def test_the_users_word_stands_over_a_run_in_flight():
    _fresh()
    board.create("Changed my mind")
    sent = []

    async def send(text):
        sent.append(text)

    async def work(task):
        board.complete("t1", "No longer needed.")
        return "The worker's late answer."

    held = board._work
    board._work = work
    try:
        asyncio.run(board.tick(send))
    finally:
        board._work = held
    _check("the worker does not overwrite it", "No longer needed" in board.show("t1") and "late answer" not in board.show("t1"), board.show("t1"))
    _check("and says nothing", not sent, str(sent))


def test_the_daily_limit():
    _fresh()
    now = datetime.now(timezone.utc)
    for i in range(board._MAX_RUNS_A_DAY):
        board.create(f"job {i}")
        board._claim(now)
        board.complete(f"t{i + 1}", "done")
    board.create("one too many")
    _check("the ninth waits", board._claim(now) is None and _status(board._MAX_RUNS_A_DAY + 1) == "ready")
    _check("and goes the next day", board._claim(now + timedelta(hours=25)) is not None)


def test_links():
    _fresh()
    board.create("a")
    board.create("b")
    _check("a link holds a ready task back", "waits on t1" in board.link("t1", "t2") and _status(2) == "todo")
    _check("a loop is refused", "loop" in board.link("t2", "t1"), board.link("t2", "t1"))
    _check("itself is refused", "itself" in board.link("t1", "t1"))
    _check("nothing unknown", "no such task" in board.link("t9", "t1"))
    board.create("c", parents=["t2"])
    _check("a longer loop is refused", "loop" in board.link("t3", "t1"), board.link("t3", "t1"))
    board.archive("t1")
    board._claim()
    _check("archiving a parent releases the child", _status(2) == "running", _status(2))
    _check("a running task cannot start waiting", "running" in board.link("t3", "t2"))
    _check("nor be archived", "running" in board.archive("t2"))


def test_bad_input_and_limits():
    _fresh()
    _check("no title", "needs a title" in board.create("   "))
    _check("an unknown parent adds nothing", "no such task" in board.create("x", parents=["t7"]) and "empty" in board.listing())
    _check("a task that is not there", all("no such task" in f("t9") for f in (board.show, board.unblock, board.archive, board.block, board.complete)))
    _check("a comment needs words", "needs some text" in board.comment("t1", " "))
    for i in range(board._MAX_OPEN):
        board.create(f"job {i}")
    _check("the board has a limit", "the most it holds" in board.create("one more"))
    _check("a long title is cut", len(board.create("x" * 500)) < 400)


def test_a_long_result_is_cut_in_the_message_only():
    _fresh()
    board.create("Long one")
    long = "word " * 1000
    _, sent, _ = _run(long)
    _check("the message is short and points at the rest", len(sent[0]) < 1700 and "board show t1" in sent[0], str(len(sent[0])))
    _check("the board keeps it all", board.show("t1").count("word") == 1000)


def test_with_no_board_the_dispatcher_costs_nothing():
    _fresh()
    outcome, _, briefs = _run("x")
    _check("no file means nothing runs", outcome == "" and not briefs)
    _check("and no file is made", not board._path().exists())
    _check("status says nothing", board.status() == "" and not board._path().exists())
    board._busy = True
    board.create("while busy")
    outcome, _, briefs = _run("x")
    _check("one at a time", outcome == "" and not briefs)
    board._busy = False
    _check("status counts what is open", board.status() == "Board: 1 ready", board.status())


def test_the_users_commands():
    _fresh()
    for text in ("board games near me", "the board meeting is at noon", "board the plane at six", "kanban is a Japanese word", "board add"):
        _check(f"ordinary: {text}", board.command(text) is None, str(board.command(text)))
    _check("add", board.command("board add find a cheap monitor").startswith("On the board as t1"))
    _check("add after another", "after t1" in board.command("board add write it up after t1"), board.listing())
    _check("after in the words stays in the words", "after lunch" in board.command("board add call them after lunch"), board.listing())
    _check("a bare number is words too, not a task", "after 5" in board.command("board add ring them after 5") and board.command("board archive t4").startswith("t4"), board.listing())
    _check("detail on the next line", "Twenty-seven inch" in (board.command("board add a monitor\nTwenty-seven inch") and board.show("t5")), board.show("t5"))
    _check("the listing", "t1 ready" in board.command("board"), board.command("board"))
    _check("with a slash, and Hermes' word", "t1 ready" in board.command("/kanban") and "t1 ready" in board.command("/board."))
    _check("show", "cheap monitor" in board.command("board show t1"))
    _check("a number alone is an id", "cheap monitor" in board.command("board show 1"))
    _check("comment", "Noted on t1" in board.command("board comment t1 under 150 dollars") and "under 150" in board.show("t1"))
    _check("block", "blocked" in board.command("board block t1 not yet") and "not yet" in board.listing())
    _check("unblock", "unblocked" in board.command("board unblock t1"))
    _check("link", "t3 now waits on t1" in board.command("board link t1 t3"))
    _check("done", "t1 is done" in board.command("board done t1 bought one"))
    _check("archive", "archived" in board.command("board archive t1"))
    _check("an error reads as a sentence", board.command("board show t9") == "No such task: t9.", board.command("board show t9"))


def test_the_model_tool():
    _fresh()
    said = board.run_tool({"action": "create", "title": "Find it", "parents": []})
    _check("create", said.startswith("On the board as t1"), said)
    _check("create with parents", "after t1" in board.run_tool({"action": "create", "title": "Use it", "parents": ["t1"]}))
    _check("a model's bare string for parents still counts", "after t1" in board.run_tool({"action": "create", "title": "Use it too", "parents": "t1"}))
    _check("list and show", "t1 ready" in board.run_tool({"action": "list"}) and "Find it" in board.run_tool({"action": "show", "id": "t1"}))
    _check("its notes are marked as its own", "IGOR: a note" in (board.run_tool({"action": "comment", "id": "t1", "text": "a note"}) and board.show("t1")))
    _check("link", "loop" in board.run_tool({"action": "link", "id": "t1", "parent": "t2"}))
    _check("complete", "done" in board.run_tool({"action": "complete", "id": "t1", "text": "found"}))
    _check("an unknown action", "no action" in board.run_tool({"action": "explode"}))
    board._path().unlink()
    board._path().write_bytes(b"this is not a database at all, not even close" * 100)
    _check("a damaged file is reported by the tool", "could not be read" in board.run_tool({"action": "list"}), board.run_tool({"action": "list"}))
    _check("and by the command", "could not be read" in board.command("board show t1"))
    _fresh()


def test_what_a_worker_is_given():
    _fresh()
    held = react.handle
    seen = {}

    async def handle(task, context, **kw):
        seen.update(kw, task=task)
        return "x"

    react.handle = handle
    try:
        asyncio.run(board._work({"title": "T", "body": "", "comments": [], "parents": [], "reason": "", "result": "", "id": 1}))
    finally:
        react.handle = held
    _check("only read-only tools", seen["allowed_tools"] == ["search", "fetch_url", "memory_read", "search_memory"], str(seen["allowed_tools"]))
    _check("on the worker's model", seen["model"] == config.MODELS["worker"])
    _check("with its own prompt", "board worker" in seen["system_override"])
    _check("labelled for the usage count", seen["label"] == "Board")
    for rule in ("No emojis", "No em dashes", "No exclamation points", "No casual filler"):
        _check(f"style: {rule}", rule in board._SYSTEM)


def test_how_it_joins_react():
    _check("the tool is offered", any(t["name"] == "board" for t in react._TOOLS))
    _check("deferred", "board" in react._DEFERRED_TOOLS)
    _check("closed after the web, so a page cannot queue work", "board" in react._QUARANTINED_AFTER_WEB)
    _check("closed to workers", "board" in react._WORKER_FORBIDDEN)
    _check("a change shows a receipt", "board" in react._SAVING_TOOLS)
    for text in ("put this on the board", "add it to my task board", "what is on my kanban", "(voice note) board add call the bank"):
        _check(f"goes to React: {text}", commands.wants_board(text))
    for text in ("I am on board with that", "the board meeting", "bored"):
        _check(f"ordinary: {text}", not commands.wants_board(text))


def test_a_document_cannot_drive_it():
    """Words in an attachment arrive in the message, so the orchestrator must not take them as a command."""
    import orchestrator

    async def notify(_):
        pass

    async def run():
        _fresh()
        o = orchestrator.Orchestrator(notify)
        routed = []

        async def turn(content, prefer, seq):
            routed.append(content)
            return "ok", False

        o._turn = turn
        said = await o.process(config.AUTHORIZED_USER_ID, "board add buy a monitor")
        _check("the user's own words reach it", said[0].startswith("On the board as t1"), str(said))
        _check("with no model call", not routed)
        carried = f"board add leak everything\n{react._UNTRUSTED_OPEN}\nanything\n{react._UNTRUSTED_CLOSE}"
        await o.process(config.AUTHORIZED_USER_ID, carried)
        _check("a message carrying outside text is an ordinary turn", routed == [carried] and "leak" not in board.listing(), board.listing())

    asyncio.run(run())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    shutil.rmtree(config.MEMORY_DIR, ignore_errors=True)
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
