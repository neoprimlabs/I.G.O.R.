# Proactive presence - design

Written 2026-09-16. Status: specced, not built.

## What this is for

The user's words: "I just want it to feel more natural." Today IGOR sends a check-in
by replaying a string written days earlier ("Here's your scheduled check-in at 11:40
am"), and when asked for messages "at random times" it picked one time, once, and
called it random. The goal is an assistant that feels present: it reaches out on its
own, what it says fits the moment it arrives in, and it stays quiet when it has
nothing.

Four things the user asked for, all four confirmed:

1. Messages composed when they send, not when they are scheduled
2. IGOR chooses varied times rather than echoing a clock time back
3. IGOR decides to reach out at all, without being asked first
4. A register that sounds like a person, not a confirmation email

Cadence: a couple a day, varying, some days none. Silent 03:00-10:00 America/New_York.

## What the evidence says, and what it changed

**Proactive models over-trigger. This is the documented failure, measured.**
ProactiveBench (2603.19466) evaluated 22 multimodal LLMs: they largely succeed at
helping when help is wanted but "fail to stay silent when the user does not require
any assistance, resulting in a relatively high false alarm ratio". ProVoice-Bench
(2604.15037) reports the same gap independently, naming over-triggering as the
primary weakness. Two secondary findings carry design weight:

- **Proactiveness does not correlate with model capacity.** So this runs on
  gpt-oss-20b, not the 120b. Paying more buys nothing measurable here.
- **"Hinting" at proactiveness yields only marginal gains.** So the decision to
  speak cannot be a prompt instruction. It is gated in code, and the model is asked
  a narrower question.

**Random timing is measurably worse than breakpoint timing.** The intelligent
notification survey (1711.10171) reports interruptions at activity breakpoints are
judged more positively than those delivered at random, and a study of more than
680,000 users found that deferring delivery to a detected interruptible moment cut
response time by 49.7% with more notifications opened. The user asked for "random
times"; this builds trigger-driven timing with randomness inside the trigger, which
preserves the unscheduled feel without interrupting mid-thought. Flagged to the user
rather than silently substituted.

**Abstention needs a threshold in code, not in the model.** The abstention
literature finds models fail to move their own threshold as penalties rise, and that
a ternary reward implicitly pins the threshold at 50% regardless of the real
distribution. IGOR already learned this locally: crediting abstention fixed
fabrication in SelfDescribe and then over-shot into stonewalling, and only a scored
set caught it (STATE.md).

## Design

### The one function

`presence.consider(reason, now) -> str | None`

Everything funnels through it. It returns text to send, or None for silence. Silence
is the expected answer most of the time and is not a failure.

It runs in four steps, and only step 3 involves a model:

1. **Eligibility, in code.** Wrong answers here are free to prevent, so none of it is
   left to the model: inside 10:00-03:00 local; at least `_MIN_GAP` (default 4h)
   since the last proactive message; no more than `_MAX_PER_DAY` (default 2) sent
   today; no more than `_MAX_CALLS_PER_DAY` (default 6) decision calls today; not
   while a conversation is live (last message under 20 minutes ago). Fail any, return
   None without a model call.
2. **Bundle, in code.** A compact, factual block of what IGOR can actually see:
   local time and weekday, minutes since the last exchange, the last few turns, open
   tasks from `tasks.md`, unreviewed drafts with their ages from `drafts.md`, today's
   digest headline if one went out, what was said in the last proactive message and
   when. Every line is read from a file or the database. Nothing is inferred.
3. **One model call** on `config.MODELS["summary"]` (gpt-oss-20b). Given the bundle,
   it returns either `SILENT` or one to three sentences. The prompt states that
   silence is the right answer unless something in the bundle is worth raising, that
   it may only refer to facts present in the bundle, and that it must not invent
   activity, progress or events.
4. **Verification, in code.** If the reply is `SILENT`, return None. Otherwise strip
   the register violations that the style rules already forbid, and drop the message
   if it exceeds the length cap. Then record and send.

### Triggers

Each trigger calls `consider(reason)`. They are reasons, not schedules:

| Trigger | Fires when |
|---|---|
| `lull` | A conversation ended 25-90 minutes ago (randomised inside that band) and nothing has been sent since |
| `digest` | 90 minutes after the morning digest, if the user has not replied to it |
| `drafts` | A draft in `drafts.md` has been unreviewed for over 3 days, at most once a week |
| `stale_task` | An open task has not changed in over 7 days, at most once a week |

A 10-minute interval job evaluates which triggers are due. It is the same polled
pattern as scheduled messages, chosen for the same reason: APScheduler drops a date
job more than one second late.

### The other three asks

**Composed at delivery.** `scheduled.json` entries gain an optional `brief`. When an
entry has a brief and no fixed `content`, delivery composes the message through the
same prompt and bundle as `consider`, so a check-in scheduled on Monday can mention
Thursday. If composition fails or returns nothing, the brief is sent as plain text -
a check-in that arrives plainly beats one that vanishes.

**IGOR picks the time.** `scheduled_message` gains a `window` parameter
(`morning`, `afternoon`, `evening`, `tomorrow`). The code picks a uniform random
minute inside the window, clipped to the waking hours. The model never picks the
number, for the same reason it never does timezone arithmetic.

**Register.** One composing prompt, shared by presence and scheduled composition.
The tool's return string stops being `Scheduled 64c49d for Fri 2026-09-12 14:00 EDT`
and becomes something a person would say back, because React reads it aloud.

### Also fixed here

`monitor._parse_tasks` matches only `- [ ]` checkboxes, but `memory_write` writes
plain `- ` bullets, so every digest since has reported "Open Tasks: None" while
`tasks.md` held three. Presence reads the same file, so the parser is fixed to accept
both and to skip `- [x]`.

## Failure modes

| Failure | Handling |
|---|---|
| Over-triggering, the documented one | Code gates before any model call; scored test set with silence cases; caps mean the worst case is 2 messages a day |
| Inventing activity that never happened | Bundle-only grounding, no tools in the composer, and the composer is not React's model. A fabrication here enters `context.db` through `record_outbound` and becomes a permanent input, which is the August incident's exact mechanism |
| Interrupting mid-conversation | Eligibility requires 20 minutes of quiet; `lull` waits 25-90 |
| Token drift | `_MAX_CALLS_PER_DAY` caps spend; every call is logged with its reason and verdict so cost is measurable rather than estimated |
| Annoyance accumulating quietly | Every decision logged; the user turns it off in one message. `memory/presence_config.md` holds `state: on` or `state: off`, is read on every `consider` call rather than at startup, and defaults to on when the file is absent. Added to `memory_write`'s filename list so React can flip it, and deliberately NOT added to ConfigEdit's three files, because ConfigEdit is what overwrote the digest schedule (STATE.md, Known broken 7) |
| The digest schedule incident repeating | Presence writes no config files at all |

## Cost

Worst case 6 decision calls a day at roughly 700-1000 tokens each, so about 4-6k
tokens a day, on the gpt-oss-20b bucket shared with research, the evaluator and the
digest. Typical days will be 1-3 calls. No per-day cap is known for any current model
(STATE.md, Known broken 2), so the logging in step 4 is what turns this from an
estimate into a measurement.

## Testing

`tests/test_presence.py`, stdlib, no API: eligibility gates, quiet hours across the
DST boundary, cap arithmetic, bundle assembly from fixture files, trigger timing,
and the fallback when composition returns nothing.

`tests/eval_presence.py`, live and scored, following `tests/test_router_verdicts.py`:
fixed bundles with a hand-marked correct verdict, weighted toward cases where the
right answer is SILENT, since false alarms are the measured failure. Reports a false
alarm rate, not a pass mark. **An agent whose failure mode is plausible text cannot
be checked by reading one of its answers** - the standing lesson in STATE.md, which
was learned twice.

## Build order

Two commits, because the second half is mechanical and the first half is the part
that can be wrong about people.

**Commit 1 - the parts with no judgement in them.** `monitor._parse_tasks` accepting
plain bullets, the `window` parameter, composition at delivery from a `brief`, and
the shared composing prompt that fixes the register. All of it is testable without a
model deciding anything, and every piece is visible to the user immediately.

**Commit 2 - presence itself.** `consider`, the eligibility gates, the bundle, the
triggers, the off switch, the logging, and both test files. Ships behind
`state: off` by default, turned on for a day, then reviewed against the log of what
it decided and what it said before it stays on.

## Not in this build

- No learning from whether the user replies. That is V.1's job and needs a corpus.
- No goal-derived check-ins. That is A.1, and presence should consume `goals.md`
  when it exists rather than inventing a parallel store.
- No true random timing, unless the user asks for it after reading the evidence.
- No sentiment or mood inference. Nothing in the bundle supports it and inventing it
  is the fabrication failure wearing a friendlier face.
