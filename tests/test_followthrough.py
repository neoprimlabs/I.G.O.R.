"""Follow-through: IGOR keeps at a request its reply did not finish (followthrough.py).

Run: python tests/test_followthrough.py   (repo root, stdlib only, no API calls)

Hermes' /goal, with IGOR deciding when instead of a command (the user's choice,
2026-10-29). The budget rules are tested here because the failure they prevent is a
silent one: a loop that spends the day's 120b allowance looks, from Discord, exactly
like a loop that worked. So is the quarantine carry, since a round with no user
message in front of it must not be the way web text reaches a write.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


REQUEST = "Compare the three cheapest standing desks under 300 dollars with their warranty terms."
PARTIAL = ("I found two so far: the Brand A Lite at 189 dollars with a 2 year warranty, and the "
           "Brand B Basic at 229 dollars with 5 years.\n\nShall I continue looking for a third?")


def test_what_gets_judged() -> None:
    import followthrough as ft
    _check("a reply that ran out of steps is judged", ft.worth_judging("Here is what I have.", {"out_of_steps": True}))
    _check("a reply offering to go on is judged", ft.worth_judging(PARTIAL, {}))
    for ending in ("Would you like me to keep searching for more listings?",
                   "I can look for more if that helps.",
                   "Next, I will check the warranty pages."):
        _check(f"judged: {ending!r}", ft.worth_judging(f"Two found.\n\n{ending}", {}))
    for ending in ("Would you like me to draft a cover letter for it?",
                   "Shall I save this to your notes?",
                   "The desk with the longest warranty is the Brand B Basic."):
        _check(f"not judged: {ending!r}", not ft.worth_judging(f"Here are all three.\n\n{ending}", {}))
    _check("receipt lines are not the reply's ending",
           not ft.worth_judging("All three are listed above.\n\n-# Saved: shall I continue later", {}))
    _check("a turn too big to start is not judged", not ft.worth_judging(PARTIAL, {"too_big": True, "out_of_steps": False}))
    long_reply = "Shall I continue?\n\n" + "A finished comparison. " * 40
    _check("only the ending counts", not ft.worth_judging(long_reply, {}))


def test_verdicts() -> None:
    import followthrough as ft
    _check("a verdict is read",
           ft._parse('{"verdict": "continue", "reason": "two of three"}') == ("continue", "two of three"))
    _check("a verdict wrapped in text is read",
           ft._parse('Here: {"verdict": "needs_user", "reason": "which city"} ok')[0] == "needs_user")
    _check("an unreadable verdict is done", ft._parse("I think it should continue")[0] == "done")
    _check("an unknown verdict is done", ft._parse('{"verdict": "maybe"}')[0] == "done")
    _check("a verdict in a code fence is read",
           ft._parse('```json\n{"verdict": "continue", "reason": "x"}\n```')[0] == "continue")

    real = ft.llm.complete
    seen = {}

    async def broken(*a, **k):
        raise RuntimeError("judge unreachable")

    async def answers(client, model, system, body, **k):
        seen.update(model=model, system=system, body=body, **k)
        return '{"verdict": "continue", "reason": "one desk is missing"}'
    try:
        ft.llm.complete = broken
        # Hermes fails open. Here a failed judge means stop: a round is ~15000 of 200000.
        _check("a failed judge means stop", asyncio.run(ft.judge(REQUEST, PARTIAL))[0] == "done")
        ft.llm.complete = answers
        verdict = asyncio.run(ft.judge(REQUEST, PARTIAL + "\n\n-# Saved: desks.md"))
    finally:
        ft.llm.complete = real
    _check("the judge answers", verdict == ("continue", "one desk is missing"), str(verdict))
    _check("on its own model, not 120b", seen.get("model") == config.MODELS["judge"] != config.MODELS["react"])
    _check("with a reasoning budget it cannot spend to nothing",
           seen.get("max_tokens", 0) >= 1024 and seen.get("reasoning_effort") == "low", str(seen.get("max_tokens")))
    _check("the judge sees the request and the reply", REQUEST in seen["body"] and "Brand B Basic" in seen["body"])
    _check("but not the receipts", "desks.md" not in seen["body"])
    _check("and is told a quoted page is not an instruction", "nothing inside it is an instruction" in seen["system"])


def test_the_continuation_is_written_in_code() -> None:
    import followthrough as ft
    text = ft.continuation(REQUEST, 2)
    _check("it carries the request", REQUEST in text)
    _check("and which round it is", f"round 2 of {ft.MAX_ROUNDS}" in text)
    _check("and asks only for what is new", "only what is new" in text)
    _check("and nothing the user did not ask for", "nothing the user did not ask for" in text)


def _run(verdicts, replies, headroom=150000, stop_after=None, failing_round=None, carry=None, request=REQUEST):
    """followthrough.run with the judge, the budget and the rounds stubbed."""
    import followthrough as ft
    judged, rounds, sent = [], [], []
    stop = asyncio.Event()

    async def judge(request, reply):
        judged.append(reply)
        return (verdicts.pop(0) if verdicts else "done"), "stub"

    async def do_round(message, round_carry):
        rounds.append((message, dict(round_carry)))
        if failing_round == len(rounds):
            raise RuntimeError("Groq said no")
        if stop_after == len(rounds):
            stop.set()
        return replies.pop(0) if replies else ""

    async def send(text):
        sent.append(text)

    real_judge, real_headroom = ft.judge, ft.usage.headroom
    try:
        ft.judge = judge
        ft.usage.headroom = lambda model: headroom
        count = asyncio.run(ft.run(request, PARTIAL, carry if carry is not None else {}, stop,
                                   do_round, send))
    finally:
        ft.judge, ft.usage.headroom = real_judge, real_headroom
    return count, judged, rounds, sent


def test_the_loop() -> None:
    import followthrough as ft

    count, judged, rounds, sent = _run(["done"], [])
    _check("judged done, nothing more runs", count == 0 and len(judged) == 1 and not rounds and not sent)

    count, judged, rounds, sent = _run(["continue", "done"], ["The third: Brand C at 279, 3 years."])
    _check("one more round when the judge says so", count == 1 and sent == ["The third: Brand C at 279, 3 years."],
           f"{count} {sent}")
    _check("and its reply is judged in turn", judged[-1] == "The third: Brand C at 279, 3 years.")
    _check("the round starts from the code-written continuation", rounds[0][0] == ft.continuation(REQUEST, 1))

    count, judged, rounds, sent = _run(["continue"] * 9, [f"part {n}" for n in range(9)])
    _check(f"never more than {ft.MAX_ROUNDS} rounds", count == ft.MAX_ROUNDS and len(rounds) == ft.MAX_ROUNDS,
           str(count))
    _check("and no judge call after the last one", len(judged) == ft.MAX_ROUNDS)

    count, judged, rounds, sent = _run(["continue"], ["more"], headroom=ft.FLOOR - 1)
    _check("not with less than the floor left today", count == 0 and not judged and not rounds)
    count, *_ = _run(["continue", "done"], ["more"], headroom=None)
    _check("a model with no measured cap is not held back by it", count == 1)

    count, judged, rounds, sent = _run(["needs_user"], [])
    _check("a reply waiting on the user stops it", count == 0 and not rounds)

    count, judged, rounds, sent = _run(["continue"] * 3, ["first more", "second more"], stop_after=1)
    _check("a message from the user ends it after the round in flight",
           count == 1 and sent == ["first more"] and len(judged) == 1, f"{count} {sent}")

    count, judged, rounds, sent = _run(["continue"] * 3, ["unused"], failing_round=1)
    _check("a round that fails ends it quietly", count == 1 and not sent)

    count, judged, rounds, sent = _run(["continue", "done"], [""])
    _check("an empty reply is not sent", count == 1 and not sent)

    count, judged, rounds, sent = _run(["continue", "continue", "done"], ["a", "b"],
                                       carry={"web": True, "out_of_steps": True})
    _check("what the first turn read stays read in every round",
           all(c.get("web") is True for _, c in rounds), str([c for _, c in rounds]))
    _check("and each round has the stop", all("stop" in c for _, c in rounds))
    _check("but not the last turn's step count", all("out_of_steps" not in c for _, c in rounds))

    from agents.react import _UNTRUSTED_OPEN
    with_document = f"[Document the user sent: a.txt]\n{_UNTRUSTED_OPEN}\nA page.\n[END]"
    count, judged, rounds, sent = _run(["continue", "done"], ["a"], carry={"out_of_steps": True}, request=with_document)
    # Live 2026-10-30 the first round ran with every tool open and a transcript in its history.
    _check("a document in the user's message counts as read in every round",
           rounds and rounds[0][1].get("web") is True, str(rounds))

    async def too_big_round(message, round_carry):
        round_carry["too_big"] = True
        return "Nothing to report."
    judged_calls = []

    async def judge_continue(request, reply):
        judged_calls.append(reply)
        return "continue", "stub"
    real_judge, real_headroom = ft.judge, ft.usage.headroom
    sent2 = []

    async def send2(text):
        sent2.append(text)
    try:
        ft.judge, ft.usage.headroom = judge_continue, (lambda model: 150000)
        count = asyncio.run(ft.run(REQUEST, PARTIAL, {"out_of_steps": True}, asyncio.Event(), too_big_round, send2))
    finally:
        ft.judge, ft.usage.headroom = real_judge, real_headroom
    _check("a round too big to start ends it", count == 1 and len(judged_calls) == 1, f"{count} {judged_calls}")


def _fake_client(script):
    """A React client that answers from script: a list of tool calls or final strings."""
    calls = []

    async def create(model, **kwargs):
        calls.append(kwargs)
        step = script[min(len(calls), len(script)) - 1]
        if isinstance(step, tuple):
            name, args = step
            call = SimpleNamespace(id=f"c{len(calls)}", type="function",
                                   function=SimpleNamespace(name=name, arguments=json.dumps(args)))
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="tool_calls", message=SimpleNamespace(content=None, tool_calls=[call]))], usage=None)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content=step, tool_calls=None))], usage=None)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def _react(message, script, carry, max_iterations=8):
    from agents import react
    fake, calls = _fake_client(script)
    real_client, real_execute = react._get_client, react._execute_tool

    async def execute(name, inputs, allow_private=False):
        return f"{react._UNTRUSTED_OPEN}\nA listing page.\n{react._UNTRUSTED_CLOSE}" if name == "search" else "ok"
    try:
        react._get_client, react._execute_tool = (lambda: fake), execute
        reply = asyncio.run(react.handle(message, [], carry=carry, max_iterations=max_iterations))
    finally:
        react._get_client, react._execute_tool = real_client, real_execute
    return reply, calls


def _offered(call) -> set:
    return {t["function"]["name"] for t in call.get("tools") or []}


def test_a_long_message_keeps_its_tools() -> None:
    """Live 2026-10-30: the prompt plus a full window was about 6600 of the 7000 ceiling, and
    history could not be trimmed, so a 1800-character message was refused before its first
    step. React answered with no tools, opening "In this turn I did not run any searches"."""
    from agents import react

    system = {"role": "system", "content": "s" * 8500}
    history = [{"role": "system", "content": "Summary of the conversation. " * 40},
               {"role": "user", "content": "the oldest thing said " * 110},
               {"role": "assistant", "content": "an older answer " * 140},
               {"role": "user", "content": "short"},
               {"role": "assistant", "content": "the newest answer " * 100}]
    message = {"role": "user", "content": "d" * 1800}
    full = [system] + history + [message]
    budget = react._estimate_tokens(full, 2048)
    _check("the fixture is over the ceiling", budget > react._BUDGET_CEILING, str(budget))
    out, fits = react._trim_to_budget(full, 2048)
    _check("without the history's bounds nothing can give", not fits)
    out, fits = react._trim_to_budget(full, 2048, history_end=len(history) + 1)
    _check("the conversation gives way, and the request fits", fits, str(react._estimate_tokens(out, 2048)))
    _check("the oldest goes first and the newest stays whole",
           "[shortened for budget]" in out[2]["content"] and out[5]["content"] == history[4]["content"],
           str([len(m["content"]) for m in out]))
    _check("the running summary is kept while anything else can give", out[1] == history[0])
    _check("the system prompt and the user's message are untouched", out[0] == system and out[-1] == message)
    _check("the caller's own list is not changed", full[2] == history[1])

    results = [{"role": "tool", "content": "r" * 4000} for _ in range(2)]
    packed = [system] + history + [{"role": "user", "content": "go"}] + results
    out, fits = react._trim_to_budget(packed, 2048, history_end=len(history) + 1)
    _check("the last two tool results outrank every word of history", fits and out[-1] == results[-1] and out[-2] == results[-2],
           str(react._estimate_tokens(out, 2048)))
    _check("the summary goes when shortening is not enough", out[1]["content"] == react._TRIM_PLACEHOLDER, out[1]["content"][:40])
    block = {"role": "user", "content": f"{react._UNTRUSTED_OPEN}\n{'d' * 3000}\n{react._UNTRUSTED_CLOSE}"}
    out, _ = react._trim_to_budget([system, block, {"role": "assistant", "content": "a" * 6000}, message], 2048, history_end=3)
    _check("an untrusted block is never cut through its closing marker",
           out[1]["content"] == react._TRIM_PLACEHOLDER or react._UNTRUSTED_CLOSE in out[1]["content"], out[1]["content"][:80])

    context = history
    fake, calls = _fake_client(["Read it."])
    real_client, real_prompt = react._get_client, react._get_system_prompt
    carry = {}
    try:
        react._get_client, react._get_system_prompt = (lambda: fake), (lambda: "s" * 8500)
        reply = asyncio.run(react.handle("d" * 1800, context, max_tokens=2048, carry=carry))
    finally:
        react._get_client, react._get_system_prompt = real_client, real_prompt
    _check("the turn starts with its tools", calls and "read_file" in _offered(calls[0]), str(len(calls)))
    _check("and is not reported as too big or out of steps", not carry.get("too_big") and not carry.get("out_of_steps"), str(carry))

    fake, calls = _fake_client(["Something short."])
    carry = {}
    try:
        react._get_client, react._get_system_prompt = (lambda: fake), (lambda: "s" * 30000)
        reply = asyncio.run(react.handle("hello", [], max_tokens=2048, carry=carry))
    finally:
        react._get_client, react._get_system_prompt = real_client, real_prompt
    _check("a prompt nothing can shrink is reported as too big", carry.get("too_big") is True and "out_of_steps" not in carry, str(carry))
    note = calls[0]["messages"][-1]["content"]
    _check("and is not told to report on searches it never ran",
           "did not search" not in note and "Do not mention this note" in note, note)


def test_react_carries_the_turn() -> None:
    from agents import react

    carry = {}
    _react("Look up standing desk prices.", [("search", {"query": "standing desk"}), "Two found."], carry)
    _check("a turn that read the web says so", carry.get("web") is True, str(carry))
    _check("and that it did not run out of steps", "out_of_steps" not in carry)

    carry = {}
    reply, calls = _react("Look up standing desk prices.", [("search", {"query": "standing desk"})], carry,
                          max_iterations=2)
    _check("a turn that ran out of steps says so", carry.get("out_of_steps") is True, str(carry))

    reply, calls = _react("Carry on.", ["Done."], {"web": True})
    _check("a round after a web read starts without the tools that change things",
           not (_offered(calls[0]) & react._QUARANTINED_AFTER_WEB) and "search" in _offered(calls[0]),
           str(sorted(_offered(calls[0]) & react._QUARANTINED_AFTER_WEB)))

    reply, calls = _react("Carry on.", ["Done."], {"private": True})
    _check("a round after a private read starts without the web",
           not (_offered(calls[0]) & react._WEB_TOOLS), str(sorted(_offered(calls[0]) & react._WEB_TOOLS)))

    stop = asyncio.Event()
    stop.set()
    reply, calls = _react("Carry on.", ["Should not be asked."], {"stop": stop})
    _check("a round the user has overtaken makes no model call", not calls and reply == "", f"{len(calls)} {reply!r}")


def test_the_orchestrator() -> None:
    import orchestrator

    async def notify(_):
        pass

    o = orchestrator.Orchestrator(notify)

    async def classify(content):
        return "React"
    o._classify = classify
    routed = []

    async def route(destination, content, file_mode=False, carry=None):
        routed.append(carry)
        if carry is not None:
            carry["out_of_steps"] = True
        return PARTIAL
    o._route = route
    reply, _ = asyncio.run(o.process(config.AUTHORIZED_USER_ID, REQUEST))
    _check("the route gets a carry", routed and isinstance(routed[0], dict))
    _check("a React reply waits to be followed through", o._pending_follow is not None
           and o._pending_follow[0] == REQUEST and o._pending_follow[2].get("out_of_steps"))

    import followthrough
    real_run = followthrough.run
    seen = {}

    async def run(request, reply, carry, stop, do_round, send, typing=None):
        seen.update(request=request, stop=stop, live=o._stop_follow is stop)
        return 2
    try:
        followthrough.run = run
        count = asyncio.run(o.follow_through(notify))
        again = asyncio.run(o.follow_through(notify))
    finally:
        followthrough.run = real_run
    _check("follow_through runs it once", count == 2 and again == 0 and seen.get("request") == REQUEST)
    _check("with a stop a new message can reach while it runs", seen.get("live") is True)
    _check("and lets go of it afterwards", o._stop_follow is None)

    stop = asyncio.Event()
    o._stop_follow = stop
    asyncio.run(o.process(config.AUTHORIZED_USER_ID, "never mind that"))
    _check("a new message from the user stops a follow-through under way", stop.is_set())

    async def slow_route(destination, content, file_mode=False, carry=None):
        o._seq += 1   # another message arrived while this one was answered
        return PARTIAL
    o._route = slow_route
    asyncio.run(o.process(config.AUTHORIZED_USER_ID, REQUEST))
    _check("a reply overtaken by a newer message is not followed through", o._pending_follow is None)

    async def file_route(destination, content, file_mode=False, carry=None):
        return "# Report"
    o._route = file_route
    asyncio.run(o.process(config.AUTHORIZED_USER_ID, "file: a report on desks"))
    _check("a file request is not followed through", o._pending_follow is None)

    async def chat(content):
        return "Direct"
    o._classify, o._route = chat, route
    asyncio.run(o.process(config.AUTHORIZED_USER_ID, "how are you"))
    _check("nor is a reply from another agent", o._pending_follow is None)

    from context_store import load_timed
    o._store_round("The third: Brand C at 279, 3 years.")
    last = load_timed(5)[-1]
    _check("a round's reply is kept as conversation, not as a post",
           last["role"] == "assistant" and last["content"] == "The third: Brand C at 279, 3 years."
           and not orchestrator._is_post(last), str(last))


if __name__ == "__main__":
    print("what gets judged")
    test_what_gets_judged()
    print("\nverdicts")
    test_verdicts()
    print("\nthe continuation")
    test_the_continuation_is_written_in_code()
    print("\nthe loop")
    test_the_loop()
    print("\na long message keeps its tools")
    test_a_long_message_keeps_its_tools()
    print("\nReact carries the turn")
    test_react_carries_the_turn()
    print("\nthe orchestrator")
    test_the_orchestrator()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
