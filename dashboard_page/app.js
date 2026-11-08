// Fills the page from /dashboard/status.json every 15 seconds, and runs the chat. Text only through
// textContent: what IGOR shows can hold words from a web page.
"use strict";

const REFRESH_MS = 15000;
const MODEL_COLOURS = ["#8be04e", "#6fb6ff", "#c8c2b6", "#b58cff"];
const $ = (id) => document.getElementById(id);

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

function fill(id, rows, empty, make) {
  const list = $(id);
  list.replaceChildren();
  if (!Array.isArray(rows)) { list.append(el("li", "Could not be read.", "bad")); return; }
  if (!rows.length) { if (empty) list.append(el("li", empty, "muted")); return; }
  for (const row of rows) list.append(make ? make(row) : el("li", row));
}

// A row's main line, then a smaller line under it.
function twoLine(main, meta) {
  const item = el("li");
  item.append(el("span", main, "main"), el("span", meta.filter(Boolean).join("  ·  "), "meta"));
  return item;
}

function number(n) {
  return Number(n || 0).toLocaleString();
}

// The usage ledger's labels, where the code's own word means nothing to the user.
const LABELS = {"React": "Tool work", "Isolated run": "Live tests", "Router": "Routing", "FollowThrough": "Follow-through",
                "SelfDescribe": "Questions about itself", "Compression": "Memory upkeep", "Compose": "Writing"};
const PLACES = {"discord.client": "Discord", "llm": "Model", "orchestrator": "Turn"};

function modelRole(roles) {
  if (roles.includes("chat")) return "Main model, for chat and tools";
  if (roles.includes("research")) return "Background model, for research and summaries";
  if (roles.includes("router")) return "Router, and reads images";
  return roles.join(", ");
}

function minutes(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 120) return seconds + " seconds";
  if (seconds < 7200) return Math.round(seconds / 60) + " minutes";
  if (seconds < 172800) return Math.round(seconds / 3600) + " hours";
  return Math.round(seconds / 86400) + " days";
}

function level(word) {
  return word === "ok" ? "ok" : word === "elevated" ? "warn" : "bad";
}

function stat(label, value, sub, cls) {
  const tile = el("div", null, "stat");
  tile.append(el("div", label, "label"), el("div", value, "value" + (cls ? " " + cls : "")));
  if (sub) tile.append(el("div", sub, "sub"));
  return tile;
}

// Sections. The tab is remembered on this browser; a bare #agents, #tasks and so on opens one directly.
const VIEWS = ["igor", "agents", "tasks", "memory", "system"];
const TAB_NAME = "igor-tab";
let view = "igor";

function show(name, focus) {
  if (!VIEWS.includes(name)) name = "igor";
  view = name;
  for (const v of VIEWS) {
    $("view-" + v).hidden = v !== name;
    const tab = $("tab-" + v);
    tab.setAttribute("aria-selected", String(v === name));
    tab.tabIndex = v === name ? 0 : -1;
  }
  try { localStorage.setItem(TAB_NAME, name); } catch (e) { /* this visit only */ }
  if (focus) $("tab-" + name).focus();
  if (name === "igor" && chat.stuck) $("chatlog").scrollTop = $("chatlog").scrollHeight;
  if (name === "tasks" || name === "memory") loadMemory();
}

for (const v of VIEWS) $("tab-" + v).addEventListener("click", () => show(v));
document.querySelector(".tabs").addEventListener("keydown", (event) => {
  const step = {ArrowRight: 1, ArrowLeft: -1, ArrowDown: 1, ArrowUp: -1}[event.key];
  if (!step) return;
  event.preventDefault();
  show(VIEWS[(VIEWS.indexOf(view) + step + VIEWS.length) % VIEWS.length], true);
});

// I.G.O.R.: the portrait, whether it is up, and a line of what matters now.
const state = {status: null, working: false};

function hero() {
  const data = state.status;
  const p = (data && data.process) || {};
  const up = p.discord === "connected";
  const summary = $("summary");
  if (!data) return;
  summary.classList.toggle("down", !up);
  $("portrait").classList.toggle("down", !up);
  $("portrait").classList.toggle("working", up && state.working);
  $("brand-mark").classList.toggle("down", !up);
  summary.textContent = !up ? "Not connected" : state.working ? "Working" : "Online";
  const models = (data.tokens || {}).models || {};
  const main = Object.values(models).find((m) => m.cap);
  const parts = [up ? "Up " + minutes(p.up_seconds) : "Discord is not connected"];
  if (main) parts.push(number(main.headroom) + " tokens free");
  if (data.proposals) parts.push(data.proposals + (data.proposals === 1 ? " proposal waits" : " proposals wait") + " for you");
  $("hero-line").textContent = parts.join("  ·  ");
}

// Agents: one card each, with its calls on each of the last seven days.
const STATES = {ready: "On call", scheduled: "Scheduled", stopped: "Stopped", off: "Off"};

function initials(name) {
  const words = name.replace(/[^A-Za-z ]/g, " ").split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : name.slice(0, 2)).toUpperCase();
}

function agentCard(c, days) {
  const today = c.calls[c.calls.length - 1] || 0;
  const card = el("article", null, "card agent" + (today ? " lively" : ""));
  const head = el("div", null, "agent-head");
  const name = el("div", null, "agent-name");
  name.append(el("h3", c.name), el("span", c.model, "model"));
  head.append(el("div", initials(c.name), "mono"), name, el("span", STATES[c.state] || c.state, "state " + c.state));
  card.append(head, el("p", c.role, "role"));
  const most = Math.max(1, ...c.calls);
  const spark = el("div", null, "spark");
  spark.setAttribute("role", "img");
  spark.setAttribute("aria-label", c.name + ", calls by day: " + c.calls.map((n, i) => days[i] + " " + n).join(", "));
  c.calls.forEach((n, i) => {
    const bar = el("span", null, n ? "" : "none");
    bar.style.height = (n ? Math.max(6, Math.round((n / most) * 100)) : 4) + "%";
    bar.title = days[i] + ": " + n + (n === 1 ? " call" : " calls");
    spark.append(bar);
  });
  const labels = el("div", null, "days");
  days.forEach((d, i) => labels.append(el("span", i === days.length - 1 ? "Today" : d)));
  const facts = el("div", null, "agent-facts");
  const calls = el("span");
  calls.append(el("b", number(c.week_calls)), c.week_calls === 1 ? " call this week" : " calls this week");
  const tokens = el("span");
  tokens.append(el("b", number(c.week_tokens)), " tokens");
  facts.append(calls, tokens);
  const when = [c.last ? "Last ran " + c.last : "Not run lately"];
  if (c.state !== "ready") when.push(c.upcoming);
  card.append(spark, labels, facts, el("p", when.join("  ·  "), "when"));
  return card;
}

function agents(data) {
  const box = $("agents");
  const a = data.agents;
  box.replaceChildren();
  $("agent-stats").replaceChildren();
  if (!a || !Array.isArray(a.cards)) { box.append(el("p", "The agents could not be read.", "bad")); return; }
  for (const c of a.cards) box.append(agentCard(c, a.days));
  const calls = a.cards.reduce((sum, c) => sum + c.week_calls, 0);
  const tokens = a.cards.reduce((sum, c) => sum + c.week_tokens, 0);
  const today = a.cards.filter((c) => c.calls[c.calls.length - 1] > 0).length;
  $("agent-stats").append(stat("Agents", a.cards.length, "parts with one job each"), stat("Calls this week", number(calls), "to a model"),
                          stat("Tokens this week", number(tokens), "all models"), stat("Active today", today, "of " + a.cards.length));
  const ranked = a.cards.filter((c) => c.week_calls > 0).sort((x, y) => y.week_calls - x.week_calls);
  const most = Math.max(1, ...ranked.map((c) => c.week_calls));
  fill("busiest", ranked, "No calls counted this week.", (c) => rankRow(c.name, c.week_calls, most, number(c.week_tokens) + " tokens"));
}

function rankRow(name, n, most, small) {
  const item = el("li");
  const who = el("span", name, "who");
  if (small) who.append(el("small", small));
  const bar = el("span", null, "bar");
  const fillBar = el("i");
  fillBar.style.width = Math.max(1, Math.round((n / most) * 100)) + "%";
  bar.append(fillBar);
  item.title = name + ": " + number(n);
  item.append(who, bar, el("span", number(n), "n"));
  return item;
}

// System: one word for the whole, then the parts.
function gauge(label, percent, value, sub, cls) {
  const tile = el("div", null, "gauge");
  const ring = el("div", null, "ring" + (cls && cls !== "ok" ? " " + cls : ""));
  ring.style.setProperty("--p", Math.max(0, Math.min(100, Math.round(percent))));
  ring.append(el("b", Math.round(percent) + "%"));
  const words = el("div");
  words.append(el("div", label, "label"), el("div", value, "value"), el("div", sub, "sub"));
  tile.append(ring, words);
  return tile;
}

function gauges(data) {
  const box = $("gauges");
  box.replaceChildren();
  const r = data.resources || {}, models = (data.tokens || {}).models || {};
  if (r.memory_total_mb) {
    const swap = r.swap_used_mb ? ", " + r.swap_used_mb + " MB in swap" : "";
    box.append(gauge("Memory free", 100 * r.memory_available_mb / r.memory_total_mb, r.memory_available_mb + " MB",
                     "of " + r.memory_total_mb + " MB" + swap, level(r.memory)));
  }
  if (r.disk_total_mb) {
    box.append(gauge("Disk free", 100 * r.disk_free_mb / r.disk_total_mb, (r.disk_free_mb / 1024).toFixed(1) + " GB",
                     "of " + (r.disk_total_mb / 1024).toFixed(0) + " GB", level(r.disk)));
  }
  const main = Object.values(models).find((m) => m.cap);
  if (main) {
    const share = 100 * main.headroom / main.cap;
    box.append(gauge("Model budget", share, number(main.headroom), "tokens free now", share < 20 ? "bad" : share < 40 ? "warn" : "ok"));
  }
}

const PART_NAMES = {commit: "the last update", process: "the process", resources: "memory and disk", tokens: "tokens",
                    scheduled: "scheduled messages", jobs: "the scheduler", board: "the board", plugins: "plugins",
                    background: "background commands", proposals: "proposals", engines: "the engines", errors: "the log", agents: "the agents"};

function systemStatus(data) {
  const p = data.process || {}, r = data.resources || {}, e = data.engines || {};
  const why = [];
  if (p.discord !== "connected") why.push(["bad", "Discord is not connected."]);
  if (r.memory && r.memory !== "ok") why.push([level(r.memory), r.memory === "critical" ? "Almost out of memory: it may restart." : "Memory is low."]);
  if (r.disk && r.disk !== "ok") why.push([level(r.disk), r.disk === "critical" ? "The disk is almost full." : "Disk space is low."]);
  if (Array.isArray(data.jobs) && !data.jobs.length) why.push(["bad", "The scheduler is not running."]);
  if (e.sandbox === false) why.push(["bad", "The code sandbox is missing."]);
  const main = Object.values((data.tokens || {}).models || {}).find((m) => m.cap);
  if (main && main.headroom < main.cap * 0.2) why.push(["warn", "Under a fifth of the day's model budget is free."]);
  for (const [part, value] of Object.entries(data)) {
    if (value && value.unreadable) why.push(["warn", "Could not read " + (PART_NAMES[part] || part) + "."]);
  }
  $("status-card").classList.toggle("attention", why.length > 0);
  $("status-word").textContent = why.length ? "Attention" : "Optimal";
  const rows = why.length ? why : [["ok", "Every part is running."]];
  fill("status-why", rows.concat([["muted", "Running for " + minutes(p.up_seconds) + "."]]), "", ([cls, text]) => el("li", text, cls));
  $("badge-system").hidden = !why.length;
  const banner = $("banner");
  const worst = why.find(([cls, text]) => cls === "bad" && /memory|disk/i.test(text));
  banner.hidden = !worst;
  banner.textContent = worst ? worst[1] : "";
}

function health(data) {
  const dl = $("health");
  dl.replaceChildren();
  const add = (name, value, cls) => { dl.append(el("dt", name)); dl.append(el("dd", value, cls)); };
  const p = data.process || {}, c = data.commit || {}, e = data.engines || {};
  add("Discord", p.discord || "unknown", p.discord === "connected" ? "ok" : "bad");
  const running = Array.isArray(data.jobs) && data.jobs.length > 0;
  add("Scheduler", running ? "running, " + data.jobs.length + " jobs" : "not running", running ? "ok" : "bad");
  add("Running for", minutes(p.up_seconds));
  add("Last update", c.subject ? c.subject + (c.when_text ? ", " + c.when_text : "") : "unknown");
  add("Code sandbox", e.sandbox ? "ready" : "missing", e.sandbox ? "ok" : "bad");
  add("Video", e.ffmpeg ? "ready" : "waiting for ffmpeg", e.ffmpeg ? "ok" : "muted");
  add("Web browser", e.lightpanda ? e.browser || "ready" : "waiting for Lightpanda", e.lightpanda ? "ok" : "muted");
}

function tokens(data) {
  const box = $("tokens");
  box.replaceChildren();
  const t = data.tokens || {};
  const models = Object.entries(t.models || {}).sort((a, b) => b[1].total - a[1].total);
  if (!models.length) { box.append(el("p", "Nothing spent in the last 24 hours.", "muted")); return; }
  // Colour follows the model, by name, so a quiet hour never repaints the others.
  const order = Object.keys(t.models || {}).sort();
  models.forEach(([name, m]) => {
    const card = el("div", null, "model");
    card.style.setProperty("--m", MODEL_COLOURS[order.indexOf(name) % MODEL_COLOURS.length]);
    const head = el("div", null, "model-head");
    const title = el("h3");
    title.append(el("i", name.includes("/") ? name.split("/")[1] : name));
    head.append(title, el("span", modelRole(m.roles || []), "role"));
    card.append(head);
    // The daily cap is a bucket that refills by the second, so spent and free do not add up to the cap.
    const use = el("p", null, "use");
    use.append(el("b", number(m.total)), " used in 24 hours. ");
    if (m.cap) {
      use.append(el("b", number(m.headroom), m.headroom < m.cap * 0.2 ? "warn" : ""),
                 " of " + number(m.cap) + " free now, refilling about " + number(Math.round(m.cap / 24 / 100) * 100) + " an hour.");
    } else {
      use.append("Its daily limit is not measured.");
    }
    card.append(use);
    const bars = el("div", null, "bars");
    const most = Math.max(1, ...m.hourly);
    m.hourly.forEach((n, i) => {
      const bar = el("span");
      bar.style.height = Math.max(2, Math.round((n / most) * 100)) + "%";
      bar.title = (t.hours[i] || "") + ", " + number(n) + " tokens";
      bars.append(bar);
    });
    card.append(bars);
    const hours = el("div", null, "hours");
    hours.append(el("span", t.hours[0] || ""), el("span", t.hours[12] || ""), el("span", "now"));
    card.append(hours);
    const chips = el("div", null, "chips");
    for (const [label, spent] of Object.entries(m.by_label).slice(0, 6)) {
      const chip = el("span", (LABELS[label] || label) + " ", "chip");
      chip.append(el("b", number(spent)));
      chips.append(chip);
    }
    card.append(chips);
    box.append(card);
  });
}

function render(data) {
  state.status = data;
  hero();
  agents(data);
  systemStatus(data);
  gauges(data);
  health(data);
  tokens(data);
  fill("scheduled", data.scheduled, "Nothing scheduled.", (s) => twoLine(s.what, [s.when, s.repeat]));
  fill("board", data.board, "Nothing on the board.");
  const jobs = Array.isArray(data.jobs) ? data.jobs : null;
  fill("jobs", jobs && jobs.filter((j) => !j.frequent), "The scheduler is not running.", (j) => twoLine(j.name, [j.when, j.every]));
  const often = jobs ? jobs.filter((j) => j.frequent).map((j) => j.name) : [];
  $("often").hidden = !often.length;
  $("often").textContent = "Also running every few minutes: " + often.join(", ") + ".";
  fill("plugins", data.plugins, "No plugins installed.");
  fill("background", data.background, "");
  const place = (where) => PLACES[where] || (where ? where[0].toUpperCase() + where.slice(1) : "");
  fill("errors", data.errors, "No errors logged.", (e) => twoLine(e.what, [e.when, place(e.where), e.count > 1 ? e.count + " times" : ""]));
  const waiting = typeof data.proposals === "number" ? data.proposals : 0;
  $("badge-memory").hidden = !waiting;
  $("badge-memory").textContent = waiting;
  $("footer").textContent = "Synced " + new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
}

// Memory and the night's work: words from memory, so they need the chat's key.
const KINDS = {research: "Research", self_improve: "Self-improvement"};
const PROOF_ORDER = ["independent", "mixed", "similar product", "self-reported", "no income shown", "against", "unchecked"];
let memoryBusy = false;

function memoryLocked(text) {
  for (const id of ["memory-locked"]) { $(id).hidden = !text; $(id).textContent = text || ""; }
}

async function loadMemory() {
  if (!chat.key) {
    memoryLocked("Memory opens from the IGOR Dashboard shortcut on your PC.");
    $("overnight").replaceChildren(el("p", "The night's work opens from the IGOR Dashboard shortcut on your PC.", "muted"));
    return;
  }
  if (memoryBusy) return;
  memoryBusy = true;
  try {
    const response = await fetch("/dashboard/memory.json", {cache: "no-store", headers: {"X-IGOR-Key": chat.key}});
    if (response.status === 401) { lockChat("The chat key has changed. Open the dashboard from the IGOR Dashboard shortcut again."); return; }
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    memoryLocked("");
    memory(data);
    overnight(data.overnight);
  } catch (e) {
    memoryLocked("Memory could not be read (" + e.message + "). Is the tunnel open?");
  } finally {
    memoryBusy = false;
  }
}

function memory(data) {
  const notes = data.notes || {}, skills = data.skills, proposals = data.proposals;
  const waiting = Array.isArray(proposals) ? proposals.length : 0;
  $("memory-stats").replaceChildren(
    stat("Notes kept", notes.unreadable ? "?" : number(notes.total), "entries across the note files"),
    stat("Note files", Array.isArray(notes.files) ? notes.files.length : "?", "user.md, tasks.md and the rest"),
    stat("Skills learned", Array.isArray(skills) ? skills.length : "?", "procedures you approved"),
    stat("Waiting for you", waiting, waiting ? "proposals to decide" : "nothing to decide", waiting ? "warn" : ""));
  fill("proposals", proposals, "Nothing is waiting for you.", (p) => {
    const item = el("li");
    const main = el("span", null, "main");
    main.append(el("span", p.id + "  " + p.kind, "tag"), p.problem);
    item.append(main, el("span", ["Change: " + p.change, p.date].filter(Boolean).join("  ·  "), "meta"));
    return item;
  });
  if (waiting) $("proposals").append(el("li", "Say approve " + proposals[0].id + " or reject " + proposals[0].id + " in Chat.", "muted"));
  fill("recent", notes.recent, "No dated notes yet.", (n) => twoLine(n.text, [n.date, n.file]));
  const files = Array.isArray(notes.files) ? notes.files : null;
  const most = files ? Math.max(1, ...files.map((f) => f.entries)) : 1;
  fill("files", files, "No note files.", (f) => rankRow(f.name, f.entries, most, "changed " + f.updated));
  fill("skills", skills, "None learned yet.");
}

function overnight(o) {
  const box = $("overnight");
  box.replaceChildren();
  $("night-when").textContent = o && o.night ? "Night of " + o.night : "";
  if (!o || o.unreadable) { box.append(el("p", "The night's work could not be read.", "bad")); return; }
  if (!o.steps.length) { box.append(el("p", "No overnight work recorded yet.", "muted")); return; }
  const sum = el("div", null, "night-sum");
  sum.append(stat("Steps", o.steps.length, "that night"));
  if (o.budget) {
    const meter = el("div", null, "meter");
    const track = el("div", null, "track");
    const used = el("i");
    used.style.width = Math.min(100, Math.round((100 * o.tokens) / o.budget)) + "%";
    track.append(used);
    track.setAttribute("role", "img");
    track.setAttribute("aria-label", number(o.tokens) + " of " + number(o.budget) + " tokens");
    meter.append(track, el("p", number(o.tokens) + " of the night's " + number(o.budget) + " tokens"));
    sum.append(meter);
  }
  box.append(sum);
  const steps = el("ul");
  for (const s of o.steps) {
    const item = el("li");
    const main = el("span", null, "main");
    main.append(el("span", KINDS[s.kind] || s.kind.replace(/_/g, " "), "tag"), s.summary);
    item.append(main, el("span", [s.at, s.title || s.goal, number(s.tokens) + " tokens"].filter(Boolean).join("  ·  "), "meta"));
    steps.append(item);
  }
  box.append(steps);
  for (const ledger of o.ledgers || []) {
    const part = el("div", null, "ledger");
    part.append(el("h3", ledger.ideas + " ideas checked for income" + (ledger.title ? ": " + ledger.title : "")));
    const list = el("ul", null, "rank");
    const counts = Object.entries(ledger.labels).sort((a, b) => PROOF_ORDER.indexOf(a[0]) - PROOF_ORDER.indexOf(b[0]));
    const most = Math.max(1, ...counts.map(([, n]) => n));
    for (const [name, n] of counts) list.append(rankRow(name[0].toUpperCase() + name.slice(1), n, most));
    part.append(list);
    box.append(part);
  }
}

function tick() {
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
  $("date").textContent = now.toLocaleDateString([], {weekday: "long", month: "short", day: "numeric"});
}

async function refresh() {
  try {
    const response = await fetch("/dashboard/status.json", {cache: "no-store"});
    if (!response.ok) throw new Error("HTTP " + response.status);
    render(await response.json());
  } catch (e) {
    const summary = $("summary");
    summary.classList.add("down");
    summary.textContent = "Link lost";
    $("hero-line").textContent = "(" + e.message + ") Is the tunnel open?";
    $("portrait").classList.add("down");
    $("brand-mark").classList.add("down");
  }
  if (view === "tasks" || view === "memory") loadMemory();
}

// Chat: the conversation as the store holds it, Discord's turns included, plus notes for replies it does
// not keep. The key arrives from the launcher after the #, which the browser never sends to a server.
const KEY_NAME = "igor-chat-key";
const chat = {key: "", after: -1, notes: 0, boot: "", pending: new Map(), timer: 0, since: 0, clock: 0, stuck: true};

try { chat.key = localStorage.getItem(KEY_NAME) || ""; } catch (e) { /* storage refused: the # still works */ }
const handed = /^#key=([A-Za-z0-9_-]{32,})$/.exec(location.hash);
if (handed) {
  chat.key = handed[1];
  try { localStorage.setItem(KEY_NAME, chat.key); } catch (e) { /* kept for this visit only */ }
  history.replaceState(null, "", location.pathname + location.search);
}

function prose(text) {
  const body = el("div", null, "msg-body");
  for (const line of String(text).split("\n")) {
    const heading = /^#{1,4}\s+/.exec(line);
    const p = el("p", null, heading ? "h" : "");
    for (const part of (heading ? line.slice(heading[0].length) : line).split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/)) {
      if (!part) continue;
      if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) p.append(el("strong", part.slice(2, -2)));
      else if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) p.append(el("code", part.slice(1, -1)));
      else p.append(document.createTextNode(part));
    }
    body.append(p);
  }
  return body;
}

function message(role, text, at, extra) {
  const item = el("li", null, "msg " + role + (extra ? " " + extra : ""));
  const head = el("div", null, "msg-head");
  head.append(el("b", role === "you" ? "You" : "I.G.O.R."), el("span", at));
  if (extra === "unprompted") head.append(el("span", "unprompted", "tag"));
  if (role === "igor") {
    const listen = el("button", "Listen", "listen");
    listen.type = "button";
    listen.addEventListener("click", () => (voice.button === listen ? stopSpeaking() : speak(text, listen)));
    head.append(listen);
  }
  item.append(head, prose(text));
  return item;
}

// Whether the newest message is in view. While the chat's section is hidden, it keeps what it was.
function atEnd(log) {
  return log.offsetParent === null ? chat.stuck : log.scrollHeight - log.scrollTop - log.clientHeight < 80;
}

function append(node, force) {
  const log = $("chatlog");
  const atBottom = atEnd(log);
  log.querySelector(".empty")?.remove();
  log.append(node);
  if (atBottom || force) log.scrollTop = log.scrollHeight;
}

function chatError(text) {
  const line = $("chat-error");
  line.hidden = !text;
  line.textContent = text || "";
}

function lockChat(why) {
  chat.key = "";
  try { localStorage.removeItem(KEY_NAME); } catch (e) { /* nothing kept */ }
  $("say").disabled = true;
  $("send").disabled = true;
  $("talk").disabled = true;
  $("say").placeholder = why;
  if (!$("chatlog").children.length) $("chatlog").append(el("li", why, "muted empty"));
}

function setWorking(on) {
  const box = $("working");
  $("stop").hidden = !on;
  if (on && box.hidden) {
    chat.since = Date.now();
    clearInterval(chat.clock);
    const show = () => {
      const s = Math.floor((Date.now() - chat.since) / 1000);
      $("working-text").textContent = "I.G.O.R. is working  " + Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    };
    show();
    chat.clock = setInterval(show, 1000);
  }
  if (!on) clearInterval(chat.clock);
  box.hidden = !on;
  if (state.working !== on) { state.working = on; hero(); }
}

async function chatPoll() {
  clearTimeout(chat.timer);
  if (!chat.key) return;
  let next = 8000;
  try {
    const query = "?after=" + chat.after + "&notes=" + chat.notes + "&boot=" + encodeURIComponent(chat.boot);
    const response = await fetch("/dashboard/chat.json" + query, {cache: "no-store", headers: {"X-IGOR-Key": chat.key}});
    if (response.status === 401) { lockChat("The chat key has changed. Open the dashboard from the IGOR Dashboard shortcut again."); return; }
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    const first = chat.after < 0;
    // The user's own words coming back mean the oldest message still marked as sending has landed.
    const landed = (role) => {
      const [turn, node] = chat.pending.entries().next().value || [];
      if (role === "you" && node) { node.remove(); chat.pending.delete(turn); }
    };
    // Read aloud: replies that arrive while the page is open, not the history it opened on, and not
    // what IGOR sent unprompted, which waits for its Listen button.
    const aloud = (role, text, unprompted) => { if (voice.aloud && !first && role === "igor" && !unprompted) speak(text); };
    for (const row of data.rows) {
      landed(row.role);
      append(message(row.role, row.text, row.at, row.unprompted ? "unprompted" : ""), first);
      aloud(row.role, row.text, row.unprompted);
    }
    for (const note of data.notes) { landed(note.role); append(message(note.role, note.text, note.at, "")); aloud(note.role, note.text, false); }
    if (first && !data.rows.length) $("chatlog").append(el("li", "No messages yet.", "muted empty"));
    // The fonts arrive after the first rows and make them taller, which leaves the newest out of view.
    if (first && document.fonts) document.fonts.ready.then(() => { $("chatlog").scrollTop = $("chatlog").scrollHeight; });
    chat.after = data.last;
    chat.notes = data.notes_last;
    chat.boot = data.boot;
    for (const [turn, node] of chat.pending) {
      if (!data.waiting.includes(turn)) { node.remove(); chat.pending.delete(turn); }
    }
    setWorking(data.working || chat.pending.size > 0);
    if (data.working || chat.pending.size) next = 2000;
  } catch (e) {
    next = 15000;
  }
  chat.timer = setTimeout(chatPoll, next);
}

async function say(text) {
  chatError("");
  let response, data = {};
  try {
    response = await fetch("/dashboard/chat", {method: "POST", cache: "no-store",
      headers: {"Content-Type": "application/json", "X-IGOR-Key": chat.key}, body: JSON.stringify({text})});
    data = await response.json().catch(() => ({}));
  } catch (e) {
    chatError("Not sent: the link is down. Is the tunnel open?");
    return false;
  }
  if (!response.ok) {
    chatError(data.error || "Not sent (HTTP " + response.status + ").");
    if (response.status === 401) lockChat(data.error || "Chat is locked.");
    return false;
  }
  sending(data.turn, text);
  return true;
}

function sending(turn, text) {
  const node = message("you", text, "sending", "pending");
  chat.pending.set(turn, node);
  append(node, true);
  setWorking(true);
  chatPoll();
}

// Voice. Talk records, and the server's Whisper turns it into the message. Replies are read in I.G.O.R.'s
// voice (Groq, through the server) or, when that cannot speak, the browser's own. The server decides what
// is said: no link addresses, no code, no markdown marks.
const VOICE_PREFS = "igor-voice";
const IGOR_VOICES = ["daniel", "troy", "austin", "autumn", "diana", "hannah"];
const DEFAULT_VOICE = "igor:daniel";
const MAX_RECORD_MS = 115000;   // At 32 kbps, well inside the server's 1 MB
const MIN_RECORD_MS = 700;
const TEST_LINE = "Good evening. This is how I.G.O.R. sounds in this voice.";
const voice = {aloud: false, choice: DEFAULT_VOICE, rec: null, stream: null, chunks: [], started: 0, clock: 0,
               run: 0, queue: [], busy: false, audio: null, button: null, noted: ""};

try {
  const saved = JSON.parse(localStorage.getItem(VOICE_PREFS) || "{}");
  voice.aloud = saved.aloud === true;
  if (typeof saved.choice === "string") voice.choice = saved.choice;
} catch (e) { /* defaults */ }

function saveVoice() {
  try { localStorage.setItem(VOICE_PREFS, JSON.stringify({aloud: voice.aloud, choice: voice.choice})); } catch (e) { /* this visit only */ }
}

function voiceNote(text) {
  if (text === voice.noted) return;
  voice.noted = text;
  $("voice-note").hidden = !text;
  $("voice-note").textContent = text;
}

function browserVoices() {
  return "speechSynthesis" in window ? speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith("en")) : [];
}

function fillVoices() {
  const select = $("voice");
  select.replaceChildren();
  const mine = el("optgroup");
  mine.label = "I.G.O.R.'s voice";
  for (const name of IGOR_VOICES) {
    const option = el("option", name[0].toUpperCase() + name.slice(1));
    option.value = "igor:" + name;
    mine.append(option);
  }
  select.append(mine);
  const local = browserVoices();
  if (local.length) {
    const group = el("optgroup");
    group.label = "This browser's voices";
    for (const v of local) {
      const option = el("option", v.name);
      option.value = "browser:" + v.name;
      group.append(option);
    }
    select.append(group);
  }
  // A browser voice may not be listed yet: the choice stays saved until the list arrives.
  select.value = voice.choice;
  if (select.value !== voice.choice) select.value = DEFAULT_VOICE;
}

function showAloud() {
  $("aloud").setAttribute("aria-pressed", String(voice.aloud));
  $("aloud").textContent = "Read replies aloud: " + (voice.aloud ? "on" : "off");
}

function marking(button) {
  if (voice.button) { voice.button.textContent = "Listen"; voice.button.classList.remove("on"); }
  voice.button = button || null;
  if (voice.button) { voice.button.textContent = "Stop"; voice.button.classList.add("on"); }
}

function stopSpeaking() {
  voice.run++;
  voice.queue = [];
  voice.busy = false;
  if (voice.audio) { voice.audio.pause(); voice.audio = null; }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  marking(null);
}

// A Listen or a Test interrupts; a reply arriving waits its turn.
function speak(text, button) {
  if (button !== undefined) stopSpeaking();
  voice.queue.push({text, button});
  if (!voice.busy) speakQueue();
}

async function speakQueue() {
  voice.busy = true;
  const run = voice.run;
  while (voice.queue.length && run === voice.run) {
    const item = voice.queue.shift();
    marking(item.button);
    await speakOne(item.text, run);
  }
  if (run === voice.run) { voice.busy = false; marking(null); }
}

function postJson(path, body) {
  return fetch(path, {method: "POST", cache: "no-store", body: JSON.stringify(body),
                      headers: {"Content-Type": "application/json", "X-IGOR-Key": chat.key}});
}

async function speakOne(text, run) {
  let data;
  try {
    const response = await postJson("/dashboard/speech", {text});
    if (!response.ok) throw new Error("HTTP " + response.status);
    data = await response.json();
  } catch (e) {
    voiceNote("Could not read that aloud (" + e.message + "). Is the tunnel open?");
    return;
  }
  if (run !== voice.run || !data.parts.length) return;
  const name = voice.choice.slice(voice.choice.indexOf(":") + 1);
  let mine = voice.choice.startsWith("igor:") && data.igor_voice;
  if (voice.choice.startsWith("igor:") && !data.igor_voice) voiceNote("Reading with the browser's voice: " + data.why + ".");
  let next = mine ? fetchPart(data.parts[0], name) : null;
  for (let i = 0; i < data.parts.length; i++) {
    if (mine) {
      const got = await next;
      if (run !== voice.run) return;
      next = i + 1 < data.parts.length ? fetchPart(data.parts[i + 1], name) : null;
      if (got.blob) {
        voiceNote("");
        await playBlob(got.blob, run);
        if (run !== voice.run) return;
        continue;
      }
      mine = false;
      voiceNote("Reading with the browser's voice: " + got.error + ".");
    }
    await sayInBrowser(data.parts[i], run);
    if (run !== voice.run) return;
  }
}

async function fetchPart(line, name) {
  try {
    const response = await postJson("/dashboard/speech.wav", {line, voice: name});
    if (response.ok) return {blob: await response.blob()};
    const data = await response.json().catch(() => ({}));
    return {error: data.error || "HTTP " + response.status};
  } catch (e) {
    return {error: "the link is down"};
  }
}

function playBlob(blob, run) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    const done = () => { URL.revokeObjectURL(url); if (voice.audio === audio) voice.audio = null; resolve(); };
    audio.addEventListener("ended", done);
    audio.addEventListener("error", done);
    audio.addEventListener("pause", done);
    if (run !== voice.run) return done();
    voice.audio = audio;
    audio.play().catch((e) => {
      if (e.name === "NotAllowedError") voiceNote("The browser held the sound back. Click anywhere on the page once, then press Listen.");
      done();
    });
  });
}

function sayInBrowser(line, run) {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window) || run !== voice.run) return resolve();
    const utterance = new SpeechSynthesisUtterance(line);
    const local = browserVoices();
    const wanted = voice.choice.startsWith("browser:") ? voice.choice.slice(8) : "";
    const pick = local.find((v) => v.name === wanted) || local.find((v) => v.lang === "en-GB") || local[0];
    if (pick) { utterance.voice = pick; utterance.lang = pick.lang; }
    utterance.onend = resolve;
    utterance.onerror = resolve;
    speechSynthesis.speak(utterance);
  });
}

const TALK = "Talk: press, speak, press again to send (Ctrl+B)";

function talkLabel(note) {
  const button = $("talk");
  if (voice.rec) {
    const s = Math.floor((Date.now() - voice.started) / 1000);
    const time = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    $("talk-time").textContent = time;
    button.setAttribute("aria-label", "Recording " + time + ". Press to send");
  } else {
    $("talk-time").textContent = note || "";
    button.setAttribute("aria-label", note || TALK);
  }
  button.classList.toggle("recording", Boolean(voice.rec));
}

async function startTalking() {
  if (!chat.key || voice.rec || $("talk").disabled) return;
  chatError("");
  stopSpeaking();
  if (!navigator.mediaDevices || !window.MediaRecorder) { chatError("This browser cannot record here."); return; }
  try {
    voice.stream = await navigator.mediaDevices.getUserMedia({audio: true});
  } catch (e) {
    chatError(e.name === "NotAllowedError" ? "The microphone was refused. Allow it for this page, then press Talk again."
                                           : "No microphone could be opened (" + e.name + ").");
    return;
  }
  const type = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t));
  const options = type ? {mimeType: type, audioBitsPerSecond: 32000} : {audioBitsPerSecond: 32000};
  const rec = new MediaRecorder(voice.stream, options);
  voice.chunks = [];
  rec.addEventListener("dataavailable", (e) => { if (e.data.size) voice.chunks.push(e.data); });
  rec.addEventListener("stop", () => sendRecording(rec));
  rec.start();
  voice.rec = rec;
  voice.started = Date.now();
  clearInterval(voice.clock);
  voice.clock = setInterval(() => {
    if (Date.now() - voice.started >= MAX_RECORD_MS) stopTalking();
    else talkLabel();
  }, 250);
  talkLabel();
}

function stopTalking() {
  if (!voice.rec) return;
  clearInterval(voice.clock);
  if (voice.rec.state !== "inactive") voice.rec.stop();
}

async function sendRecording(rec) {
  const length = Date.now() - voice.started;
  const blob = new Blob(voice.chunks, {type: rec.mimeType || "audio/webm"});
  for (const track of voice.stream ? voice.stream.getTracks() : []) track.stop();
  voice.stream = null;
  voice.rec = null;
  talkLabel();
  if (length < MIN_RECORD_MS || !blob.size) { chatError("That was too short. Press Talk, speak, then press it again."); return; }
  const button = $("talk");
  button.disabled = true;
  talkLabel("Hearing");
  try {
    const response = await fetch("/dashboard/voice", {method: "POST", cache: "no-store", body: blob,
                                                      headers: {"Content-Type": blob.type || "audio/webm", "X-IGOR-Key": chat.key}});
    const data = await response.json().catch(() => ({}));
    if (response.ok) sending(data.turn, data.text);
    else {
      chatError(data.error || "Not sent (HTTP " + response.status + ").");
      if (response.status === 401) lockChat(data.error || "Chat is locked.");
    }
  } catch (e) {
    chatError("Not sent: the link is down. Is the tunnel open?");
  } finally {
    button.disabled = !chat.key;
    talkLabel();
  }
}

$("talk").addEventListener("click", () => (voice.rec ? stopTalking() : startTalking()));
// Hermes' push-to-talk key.
document.addEventListener("keydown", (event) => {
  if (event.ctrlKey && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "b") {
    event.preventDefault();
    if (voice.rec) stopTalking(); else startTalking();
  }
});
$("aloud").addEventListener("click", () => {
  voice.aloud = !voice.aloud;
  saveVoice();
  showAloud();
  if (!voice.aloud) stopSpeaking();
});
$("voice").addEventListener("change", () => {
  voice.choice = $("voice").value;
  saveVoice();
  voiceNote("");
  speak(TEST_LINE, null);
});
$("voice-test").addEventListener("click", () => speak(TEST_LINE, null));
if ("speechSynthesis" in window) speechSynthesis.addEventListener("voiceschanged", fillVoices);
fillVoices();
showAloud();

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const box = $("say");
  const text = box.value.trim();
  if (!text || !chat.key) return;
  $("send").disabled = true;
  if (await say(text)) { box.value = ""; grow(); }
  $("send").disabled = !chat.key;
  box.focus();
});
$("say").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
$("stop").addEventListener("click", () => say("stop"));
// The box grows with what is typed, to two fifths of the window.
function grow() {
  const box = $("say");
  box.style.height = "auto";
  box.style.height = Math.min(box.scrollHeight + 2, Math.round(window.innerHeight * 0.4)) + "px";
}
$("say").addEventListener("input", grow);
$("chatlog").addEventListener("scroll", () => { chat.stuck = atEnd($("chatlog")); });

let opening = "igor";
try { opening = localStorage.getItem(TAB_NAME) || "igor"; } catch (e) { /* the first section */ }
const asked = /^#([a-z]+)$/.exec(location.hash);
if (asked && VIEWS.includes(asked[1])) opening = asked[1];
show(opening);

tick();
setInterval(tick, 1000);
refresh();
setInterval(refresh, REFRESH_MS);
if (chat.key) chatPoll();
else lockChat("Chat opens from the IGOR Dashboard shortcut on your PC.");
