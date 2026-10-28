/* 60-Second Fire Check — vanilla UI controller.
   One job at a time: POST /api/jobs, poll /api/jobs/<id>, then read /runs/<id>/report.json. */

const VIEWS = ["upload", "processing", "results"];

const STAGES = [
  ["preprocess", "Preparing video", "H.264 · 1920 px long side · one shared timeline"],
  ["sam", "Tracking objects with SAM 3.1", "one request per generic concept, four in parallel"],
  ["classify", "Labeling each track with Muse Spark", "one crop per tracked object"],
  ["inspect", "Inspection agent checking the rules", "the agent re-opens the footage with tools"],
  ["overlay", "Rendering overlay video", "boxes coloured by label · people blurred"],
];

const STATUS = {
  ok: ["OK", "ok"],
  issue: ["Issue", "issue"],
  possibly_missing: ["Not seen", "missing"],
  needs_human_check: ["Check on site", "check"],
  not_checkable: ["Verify on site", "verify"],
};
const SEVERITY = { issue: 0, needs_human_check: 1, possibly_missing: 2, not_checkable: 3, ok: 4 };

const CHECK_SVG =
  '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" ' +
  'stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.2 4.2L19 6.8"/></svg>';
const X_SVG =
  '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" ' +
  'stroke-width="3.4" stroke-linecap="round"><path d="M6 6l12 12"/><path d="M18 6L6 18"/></svg>';
const CLOCK_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" ' +
  'stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/></svg>';

const state = {
  jobId: null,
  job: null,
  report: null,
  pollTimer: null,
  clockTimer: null,
  startedAt: 0,
  renderedLog: 0,
  stageEls: null,
  samples: [],
  health: null,
};

const $ = (sel) => document.querySelector(sel);

function h(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function showView(name) {
  for (const view of VIEWS) {
    document.getElementById(`view-${view}`).classList.toggle("hidden", view !== name);
  }
  window.scrollTo(0, 0);
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail) message = body.detail;
    } catch (err) {
      /* not json */
    }
    throw new Error(message);
  }
  return res.json();
}

function mmss(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function bytes(n) {
  if (!n) return "";
  if (n > 1e6) return `${(n / 1e6).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(n / 1e3))} KB`;
}

function assetUrl(path) {
  if (!path) return null;
  const idx = String(path).indexOf("/runs/");
  return idx >= 0 ? String(path).slice(idx) : `/${String(path).replace(/^\/+/, "")}`;
}

/* ---------------------------------------------------------------- samples */

async function loadSamples() {
  const list = $("#sample-list");
  clear(list);
  try {
    const data = await api("/api/samples");
    state.samples = data.samples || [];
  } catch (err) {
    list.append(h("p", "muted", `Could not list samples: ${err.message}`));
    return;
  }
  if (!state.samples.length) {
    list.append(h("p", "muted", "No clips in data/ — upload one above."));
    return;
  }
  for (const sample of state.samples) {
    const card = h("div", "sample-card");
    const info = h("div", "sample-info");
    info.append(h("span", "sample-name", sample.label));
    const bits = [];
    if (sample.seconds) bits.push(`${sample.seconds} s`);
    if (sample.width) bits.push(`${sample.width}×${sample.height}`);
    info.append(h("span", "sample-meta", bits.join(" · ")));

    const actions = h("div", "sample-actions");
    if (sample.previous) {
      const open = h("button", "btn primary small", "Open previous result");
      open.addEventListener("click", () => reusePrevious(sample.name));
      const rerun = h("button", "btn ghost small", "Run again");
      rerun.addEventListener("click", () => startSample(sample.name));
      actions.append(open, rerun);
    } else {
      const run = h("button", "btn primary small", "Run sample");
      run.addEventListener("click", () => startSample(sample.name));
      actions.append(run);
    }
    card.append(info, actions);
    list.append(card);
  }
}

async function startSample(name) {
  const form = new FormData();
  form.append("sample", name);
  await startJob(form);
}

async function reusePrevious(name) {
  const form = new FormData();
  form.append("sample", name);
  form.append("mode", "reuse");
  try {
    const data = await api("/api/jobs", { method: "POST", body: form });
    const job = await api(`/api/jobs/${data.job_id}`);
    state.jobId = data.job_id;
    await openResults(job);
  } catch (err) {
    showUploadError(err.message);
    showView("upload");
  }
}

function showUploadError(message) {
  const box = $("#upload-error");
  box.textContent = message;
  box.classList.toggle("hidden", !message);
}

async function startJob(form) {
  showUploadError("");
  try {
    const data = await api("/api/jobs", { method: "POST", body: form });
    await watch(data.job_id);
  } catch (err) {
    showUploadError(err.message);
    showView("upload");
  }
}

/* ---------------------------------------------------------------- upload */

function setupUpload() {
  const input = $("#file-input");
  const dropzone = $("#dropzone");
  const chosen = $("#file-chosen");
  const runBtn = $("#run-btn");
  let picked = null;

  const setFile = (file) => {
    picked = file || null;
    if (!picked) {
      chosen.textContent = "No file chosen";
      runBtn.disabled = true;
      return;
    }
    chosen.textContent = `${picked.name} · ${bytes(picked.size)}`;
    runBtn.disabled = false;
  };

  input.addEventListener("change", () => setFile(input.files[0]));
  for (const ev of ["dragenter", "dragover"]) {
    dropzone.addEventListener(ev, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragover");
    });
  }
  for (const ev of ["dragleave", "drop"]) {
    dropzone.addEventListener(ev, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragover");
    });
  }
  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) setFile(file);
  });

  $("#upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!picked) return;
    const form = new FormData();
    form.append("file", picked, picked.name);
    runBtn.disabled = true;
    await startJob(form);
    runBtn.disabled = false;
  });

  $("#new-btn").addEventListener("click", () => {
    history.replaceState(null, "", location.pathname);
    showView("upload");
    loadSamples();
  });

  $("#brand").addEventListener("click", (event) => {
    event.preventDefault();
    history.replaceState(null, "", location.pathname);
    showView("upload");
  });
}

/* ---------------------------------------------------------------- processing */

function buildStages() {
  const list = $("#stage-list");
  clear(list);
  state.stageEls = {};
  for (const [key, title, hint] of STAGES) {
    const li = h("li", "stage pending");
    const icon = h("span", "stage-icon");
    const body = h("div", "stage-body");
    const detail = h("div", "stage-detail", hint);
    body.append(h("div", "stage-title", title), detail);
    li.append(icon, body);
    list.append(li);
    state.stageEls[key] = { li, icon, detail, hint };
  }
}

function renderStages(job) {
  if (!state.stageEls) buildStages();
  for (const [key] of STAGES) {
    const el = state.stageEls[key];
    const stage = (job.stages || {})[key] || { status: "pending", detail: "" };
    el.li.className = `stage ${stage.status}`;
    if (stage.status === "done") el.icon.innerHTML = CHECK_SVG;
    else if (stage.status === "failed") el.icon.innerHTML = X_SVG;
    else el.icon.innerHTML = "";
    const detail = stage.detail || el.hint;
    if (el.detail.textContent !== detail) el.detail.textContent = detail;
  }
}

function feedText(entry) {
  const wrap = h("span", "text");
  if (entry.kind === "tool") {
    const [head, ...rest] = entry.text.split(" — ");
    wrap.append(h("code", null, head));
    if (rest.length) wrap.append(document.createTextNode(` — ${rest.join(" — ")}`));
    return wrap;
  }
  if (entry.stage === "classify" && entry.text.includes(" → ")) {
    const [track, rest] = entry.text.split(" → ");
    wrap.append(h("code", null, track), h("span", "arrow", " → "), document.createTextNode(rest));
    return wrap;
  }
  wrap.textContent = entry.text;
  return wrap;
}

function appendFeed(job) {
  const list = $("#feed-list");
  const log = job.log || [];
  if (log.length < state.renderedLog) {
    clear(list);
    state.renderedLog = 0;
  }
  const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
  for (const entry of log.slice(state.renderedLog)) {
    const row = h("li", `feed-row${entry.kind === "error" ? " error" : ""}`);
    row.append(h("span", "time", entry.at || ""));
    row.append(feedText(entry));
    if (entry.image) {
      const img = h("img", "thumb");
      img.src = assetUrl(entry.image);
      img.alt = "";
      img.loading = "lazy";
      row.append(img);
    }
    list.append(row);
  }
  state.renderedLog = log.length;
  if (nearBottom) list.scrollTop = list.scrollHeight;
  $("#feed-empty").classList.toggle("hidden", log.length > 0);
  $("#feed-count").textContent = log.length ? `${log.length} events` : "";
}

function renderProcessing(job) {
  $("#proc-name").textContent = job.sample || job.id;
  const frames = (job.cost || {}).frames;
  const bits = [`${job.fps} fps`];
  if (frames) bits.push(`${frames} frames`);
  if (job.reused) bits.push("reused run");
  $("#proc-sub").textContent = bits.join(" · ");
  renderStages(job);
  appendFeed(job);
  $("#proc-error").classList.add("hidden");
}

function renderFailure(job) {
  const box = $("#proc-error");
  box.textContent = "";
  box.classList.remove("hidden");
  box.append(h("strong", null, "Run failed. "));
  box.append(document.createTextNode(job.error || "unknown error"));
  const back = h("button", "btn ghost small", "Back to upload");
  back.style.marginLeft = "10px";
  back.addEventListener("click", () => {
    showView("upload");
    loadSamples();
  });
  box.append(back);
}

async function watch(jobId) {
  state.jobId = jobId;
  state.report = null;
  state.renderedLog = 0;
  state.stageEls = null;
  buildStages();
  clear($("#feed-list"));
  showView("processing");
  history.replaceState(null, "", `#job=${jobId}`);

  clearInterval(state.pollTimer);
  clearInterval(state.clockTimer);
  state.startedAt = Date.now();
  const timer = $("#proc-timer");
  timer.textContent = "0:00";
  state.clockTimer = setInterval(() => {
    timer.textContent = mmss((Date.now() - state.startedAt) / 1000);
  }, 500);

  const tick = async () => {
    let job;
    try {
      job = await api(`/api/jobs/${jobId}`);
    } catch (err) {
      return; // job.json not written yet, or a transient read — the next tick retries
    }
    state.job = job;
    renderProcessing(job);
    if (job.status === "done") {
      stopTimers();
      await openResults(job);
    } else if (job.status === "failed") {
      stopTimers();
      renderFailure(job);
    }
  };

  await tick();
  if (!state.job || !["done", "failed"].includes(state.job.status)) {
    state.pollTimer = setInterval(tick, 1000);
  }
}

function stopTimers() {
  clearInterval(state.pollTimer);
  clearInterval(state.clockTimer);
  state.pollTimer = null;
  state.clockTimer = null;
}

/* ---------------------------------------------------------------- results */

function statCard(label, value, cls) {
  const card = h("div", `stat ${cls}`);
  card.append(h("b", null, String(value)), h("span", null, label));
  return card;
}

function seekTo(seconds) {
  const video = $("#overlay-video");
  if (!video) return;
  video.currentTime = Math.max(0, Number(seconds) || 0);
  video.play().catch(() => {});
  video.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function checklistItem(item) {
  const [label, cls] = STATUS[item.status] || [item.status, "verify"];
  const li = h("li", "item");
  const top = h("div", "item-top");
  top.append(h("span", `badge ${cls}`, label));

  const main = h("div", "item-main");
  main.append(h("div", "item-name", item.name_en || item.rule_id));
  if (item.note) main.append(h("div", "item-note", item.note));

  const ev = h("div", "item-ev");
  const seconds = item.seconds || [];
  const frames = item.frames || [];
  if (seconds.length) {
    seconds.forEach((sec, i) => {
      const chip = h("button", "time-chip");
      chip.innerHTML = CLOCK_SVG;
      chip.append(document.createTextNode(`${sec} s`));
      if (frames[i] != null) chip.title = `frame ${frames[i]}`;
      chip.addEventListener("click", () => seekTo(sec));
      ev.append(chip);
    });
  } else if (item.status === "possibly_missing") {
    ev.append(h("span", "muted", "not seen in this walk"));
  } else if (item.status === "not_checkable") {
    ev.append(h("span", "muted", "needs a physical check"));
  }

  if (item.source) {
    const details = h("details", "src");
    details.append(h("summary", null, "Source"));
    details.append(h("p", null, item.source));
    if (item.source_status && item.source_status !== "verified") {
      details.append(h("p", "unverified", `${item.source_status} — no clause number asserted`));
    }
    ev.append(details);
  }
  main.append(ev);
  top.append(main);
  li.append(top);
  return li;
}

function toolLabel(entry) {
  const args = entry.args || {};
  switch (entry.tool) {
    case "view_track": {
      const frame = args.frame != null ? `, frame ${args.frame}` : "";
      return `view_track(${args.track_id || "?"}${frame})`;
    }
    case "list_tracks":
      return `list_tracks(${args.category || ""})`;
    case "get_rule":
      return `get_rule(${args.rule_id || "?"})`;
    case "submit_report":
      if (entry.rejected) return "submit_report — rejected";
      if (entry.accepted) return "submit_report — checklist filed";
      return "submit_report";
    default:
      return entry.tool || "?";
  }
}

function traceRow(entry) {
  const li = h("li", "trace-row");
  li.append(h("span", "trace-step", `step ${entry.step}`));
  li.append(h("code", null, toolLabel(entry)));

  const notes = [];
  const images = entry.images || [];
  if (images.length) notes.push(`${images.length} image${images.length > 1 ? "s" : ""}`);
  if (entry.problems && entry.problems.length) notes.push("sent back for evidence");
  if (entry.error) notes.push(entry.error);
  if (notes.length) li.append(h("span", "trace-note", notes.join(" · ")));

  if (images.length) {
    const thumbs = h("div", "thumbs");
    for (const path of images) {
      const url = assetUrl(path);
      if (!url) continue;
      const img = h("img");
      img.src = url;
      img.alt = "";
      img.loading = "lazy";
      thumbs.append(img);
    }
    li.append(thumbs);
  }
  return li;
}

function renderResults(job, report) {
  const counts = report.counts || {};
  const stats = $("#stats");
  clear(stats);
  stats.append(
    statCard((counts.issue || 0) === 1 ? "Issue" : "Issues", counts.issue || 0, "issue"),
    statCard("Check on site", (counts.needs_human_check || 0) + (counts.not_checkable || 0), "check"),
    statCard("OK", counts.ok || 0, "ok"),
    statCard("Not seen", counts.possibly_missing || 0, "missing"),
  );

  const items = (report.items || []).slice().sort(
    (a, b) => (SEVERITY[a.status] ?? 9) - (SEVERITY[b.status] ?? 9),
  );
  const footage = report.footage || {};
  const metaBits = [`${items.length} items checked`, report.ruleset].filter(Boolean);
  if (footage.frames) metaBits.push(`${footage.frames} frames`);
  if (job.fps) metaBits.push(`${job.fps} fps`);
  $("#result-meta").textContent = metaBits.join(" · ");

  const video = $("#overlay-video");
  video.src = assetUrl((job.artifacts || {}).overlay) || "";
  video.load();
  $("#summary-text").textContent = report.summary || "No summary returned.";

  const list = $("#checklist");
  clear(list);
  for (const item of items) list.append(checklistItem(item));
  $("#checklist-count").textContent = `${items.length} items`;

  const email = report.email_draft || {};
  $("#email-body").textContent =
    `Subject: ${email.subject || ""}\n\n${email.body || "No draft email returned."}`;

  const trace = $("#trace-list");
  clear(trace);
  for (const entry of report.trace || []) trace.append(traceRow(entry));

  const site = $("#site-list");
  clear(site);
  const siteItems = items.filter((i) => ["needs_human_check", "not_checkable"].includes(i.status));
  if (!siteItems.length) {
    site.append(h("li", "site-item", "Nothing in this walk needs a person on site."));
  }
  for (const item of siteItems) {
    const li = h("li", "site-item");
    const [label, cls] = STATUS[item.status] || [item.status, "verify"];
    li.append(h("span", `badge ${cls}`, label));
    const body = h("div", "item-main");
    body.append(h("div", "item-name", item.name_en || item.rule_id));
    if (item.note) body.append(h("div", "item-note", item.note));
    li.append(body);
    site.append(li);
  }
}

async function openResults(job) {
  let report = null;
  try {
    report = await api(`/runs/${job.id}/report.json`);
  } catch (err) {
    renderFailure({ ...job, error: `report.json is not readable: ${err.message}` });
    return;
  }
  state.report = report;
  renderResults(job, report);
  showView("results");
  history.replaceState(null, "", `#result=${job.id}`);
}

/* ---------------------------------------------------------------- tabs + copy */

function setupTabs() {
  const tabs = Array.from(document.querySelectorAll(".tab"));
  for (const tab of tabs) {
    tab.addEventListener("click", () => {
      for (const other of tabs) other.classList.toggle("active", other === tab);
      for (const panel of document.querySelectorAll(".tabpanel")) {
        panel.classList.toggle("hidden", panel.id !== `tab-${tab.dataset.tab}`);
      }
    });
  }

  $("#copy-btn").addEventListener("click", async () => {
    const text = $("#email-body").textContent || "";
    const button = $("#copy-btn");
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch (err) {
      const range = document.createRange();
      range.selectNodeContents($("#email-body"));
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      button.textContent = "Press ⌘C";
    }
    setTimeout(() => {
      button.textContent = "Copy";
    }, 1600);
  });
}

/* ---------------------------------------------------------------- boot */

async function boot() {
  setupUpload();
  setupTabs();
  buildStages();
  loadSamples();

  try {
    state.health = await api("/api/health");
    $("#foot-status").textContent = state.health.key_configured
      ? "Meta API key loaded on the server"
      : "No META_MODEL_API_KEY on this server — jobs will be refused";
  } catch (err) {
    $("#foot-status").textContent = "";
  }

  const match = location.hash.match(/^#result=([A-Za-z0-9._-]+)$/);
  if (match) {
    try {
      const job = await api(`/api/jobs/${match[1]}`);
      if (job.status === "done") await openResults(job);
    } catch (err) {
      showView("upload");
    }
    return;
  }

  const running = location.hash.match(/^#job=([A-Za-z0-9._-]+)$/);
  if (running) {
    try {
      const job = await api(`/api/jobs/${running[1]}`);
      if (job.status === "done") await openResults(job);
      else if (job.status === "failed") {
        state.job = job;
        showView("processing");
        renderProcessing(job);
        renderFailure(job);
      } else {
        await watch(running[1]); // reload during a run: pick the stream back up
      }
    } catch (err) {
      showView("upload");
    }
  }
}

boot();