// Fills the page from /dashboard/status.json every 15 seconds. Text only through textContent: what IGOR
// shows can hold words from a web page.
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

function fill(id, rows, empty) {
  const list = $(id);
  list.replaceChildren();
  if (!Array.isArray(rows)) { list.append(el("li", "Could not be read.", "bad")); return; }
  if (!rows.length) { list.append(el("li", empty, "muted")); return; }
  for (const row of rows) list.append(el("li", row));
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
    box.append(gauge("Memory free", 100 * r.memory_available_mb / r.memory_total_mb, r.memory_available_mb + " MB",
                     "of " + r.memory_total_mb + " MB", r.memory === "ok" ? "ok" : r.memory === "elevated" ? "warn" : "bad"));
  }
  if (r.disk_total_mb) {
    box.append(gauge("Disk free", 100 * r.disk_free_mb / r.disk_total_mb, (r.disk_free_mb / 1024).toFixed(1) + " GB",
                     "of " + (r.disk_total_mb / 1024).toFixed(0) + " GB", r.disk === "ok" ? "ok" : r.disk === "elevated" ? "warn" : "bad"));
  }
  const main = Object.entries(models).find(([, m]) => m.cap);
  if (main) {
    const [, m] = main;
    const share = 100 * m.headroom / m.cap;
    box.append(gauge("Main model budget", share, m.headroom.toLocaleString(), "tokens free now", share < 20 ? "bad" : share < 40 ? "warn" : "ok"));
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
  add("Swap in use", r.swap_used_mb !== undefined ? r.swap_used_mb + " MB" : "unknown");
  add("Code", c.sha ? c.sha + "  " + c.subject : "unknown");
  add("Sandbox", e.sandbox ? "ready" : "missing", e.sandbox ? "ok" : "bad");
  add("Video", e.ffmpeg ? "ffmpeg installed" : "ffmpeg not installed", e.ffmpeg ? "ok" : "muted");
  add("Browser", e.browser || "unknown", e.lightpanda ? "ok" : "muted");
  $("portrait").classList.toggle("down", !up);
  const summary = $("summary");
  summary.classList.toggle("down", !up);
  summary.textContent = (up ? "Online" : "Not connected to Discord") + "  /  up " + minutes(p.up_seconds);
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
    const [vendor, model] = name.includes("/") ? name.split("/") : ["", name];
    if (vendor) title.append(el("i", vendor + " / "));
    title.append(model);
    const left = m.cap ? m.headroom.toLocaleString() + " of " + m.cap.toLocaleString() + " free now" : "No daily cap measured";
    head.append(title, el("span", m.total.toLocaleString() + " spent.  " + left, m.cap && m.headroom < m.cap * 0.2 ? "warn" : "muted"));
    card.append(head);
    const bars = el("div", null, "bars");
    const most = Math.max(1, ...m.hourly);
    m.hourly.forEach((n, i) => {
      const bar = el("span");
      bar.style.height = Math.max(2, Math.round((n / most) * 100)) + "%";
      bar.title = (t.hours[i] || "") + ":00, " + n.toLocaleString() + " tokens";
      bars.append(bar);
    });
    card.append(bars);
    const hours = el("div", null, "hours");
    hours.append(el("span", (t.hours[0] || "") + ":00"), el("span", (t.hours[12] || "") + ":00"), el("span", "now"));
    card.append(hours);
    const labels = Object.entries(m.by_label).slice(0, 6).map(([k, v]) => k + " " + v.toLocaleString()).join("  \u00b7  ");
    card.append(el("p", labels, "labels"));
    box.append(card);
  });
}

function render(data) {
  gauges(data);
  health(data);
  tokens(data);
  fill("scheduled", Array.isArray(data.scheduled) ? data.scheduled.map((s) => s.due + "  /  " + s.repeat + "  /  " + (s.what || "(no brief)")) : null, "Nothing scheduled.");
  fill("board", data.board, "The board is empty.");
  fill("jobs", Array.isArray(data.jobs) ? data.jobs.map((j) => j.id.replace(/_/g, " ") + "  /  " + j.next) : null, "The scheduler is not running.");
  fill("plugins", data.plugins, "No plugins.");
  fill("background", data.background, "No background commands.");
  fill("errors", data.errors, "No errors logged.");
  $("footer").textContent = "Synced " + new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
}

function tick() {
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
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

tick();
setInterval(tick, 1000);
refresh();
setInterval(refresh, REFRESH_MS);
