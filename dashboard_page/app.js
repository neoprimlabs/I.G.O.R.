// Fills the page from /dashboard/status.json every 15 seconds. Text only through textContent: what IGOR
// shows can hold words from a web page.
"use strict";

const REFRESH_MS = 15000;
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
  return Math.round(seconds / 3600) + " hours";
}

function level(word) {
  return word === "ok" ? "ok" : word === "elevated" ? "warn" : "bad";
}

function health(data) {
  const dl = $("health");
  dl.replaceChildren();
  const add = (name, value, cls) => { dl.append(el("dt", name)); dl.append(el("dd", value, cls)); };
  const p = data.process || {}, r = data.resources || {}, c = data.commit || {}, e = data.engines || {};
  add("Discord", p.discord || "unknown", p.discord === "connected" ? "ok" : "bad");
  add("Running for", minutes(p.up_seconds));
  add("Memory free", r.memory_available_mb !== undefined ? r.memory_available_mb + " of " + r.memory_total_mb + " MB" : "unknown", level(r.memory));
  add("Swap in use", r.swap_used_mb !== undefined ? r.swap_used_mb + " MB" : "unknown");
  add("Disk free", r.disk_free_mb !== undefined ? (r.disk_free_mb / 1024).toFixed(1) + " GB" : "unknown", level(r.disk));
  add("Code", c.sha ? c.sha + ", " + c.subject : "unknown");
  add("Proposals waiting", typeof data.proposals === "number" ? data.proposals : "unknown", data.proposals ? "warn" : "");
  add("Sandbox", e.sandbox ? "ready" : "missing", e.sandbox ? "ok" : "bad");
  add("Video (ffmpeg)", e.ffmpeg ? "installed" : "not installed", e.ffmpeg ? "ok" : "muted");
  add("Browser", e.browser || "unknown", e.lightpanda ? "ok" : "muted");
  const banner = $("banner");
  const worst = [r.disk === "critical" && "The disk is almost full.", r.memory === "critical" && "IGOR is almost out of memory and may restart.",
                 r.disk === "elevated" && "Disk space is low.", r.memory === "elevated" && "Memory is low."].find(Boolean);
  banner.hidden = !worst;
  banner.textContent = worst || "";
  $("summary").textContent = (p.discord === "connected" ? "Connected" : "Not connected to Discord") + ", running for " + minutes(p.up_seconds) + ".";
}

function tokens(data) {
  const box = $("tokens");
  box.replaceChildren();
  const t = data.tokens || {};
  const models = Object.entries(t.models || {}).sort((a, b) => b[1].total - a[1].total);
  if (!models.length) { box.append(el("p", "Nothing spent in the last 24 hours.", "muted")); return; }
  for (const [name, m] of models) {
    const card = el("div", null, "model");
    card.append(el("h3", name));
    const left = m.cap ? m.headroom.toLocaleString() + " of " + m.cap.toLocaleString() + " free now" : "No daily cap measured";
    card.append(el("p", m.total.toLocaleString() + " tokens spent. " + left + ".", m.cap && m.headroom < m.cap * 0.2 ? "warn" : "muted"));
    const bars = el("div", null, "bars");
    const most = Math.max(1, ...m.hourly);
    m.hourly.forEach((n, i) => {
      const bar = el("span");
      bar.style.height = Math.round((n / most) * 100) + "%";
      bar.title = (t.hours[i] || "") + ":00, " + n.toLocaleString() + " tokens";
      bars.append(bar);
    });
    card.append(bars);
    const hours = el("div", null, "hours");
    hours.append(el("span", (t.hours[0] || "") + ":00"), el("span", "now"));
    card.append(hours);
    const labels = Object.entries(m.by_label).slice(0, 6).map(([k, v]) => k + " " + v.toLocaleString()).join(", ");
    card.append(el("p", "Most went on: " + labels + ".", "muted"));
    box.append(card);
  }
}

function render(data) {
  health(data);
  tokens(data);
  fill("scheduled", Array.isArray(data.scheduled) ? data.scheduled.map((s) => s.due + ", " + s.repeat + ": " + (s.what || "(no brief)")) : null, "Nothing scheduled.");
  fill("board", data.board, "The board is empty.");
  fill("jobs", Array.isArray(data.jobs) ? data.jobs.map((j) => j.id + ": " + j.next) : null, "The scheduler is not running.");
  fill("plugins", data.plugins, "No plugins.");
  fill("background", data.background, "No background commands.");
  fill("errors", data.errors, "No errors logged.");
  $("footer").textContent = "Updated " + new Date().toLocaleTimeString() + ". Refreshes every 15 seconds.";
}

async function refresh() {
  try {
    const response = await fetch("/dashboard/status.json", {cache: "no-store"});
    if (!response.ok) throw new Error("HTTP " + response.status);
    render(await response.json());
  } catch (e) {
    $("summary").textContent = "IGOR could not be reached (" + e.message + "). Is the tunnel open?";
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
