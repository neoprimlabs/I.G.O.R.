# Overnight Work Loop - Design

Status: **approved by the user 2026-10-16** ("let's build the overnight loop").

## Intent

IGOR should do real work while the user is not driving it: research towards a
feature that could earn income, and finding ways to improve itself. The user wants
speed and a system that compounds - more of the find-problem / fix / test loop
running without them - in the spirit of Hermes' persistent goals, tailored to one
person. The user is not an expert in agent systems and wants to check the work in
one place and keep what is good.

A desktop dashboard is coming soon and will replace Discord as the main interface.
It is a separate project. This loop is its first data source, so its output must be
files and status a dashboard can read, not chat messages.

## Goals

- Standing goals IGOR works on overnight without being asked, picking up where the
  last session stopped. Two to start:
  1. **Research:** autonomous systems, aimed at finding and shaping one feature the
     user could build that generates income.
  2. **Self-improvement:** what IGOR got wrong recently, and a concrete proposed fix
     for each.
- Every result is a file in one work folder, with its sources and what it is unsure
  about at the top.
- The user hears about it once a day: an "Overnight" section in the morning digest,
  finished papers attached.
- A copy of the work folder lands on the user's PC with the existing daily backup.

## Non-goals (this build)

- The desktop dashboard. Coming soon, separate design.
- Building or launching the income feature. This loop researches and proposes; the
  user decides what gets built.
- IGOR applying changes to its own code or prompts. It proposes; applying stays with
  the user and Claude Code sessions.
- Recursive self-improvement (IGOR changing how it improves). The Darwin Gödel
  Machine shows where that goes without an untouchable measure: it learned to strip
  its developers' hallucination markers to raise its score.

## Anti-goals - failure even if it technically works

- **Spending the user's chat budget.** If the main model is short in the morning
  because of overnight work, the loop has failed. It never calls `gpt-oss-120b`.
- **Noise.** Messages overnight, or a digest section full of "worked on X" with
  nothing to show. The user's standing rule: an interruption must be worth it.
- **Unverifiable output.** A paper whose claims cannot be traced to a source, or a
  proposal that cannot be checked, is worse than nothing - IGOR's history includes
  confident fabrication (2026-08-13, 2026-10-14, 2026-10-16).
- **Any outward action.** No spending, sign-ups, posting, email or messages to anyone
  but the user. This is enforced by construction (below), not by prompt.
- **Unbounded growth** of files, findings or proposals.

## Constraints

- Groq free tier. `gpt-oss-120b` is 200000 tokens/day, shared by Direct and React -
  off limits. `gpt-oss-20b` is a separate bucket whose daily cap is **unmeasured**.
- Exa search terms forbid selling anything obtained through it. Papers are for the
  user's own decisions; their sources are cited, never resold as a dataset.
- 1 OCPU / 956 MB host. No new services, no new dependencies unless unavoidable.
- `IGOR_SPEC.md`: simple, not a multi-agent framework; resource efficient; auditable
  ("no black boxes"); graceful degradation.
- `ARCHITECTURE.md` is at its read-size limit; documenting this means condensing
  something else.

## Decided with the user

- Overnight work, two standing goals as above.
- One place to check the work and keep files.
- The overnight report goes **inside the morning digest**, not a separate message.
- Self-improvement produces proposals; nothing changes without the user's yes.
- The dashboard waits; this comes first.

## The design (builder's calls marked [call])

**Goals file** - `memory/goals.md`, human-readable like the watch files: name, kind
(`research` or `self_improve`), a brief, status (active / paused / done), and the
last session's note on where it stopped. React gets a `goal` tool (add, list, pause,
resume, retire) so the user can manage goals in plain language. [call: a file the
user can read, per the spec's auditability principle]

**Runner** - an APScheduler job, not a model loop: one work session per hour from
00:00 to 07:00 local, the goal least recently worked first. Sessions are short and
save their state, so a restart or a failure loses at most one session. [call: hourly,
not continuous - bounded by budget and in keeping with the quiet-hours design]

**No tools that act.** A session is a fixed pipeline in code, the shape the research
loop already proves: model calls with no tools, a web search done by code, file
writes done by code and only inside `memory/work/`. A session cannot spend, post or
message anyone because nothing it runs can. This is also the Dual-LLM pattern the
research loop already follows: the context that reads web content holds no tools.

**Research session** - reuses the research loop's PLAN / SEARCH / DISTILL steps for
a few searches, appending sourced findings to the goal's folder. When enough new
findings exist, a WRITE step drafts or revises the goal's paper, and the existing
Evaluator checks it before it counts as finished: every claim cites a source, and it
has a "What I am unsure about" section. [call: reuse, not a new pipeline]

**Self-improvement session** - reads new entries in `corrections.md` since the last
session plus recent errors IGOR logged, and writes proposals to
`memory/work/self-improvement/proposals.md`. Each proposal: what went wrong (quoted
evidence), the proposed change, its risk (a preference, a prompt change, or code),
and the test that would prove it. `corrections.md` holds text derived from untrusted
input; this is safe only because nothing in a proposal is applied without the user.
Today's review found reformulations are about one-third real signal, so nothing is
auto-approved. [call: proposals only, matching the decision above]

**Approving a preference** - the one kind of proposal IGOR can apply on a yes: a
standing preference ("no language-specific jobs") goes into `memory/preferences.md`,
which Direct, React and the job ranking read. Capped in size, each entry dated, so
it cannot grow without bound - the Hermes memory pattern. Prompt and code proposals
are handed to a Claude Code session. [call - this closes the gap found today, where
a preference saved to `user.md` never reached the job watch]

**Work folder** - `memory/work/<goal>/`: `findings.md`, dated papers, a `log.json` of
what each session did and spent. Inside `memory/` so the existing backup carries it
and git never does. Old findings are summarised into the paper and trimmed. [call]

**Morning digest** - a new `overnight` section built in code from the session logs,
no model call: what finished, what is in progress, proposals waiting, tokens spent.
Finished papers attached as files. Empty nights say nothing. [call]

**PC copy** - `scripts/backup_memory.ps1` also extracts `memory/work/` from the
archive it already pulls into `Documents\IGOR Work`. [call]

**Budget** - a per-night cap on `gpt-oss-20b`, default 60000 tokens, counted by the
token ledger under the label `Overnight`; the night stops at the cap or the first
429. The first 429 on 20b is also the measurement of its daily cap - recorded in
`config.DAILY_TOKEN_CAPS` and STATE.md. Searches capped per night too, since Exa
calls have their own cost. [call: 60000 until 20b's cap is measured]

**Smaller calls, settled by the builder check:**
- "Recent errors IGOR logged" means ERROR lines in IGOR's own log file
  (`config.LOG_FILE`), which IGOR can read; the systemd journal may not be readable
  by the `igor` user. [call]
- Papers attach to the digest through the file-sending path the research loop
  already uses (`notify_file`); the digest text itself stays code-built. [call]
- A paper is (re)written once a goal has at least 12 new sourced findings, or after
  three research sessions, whichever comes first. [call]
- At most 12 searches a night across all goals. [call]
- The two goals alternate; a paused goal is skipped; a night with no active goals
  does nothing and costs nothing. [call]
- Overnight files are separate from the user-started `deep research` loop's
  `research.md`, so the two never write to the same file. [call]

## Failure modes

| What happens | Behaviour |
|---|---|
| 429 on 20b | Session stops, night stops, cap recorded if daily |
| Empty or clipped model output | `llm.complete` already retries; a session that still gets nothing records "no progress" and moves on |
| Exa down | Research session skipped, self-improvement still runs |
| Restart mid-session | State is saved per step; the next session resumes |
| Evaluator fails a paper | Stays a draft, reason logged, next session revises |
| Disk or file errors | Logged; never raises into the scheduler |

## Testing

Offline, no API: goal file parsing; session state and resume; budget stop; the
digest section text; that a session's code path has no way to write outside
`memory/work/`. Then **one** live night, its spend read from the ledger, before
leaving it running.

## Answered

1. **The income feature and the spec.** Answered by the clone decision (GAMEPLAN
   Phase K): the user's IGOR stays private as the spec says, and anything
   public-facing is a clone with its own spec section. This loop only researches.
2. **Conversation history retention** is out of scope here; it stays open as a
   flagged deviation in STATE.md for the user to decide.
3. **Overnight budget:** 60000 tokens on 20b per night [call, taken as the default
   when the user said to build], adjustable in config.

## Clone-ready (GAMEPLAN Phase K)

Built so a clone can run it unchanged: goals come from `goals.md`, the voice papers
are written in comes from the agent's persona, nothing about the user is in code,
and the work folder lives in the agent's own `memory/`.

## Goals file format

Uses GAMEPLAN A.1's specified format rather than a new one: id, one-line statement,
status, created, last_reviewed, next_action; finished goals move to
`goals_archive.md`. A.1's prompt injection is not part of this build - the loop
reads the file directly.
