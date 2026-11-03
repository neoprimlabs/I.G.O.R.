// Fills the page from /dashboard/status.json every 15 seconds, and runs the chat. Text only through
// textContent: what IGOR shows can hold words from a web page.
"use strict";

const REFRESH_MS = 15000;
const MODEL_COLOURS = ["#6db33f", "#6fb6ff", "#c8c2b6", "#e8b04b"];
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

function gauge(label, percent, value, sub, state) {
  const tile = el("div", null, "gauge");
  const ring = el("div", null, "ring" + (state && state !== "ok" ? " " + state : ""));
  ring.style.setProperty("--p", Math.max(0, Math.min(100, Math.round(percent))));
  ring.append(el("b", Math.round(percent) + "%"));
  const words = el("div");
  words.append(el("div", label, "label"), el("div", value, "value"), el("div", sub, "sub"));
  tile.append(ring, words);
  return tile;
}

function counter(label, value, sub, state) {
  const tile = el("div", null, "gauge");
  const ring = el("div", null, "ring" + (state ? " " + state : ""));
  ring.style.setProperty("--p", 100);
  ring.append(el("b", value));
  const words = el("div");
  words.append(el("div", label, "label"), el("div", sub, "sub"));
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
                     "of " + r.memory_total_mb + " MB" + swap, r.memory === "ok" ? "ok" : r.memory === "elevated" ? "warn" : "bad"));
  }
  if (r.disk_total_mb) {
    box.append(gauge("Disk free", 100 * r.disk_free_mb / r.disk_total_mb, (r.disk_free_mb / 1024).toFixed(1) + " GB",
                     "of " + (r.disk_total_mb / 1024).toFixed(0) + " GB", r.disk === "ok" ? "ok" : r.disk === "elevated" ? "warn" : "bad"));
  }
  const main = Object.entries(models).find(([, m]) => m.cap);
  if (main) {
    const [, m] = main;
    const share = 100 * m.headroom / m.cap;
    box.append(gauge("Model budget", share, m.headroom.toLocaleString(), "tokens free now", share < 20 ? "bad" : share < 40 ? "warn" : "ok"));
  }
  if (typeof data.proposals === "number") {
    box.append(counter("Proposals", data.proposals, data.proposals ? "waiting for you" : "none waiting", data.proposals ? "warn" : ""));
  }
}

function health(data) {
  const dl = $("health");
  dl.replaceChildren();
  const add = (name, value, cls) => { dl.append(el("dt", name)); dl.append(el("dd", value, cls)); };
  const p = data.process || {}, r = data.resources || {}, c = data.commit || {}, e = data.engines || {};
  const up = p.discord === "connected";
  add("Discord", p.discord || "unknown", up ? "ok" : "bad");
  add("Running for", minutes(p.up_seconds));
  add("Last update", c.subject ? c.subject + (c.when_text ? ", " + c.when_text : "") : "unknown");
  add("Code sandbox", e.sandbox ? "ready" : "missing", e.sandbox ? "ok" : "bad");
  add("Video", e.ffmpeg ? "ready" : "waiting for ffmpeg", e.ffmpeg ? "ok" : "muted");
  add("Web browser", e.lightpanda ? e.browser || "ready" : "waiting for Lightpanda", e.lightpanda ? "ok" : "muted");
  $("portrait").classList.toggle("down", !up);
  const summary = $("summary");
  summary.classList.toggle("down", !up);
  summary.textContent = (up ? "Online" : "Not connected to Discord") + "  ·  up " + minutes(p.up_seconds);
  const banner = $("banner");
  const worst = [r.disk === "critical" && "The disk is almost full.", r.memory === "critical" && "IGOR is almost out of memory and may restart.",
                 r.disk === "elevated" && "Disk space is low.", r.memory === "elevated" && "Memory is low."].find(Boolean);
  banner.hidden = !worst;
  banner.textContent = worst || "";
}

function tokens(data) {
  const box = $("tokens");
  box.replaceChildren();
  const t = data.tokens || {};
  const models = Object.entries(t.models || {}).sort((a, b) => b[1].total - a[1].total);
  if (!models.length) { box.append(el("p", "Nothing spent in the last 24 hours.", "muted")); return; }
  models.forEach(([name, m], index) => {
    const card = el("div", null, "model");
    card.style.setProperty("--m", MODEL_COLOURS[index % MODEL_COLOURS.length]);
    const head = el("div", null, "model-head");
    const title = el("h3");
    title.append(el("i", name.includes("/") ? name.split("/")[1] : name));
    head.append(title, el("span", modelRole(m.roles || []), "role"));
    card.append(head);
    // The daily cap is a bucket that refills by the second, so spent and free do not add up to the cap.
    const use = el("p", null, "use");
    use.append(el("b", m.total.toLocaleString()), " used in 24 hours. ");
    if (m.cap) {
      use.append(el("b", m.headroom.toLocaleString(), m.headroom < m.cap * 0.2 ? "warn" : ""),
                 " of " + m.cap.toLocaleString() + " free now, refilling about " + (Math.round(m.cap / 24 / 100) * 100).toLocaleString() + " an hour.");
    } else {
      use.append("Its daily limit is not measured.");
    }
    card.append(use);
    const bars = el("div", null, "bars");
    const most = Math.max(1, ...m.hourly);
    m.hourly.forEach((n, i) => {
      const bar = el("span");
      bar.style.height = Math.max(2, Math.round((n / most) * 100)) + "%";
      bar.title = (t.hours[i] || "") + ", " + n.toLocaleString() + " tokens";
      bars.append(bar);
    });
    card.append(bars);
    const hours = el("div", null, "hours");
    hours.append(el("span", t.hours[0] || ""), el("span", t.hours[12] || ""), el("span", "now"));
    card.append(hours);
    const chips = el("div", null, "chips");
    for (const [label, spent] of Object.entries(m.by_label).slice(0, 6)) {
      const chip = el("span", (LABELS[label] || label) + " ", "chip");
      chip.append(el("b", spent.toLocaleString()));
      chips.append(chip);
    }
    card.append(chips);
    box.append(card);
  });
}

function render(data) {
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
  $("footer").textContent = "Synced " + new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
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
    summary.textContent = "Link lost (" + e.message + "). Is the tunnel open?";
    $("portrait").classList.add("down");
  }
}

// Chat: the conversation as the store holds it, Discord's turns included, plus notes for replies it does
// not keep. The key arrives from the launcher after the #, which the browser never sends to a server.
const KEY_NAME = "igor-chat-key";
const chat = {key: "", after: -1, notes: 0, boot: "", pending: new Map(), timer: 0, since: 0, clock: 0};

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

function append(node, force) {
  const log = $("chatlog");
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
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

function talkLabel() {
  const button = $("talk");
  if (voice.rec) {
    const s = Math.floor((Date.now() - voice.started) / 1000);
    button.textContent = "Send voice " + Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  } else {
    button.textContent = "Talk";
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
  button.textContent = "Hearing";
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
  if (await say(text)) box.value = "";
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

tick();
setInterval(tick, 1000);
refresh();
setInterval(refresh, REFRESH_MS);
if (chat.key) chatPoll();
else lockChat("Chat opens from the IGOR Dashboard shortcut on your PC.");
