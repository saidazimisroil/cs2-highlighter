// CS2 Highlights frontend: plain JS, no build step. Hash routes:
//   #/demos                         demo library (add, search, remove)
//   #/demo/<id>[/<steamid>/<mode>]  pick a player and a mode, choose clips, render
//   #/videos                        renders in progress + finished videos in data/output
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}
const postJSON = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : word.endsWith("s") ? "es" : "s"}`;
const size = (b) => (b >= 2 ** 30 ? `${(b / 2 ** 30).toFixed(1)} GB` : `${Math.round(b / 2 ** 20)} MB`);
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)} d ago`;
  return new Date(t * 1000).toLocaleDateString();
}

// localStorage is a convenience (settings, recent demos); the page works without it
const store = {
  get(k, d) {
    try { const v = localStorage.getItem("cs2hl." + k); return v === null ? d : JSON.parse(v); } catch { return d; }
  },
  set(k, v) { try { localStorage.setItem("cs2hl." + k, JSON.stringify(v)); } catch {} },
};

const MAP_COLORS = {
  nuke: "#3d7fb5", dust2: "#c9a36a", mirage: "#c98a4b", inferno: "#c4573a", ancient: "#4f8f5a",
  anubis: "#c8a24a", train: "#7a8590", overpass: "#6c9a7d", vertigo: "#8a7fd0", office: "#8f6f9f", italy: "#b8774a",
};
const mapKey = (m) => (m || "").replace(/^(de|cs|ar)_/, "");
const mapName = (m) => { const k = mapKey(m); return k ? k[0].toUpperCase() + k.slice(1) : "Unknown map"; };
const mapColor = (m) => MAP_COLORS[mapKey(m)] || "#5b6475";
const mapTile = (m) => `<div class="map-tile" style="--map:${mapColor(m)}">${esc(mapKey(m).toUpperCase() || "?")}</div>`;
const srcBadge = (s) => (s ? `<span class="src ${s.toLowerCase().replace(/\s+/g, "-")}">${esc(s)}</span>` : "");
const scorePill = (sc) => (sc ? `<span class="score-pill" title="T – CT"><span class="t">${sc[0]}</span>–<span class="ct">${sc[1]}</span></span>` : "");

const ICON = {
  trash: `<svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/></svg>`,
  upload: `<svg viewBox="0 0 24 24"><path d="M12 16V5M7 10l5-5 5 5M5 19h14"/></svg>`,
  play: `<svg viewBox="0 0 24 24"><path d="M8 5l11 7-11 7z" fill="currentColor"/></svg>`,
  back: `<svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>`,
};

const state = {
  demos: [], videos: [], jobs: new Map(), polling: new Set(),
  demo: null, // the open demo (GET /api/demos/<id>)
  steamid: null, mode: "best", weapons: new Set(), weaponList: [], counts: {},
  highlights: [], highlightsFor: null, hlToken: 0,
};

// ---- sidebar --------------------------------------------------------------------------------
async function loadStatus() {
  const s = await api("/api/status");
  const chip = (ok, label, tip) => `<span class="chip ${ok ? "ok" : "bad"}" title="${esc(tip || "")}">${label}</span>`;
  const recorder = s.mode === "hlae"
    ? chip(s.hlae, "HLAE", s.hlae || "HLAE.exe not found — see README setup") +
      chip(s.cs2, "CS2", s.cs2 || "cs2.exe not found — set CS2_PATH")
    : chip(s.csdm, "CS Demo Manager", s.csdm || "csdm.exe not found — see README setup");
  $("#status").innerHTML = recorder + chip(s.ffmpeg && s.ffprobe, "FFmpeg", s.ffmpeg || "not found");
}

// recently opened demos: [[id, opened at (s)], …], newest first
const recentDemos = () => store.get("recent", []).filter((r) => Array.isArray(r));
function rememberDemo(id) {
  store.set("recent", [[id, Date.now() / 1000], ...recentDemos().filter(([x]) => x !== id)].slice(0, 8));
}

function renderSidebar() {
  const live = [...state.jobs.values()].filter((j) => !["done", "error"].includes(j.status)).length;
  $("#nav-demos").textContent = state.demos.length || "";
  const nv = $("#nav-videos");
  nv.textContent = live ? `${live} rendering` : state.videos.length || "";
  nv.classList.toggle("live", !!live);
  const byId = new Map(state.demos.map((d) => [d.id, d]));
  const recent = recentDemos().map(([id, t]) => [byId.get(id), t]).filter(([d]) => d && d.exists).slice(0, 5);
  $("#recent").innerHTML = recent.length
    ? recent.map(([d, t]) => `<a href="#/demo/${d.id}" class="${state.demo?.id === d.id ? "active" : ""}" title="${esc(d.name)}">
        <span class="dot" style="--map:${mapColor(d.summary?.map)}"></span>${esc(mapName(d.summary?.map))}
        <span class="when">${ago(t)}</span></a>`).join("")
    : `<div class="muted small" style="padding:4px 10px">Demos you open show up here.</div>`;
}

$("#menu").onclick = () => { $("#sidebar").classList.add("open"); $("#scrim").classList.add("open"); };
$("#scrim").onclick = closeMenu;
function closeMenu() { $("#sidebar").classList.remove("open"); $("#scrim").classList.remove("open"); }

function toast(html, ms = 6000) {
  const t = $("#toast");
  t.innerHTML = html;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), ms);
}

// ---- router ---------------------------------------------------------------------------------
let routeToken = 0;
function route() {
  const [page = "demos", ...args] = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const token = ++routeToken;
  closeMenu();
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === (page === "demo" ? "demos" : page)));
  if (page === "demo" && args[0]) showDemo(args[0], args[1], args[2], token);
  else if (page === "videos") showVideos();
  else showDemos();
  renderSidebar();
}
window.onhashchange = route;
const stale = (token) => token !== routeToken;

// ---- demos view -----------------------------------------------------------------------------
let demoPoll = null;
async function loadDemos() {
  state.demos = await api("/api/demos");
  renderSidebar();
  renderDemoGrid(); // no-ops off the demos page
  renderDemoHead();
  // summaries (map, score, source) are filled in on the server in the background
  clearTimeout(demoPoll);
  if (state.demos.some((d) => d.pending)) demoPoll = setTimeout(() => loadDemos().catch(() => {}), 2000);
}

function showDemos() {
  state.demo = null;
  view.innerHTML = `<div class="page">
    <div class="page-head"><h1>Demos</h1></div>
    <div class="panel">
      <div class="add-bar">
        <input id="demo-path" type="text" placeholder="Paste a .dem path, e.g. C:\\...\\csgo\\match.dem" spellcheck="false">
        <button id="use-path" class="primary">Add demo</button>
      </div>
      <label id="drop" class="drop">${ICON.upload}<input id="demo-file" type="file" accept=".dem" hidden>
        <span>Drop a <b>.dem</b> here, or click to upload a copy</span></label>
      <div id="add-msg" class="add-msg muted" hidden></div>
    </div>
    <div class="demo-tools">
      <span class="muted small" id="demo-count"></span>
      <input id="demo-search" type="search" placeholder="Search map, file or source" value="${esc(store.get("search", ""))}">
    </div>
    <div id="demo-grid" class="demo-grid"></div>
  </div>`;
  renderDemoGrid();
  loadDemos().catch((e) => addMsg(e.message, true));
}

function renderDemoGrid() {
  const grid = $("#demo-grid");
  if (!grid) return;
  const q = ($("#demo-search")?.value || "").trim().toLowerCase();
  const shown = state.demos.filter((d) => !q ||
    [d.name, d.summary?.map, mapName(d.summary?.map), d.summary?.source].some((s) => (s || "").toLowerCase().includes(q)));
  $("#demo-count").textContent = q ? `${shown.length} of ${plural(state.demos.length, "demo")}` : plural(state.demos.length, "demo");
  if (!state.demos.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">No demos yet. Add one above — CS2 match demos are usually in
      <code>…\\Counter-Strike Global Offensive\\game\\csgo\\</code></div>`;
    return;
  }
  grid.innerHTML = shown.map((d) => {
    const s = d.summary || {};
    const map = d.pending ? `<span class="shimmer"></span>` : esc(s.error ? "Unreadable demo" : mapName(s.map));
    const meta = d.exists ? `added ${ago(d.added)} · ${size(d.size)}` : "File is missing";
    const card = `${d.pending ? `<div class="map-tile"></div>` : mapTile(s.map)}
      <div class="demo-body">
        <div class="demo-top"><span class="demo-map">${map}</span>${scorePill(s.score)}${srcBadge(s.source)}</div>
        <div class="muted small">${meta}</div>
        <div class="file muted small" title="${esc(d.path)}">${esc(d.name)}</div>
      </div>`;
    return `<article class="demo-card ${d.exists ? "" : "missing"}">
      ${d.exists ? `<a class="demo-main" href="#/demo/${d.id}">${card}</a>` : `<div class="demo-main">${card}</div>`}
      <button class="icon-btn remove" data-remove="${d.id}" title="Remove from the list" aria-label="Remove">${ICON.trash}</button>
    </article>`;
  }).join("") || `<div class="empty" style="grid-column:1/-1">No demos match “${esc(q)}”.</div>`;
}

function addMsg(text, err = false) {
  const el = $("#add-msg");
  if (!el) return;
  el.hidden = !text;
  el.textContent = text;
  el.classList.toggle("err", err);
}

async function addDemo(promise) {
  addMsg("Reading demo… (a few seconds)");
  try {
    const demo = await promise;
    state.demo = demo; // already parsed: the demo page opens without a second wait
    location.hash = `#/demo/${demo.id}`;
  } catch (e) {
    addMsg("⚠ " + e.message, true);
  }
}

function upload(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".dem")) return addMsg("⚠ Please choose a .dem file", true);
  const fd = new FormData();
  fd.append("file", file);
  addMsg(`Uploading ${file.name}…`);
  addDemo(api("/api/demos/upload", { method: "POST", body: fd }));
}

async function removeDemo(id) {
  const d = state.demos.find((x) => x.id === id);
  if (!d) return;
  const what = d.upload
    ? `Remove ${d.name}?\n\nThis deletes the uploaded copy in the app's data folder.`
    : `Remove ${d.name} from the list?\n\nThe file itself stays where it is:\n${d.path}`;
  if (!confirm(what)) return;
  await api(`/api/demos/${id}`, { method: "DELETE" });
  store.set("recent", recentDemos().filter(([x]) => x !== id));
  await loadDemos();
}

// ---- demo view: player, mode, clips, render -------------------------------------------------
const MODES = [
  { id: "best", label: "Best rounds", empty: "This player has no kills in the match." },
  { id: "knife", label: "Knife kills", empty: "This player has no knife kills in the match." },
  { id: "misses", label: "Missed shots", empty: "No missed shots with the picked guns." },
  { id: "deaths", label: "Deaths", empty: "This player didn't die in the match." },
];
const SNIPERS = ["weapon_awp", "weapon_ssg08", "weapon_scar20", "weapon_g3sg1"];
const SETTINGS = { pre: "4", post: "2", mvp: "10", killfeed: "all", quality: "1280x720x60" };
const setting = (k) => store.get("set." + k, SETTINGS[k]);

async function showDemo(id, steamid, mode, token) {
  if (state.demo?.id !== id) {
    view.innerHTML = `<div class="page"><a class="back" href="#/demos">${ICON.back} Demos</a>
      <div class="empty">Reading demo… (a few seconds)</div></div>`;
    try {
      state.demo = await api(`/api/demos/${id}`);
    } catch (e) {
      if (stale(token)) return;
      view.innerHTML = `<div class="page"><a class="back" href="#/demos">${ICON.back} Demos</a>
        <div class="warning">⚠ ${esc(e.message)}</div></div>`;
      return;
    }
    if (stale(token)) return;
  }
  rememberDemo(id);
  renderSidebar();
  if (!$("#demo-page") || $("#demo-page").dataset.id !== id) {
    Object.assign(state, { steamid: null, highlights: [], highlightsFor: null, weaponList: [], counts: {} });
    renderDemoPage();
  }
  if (!state.demos.length) loadDemos().catch(() => {});

  mode = MODES.some((m) => m.id === mode) ? mode : "best";
  const playerChanged = steamid !== state.steamid;
  const modeChanged = mode !== state.mode;
  state.steamid = state.demo.players.some((p) => p.steamid === steamid) ? steamid : null;
  state.mode = mode;
  renderPlayers();
  $("#create").hidden = !state.steamid;
  $("#render-bar").hidden = !state.steamid;
  if (!state.steamid) return;
  if (playerChanged) await loadPlayer(token);
  else if (modeChanged) renderTabs();
  if (stale(token)) return;
  if (playerChanged || modeChanged) loadHighlights();
}

function renderDemoPage() {
  const d = state.demo;
  view.innerHTML = `<div class="page" id="demo-page" data-id="${d.id}">
    <a class="back" href="#/demos">${ICON.back} Demos</a>
    <div class="demo-head" id="demo-head"></div>
    ${d.warning ? `<div class="warning">⚠ ${esc(d.warning)}</div>` : ""}
    <section class="panel"><h2>Player</h2><div id="teams" class="teams"></div></section>
    <section class="panel" id="create" hidden>
      <div class="tabs" id="mode-tabs" role="tablist"></div>
      <div id="weapons" class="weapons" hidden></div>
      <div class="create-grid">
        <div>
          <div class="list-head"><span id="list-count"></span>
            <span><button class="link" id="sel-all">Select all</button> · <button class="link" id="sel-none">None</button></span></div>
          <ol id="highlights" class="highlights"></ol>
        </div>
        <aside class="settings"><details ${store.get("settingsOpen", true) ? "open" : ""} id="settings-box">
          <summary>Settings</summary>
          <div class="fields">
            <label title="Seconds recorded before the first kill, death or missed shot">Before the moment
              <span class="unit"><input data-set="pre" type="number" min="1" max="10" step="0.5" value="${esc(setting("pre"))}"> s</span></label>
            <label title="Seconds recorded after the last kill, death or missed shot">After the moment
              <span class="unit"><input data-set="post" type="number" min="0.5" max="10" step="0.5" value="${esc(setting("post"))}"> s</span></label>
            <label title="Keep recording after the round ends so the MVP music plays. 0 = off">MVP music
              <span class="unit"><input data-set="mvp" type="number" min="0" max="20" step="1" value="${esc(setting("mvp"))}"> s</span></label>
            <label title="Which kills appear in the kill feed (top right)">Kill feed
              <select data-set="killfeed">
                <option value="all">All kills</option><option value="player">Only the player's</option></select></label>
            <label>Quality
              <select data-set="quality">
                <option value="1280x720x60">720p · 60 fps (faster)</option>
                <option value="1920x1080x60">1080p · 60 fps</option>
                <option value="1280x720x30">720p · 30 fps (fastest)</option></select></label>
          </div></details></aside>
      </div>
    </section>
    <div class="render-bar" id="render-bar" hidden>
      <span id="summary" class="muted"></span>
      <button id="render" class="primary">Render video</button>
    </div>
  </div>`;
  $$("select[data-set]").forEach((s) => (s.value = setting(s.dataset.set)));
  $("#settings-box").ontoggle = (e) => store.set("settingsOpen", e.target.open);
  renderDemoHead();
}

function renderDemoHead() {
  const d = state.demo;
  const head = $("#demo-head");
  if (!d || !head) return;
  const t = d.players.find((p) => p.team === 2), ct = d.players.find((p) => p.team === 3);
  const listed = state.demos.find((x) => x.id === d.id);
  const score = listed?.summary?.score || (t && ct ? [t.score[0], ct.score[0]] : null);
  head.innerHTML = `${mapTile(d.map)}<div>
    <h1>${esc(mapName(d.map))} ${scorePill(score)} ${srcBadge(listed?.summary?.source)}</h1>
    <div class="muted small">${esc(d.name)} · ${d.rounds} rounds</div></div>`;
}

function renderPlayers() {
  const teams = { 2: [], 3: [] };
  for (const p of state.demo.players) (teams[p.team] || (teams[p.team] = [])).push(p);
  // the round score is the same for the whole team, so it goes in the heading; rows show K–D
  const block = (team, cls, title) => {
    const ps = teams[team] || [];
    const score = ps.length ? ` · ${ps[0].score[0]}–${ps[0].score[1]}` : "";
    return `<div class="team ${cls}"><h3>${title}${score}</h3>${ps
      .map((p) => `<a class="player ${p.steamid === state.steamid ? "active" : ""}"
          href="#/demo/${state.demo.id}/${p.steamid}/${state.mode}">
          <span>${esc(p.name)}</span><span class="kd" title="kills–deaths">${p.kd[0]}–${p.kd[1]}</span></a>`)
      .join("")}</div>`;
  };
  $("#teams").innerHTML = block(2, "t", "Ended as T") + block(3, "ct", "Ended as CT");
}

async function loadPlayer(token) {
  state.highlights = [];
  state.counts = {};
  renderTabs();
  state.weaponList = await api(`/api/demos/${state.demo.id}/weapons?steamid=${state.steamid}`).catch(() => []);
  if (stale(token)) return;
  // snipers if the player missed with one, otherwise the gun they missed with most
  const missed = state.weaponList.filter((w) => w.misses);
  const snipers = missed.filter((w) => SNIPERS.includes(w.weapon));
  const top = [...missed].sort((a, b) => b.misses - a.misses)[0];
  state.weapons = new Set((snipers.length ? snipers : top ? [top] : []).map((w) => w.weapon));
  state.counts.misses = missed.reduce((n, w) => n + w.misses, 0);
  renderTabs();
  // clip counts for the other tabs (the server has the match parsed, so these are quick)
  for (const m of ["best", "knife", "deaths"]) {
    api(`/api/demos/${state.demo.id}/highlights?${highlightQuery(m)}`).then((hs) => {
      if (stale(token)) return;
      state.counts[m] = hs.length;
      renderTabs();
    }).catch(() => {});
  }
}

function renderTabs() {
  $("#mode-tabs").innerHTML = MODES.map((m) => {
    const n = state.counts[m.id];
    return `<a class="tab ${m.id === state.mode ? "active" : ""}" role="tab" href="#/demo/${state.demo.id}/${state.steamid}/${m.id}">
      ${m.label}${n === undefined ? "" : `<span class="n">${n}</span>`}</a>`;
  }).join("");
  renderWeapons();
}

function renderWeapons() {
  const box = $("#weapons");
  box.hidden = state.mode !== "misses";
  if (box.hidden) return;
  if (!state.weaponList.length) {
    box.innerHTML = `<span class="lbl">This player didn't fire a gun in the match.</span>`;
    return;
  }
  const all = state.weapons.size === 0;
  box.innerHTML = `<span class="lbl">Guns</span>
    <button class="wchip ${all ? "on" : ""}" data-weapon="">All guns <b>${state.counts.misses ?? ""}</b></button>` +
    state.weaponList.map((w) => `<button class="wchip ${state.weapons.has(w.weapon) ? "on" : ""} ${w.misses ? "" : "zero"}"
      data-weapon="${w.weapon}" title="${w.misses} of ${plural(w.bursts, "burst")} missed">${esc(w.name)} <b>${w.misses}</b></button>`).join("");
}

function highlightQuery(mode, extra = {}) {
  return new URLSearchParams({
    steamid: state.steamid, mode, pre_s: setting("pre"), post_s: setting("post"), mvp_s: setting("mvp"),
    weapons: mode === "misses" ? [...state.weapons].join(",") : "", ...extra,
  });
}

// ids of the highlights with a per-round option on ("until_death" / "until_round_end")
const idsWith = (flag) => state.highlights.filter((h) => h[flag]).map((h) => h.id);

async function loadHighlights({ quiet = false } = {}) {
  if (!quiet) $("#highlights").innerHTML = `<li class="muted">Finding clips…</li>`;
  // picks and per-clip options carry over only for the same player and mode
  const key = `${state.demo.id}|${state.steamid}|${state.mode}`;
  const same = state.highlightsFor === key;
  const keep = new Set(state.highlights.filter((h) => h.selected).map((h) => h.id));
  const known = new Set(state.highlights.map((h) => h.id));
  const q = highlightQuery(state.mode, {
    until_death: same ? idsWith("until_death").join(",") : "",
    until_round_end: same ? idsWith("until_round_end").join(",") : "",
  });
  const token = ++state.hlToken;
  try {
    const hs = await api(`/api/demos/${state.demo.id}/highlights?${q}`);
    if (token !== state.hlToken) return; // a newer request (another player, mode or gun) won
    // kept clips stay as picked; clips that just appeared (e.g. another gun ticked) come in picked
    if (same) hs.forEach((h) => (h.selected = known.has(h.id) ? keep.has(h.id) : h.selected));
    state.highlights = hs;
    state.highlightsFor = key;
    if (state.mode !== "misses") state.counts[state.mode] = hs.length;
    renderTabs();
    renderHighlights();
  } catch (e) {
    if (token === state.hlToken) $("#highlights").innerHTML = `<li class="warning">⚠ ${esc(e.message)}</li>`;
  }
}

const metaText = (h) => {
  if (state.mode === "deaths") return h.segments.length > 1 ? "player's view, then killer's view" : "player's view only";
  return (h.misses ? plural(h.misses, "miss") : `${plural(h.kills, "kill")} · ${h.headshots} HS`) +
    ` · ${plural(h.segments.length, "segment")}`;
};

function renderHighlights() {
  const hs = state.highlights;
  $("#list-count").textContent = hs.length ? plural(hs.length, "clip") : "";
  if (!hs.length) {
    $("#highlights").innerHTML = `<li class="empty">${MODES.find((m) => m.id === state.mode).empty}</li>`;
    updateSummary();
    return;
  }
  const max = Math.max(...hs.map((h) => h.score));
  // best rounds: best first, so the picks are at the top. Other modes: match order, as in the video.
  const sorted = state.mode === "best" ? [...hs].sort((a, b) => b.score - a.score) : hs;
  $("#highlights").innerHTML = sorted
    .map((h) => `<li class="hl ${h.selected ? "on" : ""}">
        <input type="checkbox" data-id="${h.id}" ${h.selected ? "checked" : ""} aria-label="Include ${esc(h.label)}">
        <div><div class="label">${esc(h.label)}</div>
          <div class="meta">${metaText(h)}</div>
          ${state.mode === "deaths" ? "" : `<div class="opts">
            <label title="${h.died ? "Keep recording until the player dies this round" : "Survived this round"}" class="${h.died ? "" : "off"}">
              <input type="checkbox" data-id="${h.id}" data-opt="until_death" ${h.until_death ? "checked" : ""} ${h.died ? "" : "disabled"}> until death</label>
            <label title="Keep recording until the round ends">
              <input type="checkbox" data-id="${h.id}" data-opt="until_round_end" ${h.until_round_end ? "checked" : ""}> until round end</label>
          </div>`}</div>
        <div class="bar" title="score ${h.score}"><i style="width:${Math.round((h.score / max) * 100)}%"></i></div>
        <span class="dur">${h.duration_s.toFixed(0)}s</span>
      </li>`)
    .join("");
  updateSummary();
}

function updateSummary() {
  const sel = state.highlights.filter((h) => h.selected);
  const secs = sel.reduce((s, h) => s + h.duration_s, 0);
  $("#summary").textContent = sel.length
    ? `${plural(sel.length, "clip")} · about ${Math.round(secs + 3)} s of video (plus title)`
    : "Tick the clips you want";
  $("#render").disabled = !sel.length;
}

async function render() {
  const [width, height, framerate] = setting("quality").split("x").map(Number);
  const body = {
    demo_id: state.demo.id, steamid: state.steamid, mode: state.mode,
    weapons: state.mode === "misses" ? [...state.weapons] : [],
    killfeed_all: setting("killfeed") === "all",
    highlight_ids: state.highlights.filter((h) => h.selected).map((h) => h.id),
    pre_s: +setting("pre"), post_s: +setting("post"), mvp_s: +setting("mvp"),
    until_death: idsWith("until_death"), until_round_end: idsWith("until_round_end"),
    width, height, framerate,
  };
  $("#render").disabled = true;
  try {
    const job = await postJSON("/api/jobs", body);
    state.jobs.set(job.id, job);
    poll(job.id);
    renderSidebar();
    toast(`Rendering started — CS2 will open. <a href="#/videos">View in Videos</a>`);
  } catch (e) {
    toast(`⚠ ${esc(e.message)}`);
  } finally {
    updateSummary();
  }
}

// ---- videos view ----------------------------------------------------------------------------
const STATUS_TEXT = {
  queued: "Waiting for the previous video", recording: "Recording in CS2…",
  compiling: "Putting the video together…", done: "Done", error: "Failed",
};
const MODE_TITLE = { best: "Highlights", knife: "Knife kills", misses: "Missed shots", awp_miss: "AWP misses", deaths: "Deaths" };

async function loadVideos() {
  state.videos = await api("/api/videos");
  renderSidebar();
}

function showVideos() {
  state.demo = null;
  view.innerHTML = `<div class="page">
    <div class="page-head"><h1>Videos</h1></div>
    <div id="renders"></div>
    <div class="section-title"><h2>Library</h2><span class="muted small" id="lib-count"></span></div>
    <div id="library" class="video-grid"></div>
  </div>`;
  renderJobs();
  renderLibrary();
  loadVideos().then(renderLibrary).catch(() => {});
}

function renderJobs() {
  const box = $("#renders");
  if (!box) return;
  const list = [...state.jobs.values()].sort((a, b) => a.created - b.created); // renderJob prepends: newest ends on top
  if (!list.length) { box.innerHTML = ""; return; }
  if (!$(".section-title", box)) box.innerHTML = `<div class="section-title" style="margin-top:0"><h2>Renders</h2>
    <span class="muted small">this session</span></div><div id="job-list"></div>`;
  for (const j of list) renderJob(j);
}

function renderJob(job) {
  const host = $("#job-list");
  if (!host) return;
  let el = document.getElementById(`job-${job.id}`);
  if (!el) {
    el = document.createElement("div");
    el.className = "job";
    el.id = `job-${job.id}`;
    host.prepend(el);
  }
  if (el.dataset.status === "done" && job.status === "done") return; // finished: nothing changes now
  const logOpen = el.querySelector("details")?.open ?? job.status !== "done";
  el.dataset.status = job.status;
  el.innerHTML = `
    <div class="job-head">
      <b>${esc(job.player)} · ${esc(mapName(job.map))} · ${plural(job.clips.length, "clip")}</b>
      <span class="badge ${job.status}">${STATUS_TEXT[job.status] || job.status}</span>
    </div>
    ${job.error ? `<div class="err">${esc(job.error)}</div>` : ""}
    ${job.status === "done" ? `<div class="muted small" style="margin-top:6px">Saved to the library below.</div>` : ""}
    <details ${logOpen ? "open" : ""}><summary class="muted small" style="margin-top:8px;cursor:pointer">Log</summary>
      <pre>${esc((job.log || []).join("\n"))}</pre></details>`;
  const pre = el.querySelector("pre");
  pre.scrollTop = pre.scrollHeight;
}

function renderLibrary() {
  const grid = $("#library");
  if (!grid) return;
  $("#lib-count").textContent = state.videos.length ? plural(state.videos.length, "video") : "";
  grid.innerHTML = state.videos.length ? state.videos.map((v) => {
    const title = v.title ? v.title[0] + v.title.slice(1).toLowerCase() : MODE_TITLE[v.mode] || "Highlights";
    const src = `/api/videos/${encodeURIComponent(v.name)}`;
    return `<article class="video-card">
      <button class="poster" data-play="${esc(src)}" style="--map:${mapColor(v.map)}" aria-label="Play">
        ${ICON.play}<span class="map-name">${esc(mapKey(v.map).toUpperCase())}</span></button>
      <div class="video-info">
        <div class="row"><b>${esc(v.player)}</b><span class="src">${esc(title)}</span></div>
        <div class="row"><span class="muted small">${ago(v.mtime)} · ${size(v.size)}${v.clips ? ` · ${plural(v.clips.length, "clip")}` : ""}</span>
          <a class="dl" href="${esc(src)}" download>Download</a></div>
      </div></article>`;
  }).join("") : `<div class="empty" style="grid-column:1/-1">No videos yet. Open a demo, pick a player and render one.</div>`;
}

// ---- jobs: poll until done ------------------------------------------------------------------
async function poll(id) {
  if (state.polling.has(id)) return;
  state.polling.add(id);
  while (true) {
    try {
      const job = await api(`/api/jobs/${id}`);
      const was = state.jobs.get(id)?.status;
      state.jobs.set(id, job);
      renderJob(job);
      if (was !== job.status) renderSidebar();
      if (job.status === "done" || job.status === "error") {
        if (job.status === "done") {
          loadVideos().then(renderLibrary);
          toast(`Video ready: ${esc(job.player)} · ${esc(mapName(job.map))}. <a href="#/videos">Watch</a>`, 10000);
        }
        break;
      }
    } catch (e) {
      break;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  state.polling.delete(id);
}

async function loadJobs() {
  for (const j of await api("/api/jobs")) {
    state.jobs.set(j.id, j);
    if (!["done", "error"].includes(j.status)) poll(j.id);
  }
  renderJobs();
  renderSidebar();
}

// ---- events (delegated: views re-render their contents) -------------------------------------
view.addEventListener("click", (e) => {
  const t = e.target;
  if (t.closest("#use-path")) {
    const path = $("#demo-path").value.trim();
    if (path) addDemo(postJSON("/api/demos/path", { path }));
  } else if (t.closest("[data-remove]")) {
    removeDemo(t.closest("[data-remove]").dataset.remove).catch((err) => toast(`⚠ ${esc(err.message)}`));
  } else if (t.closest("[data-weapon]")) {
    const w = t.closest("[data-weapon]").dataset.weapon;
    if (!w) state.weapons = new Set(); // "All guns"
    else if (state.weapons.has(w)) state.weapons.delete(w);
    else state.weapons.add(w);
    renderWeapons();
    loadHighlights({ quiet: true });
  } else if (t.closest("#sel-all, #sel-none")) {
    const on = !!t.closest("#sel-all");
    state.highlights.forEach((h) => (h.selected = on));
    renderHighlights();
  } else if (t.closest("#render")) {
    render();
  } else if (t.closest("[data-play]")) {
    const btn = t.closest("[data-play]");
    const video = document.createElement("video");
    Object.assign(video, { controls: true, autoplay: true, src: btn.dataset.play });
    btn.replaceWith(video);
  }
});

view.addEventListener("change", (e) => {
  const t = e.target;
  if (t.id === "demo-file") return upload(t.files[0]);
  if (t.dataset.set) {
    store.set("set." + t.dataset.set, t.value);
    if (["pre", "post", "mvp"].includes(t.dataset.set)) loadHighlights({ quiet: true });
    return;
  }
  const h = state.highlights.find((x) => x.id === t.dataset.id);
  if (!h) return;
  if (t.dataset.opt) {
    h[t.dataset.opt] = t.checked;
    loadHighlights({ quiet: true }); // the server works out the new clip length
    return;
  }
  h.selected = t.checked;
  t.closest(".hl").classList.toggle("on", h.selected);
  updateSummary();
});

view.addEventListener("input", (e) => {
  if (e.target.id === "demo-search") {
    store.set("search", e.target.value);
    renderDemoGrid();
  }
});
view.addEventListener("keydown", (e) => {
  if (e.target.id === "demo-path" && e.key === "Enter") $("#use-path").click();
});
// drop a .dem anywhere on the demos page; elsewhere a dropped file must not replace the page
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());
view.addEventListener("dragover", (e) => {
  if (!$("#drop")) return;
  e.preventDefault();
  $("#drop").classList.add("over");
});
view.addEventListener("dragleave", (e) => { if (!view.contains(e.relatedTarget)) $("#drop")?.classList.remove("over"); });
view.addEventListener("drop", (e) => {
  if (!$("#drop")) return;
  e.preventDefault();
  $("#drop").classList.remove("over");
  upload(e.dataTransfer.files[0]);
});

// ---- start ----------------------------------------------------------------------------------
loadStatus().catch(() => {});
loadJobs().catch(() => {});
loadVideos().catch(() => {});
loadDemos().catch(() => {});
route();
