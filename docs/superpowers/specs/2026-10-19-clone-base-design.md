# Clone base and IGOR-C - design

Status: draft for the user's approval, 2026-10-19. GAMEPLAN Phase K.

## Intent

One engine, many agents. The user's IGOR stays private and unchanged. The first clone,
**IGOR-C**, is the customer-facing version: invited testers first, then a demo and a
template for client work (the freelance route chosen 2026-10-18). Later clones (the
a second tester's, a "different feel" one) reuse the same base.

## How Hermes does it (checked 2026-10-19)

- **Profiles:** each agent is its own home folder (config, keys, memory, skills, cron,
  SOUL.md); one shared install of the code. Cloning copies settings, personality and
  memory files, never sessions; bot tokens and cron jobs are left out by default.
- **SOUL.md:** one personality file per profile, first in the prompt, voice only.
- **Distributions:** an agent packaged as a git repo with no keys, memory or sessions.
- **Other people:** an allowlist or pairing codes the owner approves, a separate
  conversation per person, dangerous tools in a Docker sandbox. No per-user budgets.

Where IGOR differs, and why: a 1 GB server cannot spare Docker, so IGOR-C has no
dangerous tools at all rather than sandboxed ones; Groq's free limits are per account,
so IGOR-C needs per-tester allowances Hermes does not have; and agents run as separate
Linux users, which is stronger isolation than Hermes profiles, which share one user.

## Layout (approved by the user 2026-10-19)

```
/opt/igor/            engine, shared; IGOR's home stays here, nothing moves
  .env                IGOR's keys, chmod 600
  memory/             IGOR's memory
/srv/igor-c/          IGOR-C's home, owned by Linux user igor-c, mode 700
  .env                its Discord bot token, Groq key, tester ids
  memory/
    home.md           what this agent runs (below)
    prompt_*.md       its own instructions and personality
    context.db        one conversation per tester
igor.service          engine + /opt/igor
igor-c.service        engine + /srv/igor-c, User=igor-c, Environment=IGOR_HOME=/srv/igor-c
```

The repo gains `homes/template/`: a starter `home.md`, starter prompt files and an
`.env.example`, with no keys and no memory (the distribution idea).

## What a home decides

`config.IGOR_HOME` comes from the environment and defaults to the engine folder, so
IGOR needs no change. `MEMORY_DIR`, the log file and the `.env` load all come from the
home. `.env` is loaded from the home explicitly, never found by search, so a clone can
never pick up IGOR's keys.

`memory/home.md`, absent for IGOR, which means "everything as today":

- **Name:** used in the few code-built strings that say "I.G.O.R." (digest titles,
  compose fallbacks).
- **Features:** which scheduled jobs register (digest, job watch, gig watch, price,
  stock, overnight, presence, advocacy draft). IGOR-C v1: none, plus due scheduled
  messages.
- **Tools:** which React tools exist. IGOR-C v1: search, fetch_url,
  scheduled_message, search_history; never shell, write_file, patch_file, python_run,
  restart_self, send_message, goal, proposal or delegation.
- **Users:** owner only (IGOR), or owner plus testers (IGOR-C).

Personality and instructions use the mechanism IGOR already has: each agent prefers
`memory/prompt_<agent>.md` over its built-in prompt. IGOR-C's home ships its own Direct,
React, Monitor and Evaluator prompts. IGOR's built-in prompts are untouched.

## Several users (IGOR-C)

- Testers are numeric Discord ids in the home's `.env` (Hermes' allowlist). Pairing
  codes come later.
- `context_store` gains a user id column; each tester sees only their own history.
  IGOR keeps using one conversation.
- A daily token allowance per tester, counted in the existing usage ledger; at the
  limit the tester gets a plain "back tomorrow" reply and no model call is made.
- Testers never see the owner's data: IGOR-C's memory has none of it, and its Linux
  user cannot read `/opt/igor`.

## Deploys

The root-owned deploy script pulls once, runs its checks, restarts every agent unit,
and waits for each gateway; a failure in either rolls back both. Edited from
`C:\Dev\IGOR_backup\deploy-machinery\`, never moved into the repo.

## Build order

1. Homes: `IGOR_HOME`, home-scoped `.env`, memory and log; lock IGOR's `.env`. No
   behaviour change for IGOR; the full suite must pass unchanged.
2. `home.md`: features and tools per home, defaulting to today's set.
3. Several users: allowlist, per-user conversations, per-tester allowances.
4. IGOR-C: its home from the template, Linux user, unit, deploy script, Discord bot.

Steps 1 and 2 are safe to build now. Steps 3 and 4 wait on the decisions below.

## Decisions for the user

1. **Budget:** testers on the user's Groq account share IGOR's 200000-token daily
   budget, with a cap each; or IGOR-C goes on Groq's paid tier.
2. **What IGOR-C offers:** its prompts and tools follow from the offer, which the user
   is working out by reading real gigs.
3. **The spec:** IGOR_SPEC says IGOR is never public-facing, and K.4 asks for a spec
   section the user ratifies before an outward clone. A section for invite-only testing
   is drafted with step 3.
4. **Discord:** the user creates IGOR-C's bot in the Discord developer portal.

## Not in scope

One process serving every agent (Hermes' newer multiplexed gateway), packaging
clones as git repos, pairing codes, a web UI, public sign-up.
