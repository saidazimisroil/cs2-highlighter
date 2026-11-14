const $ = (s) => document.querySelector(s);
const state = { demo: null, steamid: null, highlights: [], polling: new Set() };

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const show = (id) => $(id).classList.remove("hidden");

// ---- setup status ---------------------------------------------------------------------------
async function loadStatus() {
  const s = await api("/api/status");
  const chip = (ok, label, tip) => `<span class="chip ${ok ? "ok" : "bad"}" title="${esc(tip || "")}">${label}</span>`;
  const recorder = s.mode === "hlae"
    ? chip(s.hlae, "HLAE", s.hlae || "HLAE.exe not found — see README setup") +
      chip(s.cs2, "CS2", s.cs2 || "cs2.exe not found — set CS2_PATH")
    : chip(s.csdm, "CS Demo Manager", s.csdm || "csdm.exe not found — see README setup");
  $("#status").innerHTML = recorder + chip(s.ffmpeg && s.ffprobe, "FFmpeg", s.ffmpeg || "not found");
}

// ---- demos ----------------------------------------------------------------------------------
async function loadRecent() {
  const demos = (await api("/api/demos")).filter((d) => d.exists);
  $("#recent").innerHTML = demos
    .map((d) => `<button data-id="${d.id}" title="${esc(d.path)}">${esc(d.name)}</button>`)
    .join("");
}

function busyDemo(msg) {
  $("#demo-info").textContent = msg;
}

async function openDemo(promise) {
  busyDemo("Reading demo… (a few seconds)");
  $("#demo-warning").classList.add("hidden");
  try {
    const demo = await promise;
    state.demo = demo;
    state.steamid = null;
    state.highlightsFor = null; // don't carry picks or per-round options over to another demo
    busyDemo(`${demo.name} — ${demo.map}, ${demo.rounds} rounds`);
    if (demo.warning) {
      $("#demo-warning").textContent = "⚠ " + demo.warning;
      show("#demo-warning");
    }
    renderPlayers();
    $("#step-highlights").classList.add("hidden");
    show("#step-player");
    loadRecent();
  } catch (e) {
    busyDemo("⚠ " + e.message);
  }
}

$("#use-path").onclick = () => {
  const path = $("#demo-path").value.trim();
  if (path) openDemo(api("/api/demos/path", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) }));
};
$("#demo-path").onkeydown = (e) => e.key === "Enter" && $("#use-path").click();
$("#recent").onclick = (e) => {
  const id = e.target.dataset.id;
  if (id) openDemo(api(`/api/demos/${id}`));
};

function upload(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  busyDemo(`Uploading ${file.name}…`);
  openDemo(api("/api/demos/upload", { method: "POST", body: fd }));
}
$("#demo-file").onchange = (e) => upload(e.target.files[0]);
const drop = $("#drop");
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files[0]); };

// ---- players --------------------------------------------------------------------------------
function renderPlayers() {
  const teams = { 2: [], 3: [] };
  for (const p of state.demo.players) (teams[p.team] || (teams[p.team] = [])).push(p);
  const block = (team, cls, title) =>
    `<div class="team ${cls}"><h3>${title}</h3>${(teams[team] || [])
      .map((p) => `<button class="player ${p.steamid === state.steamid ? "active" : ""}" data-sid="${p.steamid}">
          <span>${esc(p.name)}</span><span class="muted">${p.score[0]}–${p.score[1]}</span></button>`)
      .join("")}</div>`;
  $("#teams").innerHTML = block(2, "t", "Ended as T") + block(3, "ct", "Ended as CT");
}
$("#teams").onclick = (e) => {
  const btn = e.target.closest(".player");
  if (!btn) return;
  state.steamid = btn.dataset.sid;
  renderPlayers();
  loadHighlights();
};

// ---- highlights -----------------------------------------------------------------------------
// ids of the highlights with a per-round option on ("until_death" / "until_round_end")
const idsWith = (flag) => state.highlights.filter((h) => h[flag]).map((h) => h.id);

async function loadHighlights({ quiet = false } = {}) {
  show("#step-highlights");
  if (!quiet) $("#highlights").innerHTML = `<li class="muted">Finding highlights…</li>`;
  // picks and per-clip options carry over only for the same player and mode
  const key = `${state.steamid}|${$("#mode").value}`;
  const samePlayer = state.highlightsFor === key;
  const q = new URLSearchParams({ steamid: state.steamid, mode: $("#mode").value, pre_s: $("#pre").value,
                                   post_s: $("#post").value, mvp_s: $("#mvp").value,
                                   until_death: samePlayer ? idsWith("until_death").join(",") : "",
                                   until_round_end: samePlayer ? idsWith("until_round_end").join(",") : "" });
  const keep = new Set(state.highlights.filter((h) => h.selected).map((h) => h.id));
  state.highlights = await api(`/api/demos/${state.demo.id}/highlights?${q}`);
  if (samePlayer) state.highlights.forEach((h) => (h.selected = keep.has(h.id)));
  state.highlightsFor = key;
  renderHighlights();
}

const EMPTY_TEXT = {
  best: "This player has no kills in the match.",
  knife: "This player has no knife kills in the match.",
  awp_miss: "This player has no missed AWP shots in the match.",
};
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : word.endsWith("s") ? "es" : "s"}`;
const metaText = (h) => (h.misses ? plural(h.misses, "miss") : `${plural(h.kills, "kill")} · ${h.headshots} HS`) +
  ` · ${plural(h.segments.length, "segment")}`;

function renderHighlights() {
  const hs = state.highlights;
  if (!hs.length) {
    $("#highlights").innerHTML = `<li class="muted">${EMPTY_TEXT[$("#mode").value]}</li>`;
    updateSummary();
    return;
  }
  const max = Math.max(...hs.map((h) => h.score));
  // best rounds: best first, so the picks are at the top. Other modes: match order, as in the video.
  const sorted = $("#mode").value === "best" ? [...hs].sort((a, b) => b.score - a.score) : hs;
  $("#highlights").innerHTML = sorted
    .map((h) => `<li class="hl ${h.selected ? "on" : ""}">
        <input type="checkbox" data-id="${h.id}" ${h.selected ? "checked" : ""}>
        <div><div class="label">${esc(h.label)}</div>
          <div class="meta">${metaText(h)}</div>
          <div class="opts">
            <label title="${h.died ? "Keep recording until the player dies this round" : "Survived this round"}" class="${h.died ? "" : "off"}">
              <input type="checkbox" data-id="${h.id}" data-opt="until_death" ${h.until_death ? "checked" : ""} ${h.died ? "" : "disabled"}> until death</label>
            <label title="Keep recording until the round ends">
              <input type="checkbox" data-id="${h.id}" data-opt="until_round_end" ${h.until_round_end ? "checked" : ""}> until round end</label>
          </div></div>
        <div class="score" title="score ${h.score}"><i style="width:${Math.round((h.score / max) * 100)}%"></i></div>
        <span class="dur">${h.duration_s.toFixed(0)}s</span>
      </li>`)
    .join("");
  updateSummary();
}

$("#highlights").onchange = (e) => {
  const h = state.highlights.find((x) => x.id === e.target.dataset.id);
  if (!h) return;
  const opt = e.target.dataset.opt;
  if (opt) {
    h[opt] = e.target.checked;
    loadHighlights({ quiet: true }); // the server works out the new clip length
    return;
  }
  h.selected = e.target.checked;
  e.target.closest(".hl").classList.toggle("on", h.selected);
  updateSummary();
};
$("#mode").onchange = $("#pre").onchange = $("#post").onchange = $("#mvp").onchange =
  () => state.steamid && loadHighlights();

function updateSummary() {
  const sel = state.highlights.filter((h) => h.selected);
  const secs = sel.reduce((s, h) => s + h.duration_s, 0);
  $("#summary").textContent = sel.length
    ? `${sel.length} clip${sel.length > 1 ? "s" : ""} · about ${Math.round(secs + 3)} s of video (plus title)`
    : "Tick the clips you want";
  $("#render").disabled = !sel.length;
}

$("#render").onclick = async () => {
  const [width, height, framerate] = $("#quality").value.split("x").map(Number);
  const body = {
    demo_id: state.demo.id,
    steamid: state.steamid,
    mode: $("#mode").value,
    killfeed_all: $("#killfeed").value === "all",
    highlight_ids: state.highlights.filter((h) => h.selected).map((h) => h.id),
    pre_s: +$("#pre").value,
    post_s: +$("#post").value,
    mvp_s: +$("#mvp").value,
    until_death: idsWith("until_death"),
    until_round_end: idsWith("until_round_end"),
    width, height, framerate,
  };
  $("#render").disabled = true;
  try {
    const job = await api("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    show("#step-jobs");
    poll(job.id);
    $("#step-jobs").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    alert(e.message);
  } finally {
    updateSummary();
  }
};

// ---- jobs -----------------------------------------------------------------------------------
const STATUS_TEXT = {
  queued: "Waiting for the previous video",
  recording: "Recording in CS2…",
  compiling: "Putting the video together…",
  done: "Done",
  error: "Failed",
};

function jobEl(id) {
  let el = document.getElementById(`job-${id}`);
  if (!el) {
    el = document.createElement("div");
    el.className = "job";
    el.id = `job-${id}`;
    $("#jobs").prepend(el);
  }
  return el;
}

function renderJob(job) {
  const el = jobEl(job.id);
  const logOpen = el.querySelector("details")?.open ?? job.status !== "done";
  const video = job.status === "done"
    ? `<video controls preload="metadata" src="/api/jobs/${job.id}/video"></video>
       <div><a href="/api/jobs/${job.id}/video" download>Download MP4</a> <span class="muted">· ${esc(job.output)}</span></div>`
    : "";
  if (job.status === "done" && el.querySelector("video")) return; // don't reset playback
  el.innerHTML = `
    <div class="job-head">
      <b>${esc(job.player)} · ${esc(job.map)} · ${job.clips.length} clips</b>
      <span class="badge ${job.status}">${STATUS_TEXT[job.status] || job.status}</span>
    </div>
    ${job.error ? `<div style="color:var(--err);margin-top:8px">${esc(job.error)}</div>` : ""}
    ${video}
    <details ${logOpen ? "open" : ""}><summary class="muted" style="margin-top:8px;cursor:pointer">Log</summary>
      <pre>${esc(job.log.join("\n"))}</pre></details>`;
  const pre = el.querySelector("pre");
  pre.scrollTop = pre.scrollHeight;
}

async function poll(id) {
  if (state.polling.has(id)) return;
  state.polling.add(id);
  while (true) {
    try {
      const job = await api(`/api/jobs/${id}`);
      renderJob(job);
      if (job.status === "done" || job.status === "error") break;
    } catch (e) {
      break;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  state.polling.delete(id);
}

async function loadJobs() {
  const list = await api("/api/jobs");
  if (list.length) show("#step-jobs");
  for (const j of list.reverse()) poll(j.id);
}

loadStatus();
loadRecent();
loadJobs();
