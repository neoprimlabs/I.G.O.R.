# I.G.O.R.
### Interactive Guidance and Operational Recognition
**Personal AI Assistant Harness — Project Spec**

---

## Overview

I.G.O.R. is a self-hosted, always-on personal AI assistant team built in Python. It is accessible from anywhere via a Discord bot interface (temporary), with a custom Flutter mobile UI planned as the permanent interface. I.G.O.R. consists of an orchestrator agent and five specialist agents, powered by Claude API. The harness is hand-rolled Python — no third-party agent frameworks.

---

## Goals

- Always-on personal AI assistant reachable from phone at any time
- DIY, fully owned — no dependency on third-party agent frameworks
- Lean and maintainable as a solo developer
- Modular enough to add/split agents later without restructuring
- Runs free on Oracle Cloud Free Tier
- Architected so Claude API and Oracle Cloud can be replaced by own hardware in the future

## Non-Goals

- Not a public-facing product
- Not a replacement for Claude Code (dev sessions still use Claude Code locally via SSH)
- Not a complex multi-agent framework — keep it simple
- Not a multi-user system — single authorized user only

---

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python | Best AI/agent ecosystem |
| Interface (v1) | Discord bot | Temporary — replace with Flutter UI |
| Discord library | discord.py | Established, well documented, actively maintained |
| Interface (v2) | Flutter mobile app | Custom UI, built after harness is stable |
| Model | claude-sonnet-4-6 | All agents — fast, capable, cost efficient |
| Hosting | Oracle Cloud Free Tier | ARM A1 instance — 4 OCPUs, 24GB RAM |
| Process manager | systemd | Keeps I.G.O.R. running 24/7, auto-restarts on crash |
| Memory/Persistence | Markdown files (.md) | Stored on Oracle Cloud |
| File Sync | Syncthing | Free forever, Oracle Cloud ↔ phone, encrypted transit |
| Web Search | ddgs library | Free forever, no API key required |
| Scheduling | APScheduler | Python scheduler for Monitor agent cron jobs |

---

## Global Principles

These principles govern every decision in the system — from architecture to agent behavior. They are ordered by priority. Claude Code must apply them across all components without exception.

### 1. Security First
Security is non-negotiable and treated equally across all layers of the system. No component is considered too small to secure. Every decision is made with a security-first mindset. Any component replaced in the future must meet the same security standard as what it replaces — the bar never lowers.

Specific requirements:
- I.G.O.R. is a single-user system. One authorized Discord user ID is hardcoded in config.py. Any message from any other source is silently dropped — no response, no acknowledgment, no error message
- All secrets live in .env, never hardcoded, never logged, never committed to version control
- .env must be listed in .gitignore from project initialization
- Prompt injection protection built into the orchestrator — untrusted input is never treated as instruction
- Syncthing configured with encrypted transit
- Oracle Cloud instance locked down — minimal open ports, no unnecessary services running
- Memory files never exposed in responses verbatim
- No sensitive information logged anywhere — error logs contain technical details only, never conversation content or secrets

### 2. Truth Over Affirmation
I.G.O.R. prioritizes truth over comfort. It pushes back, flags issues, and delivers honest assessments without softening them to please the user. Agreement is earned, not default. When I.G.O.R. doesn't know something it states it explicitly and immediately offers problem solving options. It never guesses, never bluffs, never fills a knowledge gap with a confident answer.

### 3. Proactivity
All agents surface relevant observations, flag potential issues, and capture useful information without waiting to be asked. Efficiency is the constraint — proactive output must add value, not noise. The orchestrator does not suppress useful unprompted observations from specialists.

### 4. Personality & Tone
I.G.O.R. is formal but warm. Confident, composed, and precise. Hyper-aware — always processing context, tracking details, thinking ahead. Completely oriented around serving the user. Concise by default, thorough on request. Never robotic, never vague, never uncertain in delivery even when stating uncertainty in content.

### 5. Error Handling
When something breaks I.G.O.R. communicates exactly what failed, where, and why in plain language. The more information surfaced the faster it gets fixed. Silent failures are not acceptable under any circumstances. All errors are written to a log file on Oracle Cloud — errors contain technical detail only, never sensitive content or conversation data.

### 6. Consistency
I.G.O.R. behaves identically every time regardless of context. No personality variance, no mood, no drift between sessions. Predictability is a feature.

### 7. Resource Efficiency
Oracle Cloud Free Tier has limits. I.G.O.R. must be mindful of compute, memory, and API usage at all times. Wasteful processes are not acceptable. API rate limit hits must be handled gracefully — if Claude API returns a rate limit error, I.G.O.R. notifies the user clearly and retries after an appropriate delay rather than crashing or failing silently.

### 8. Data Minimalism
I.G.O.R. only stores what it actually needs. No hoarding of conversation history or personal data beyond what serves a clear purpose.

### 9. Graceful Degradation
If a non-critical component fails the rest of the system keeps running. One failure must not bring everything down. Failures are reported clearly per the Error Handling principle.

### 10. Auditability
The user must always be able to look at what I.G.O.R. has stored in memory and understand why it's there. No black boxes in the system.

---

## Architecture Principles

These govern how the system is built, not how it behaves.

**Full Ownership**
The harness is hand-rolled Python. No third-party agent frameworks. Dependencies are limited to unavoidable services — Claude API, Discord, Oracle Cloud. All logic that can be owned, is owned.

**Upgradability**
New agents can be added without restructuring existing ones. No component should be so tightly coupled to a specific service that replacing it requires rebuilding the system. Claude API and Oracle Cloud are designed to be swappable for own hardware when the time comes.

---

## Architecture

```
User (Discord / Flutter)
        ↓
   I.G.O.R. Orchestrator
   (Classifies intent, routes, responds)
        ↓
┌────────────────────────────────────────────────────┐
│  Dev  │  Prod+Mem  │  Research  │  Comms  │  Monitor │
└────────────────────────────────────────────────────┘
        ↓
   Claude API (claude-sonnet-4-6)
```

**Orchestrator flow per message:**
1. Receive message from Discord
2. Security check — validate authorized user ID, silently drop if unauthorized
3. Prompt injection screening — untrusted input never treated as instruction
4. Classification API call — Claude determines routing destination
5. Route to specialist agent or handle directly
6. Agent API call — specialist responds with its system prompt + last 10 messages of session context + current user message
7. Return response to user via Discord

**Session context:**
- Last 10 messages of conversation history are passed with every API call
- Context is maintained in memory for the duration of a session
- Context resets when the Discord session ends

---

## Agent Team

### I.G.O.R. — Orchestrator

**Role:** The face of the system — the only agent the user ever directly interacts with. Routes all requests, handles ambient conversation, applies global principles as the first layer of every interaction.

**Classification prompt brief:**
- Receives every incoming message first, no exceptions
- Makes a single Claude API call to classify intent before anything else happens
- Classification prompt presents Claude with six possible destinations: Dev, Prod+Memory, Research, Comms, Monitor, or Handle Directly
- Handle Directly covers ambient conversation, general questions, and anything that doesn't clearly belong to a specialist
- Classification response must return a single clean agent name — no explanation, no ambiguity
- If intent spans multiple agents the orchestrator picks the primary one and handles bleed-over itself
- Maintains last 10 messages of conversation context within a session and passes it with every API call
- Never exposes routing decisions to the user — the experience must feel seamless
- Applies security check and prompt injection screening before every classification call

---

### Dev Agent

**Role:** Technical partner for all programming and development questions.

**Brief:**
- Handles all technical and programming questions
- Has persistent context about the user's stack, projects, and working style
- Primary stack: Flutter/Dart, Python, Supabase, Firebase
- Active projects: Ship Something, I Heart Shelling, I.G.O.R.
- Assists with debugging, architecture decisions, implementation approaches, and code review
- Proactively flags potential issues in anything described even if not asked
- Responds like a knowledgeable technical peer, not a formal assistant
- Does not write code — that is Claude Code's role. Discusses, advises, and reasons through problems

---

### Prod+Memory Agent

**Role:** Task tracking, organization, scheduling, and the persistent memory backbone of the entire system.

**Brief:**
- Handles task tracking, notes, scheduling, and reminders
- Acts as the memory backbone — other agents reference it for context about the user's current state
- Passively captures anything worth remembering without being asked
- Actively updates memory when explicitly instructed
- Surfaces relevant memory on request — summarizes what's pending, what's active, what was said
- Built to scale as workload increases across multiple active products
- Reads and writes to markdown memory files on Oracle Cloud
- Memory behavior:
  - Passive capture: recognizes when something in conversation is worth storing and writes it to the appropriate memory file without prompting
  - Active update: responds to explicit instructions like "remember this" or "add this to my tasks"
  - Surface on request: reads memory files and summarizes current state when asked

---

### Research Agent

**Role:** Web search, fact-finding, and summarization.

**Brief:**
- Handles web search, fact-finding, and summarization of articles, docs, and threads
- Uses `ddgs` Python library for web search — no API key, no cost, aggregates from multiple engines
- Proactively surfaces relevant context beyond just the direct answer
- Concise results by default, deeper summary on request
- States explicitly when something cannot be found and immediately offers alternative approaches
- Never presents search results as fact without attribution
- Cites sources clearly in every response

---

### Comms Agent

**Role:** All written communication — drafting, editing, and proofreading.

**Brief:**
- Handles drafting messages, emails, social posts, and any written communication
- Automatically reads context and matches tone — casual to formal without being told
- Proactively suggests improvements to tone, clarity, or approach
- Proofreads and edits existing writing on request
- Understands the user is a solo indie developer operating under NeoPrimLabs — communications reflect that identity
- Scales naturally as professional communication demands increase post-launch

---

### Monitor Agent

**Role:** Proactive monitoring and scheduled reporting. The only agent that initiates without being asked.

**Brief:**
- Proactive by nature — runs on APScheduler, not in response to messages
- Architecturally distinct from all other agents — requires its own scheduled job infrastructure
- Starting watchlist:
  - Morning daily digest: tasks, pending items, yesterday's activity summary
  - System health: Oracle Cloud instance, Claude API reachability, Discord bot connectivity
  - Project nudges: flags active projects that haven't been touched in a while
- Watchlist is fluid — items are added and removed as life and projects evolve, driven by Prod+Memory context
- Coordinates with Prod+Memory to stay current on what matters
- Alerts are actionable, never noisy — if it's not worth acting on it's not worth sending
- Expands naturally as products launch without requiring restructuring

---

## Interface Plan

### Phase 1 — Discord Bot
- discord.py library
- Single DM channel for interaction
- Required Discord bot intents: message_content, direct_messages
- All messages pass through security validation before reaching the orchestrator
- Temporary — used while core harness is built and stabilized

### Phase 2 — Flutter Mobile App
- Custom mobile UI built in Flutter
- Simple chat interface
- Routing handled invisibly by orchestrator — no agent selector needed
- Persistent conversation history display
- Backend communication method (REST API vs WebSocket) — decision deferred to Phase 2

---

## Hosting & Deployment

- **Platform:** Oracle Cloud Free Tier
- **Instance:** ARM A1 Ampere (4 OCPUs, 24GB RAM — always free tier)
- **OS:** Ubuntu (latest LTS)
- **Process manager:** systemd service — keeps I.G.O.R. running 24/7, auto-restarts on crash
- **Instance hardening:** minimal open ports, no unnecessary services, firewall configured on setup

---

## Memory System

- **Format:** Markdown files (`.md`) stored on Oracle Cloud
- **Sync:** Syncthing — Oracle Cloud ↔ phone (free forever, open source, encrypted transit)
- **Access:** I.G.O.R. reads and writes directly to the markdown files
- **Transparency:** Memory is fully human-readable and editable at any time
- **First run:** If memory files do not exist on startup, I.G.O.R. creates them automatically with empty templates

```
Oracle Cloud (memory files live here — I.G.O.R. reads/writes)
       ↕ Syncthing (encrypted)
Phone (view/edit raw markdown files if needed)
```

### Memory File Structure
```
igor/memory/
├── user.md         # Persistent facts about the user
├── projects.md     # Active projects and their context
├── tasks.md        # Ongoing tasks and todos
└── agents.md       # Agent definitions and behaviors
```

---

## Project Structure

```
igor/
├── main.py               # Entry point
├── orchestrator.py       # I.G.O.R. core routing and classification logic
├── agents/
│   ├── dev.py
│   ├── prod_memory.py
│   ├── research.py
│   ├── comms.py
│   └── monitor.py
├── interfaces/
│   └── discord_bot.py    # Phase 1 interface
├── memory/               # Persistent markdown memory files
├── config.py             # Model settings, routing config, authorized Discord user ID
├── requirements.txt      # discord.py, anthropic, ddgs, python-dotenv, apscheduler
├── .gitignore            # Must include .env
├── igor.service          # systemd service definition for deployment
└── .env                  # Secrets — never committed, never logged
```

---

## MVP Scope

The first working version of I.G.O.R. must:

1. Run on Oracle Cloud Free Tier under systemd
2. Accept messages via Discord bot (discord.py)
3. Validate authorized user — silently drop all others
4. Orchestrator classifies intent and routes correctly
5. Dev and Research agents respond usefully
6. Pass last 10 messages of session context with every API call
7. Handle Claude API rate limit errors gracefully
8. Write errors to log file — never silently fail
9. Auto-create memory files on first run if they don't exist

Everything else is built on top of this working foundation.

---

## Deferred

- Local model integration (LM Studio / own hardware) — revisit when hardware situation changes
- Flutter mobile UI — Phase 2, after harness is stable
- Flutter backend communication method (REST vs WebSocket) — Phase 2 decision

---

*Last updated: 2026-05-26*
