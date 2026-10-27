// Multicam web UI — vanilla JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const KEY_LS = "multicam.comfyKey";
const STEP_NAMES = {transcribe: "Transcribe", plan: "Plan cuts", angles: "Generate angles",
  cameras: "Generate cameras", align: "Align lip-sync", render: "Render"};
let cfg = {}, S = null, mode = "talk", file = null, sel = null, polling = null;

const toast = (msg, ms = 2600) => { const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), ms); };
const fmt = t => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const key = () => localStorage.getItem(KEY_LS) || "";
const JUDGE_LS = "multicam.judgeCode";
const judge = () => (cfg.judge_code && localStorage.getItem(JUDGE_LS)) || "";
let judgeLeft = null;  // jobs left on a valid judge code (null = unknown / none)
const canPay = () => !!(key() || cfg.hosted || judge());

async function api(path, opts = {}) {
  const headers = {...(opts.headers || {})};
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  if (opts.withKey && key()) headers["X-Comfy-Key"] = key();
  else if (opts.withKey && judge()) headers["X-Judge-Code"] = judge();
  const r = await fetch(path, {method: opts.method || (opts.body ? "POST" : "GET"), headers, body: opts.body});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)); e.status = r.status; e.data = data.detail; throw e; }
  return data;
}

function confirmBox(title, html, yes = "Confirm") {
  const d = $("#confirm"); $("#cTitle").textContent = title; $("#cBody").innerHTML = html; $("#cYes").textContent = yes;
  d.showModal();
  return new Promise(res => d.addEventListener("close", () => res(d.returnValue === "yes"), {once: true}));
}

// ---------- engine / demo budget helpers
const reshoot = () => (S?.engine || cfg.engine) === "reshoot";
const jobUsd = () => (cfg.cost_usd?.[S?.engine || cfg.engine] ?? cfg.cost_per_job_usd ?? 0.05);
const demoOut = (n = 1) => cfg.hosted && !key() && (cfg.hosted_jobs_left ?? 0) < n;  // free demo jobs can't cover n
const camLabel = k => reshoot() ? (cfg.reshoot_angles?.[k]?.label || k) : (S.stills?.[k]?.label || k);
async function needOwnKey(n) {
  await confirmBox("Free demo runs are used up — use your own key",
    `<p>The public demo's free Comfy Cloud credits ${cfg.hosted_jobs_left ? `only cover <b>${cfg.hosted_jobs_left}</b> more job(s); this needs <b>${n}</b>` : "are gone"}.</p>
    <p><b>Paste your own Comfy Cloud API key</b> in step 1 (platform.comfy.org → API keys). It stays in your browser and is never stored on this server.
    A re-shoot clip costs about <b>$${(cfg.cost_usd?.reshoot ?? 0.05).toFixed(2)}</b>, a whole video about <b>$${(4 * (cfg.cost_usd?.reshoot ?? 0.05)).toFixed(2)}</b>.</p>
    <p class="note">Or <a href="${cfg.repo_url}" target="_blank" rel="noopener">run Multicam yourself</a> — it's open source.</p>`, "Paste my key");
  $("#keyCard").classList.add("attention"); $("#keyCard").scrollIntoView({behavior: "smooth", block: "center"}); $("#key").focus();
}

// ---------- key
function renderKey() {
  const k = key(), b = $("#keyBadge");
  $("#key").value = k;
  $("#keyCard").classList.toggle("attention", !!(cfg.hosted && !k && !(cfg.hosted_jobs_left > 0)));
  if (cfg.hosted && !k && !(cfg.hosted_jobs_left > 0)) { b.textContent = "free demo used up · paste your key"; b.className = "badge bad"; }
  else if (cfg.hosted && !k) { b.textContent = `demo key · ${cfg.hosted_jobs_left ?? "∞"} jobs left`; b.className = "badge warn"; }
  else if (k) { b.textContent = "saved in this browser"; b.className = "badge ok"; }
  else if (judge()) { b.textContent = `judge code · ${judgeLeft ?? "?"} jobs left`; b.className = "badge warn"; }
  else if (cfg.byok_only && cfg.public) { b.textContent = "required to generate"; b.className = "badge bad"; }
  else { b.textContent = "not set"; b.className = "badge"; }
}
$("#key").addEventListener("change", e => { const v = e.target.value.trim();
  v ? localStorage.setItem(KEY_LS, v) : localStorage.removeItem(KEY_LS); renderKey(); });
$("#judgeCheck").onclick = async () => {
  const v = $("#judge").value.trim();
  if (!v) { localStorage.removeItem(JUDGE_LS); judgeLeft = null; renderKey(); return; }
  try {
    const r = await fetch("/api/check-key", {method: "POST", headers: {"X-Judge-Code": v}});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "invalid code");
    localStorage.setItem(JUDGE_LS, v); judgeLeft = d.judge_jobs_left; renderKey();
    toast(`Judge code OK · ${d.judge_jobs_left}/${d.judge_jobs_total} jobs left (leave the key field empty to use it)`, 4500);
  } catch (e) { localStorage.removeItem(JUDGE_LS); judgeLeft = null; renderKey(); toast(e.message, 4000); }
};
$("#keyToggle").onclick = () => { const i = $("#key"); i.type = i.type === "password" ? "text" : "password"; };
$("#keyForget").onclick = e => { e.preventDefault(); localStorage.removeItem(KEY_LS); renderKey(); toast("Key removed from this browser"); };
$("#keyCheck").onclick = async () => {
  const v = $("#key").value.trim(); if (v) localStorage.setItem(KEY_LS, v);
  const b = $("#keyCheck"); b.disabled = true; b.textContent = "Checking…";
  try { const r = await api("/api/check-key", {method: "POST", withKey: true});
    toast(r.balance_usd != null ? `Key OK · balance $${r.balance_usd.toFixed(2)}` : "Key OK");
    $("#keyBadge").textContent = r.balance_usd != null ? `OK · $${r.balance_usd.toFixed(2)}` : "OK"; $("#keyBadge").className = "badge ok";
  } catch (e) { toast("Key rejected: " + e.message, 4000); $("#keyBadge").textContent = "invalid"; $("#keyBadge").className = "badge bad"; }
  b.disabled = false; b.textContent = "Check";
};

// ---------- upload
const drop = $("#drop");
["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => setFile(e.dataTransfer.files[0]));
$("#file").onchange = e => setFile(e.target.files[0]);
let fileDur = null;
const canStart = () => { $("#start").disabled = !(file && $("#consent").checked && !(cfg.max_upload_s && fileDur > cfg.max_upload_s + 0.5)); };
$("#consent").onchange = canStart;
function setFile(f) {
  if (!f) return; file = f; fileDur = null;
  if (cfg.max_upload_mb && f.size > cfg.max_upload_mb * 1048576) { toast(`Max ${cfg.max_upload_mb} MB here`, 4000); file = null; canStart(); return; }
  const box = $("#dropFile"); box.hidden = false;
  box.innerHTML = `<video src="${URL.createObjectURL(f)}" muted playsinline></video><div><b>${esc(f.name)}</b><br><small class="muted" id="fileMeta">${(f.size / 1e6).toFixed(1)} MB</small></div>`;
  $("video", box).onloadedmetadata = e => { fileDur = e.target.duration;
    const over = cfg.max_upload_s && fileDur > cfg.max_upload_s + 0.5;
    $("#fileMeta").innerHTML = `${(f.size / 1e6).toFixed(1)} MB · ${fileDur.toFixed(1)} s` + (over ? ` · <span style="color:var(--bad)">over the ${cfg.max_upload_s}s limit — trim it</span>` : "");
    canStart(); };
  canStart();
}
$$("#mode button").forEach(b => b.onclick = () => { mode = b.dataset.mode;
  $$("#mode button").forEach(x => x.classList.toggle("on", x === b));
  $("#scriptBox").hidden = mode !== "talk"; $("#lyricsBox").hidden = mode !== "music"; });
$("#start").onclick = async () => {
  const fd = new FormData(); fd.append("file", file); fd.append("mode", mode); fd.append("consent", $("#consent").checked ? "1" : "");
  fd.append("script", $("#script").value); fd.append("lyrics", $("#lyrics").value);
  const b = $("#start"); b.disabled = true; b.textContent = "Uploading…";
  try { S = await api("/api/projects", {body: fd}); location.hash = "p=" + S.id; render(); poll(); }
  catch (e) { toast(e.message, 4000); }
  b.disabled = false; b.textContent = "Analyze — free";
};

const TERMINAL = ["success", "error", "non_retryable_error", "lost", "cancelled"];
const jobSecs = j => Math.round(!TERMINAL.includes(j.status) && j.started ? Date.now() / 1000 - j.started : (j.elapsed_s || 0));
// ---------- project rendering
function render() {
  if (!S) return;
  $("#progressCard").hidden = false;
  $(".hero").hidden = $("#uploadCard").hidden = true; $("#newProject").hidden = false;
  const busy = S.busy;
  $("#busyBadge").textContent = busy ? `${busy}…` : (S.error ? "error" : "idle");
  $("#busyBadge").className = "badge " + (busy ? "warn" : S.error ? "bad" : "ok");
  $("#steps").innerHTML = Object.entries(STEP_NAMES).map(([k, n]) => { const st = S.steps[k] || {};
    return `<li class="${st.status || "pending"}"><div class="t"><span class="dot"></span>${n}</div><div class="m" title="${esc(st.msg)}">${esc(st.msg || st.status || "")}</div></li>`; }).join("");
  $("#jobs").innerHTML = (S.jobs || []).slice().reverse().map(j => `<div class="job ${j.status}"><div style="flex:1;min-width:0"><div>${esc(j.label)}</div>
    ${["success", "error", "non_retryable_error", "lost", "cancelled"].includes(j.status) ? "" : '<div class="bar"></div>'}</div>
    <div class="st">${esc(j.status)} · ${jobSecs(j)}s</div></div>`).join("");
  $("#err").hidden = !S.error; $("#err").textContent = S.error ? "⚠ " + S.error : "";
  if (S.plan) { renderCams(); renderEstimate(); $("#planCard").hidden = false; }
  if (S.renders?.length) renderResult();
  $("#generate").disabled = $("#renderFree").disabled = $("#anglesBtn").disabled = !!busy || !S.plan;
}

function renderCams() {
  $("#anglesRow").hidden = reshoot();
  const eng = `<div class="seg engine" id="engineSel">
      <button data-engine="reshoot" class="${reshoot() ? "on" : ""}">🎥 Re-shoot <small>follows your real motion & lips · recommended</small></button>
      <button data-engine="idlora" class="${reshoot() ? "" : "on"}">🖼 Classic <small>Qwen still + ID LoRA</small></button></div>`;
  if (reshoot()) {
    const presets = Object.entries(cfg.reshoot_angles || {});
    $("#engine").innerHTML = eng;
    $("#cams").innerHTML = ["B", "C"].map(cam => { const cur = S.cam_stills[cam];
      return `<div class="cam"><div class="cam-h"><span class="tag ${cam}">CAM ${cam}</span>${esc(camLabel(cur))}</div>
        <div class="presets">${presets.map(([k, v]) => `<button class="preset ${k === cur ? "on" : ""}" data-cam="${cam}" data-still="${k}" title="${esc(v.variants.join(" · "))}">${esc(v.label)}</button>`).join("")}</div>
        <div class="muted small">Each insert on CAM ${cam} gets its own camera move (${esc((cfg.reshoot_angles?.[cur]?.variants || []).join(" → "))}).</div></div>`; }).join("");
  } else {
  const stills = Object.entries(S.stills || {});
  $("#cams").innerHTML = ["B", "C"].map(cam => { const cur = S.cam_stills[cam];
    const grid = stills.length ? `<div class="stills">${stills.map(([k, v]) =>
      `<button class="still ${k === cur ? "on" : ""}" data-cam="${cam}" data-still="${k}" title="${esc(v.label)}"><img src="${v.url}" loading="lazy"><span>${esc(v.label)}</span></button>`).join("")}</div>`
      : `<div class="empty">Angle stills come from 1 Qwen job (8 views of you).<br>Default for CAM ${cam}: <b>${esc(cur)}</b></div>`;
    return `<div class="cam"><div class="cam-h"><span class="tag ${cam}">CAM ${cam}</span>${esc(S.stills?.[cur]?.label || cur)}</div>${grid}</div>`; }).join("");
  $("#engine").innerHTML = eng;
  }
  $$("#engineSel button").forEach(b => b.onclick = async () => {
    S = await api(`/api/projects/${S.id}/cams`, {json: {engine: b.dataset.engine}}); render();
    toast(b.dataset.engine === "reshoot" ? "Re-shoot: AI re-films your real take from new angles (motion + lips follow you)" : "Classic: Qwen angle still + LTX-2.3 ID LoRA"); });
  $$("#cams .still, #cams .preset").forEach(b => b.onclick = async () => {
    S = await api(`/api/projects/${S.id}/cams`, {json: {[b.dataset.cam]: b.dataset.still}}); render();
    toast(`CAM ${b.dataset.cam} → ${b.classList.contains("preset") ? b.textContent : b.title}. Applies to new generations / re-rolls.`); });
  $("#impChunk").innerHTML = S.plan.chunks.map(c => `<option value="${c.id}">${c.id} · ${fmt(c.start)} “${esc(c.words.slice(0, 28))}…”</option>`).join("");
}

function renderEstimate() {
  const e = S.estimate; if (!e) return;
  const has = e.jobs > 0;
  $("#estimate").innerHTML = `<div><div class="big">${has ? `≈ $${e.usd.toFixed(2)}` : "$0.00"}</div>
    <small>${has ? `${e.jobs} Comfy Cloud job${e.jobs > 1 ? "s" : ""} × ~$${e.per_job_usd.toFixed(3)} · ${e.angle_jobs ? "1 angle job + " : ""}${e.camera_jobs.length} ${e.engine === "reshoot" ? "re-shoot" : "camera"} clip${e.camera_jobs.length === 1 ? "" : "s"}` : "every insert already has a clip — rendering is free"}</small></div>
    <small>${S.plan.shots.filter(s => s.chunk && s.cam !== "A").length} AI inserts · ${S.plan.duration.toFixed(1)}s</small>`;
  $("#generate").textContent = !has ? "Generate (nothing to do)" : demoOut(e.jobs) ? "🔑 Paste your Comfy key to generate" : `Generate · $${e.usd.toFixed(2)}`;
  $("#generate").style.display = has ? "" : "none";
}

$("#generate").onclick = async () => {
  const e = S.estimate;
  if (!canPay()) { toast("Paste your own Comfy Cloud key first (step 1) — get one at platform.comfy.org → API keys", 4500); $("#keyCard").classList.add("attention"); $("#key").focus(); return; }
  if (demoOut(e.jobs)) return needOwnKey(e.jobs);
  const ok = await confirmBox("Spend on Comfy Cloud?",
    `<p>This submits <b>${e.jobs}</b> job(s) to Comfy Cloud with your key — about <b>$${e.usd.toFixed(2)}</b>.</p><ul>
    ${e.angle_jobs ? "<li>Qwen-Image-Edit: 8 angle stills from your best frame</li>" : ""}
    ${e.camera_jobs.map(j => `<li>${e.engine === "reshoot" ? `LTX-2.3 CrossView re-shoot · CAM ${j.cam} (${esc(camLabel(S.cam_stills[j.cam]))})` : `LTX-2.3 ID LoRA · CAM ${j.cam}`} · ${j.chunk}</li>`).join("")}</ul>
    <p class="note">Typical wait: 1–3 min per job (they run in parallel).</p>`, `Confirm · $${e.usd.toFixed(2)}`);
  if (!ok) return;
  try { S = await api(`/api/projects/${S.id}/generate`, {json: {confirm: true}, withKey: true}); render(); poll(); }
  catch (err) { toast(err.message, 4500); }
};
$("#anglesBtn").onclick = async () => {
  if (!canPay()) { toast("Paste your own Comfy Cloud key first (step 1) — get one at platform.comfy.org → API keys", 4500); $("#keyCard").classList.add("attention"); $("#key").focus(); return; }
  const ok = await confirmBox("Generate angle stills?", `<p>1 Qwen-Image-Edit job → 8 views of you (close-up, wide, 45°/90° left/right, low, aerial). About <b>$${(cfg.cost_per_job_usd || 0.03).toFixed(2)}</b>. Then pick one per camera.</p>`, "Generate stills");
  if (!ok) return;
  try { S = await api(`/api/projects/${S.id}/angles`, {json: {confirm: true}, withKey: true}); render(); poll(); }
  catch (e) { toast(e.message, 4500); }
};
$("#renderFree").onclick = () => doRender();
$("#rerender").onclick = () => doRender();
async function doRender() {
  try { S = await api(`/api/projects/${S.id}/render`, {json: {}}); render(); poll(); toast("Rendering — free, local ffmpeg"); }
  catch (e) { toast(e.message, 4000); }
}
$("#impStill").onchange = async e => { const fd = new FormData(); fd.append("cam", $("#impCam").value); fd.append("file", e.target.files[0]);
  S = await api(`/api/projects/${S.id}/stills`, {body: fd}); render(); toast("Still imported"); };
$("#impClip").onchange = async e => { const fd = new FormData(); fd.append("cam", $("#impCam").value); fd.append("chunk", $("#impChunk").value);
  if ($("#impSrc").value) fd.append("src_start", $("#impSrc").value); fd.append("file", e.target.files[0]);
  S = await api(`/api/projects/${S.id}/clips`, {body: fd}); render(); toast("Clip imported"); };

// ---------- result + editor
let shownSrc = null;
function renderResult() {
  $("#resultCard").hidden = false;
  const r = S.renders[S.renders.length - 1];
  $("#verBadge").textContent = `v${r.v} · ${r.shots.filter(s => s.src === "generated").length} AI shots`;
  setPlayerSrc();
  $("#download").href = $("#labels").checked ? r.labeled_url : r.clean_url;
  $("#download").setAttribute("download", `multicam_v${r.v}${$("#labels").checked ? "_labeled" : ""}.mp4`);
  renderTimeline();
  const dirty = (S.edits_t || 0) > (r.t || 0);
  $("#rerender").disabled = !!S.busy || !dirty;
  $("#editInfo").textContent = S.busy ? "working…" : dirty ? "edits pending" : "up to date";
}
function setPlayerSrc() {
  const r = S.renders[S.renders.length - 1], v = $("#player");
  const src = $("#compare").checked ? S.source_url : ($("#labels").checked ? r.labeled_url : r.clean_url);
  if (src === shownSrc) return;
  const t = v.currentTime || 0, playing = !v.paused; shownSrc = src; v.src = src;
  v.addEventListener("loadedmetadata", () => { v.currentTime = t; if (playing) v.play(); }, {once: true});
}
$("#labels").onchange = () => renderResult();
$("#compare").onchange = () => setPlayerSrc();

function renderTimeline() {
  const P = S.plan, D = P.duration;
  $("#timeline").innerHTML = P.shots.map(s => { const ins = !!s.chunk; const dropped = ins && s.cam === "A";
    const nc = ins && s.cam !== "A" && !s.has_clip;
    return `<button class="seg-shot ${s.cam} ${ins ? "ins" : ""} ${dropped ? "dropped" : ""} ${nc ? "nc" : ""} ${sel === s.i ? "sel" : ""}"
      style="flex:${(s.end - s.start).toFixed(3)}" data-i="${s.i}" title="${esc(s.words)}">
      <span>${dropped ? "A · dropped" : s.cam}</span><span class="w">${esc(s.words)}</span></button>`; }).join("") + `<div class="ph" id="ph" style="top:0;height:100%"></div>`;
  const step = D > 40 ? 10 : D > 20 ? 5 : 2;
  let ru = ""; for (let t = 0; t <= D + 1e-6; t += step) ru += `<span style="left:${(t / D) * 100}%">${t}s</span>`;
  $("#ruler").innerHTML = ru;
  $$("#timeline .seg-shot").forEach(b => b.onclick = ev => {
    const s = P.shots.find(x => x.i === +b.dataset.i);
    const rect = b.getBoundingClientRect(); const v = $("#player");
    v.currentTime = s.start + (ev.clientX - rect.left) / rect.width * (s.end - s.start);
    if (s.chunk) { sel = s.i; renderTimeline(); }
  });
  renderShotPanel();
}
function renderShotPanel() {
  const s = S.plan?.shots.find(x => x.i === sel), el = $("#shotPanel");
  if (!s) { el.innerHTML = `<p class="muted">Pick a colored shot on the timeline to switch its camera, drop it, or re-roll it. Grey = your real CAM A.</p>`; return; }
  const clips = S.clips[s.chunk] || {}; const al = s.align;
  el.innerHTML = `<div class="row" style="justify-content:space-between"><span class="tag ${s.cam}">${s.cam === "A" ? "CAM A (dropped insert)" : "CAM " + s.cam}</span>
      <span class="meta">${fmt(s.start)} → ${fmt(s.end)} · ${s.chunk}</span></div>
    <div class="words">“${esc(s.words)}”</div>
    <div class="meta">${al ? (al.status === "ok" ? `lip-sync: ${al.method || "words"} · offset ${al.offset >= 0 ? "+" : ""}${(+al.offset).toFixed(2)}s · speed ×${(+al.speed).toFixed(2)}` : `align: ${esc(al.status)} — ${esc(al.reason || "")}`) : "not aligned yet"}</div>
    <div class="camsel">${["B", "C", "A"].map(c => `<button data-cam="${c}" class="${s.cam === c ? "on" : ""}">${c === "A" ? "Drop (use A)" : `CAM ${c}`}${c !== "A" && !clips[c] ? " · needs clip" : ""}</button>`).join("")}</div>
    <div class="camsel">
      <button data-act="reroll" ${s.cam === "A" ? "disabled" : ""}>🎲 Re-roll CAM ${s.cam === "A" ? s.planned_cam : s.cam} · $${jobUsd().toFixed(2)}</button>
      <select id="rerollStill" ${s.cam === "A" ? "disabled" : ""}>${(reshoot() ? Object.entries(cfg.reshoot_angles || {}) : Object.entries(S.stills || {})).map(([k, v]) =>
        `<option value="${k}" ${k === S.cam_stills[s.cam] ? "selected" : ""}>angle: ${esc(v.label)}</option>`).join("")}</select>
      ${s.planned_cam !== s.cam ? `<button data-cam="${s.planned_cam}">↺ Reset</button>` : ""}
    </div>`;
  $$("button[data-cam]", el).forEach(b => b.onclick = async () => {
    S = await api(`/api/projects/${S.id}/shots`, {method: "PATCH", json: {edits: {[s.i]: {cam: b.dataset.cam}}}}); render();
    toast(b.dataset.cam === "A" ? "Insert dropped — hit Re-render (free)" : `Shot → CAM ${b.dataset.cam}${clips[b.dataset.cam] ? " — Re-render (free)" : " — no clip yet: re-roll to generate it"}`); });
  const rb = $('button[data-act="reroll"]', el);
  if (rb) rb.onclick = async () => {
    if (!canPay()) { toast("Paste your own Comfy Cloud key first (step 1) — get one at platform.comfy.org → API keys", 4500); return; }
    if (demoOut(1)) return needOwnKey(1);
    const still = $("#rerollStill")?.value; const cam = s.cam;
    const ok = await confirmBox("Re-roll this shot?", `<p>1 ${reshoot() ? "LTX-2.3 CrossView re-shoot (next camera move)" : "LTX-2.3 ID LoRA"} job for <b>CAM ${cam}</b> on “${esc(s.words)}” with a new seed${still && still !== S.cam_stills[cam] ? ` and the <b>${esc(camLabel(still))}</b> angle` : ""}. About <b>$${jobUsd().toFixed(2)}</b>. The old version is kept.</p>`, "Re-roll");
    if (!ok) return;
    try { S = await api(`/api/projects/${S.id}/reroll`, {json: {shot: s.i, cam, still, confirm: true}, withKey: true}); render(); poll(); }
    catch (e) { toast(e.message, 4500); }
  };
}
$("#player").addEventListener("timeupdate", () => { const ph = $("#ph"); if (ph && S?.plan) ph.style.left = `${($("#player").currentTime / S.plan.duration) * 100}%`; });

// ---------- share
$("#shareBtn").onclick = async () => {
  const b = $("#shareBtn"); b.disabled = true; b.textContent = "Packing…";
  try { const r = await api(`/api/projects/${S.id}/share`, {json: {title: S.name}});
    const box = $("#share"); box.hidden = false;
    box.innerHTML = `<b>Share bundle ready</b><span class="muted small">Self-contained page + mp4. Upload the folder anywhere static (GitHub Pages, Netlify, S3).</span>
      <div class="row gap"><a class="btn" href="${r.page}" target="_blank">Open page</a><a class="btn" href="${r.zip}" download>⬇ .zip</a></div>
      <a class="btn" href="${r.x_intent}" target="_blank" rel="noopener">Post on X · #OpenSourceHiggsfield</a>`;
  } catch (e) { toast(e.message, 4000); }
  b.disabled = false; b.textContent = "↗ Share";
};

function renderBanner() {
  const b = $("#banner");
  $("#judgeBox").hidden = !cfg.judge_code;
  if (cfg.public && cfg.byok_only) {
    const per = (cfg.cost_usd?.[cfg.engine] ?? cfg.cost_per_job_usd ?? 0.05), url = cfg.get_key_url || "https://platform.comfy.org/profile/api-keys";
    $("#keyCard .note").innerHTML = `🔑 <b>Bring your own key:</b> generation runs on <b>your</b> Comfy Cloud credits. Get a key at <a href="${url}" target="_blank" rel="noopener">platform.comfy.org → API keys</a>. It's saved only in <b>this browser</b> (localStorage) and sent per request to this server, which forwards it to Comfy Cloud and <b>never stores or logs it</b>. Free steps (transcribe, plan, edit, render) don't need it. <a href="#" id="keyForget">Forget key</a>`;
    $("#keyForget").onclick = e => { e.preventDefault(); localStorage.removeItem(KEY_LS); renderKey(); toast("Key removed from this browser"); };
    if (cfg.max_upload_s) $("#dropHint").textContent = `Up to ${cfg.max_upload_s} s, you talking to camera (or performing). Your own face, or someone who consented.`;
    b.hidden = false; b.classList.remove("out");
    b.innerHTML = `<b>Bring your own Comfy Cloud key.</b> Try the free steps (upload, analyze, plan, edit, render) right away; to generate AI cameras,
      <a href="#keyCard" id="bannerKey"><b>paste your own key</b></a> (<a href="${url}" target="_blank" rel="noopener">get one</a>) — ≈ $${per.toFixed(3)} per clip, ≈ $${(4 * per).toFixed(2)} per video, billed by Comfy.
      ${cfg.max_upload_s ? `Uploads ≤ ${cfg.max_upload_s}s` : ""}${cfg.ttl_minutes ? ` · deleted after ${cfg.ttl_minutes} min` : ""} · or <a href="${cfg.repo_url}" target="_blank" rel="noopener">run it yourself</a>.`;
    $("#bannerKey").onclick = e => { e.preventDefault(); $("#keyCard").classList.add("attention"); $("#keyCard").scrollIntoView({behavior: "smooth", block: "center"}); $("#key").focus(); };
    return;
  }
  if (cfg.hosted) $("#keyCard .note").innerHTML = `${(cfg.hosted_jobs_left ?? 0) > 0 ? "🔒 Optional here: leave it empty to use the capped demo credits." : "🔑 <b>Required now</b>: the free demo credits are used up — get a key at <a href=\"https://platform.comfy.org/profile/api-keys\" target=\"_blank\" rel=\"noopener\">platform.comfy.org → API keys</a>."} If you paste your own key it's saved only in <b>this browser</b> (localStorage) and sent per request to this server, which forwards it to Comfy Cloud and <b>never stores or logs it</b>. <a href="#" id="keyForget">Forget key</a>`,
    $("#keyForget").onclick = e => { e.preventDefault(); localStorage.removeItem(KEY_LS); renderKey(); toast("Key removed from this browser"); };
  if (cfg.max_upload_s) $("#dropHint").textContent = `Up to ${cfg.max_upload_s} s, you talking to camera (or performing). Your own face, or someone who consented.`;
  if (!cfg.hosted) { b.hidden = true; return; }
  const left = cfg.hosted_jobs_left ?? 0, tot = cfg.hosted_jobs_total || 1;
  b.hidden = false;
  b.classList.toggle("out", left <= 0);
  const per = (cfg.cost_usd?.[cfg.engine] ?? cfg.cost_per_job_usd ?? 0.05);
  if (left <= 0) {
    b.innerHTML = `<b>Free demo runs are used up.</b> 🔑 <a href="#keyCard" id="bannerKey"><b>Paste your own Comfy Cloud key</b></a> to keep going
      (≈ $${per.toFixed(2)} per clip, ≈ $${(4 * per).toFixed(2)} per video, billed to you by Comfy; the key stays in your browser) —
      or <a href="${cfg.repo_url}" target="_blank" rel="noopener">run Multicam yourself</a>. Free steps (analyze, plan, edit, render) still work.`;
    $("#bannerKey").onclick = e => { e.preventDefault(); $("#keyCard").classList.add("attention"); $("#keyCard").scrollIntoView({behavior: "smooth", block: "center"}); $("#key").focus(); };
    return;
  }
  b.innerHTML = `<b>Public demo</b> · generation runs on the maintainer's Comfy Cloud credits, capped at <b>${tot} jobs total</b>
    <span class="meter"><i style="width:${Math.round(100 * left / tot)}%"></i></span><b>${left} left</b>
    ${cfg.ip_jobs_per_day ? ` · ${cfg.ip_jobs_per_day} jobs per visitor per day` : ""}${cfg.max_upload_s ? ` · uploads ≤ ${cfg.max_upload_s}s` : ""}${cfg.ttl_minutes ? ` · deleted after ${cfg.ttl_minutes} min` : ""}.
    A full video ≈ 4 jobs, so that's only a few free videos — <b>please paste your own Comfy key</b> if you can (≈ $${(4 * per).toFixed(2)} per video), or <a href="${cfg.repo_url}" target="_blank" rel="noopener">run it yourself</a>.`;
}
async function refreshCfg() { cfg = await api("/api/config").catch(() => cfg); renderBanner(); renderKey(); }

// ---------- polling / boot
function poll() { clearTimeout(polling); polling = setTimeout(async () => {
  try { S = await api(`/api/projects/${S.id}`); render(); } catch (e) { /* server restarting */ }
  if (S.busy) poll(); else { loadRecent(); if (cfg.hosted) refreshCfg(); } }, 1200); }
async function loadRecent() {
  const list = await api("/api/projects").catch(() => []);
  $("#recentCard").hidden = !list.length;
  $("#recent").innerHTML = list.map(p => `<a href="#p=${p.id}"><span>${esc(p.name)} <span class="muted small">· ${p.mode}</span></span><span class="muted small">${p.renders} render${p.renders === 1 ? "" : "s"} · ${new Date(p.created * 1000).toLocaleString()}</span></a>`).join("");
}
async function openFromHash() {
  const m = location.hash.match(/p=([a-f0-9]+)/);
  if (!m) return;
  try { S = await api(`/api/projects/${m[1]}`); sel = null; shownSrc = null; render(); if (S.busy) poll();
    $("#progressCard").scrollIntoView({behavior: "smooth", block: "start"}); } catch (e) { toast("Project not found"); }
}
window.addEventListener("hashchange", openFromHash);
$("#newProject").onclick = e => { e.preventDefault(); location.hash = ""; S = null; clearTimeout(polling);
  ["#progressCard", "#planCard", "#resultCard"].forEach(s => $(s).hidden = true);
  $(".hero").hidden = $("#uploadCard").hidden = false; $("#newProject").hidden = true; window.scrollTo(0, 0); };
(async () => {
  cfg = await api("/api/config").catch(() => ({}));
  $("#repoLink").href = cfg.repo_url || "#";
  renderKey(); renderBanner(); loadRecent(); openFromHash();
  if (judge()) fetch("/api/check-key", {method: "POST", headers: {"X-Judge-Code": judge()}}).then(r => r.json())
    .then(d => { judgeLeft = d.judge_jobs_left ?? null; renderKey(); }).catch(() => {});
})();
