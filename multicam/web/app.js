// Multicam web UI — vanilla JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const KEY_LS = "multicam.comfyKey";
const STEP_NAMES = {transcribe: "Transcribe", plan: "Plan cuts", angles: "Generate angles",
  cameras: "Generate cameras", align: "Align lip-sync", render: "Render"};
let cfg = {}, S = null, mode = "talk", file = null, sel = null, polling = null;

// typed text survives a refresh: localStorage per project (kind "p" | "s" | "d" | "new" for the home form)
const textDraft = {
  k: (kind, id) => `multicam.text.${kind}.${id}`,
  get(kind, id) { try { return JSON.parse(localStorage.getItem(this.k(kind, id)) || "null"); } catch { return null; } },
  set(kind, id, v) { try { localStorage.setItem(this.k(kind, id), JSON.stringify(v)); } catch { /* quota / private mode */ } },
  clear(kind, id) { localStorage.removeItem(this.k(kind, id)); },
};
const toast = (msg, ms = 2600) => { const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), ms); };
const fmt = t => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const key = () => localStorage.getItem(KEY_LS) || "";
const JUDGE_LS = "multicam.judgeCode";
const judge = () => (cfg.judge_code && localStorage.getItem(JUDGE_LS)) || "";
let judgeLeft = null;  // jobs left on a valid promo code (null = unknown / none)
// free trial runs on the hosted key: a visitor may pay with one (the server enforces pool, 1 per visitor, floor)
const trialOk = () => !!(cfg.trial && (cfg.trial.runs_left > 0 || cfg.trial.used_by_you));
// Which credits pay for the next paid action. An explicit pick in the payment switch (PAY_LS) wins; otherwise a
// saved key is the default, then a promo code, and the free run / demo credits only when neither is present.
// "key" without a saved key (or "judge" without a code) means "nothing to pay with": we prompt, never fall back.
const PAY_LS = "multicam.payMode";
const freeAvail = () => trialOk() || !!(cfg.hosted && (cfg.hosted_jobs_left ?? 0) > 0);
const payPick = () => { const m = localStorage.getItem(PAY_LS);
  return m === "key" || (m === "judge" && cfg.judge_code) || (m === "free" && freeAvail()) ? m : null; };  // a used-up free pick no longer counts
function payMode() {
  const m = payPick();
  if (m) return m;
  if (key()) return "key";
  if (judge()) return "judge";
  if (cfg.trial || cfg.hosted) return "free";
  return "key";
}
function setPayPick(m) { m ? localStorage.setItem(PAY_LS, m) : localStorage.removeItem(PAY_LS); }
const canPay = () => { const m = payMode();
  return m === "key" ? !!key() : m === "judge" ? !!judge() : freeAvail(); };
// one short, unambiguous name for what will be charged (chip, counter, confirm dialogs)
function payLabel() {
  const m = payMode(), t = cfg.trial;
  if (m === "key") return key() ? "your own Comfy key" : "your own Comfy key (not set)";
  if (m === "judge") return judge() ? `promo code${judgeLeft != null ? ` (${judgeLeft} jobs left)` : ""}` : "promo code (not set)";
  if (t) return t.used_by_you ? "your free run" : "a free run";
  return "the public demo credits";
}
const payLine = () => `<p class="note pay-line"><b>Paid with:</b> ${esc(payLabel())}${payMode() === "free" && key()
  ? " (your saved key will <b>not</b> be charged)" : ""}. Change it with the payment chip in the header.</p>`;
const paidConfirm = (title, html, yes) => confirmBox(title, html + payLine(), yes);

async function api(path, opts = {}) {
  const headers = {...(opts.headers || {})};
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  if (opts.withKey) {  // exactly the credential of the chosen payment mode, plus the mode itself
    const m = payMode();
    headers["X-Pay-Mode"] = m === "judge" ? "promo" : m === "free" ? "trial" : "key";
    if (m === "key" && key()) headers["X-Comfy-Key"] = key();
    if (m === "judge" && judge()) headers["X-Judge-Code"] = judge();
  }
  const r = await fetch(path, {method: opts.method || (opts.body ? "POST" : "GET"), headers, body: opts.body});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)); e.status = r.status; e.data = data.detail;
    if (r.status === 403 && opts.withKey && outOfRuns(e.message)) { runsOut = runsCtx(e.message); renderReqRuns(); e.outOfRuns = true; }
    throw e; }
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
const demoOut = (n = 1) => cfg.hosted && payMode() === "free" && (cfg.hosted_jobs_left ?? 0) < n;  // free demo jobs can't cover n
const camLabel = k => reshoot() ? (cfg.reshoot_angles?.[k]?.label || k) : (S.stills?.[k]?.label || k);
// ---------- out of runs: a "Request more runs" mailto. The email names the state only, never a code or key.
const RUNS_MAIL = "multicam@apsolut.dev";
const RUNS_CTX = {promo: "promo code used up", promoGone: "promo code expired / no longer valid", trial: "free runs used",
  demo: "free demo credits used up", api: "the server said my payment mode has no runs left"};
function reqRunsHref(ctx) {
  const body = `Hi Alek,\n\nI ran out of runs on Multicam and would love 2 more to keep testing.\n\nWhat I want to make: \n\n(Context: ${ctx || "no runs left"})\n\nThanks!`;
  return `mailto:${RUNS_MAIL}?subject=${encodeURIComponent("Multicam: request more runs")}&body=${encodeURIComponent(body)}`;
}
// solo = the only action in its spot (yellow accent); otherwise an outline secondary button
const reqRunsBtn = (ctx, solo) => `<a class="btn req-runs${solo ? " solo" : ""}" href="${reqRunsHref(ctx)}">Request more runs</a>`;
const outOfRuns = msg => /used up|expired|isn't valid any more|no longer valid|no runs left|\b0 (of \d+ )?(jobs |runs )?left|used your free run/i.test(msg || "");
function runsCtx(msg) {  // a fixed state label for the email; the server text itself is never copied in
  const m = String(msg || "").toLowerCase();
  if (/promo/.test(m)) return /isn't valid any more|expired|no longer valid/.test(m) ? RUNS_CTX.promoGone : RUNS_CTX.promo;
  if (/free run/.test(m)) return RUNS_CTX.trial;
  if (/demo/.test(m)) return RUNS_CTX.demo;
  return RUNS_CTX.api;
}
let runsOut = null;  // set by a "used up / expired" answer from the API; cleared when a working way to pay shows up
function outNow() {  // the state description when nothing can pay right now, else null
  if (key() && payMode() === "key") return null;
  if (judge() && judgeLeft > 0 && payMode() === "judge") return null;
  if (runsOut) return runsOut;
  if (judgeLeft === 0 && payMode() === "judge") return RUNS_CTX.promo;
  if ((cfg.trial || cfg.hosted) && !key() && !freeAvail() && !(judge() && judgeLeft > 0)) return cfg.trial ? RUNS_CTX.trial : RUNS_CTX.demo;
  return null;
}
function renderReqRuns() {
  const ctx = outNow(), href = reqRunsHref(ctx || "");
  for (const id of ["reqRunsGo", "reqRunsSheet"]) { const a = $("#" + id); if (!a) continue; a.hidden = !ctx; a.href = href; }
  const bx = $("#reqRunsBox"); if (bx) bx.hidden = !ctx;
  const n = $("#reqRunsMail"); if (n) n.href = reqRunsHref(ctx || "asking ahead");
}
// the payment sheet lives in the header (ux.js); everything that used to point at the key card opens it
function openPay(reason) { window.uxOpenPay ? window.uxOpenPay(reason) : null; }
function payMissing() {  // the chosen payment can't pay: open the sheet on the right field, never fall back silently
  const m = payMode();
  if (m === "judge") { openPay("Promo code selected: enter it and press Use code (or pick another way to pay)."); $("#judge").focus(); return; }
  if (m === "free") { openPay("The free runs are used up. Paste your own Comfy Cloud key (or a promo code) to generate."); return; }
  toast("Paste your own Comfy Cloud key first — get one at platform.comfy.org → API keys", 4500); openPay(); $("#key").focus();
}
async function needOwnKey(n) {
  await confirmBox("Free demo runs are used up — use your own key",
    `<p>The public demo's free Comfy Cloud credits ${cfg.hosted_jobs_left ? `only cover <b>${cfg.hosted_jobs_left}</b> more job(s); this needs <b>${n}</b>` : "are gone"}.</p>
    <p><b>Paste your own Comfy Cloud API key</b> in the payment sheet (platform.comfy.org → API keys). It stays in your browser and is never stored on this server.
    A re-shoot clip costs about <b>$${(cfg.cost_usd?.reshoot ?? 0.05).toFixed(2)}</b>, a whole video about <b>$${(4 * (cfg.cost_usd?.reshoot ?? 0.05)).toFixed(2)}</b>.</p>
    <p class="note">Or <a href="${cfg.repo_url}" target="_blank" rel="noopener">run Multicam yourself</a> — it's open source.</p>
    <p class="req-runs-row">${reqRunsBtn(RUNS_CTX.demo)}</p>`, "Paste my key");
  openPay("The free runs are used up. Paste your own Comfy Cloud key (or a promo code) to generate."); $("#key").focus();
}

// ---------- key
function renderKey() {
  const k = key(), b = $("#keyBadge");
  if (document.activeElement !== $("#key")) $("#key").value = k;  // never wipe a key that is being typed
  $("#keyCard").classList.toggle("attention", !!(cfg.hosted && !k && !(cfg.hosted_jobs_left > 0)));
  if (cfg.hosted && !k && !(cfg.hosted_jobs_left > 0)) { b.textContent = "free demo used up · paste your key"; b.className = "badge bad"; }
  else if (cfg.hosted && !k) { b.textContent = `demo key · ${cfg.hosted_jobs_left ?? "∞"} jobs left`; b.className = "badge warn"; }
  else if (payMode() === "free" && freeAvail()) { b.textContent = k ? "free run selected · key not charged" : cfg.trial?.used_by_you ? "using your free run" : `free run · ${cfg.trial?.runs_left ?? cfg.hosted_jobs_left} left`; b.className = "badge ok"; }
  else if (payMode() === "judge" && judge()) { b.textContent = `promo code · ${judgeLeft ?? "?"} jobs left`; b.className = "badge warn"; }
  else if (payMode() === "key" && !k && payPick() === "key") { b.textContent = "key selected · paste it"; b.className = "badge bad"; }
  else if (k) { b.textContent = "saved in this browser"; b.className = "badge ok"; }
  else if (judge()) { b.textContent = `promo code · ${judgeLeft ?? "?"} jobs left`; b.className = "badge warn"; }
  else if (cfg.trial && trialOk()) { b.textContent = cfg.trial.used_by_you ? "using your free run" : `optional · ${cfg.trial.runs_left} of ${cfg.trial.runs_total} free runs left`; b.className = "badge ok"; }
  else if (cfg.trial) { b.textContent = "free runs used · paste your key"; b.className = "badge bad"; }
  else if (cfg.byok_only && cfg.public) { b.textContent = "required to generate"; b.className = "badge bad"; }
  else { b.textContent = "not set"; b.className = "badge"; }
  renderReqRuns();
  window.uxSync?.();
}
const saveKey = e => { const v = e.target.value.trim();
  if (v === key()) return;
  if (v) { localStorage.setItem(KEY_LS, v); setPayPick("key"); } else localStorage.removeItem(KEY_LS);
  renderKey(); };
$("#key").addEventListener("input", saveKey);   // paste + Generate without leaving the field still uses it
$("#key").addEventListener("change", saveKey);
// promo code: a visible text field (case kept, server compares exactly), checked as you type or paste; codes are never logged
function promoStatus(kind, text) {
  const st = $("#judgeSt"), inp = $("#judge");
  st.hidden = !kind; st.textContent = text || ""; st.className = "promo-st" + (kind ? " " + kind : "");
  const rq = $("#reqRunsPromo"), gone = kind === "bad" && /used up|expired/.test(text || "");  // used-up / expired code: one tap to ask for more
  if (rq) { rq.hidden = !gone; if (gone) rq.href = reqRunsHref(/expired/.test(text) ? RUNS_CTX.promoGone : RUNS_CTX.promo); }
  inp.classList.toggle("invalid", kind === "bad"); inp.setAttribute("aria-invalid", kind === "bad" ? "true" : "false");
}
let promoSeq = 0, promoTimer = null;
async function checkPromo(v, loud, onLoad) {
  const seq = ++promoSeq;
  if (!v) { localStorage.removeItem(JUDGE_LS); judgeLeft = null; promoStatus(null); renderKey(); return; }
  promoStatus("wait", "Checking…");
  try {
    const r = await fetch("/api/check-key", {method: "POST", headers: {"X-Judge-Code": v}});
    const d = await r.json().catch(() => ({}));
    if (seq !== promoSeq) return;  // a newer keystroke won
    if (!r.ok) throw Object.assign(new Error("That code isn't valid"), {bad: true});
    judgeLeft = d.judge_jobs_left;
    if (!(d.judge_jobs_left > 0)) { localStorage.removeItem(JUDGE_LS); runsOut = d.judge_jobs_total > 0 ? RUNS_CTX.promo : RUNS_CTX.promoGone;
      promoStatus("bad", d.judge_jobs_total > 0 ? "This code is used up" : "This code has expired"); renderKey(); return; }
    runsOut = null;
    localStorage.setItem(JUDGE_LS, v); if (!onLoad) setPayPick("judge"); renderKey();
    promoStatus("ok", `Code works · ${d.judge_jobs_left} jobs left`);
    if (loud) toast(`Promo code OK · ${d.judge_jobs_left}/${d.judge_jobs_total} jobs left · it now pays for your runs`, 4500);
  } catch (e) {
    if (seq !== promoSeq) return;
    if (e.bad) { localStorage.removeItem(JUDGE_LS); judgeLeft = null; renderKey(); }
    promoStatus(e.bad ? "bad" : "wait", e.bad ? "That code isn't valid" : "Couldn't check the code, try again");
  }
}
$("#judge").addEventListener("input", () => {
  clearTimeout(promoTimer); const v = $("#judge").value.trim();
  if (!v) return checkPromo("");
  promoStatus(null); promoTimer = setTimeout(() => checkPromo(v), 600);
});
$("#judge").addEventListener("blur", () => { const t = $("#judge").value.trim(); if (t !== $("#judge").value) $("#judge").value = t; });
$("#judgeCheck").onclick = () => { clearTimeout(promoTimer); const v = $("#judge").value.trim(); $("#judge").value = v; checkPromo(v, true); };
$("#keyToggle").onclick = () => { const i = $("#key"), show = i.type === "password"; i.type = show ? "text" : "password";
  $("#keyToggle").setAttribute("aria-pressed", show); $("#keyToggle").setAttribute("aria-label", show ? "Hide key" : "Show key"); };
$("#keyForget").onclick = e => { e.preventDefault(); localStorage.removeItem(KEY_LS); if (payPick() === "key") setPayPick(null); renderKey(); toast("Key removed from this browser"); };
$("#keyCheck").onclick = async () => {
  const v = $("#key").value.trim(); if (v) { localStorage.setItem(KEY_LS, v); setPayPick("key"); }
  const b = $("#keyCheck"); b.disabled = true; b.textContent = "Checking…";
  try { const r = await api("/api/check-key", {method: "POST", headers: {"X-Comfy-Key": key(), "X-Pay-Mode": "key"}});
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
$("#fileCam").onchange = e => setFile(e.target.files[0]);  // phones: record a new take with the front camera
let fileDur = null;
// the own-face box does not disable Analyze: pressing it unticked shows a visible error on the box instead (consentError)
const canStart = () => { $("#start").disabled = !(file && !(cfg.max_upload_s && fileDur > cfg.max_upload_s + 0.5));
  window.uxSync?.(); };  // ux.js: step states, the reason under the button, the preview
const CONSENT_ERR = "Tick the box to confirm, then tap Analyze.";
function consentError(on) {  // shown only after a tap on Analyze; cleared as soon as the box is ticked
  const row = $("#consentRow"), box = $("#consent"), err = $("#consentErr");
  row.classList.toggle("invalid", on); box.setAttribute("aria-invalid", on ? "true" : "false");
  // role=alert region: filling it makes screen readers say "Error: ..."; the "!" icon means it never relies on colour alone
  err.innerHTML = on ? `<span class="consent-err-ic" aria-hidden="true">!</span><span class="vh">Error: </span>${CONSENT_ERR}` : "";
  if (!on) return;
  box.scrollIntoView({block: "nearest"});
  box.focus({preventScroll: true});
}
$("#consent").onchange = () => { if ($("#consent").checked) consentError(false); canStart(); };
// runs before every Analyze handler (app.js, scene.js and dub.js all start from #start): never a silent no-op
window.addEventListener("click", e => {
  if (!e.target.closest?.("#start") || $("#consent").checked || $("#start").disabled) return;
  e.preventDefault(); e.stopImmediatePropagation(); consentError(true);
}, true);
// too big / too long: checked the moment a file is picked, against the server's limits (/api/config). The clip is
// dropped (no stale preview, nothing uploads) and a persistent role=alert message stays in step 1 until a good file.
const mbOf = n => n / 1048576;
const num = (v, lim) => (Math.round(v) > lim ? Math.round(v) : Math.ceil(v * 10) / 10);  // "60.4 MB", never "60 MB" over a 60 MB limit
function fileError(title, help) {
  const el = $("#fileErr");
  el.innerHTML = title ? `<span class="vh">Error: </span>${esc(title)}<small>${esc(help)}</small>` : "";
  if (title) el.scrollIntoView({block: "nearest"});
}
function rejectFile(title, help) {
  file = null; fileDur = null;
  const box = $("#dropFile"), v = $("video", box);
  if (v?.src) URL.revokeObjectURL(v.src);
  box.hidden = true; box.innerHTML = "";
  ["#file", "#fileCam"].forEach(s => { $(s).value = ""; });  // picking the same file again fires change again
  fileError(title, help);
  canStart();
}
const tooBigMsg = mb => [`This video is ${num(mb, cfg.max_upload_mb)} MB. The limit is ${cfg.max_upload_mb} MB.`,
  `Record a shorter take (about ${Math.min(20, cfg.max_upload_s || 20)} s), or set your camera to 1080p.`];
const tooLongMsg = s => [`This video is ${num(s, cfg.max_upload_s)} seconds. The limit is ${cfg.max_upload_s} seconds.`,
  "Trim it in your phone's Photos app, or record a shorter take."];
function setFile(f) {
  if (!f) return;
  if (cfg.max_upload_mb && f.size > cfg.max_upload_mb * 1048576) return rejectFile(...tooBigMsg(mbOf(f.size)));
  fileError("");  // a file within the size limit replaces any earlier error
  file = f; fileDur = null;
  const box = $("#dropFile"); box.hidden = false;
  box.innerHTML = `<video src="${URL.createObjectURL(f)}" muted playsinline></video><div><b>${esc(f.name)}</b><br><small class="muted" id="fileMeta">${(f.size / 1e6).toFixed(1)} MB</small></div>`;
  $("video", box).onloadedmetadata = e => {
    if (file !== f) return;  // a newer pick won
    const d = e.target.duration;
    if (cfg.max_upload_s && d > cfg.max_upload_s + 0.5) return rejectFile(...tooLongMsg(d));
    fileDur = d;
    $("#fileMeta").textContent = `${(f.size / 1e6).toFixed(1)} MB · ${d.toFixed(1)} s`;
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
  b.disabled = false; b.textContent = "Analyze my clip — free"; canStart();
};

const TERMINAL = ["success", "error", "non_retryable_error", "lost", "cancelled"];
const jobSecs = j => Math.round(!TERMINAL.includes(j.status) && j.started ? Date.now() / 1000 - j.started : (j.elapsed_s || 0));
// ---------- project rendering
function render() {
  if (!S) return;
  // pipeline details only once something has run (a fresh upload shows nothing debug-like)
  $("#progressCard").hidden = !Object.values(S.steps || {}).some(st => st.status && st.status !== "pending") && !S.busy && !(S.jobs || []).length;
  $(".hero").hidden = $("#uploadCard").hidden = true; $("#newProject").hidden = false;
  const busy = S.busy && !S.stale;  // a stale run (server restarted / no progress) is not "working"
  $("#busyBadge").textContent = S.stale ? "stalled" : busy ? `${busy}…` : S.error ? "error" : S.plan ? "Analyzed" : "";  // never a bare "idle"
  $("#busyBadge").hidden = !$("#busyBadge").textContent;
  $("#busyBadge").className = "badge " + (busy ? "warn" : S.error || S.stale ? "bad" : "ok");
  $("#steps").innerHTML = Object.entries(STEP_NAMES).map(([k, n]) => { const st = S.steps[k] || {};
    return `<li class="${st.status || "pending"}"><div class="t"><span class="dot"></span>${n}</div><div class="m" title="${esc(st.msg)}">${esc(st.msg || st.status || "")}</div></li>`; }).join("");
  $("#jobs").innerHTML = (S.jobs || []).slice().reverse().map(j => `<div class="job ${j.status}"><div style="flex:1;min-width:0"><div>${esc(j.label)}</div>
    ${["success", "error", "non_retryable_error", "lost", "cancelled"].includes(j.status) ? "" : '<div class="bar"></div>'}</div>
    <div class="st">${esc(j.status)} · ${jobSecs(j)}s${j.status === "success" && j.prompt_id ? ` · <a href="/api/projects/${S.id}/workflow/${encodeURIComponent(j.prompt_id)}" download title="the exact ComfyUI API graph this job ran">workflow</a>` : ""}</div></div>`).join("");
  const errText = S.error || (S.stale ? "This run stopped making progress (the server may have restarted). Tap Retry." : "");
  $("#err").hidden = !errText; $("#err").textContent = errText;
  $("#retryRow").hidden = !S.retry;
  if (S.plan) { renderCams(); renderEstimate(); $("#planCard").hidden = false; }
  if (S.renders?.length) renderResult();
  $("#generate").disabled = $("#renderFree").disabled = $("#anglesBtn").disabled = !!busy || !S.plan;
}

// angle picker tiles: an example of each angle from ONE demo take of Alek (only the angle differs), an honest
// strength label and a one-line hint. Free: static stills + two 2 s loops from existing clips, no Comfy jobs.
const ANGLE_INFO = {
  right90: {img: "side", vid: "side", s: "Strong", hint: "Side: strong profile, the clearest change"},
  left90: {img: "side", vid: "side", s: "Strong", flip: true, hint: "Side: strong profile, the clearest change"},
  right45: {img: "threequarter", s: "Subtle", hint: "3/4 right: small turn, can look close to your take"},
  left45: {img: "threequarter", s: "Subtle", hint: "3/4 left: small turn, can look close to your take"},
  wide: {img: "wide", vid: "wide", s: "Strong", hint: "Wide: pulls back, shows the room"},
  closeup: {img: "closeup", s: "Strong", hint: "Close-up: tighter on the face"},
  lowangle: {img: "lowangle", s: "Strong", hint: "Low angle: camera below, looking up"},
  aerial: {img: "high", s: "Strong", hint: "High angle: camera above, looking down"},
};
function angleTile(cam, k, v, on) {
  const a = ANGLE_INFO[k] || {s: "", hint: v.label}, base = "/gallery/media/angles/ang_";
  const media = a.vid ? `<video src="${base}${a.vid}.mp4" poster="${base}${a.img}.jpg" muted loop playsinline autoplay preload="none" aria-hidden="true"></video>`
    : a.img ? `<img src="${base}${a.img}.jpg" alt="" loading="lazy">` : "";
  return `<button type="button" class="preset ang ${on ? "on" : ""}" role="radio" aria-checked="${on}" data-cam="${cam}" data-still="${k}" data-label="${esc(v.label)}" title="${esc(v.variants.join(" · "))}">
    <span class="ang-m${a.flip ? " flip" : ""}">${media}<span class="ang-s ${a.s === "Subtle" ? "sub" : ""}">${a.s}</span></span>
    <span class="ang-n">${esc(v.label)}</span><span class="ang-h">${esc(a.hint.replace(/^[^:]+: /, ""))}</span></button>`;
}
function renderCams() {
  $("#anglesRow").hidden = reshoot();
  const eng = `<div class="seg engine" id="engineSel">
      <button type="button" data-engine="reshoot" class="${reshoot() ? "on" : ""}" aria-pressed="${reshoot()}"><span class="seg-l">Re-shoot<span class="seg-tag">Recommended</span></span><small>Follows your real motion and lips</small></button>
      <button type="button" data-engine="idlora" class="${reshoot() ? "" : "on"}" aria-pressed="${!reshoot()}"><span class="seg-l">Classic</span><small>Still image + ID LoRA</small></button></div>`;
  if (reshoot()) {
    const presets = Object.entries(cfg.reshoot_angles || {});
    $("#engine").innerHTML = eng;
    const fr = S.framing || {};
    $("#cams").innerHTML = (fr.note ? `<p class="framing-note" role="note"><svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M9 5v14M15 5v14"/></svg><span>${esc(fr.note)}</span></p>` : "") +
      ["B", "C"].map(cam => { const cur = S.cam_stills[cam];
      return `<div class="cam"><div class="cam-h"><span class="tag ${cam}">CAM ${cam}</span>${esc(camLabel(cur))}</div>
        <div class="ang-tiles" role="radiogroup" aria-label="Camera ${cam} angle">${presets.map(([k, v]) => angleTile(cam, k, v, k === cur)).join("")}</div>
        <div class="muted small">Each insert on CAM ${cam} gets its own camera move (${esc((cfg.reshoot_angles?.[cur]?.variants || []).join(" → "))}).</div></div>`; }).join("");
  } else {
  const stills = Object.entries(S.stills || {});
  $("#cams").innerHTML = ["B", "C"].map(cam => { const cur = S.cam_stills[cam];
    const grid = stills.length ? `<div class="stills">${stills.map(([k, v]) =>
      `<button class="still ${k === cur ? "on" : ""}" aria-pressed="${k === cur}" data-cam="${cam}" data-still="${k}" title="${esc(v.label)}"><img src="${v.url}" loading="lazy"><span>${esc(v.label)}</span></button>`).join("")}</div>`
      : `<div class="empty">Angle stills come from 1 Qwen job (8 views of you).<br>Default for CAM ${cam}: <b>${esc(cur)}</b></div>`;
    return `<div class="cam"><div class="cam-h"><span class="tag ${cam}">CAM ${cam}</span>${esc(S.stills?.[cur]?.label || cur)}</div>${grid}</div>`; }).join("");
  $("#engine").innerHTML = eng;
  }
  $$("#engineSel button").forEach(b => b.onclick = async () => {
    S = await api(`/api/projects/${S.id}/cams`, {json: {engine: b.dataset.engine}}); render();
    toast(b.dataset.engine === "reshoot" ? "Re-shoot: AI re-films your real take from new angles (motion + lips follow you)" : "Classic: Qwen angle still + LTX-2.3 ID LoRA"); });
  $$("#cams .still, #cams .preset").forEach(b => b.onclick = async () => {
    S = await api(`/api/projects/${S.id}/cams`, {json: {[b.dataset.cam]: b.dataset.still}}); render();
    toast(`CAM ${b.dataset.cam} → ${b.classList.contains("preset") ? (b.dataset.label || b.textContent) : b.title}. Applies to new generations / re-rolls.`); });
  $("#impChunk").innerHTML = S.plan.chunks.map(c => `<option value="${c.id}">${c.id} · ${fmt(c.start)} “${esc(c.words.slice(0, 28))}…”</option>`).join("");
}

function renderEstimate() {
  const e = S.estimate; if (!e) return;
  const has = e.jobs > 0, nIns = S.plan.shots.filter(s => s.chunk && s.cam !== "A").length;
  // no inserts at all is not "everything is generated": say why instead of a silent $0.00
  const none = !nIns ? `No AI inserts planned${S.plan.note ? `: ${esc(S.plan.note)}` : " for this clip."}` : "every insert already has a clip — rendering is free";
  $("#estimate").innerHTML = `<div><div class="big">${has ? `≈ $${e.usd.toFixed(2)}` : "$0.00"}</div>
    <small>${has ? `${e.jobs} Comfy Cloud job${e.jobs > 1 ? "s" : ""} × ~$${e.per_job_usd.toFixed(3)} · ${e.angle_jobs ? "1 angle job + " : ""}${e.camera_jobs.length} ${e.engine === "reshoot" ? "re-shoot" : "camera"} clip${e.camera_jobs.length === 1 ? "" : "s"}` : none}</small>
    ${has && S.plan.note ? `<small class="plan-note" style="display:block;margin-top:4px">${esc(S.plan.note)}</small>` : ""}</div>
    <small>${nIns} AI inserts · ${S.plan.duration.toFixed(1)}s</small>`;
  $("#generate").textContent = !has ? "Generate (nothing to do)" : demoOut(e.jobs) ? "Paste your Comfy key to generate" : `Generate · $${e.usd.toFixed(2)}`;
  $("#generate").style.display = has ? "" : "none";
}

$("#generate").onclick = async () => {
  const e = S.estimate;
  if (!canPay()) return payMissing();
  if (demoOut(e.jobs)) return needOwnKey(e.jobs);
  const ok = await paidConfirm("Spend on Comfy Cloud?",
    `<p>This submits <b>${e.jobs}</b> job(s) to Comfy Cloud, paid with <b>${esc(payLabel())}</b> — about <b>$${e.usd.toFixed(2)}</b>.</p><ul>
    ${e.angle_jobs ? "<li>Qwen-Image-Edit: 8 angle stills from your best frame</li>" : ""}
    ${e.camera_jobs.map(j => `<li>${e.engine === "reshoot" ? `LTX-2.3 CrossView re-shoot · CAM ${j.cam} (${esc(camLabel(S.cam_stills[j.cam]))})` : `LTX-2.3 ID LoRA · CAM ${j.cam}`} · ${j.chunk}</li>`).join("")}</ul>
    <p class="note">Typical wait: 1–3 min per job (they run in parallel).</p>`, `Confirm · $${e.usd.toFixed(2)}`);
  if (!ok) return;
  try { S = await api(`/api/projects/${S.id}/generate`, {json: {confirm: true}, withKey: true}); render(); poll(); }
  catch (err) { toast(err.message, 4500); }
};
$("#anglesBtn").onclick = async () => {
  if (!canPay()) return payMissing();
  const ok = await paidConfirm("Generate angle stills?", `<p>1 Qwen-Image-Edit job → 8 views of you (close-up, wide, 45°/90° left/right, low, aerial). About <b>$${(cfg.cost_per_job_usd || 0.03).toFixed(2)}</b>. Then pick one per camera.</p>`, "Generate stills");
  if (!ok) return;
  try { S = await api(`/api/projects/${S.id}/angles`, {json: {confirm: true}, withKey: true}); render(); poll(); }
  catch (e) { toast(e.message, 4500); }
};
$("#renderFree").onclick = () => doRender();
$("#rerender").onclick = () => doRender();
// Retry after a restart / stalled run: free (resumes finished Comfy jobs, re-renders), never a new paid job
$("#retryBtn").onclick = async () => {
  const b = $("#retryBtn"); b.disabled = true;
  try { S = await api(`/api/projects/${S.id}/retry`, {method: "POST", withKey: true}); render(); poll(); toast("Retrying — free"); }
  catch (e) { toast(e.message, 5000); }
  b.disabled = false;
};
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
// "Download ComfyUI workflow": the exact API graph(s) a project submitted to Comfy Cloud (zip if several)
const _wfSeen = {};
async function wfLink(el, kind, id, stamp) {
  if (!el || !id) return;
  const k = `${kind}/${id}`;
  if (_wfSeen[k] === stamp) return;
  _wfSeen[k] = stamp;
  const r = await api(`/api/${kind}/${id}/workflows`).catch(() => null);
  const n = r?.workflows?.length || 0;
  el.hidden = !n;
  if (!n) return;
  el.href = n === 1 ? r.workflows[0].url : r.zip;
  el.setAttribute("download", n === 1 ? `multicam_${r.workflows[0].name}` : "multicam-comfyui-workflows.zip");
  el.textContent = n === 1 ? "ComfyUI workflow" : `ComfyUI workflows (${n}, zip)`;
  el.title = "The exact ComfyUI API graph(s) this project ran on Comfy Cloud — open in ComfyUI or re-run them";
}
let shownSrc = null;
function renderResult() {
  $("#resultCard").hidden = false;
  const r = S.renders[S.renders.length - 1];
  $("#verBadge").textContent = `v${r.v} · ${r.shots.filter(s => s.src === "generated").length} AI shots`;
  setPlayerSrc();
  $("#download").href = $("#labels").checked ? r.labeled_url : r.clean_url;
  $("#download").setAttribute("download", `multicam_v${r.v}${$("#labels").checked ? "_labeled" : ""}.mp4`);
  wfLink($("#wfDownload"), "projects", S.id, `${r.v}|${(S.jobs || []).length}`);
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
    <div class="camsel">${["B", "C", "A"].map(c => `<button data-cam="${c}" class="${s.cam === c ? "on" : ""}" aria-pressed="${s.cam === c}">${c === "A" ? "Drop (use A)" : `CAM ${c}`}${c !== "A" && !clips[c] ? " · needs clip" : ""}</button>`).join("")}</div>
    <div class="camsel">
      <button data-act="reroll" ${s.cam === "A" ? "disabled" : ""}>Re-roll CAM ${s.cam === "A" ? s.planned_cam : s.cam} · $${jobUsd().toFixed(2)}</button>
      <select id="rerollStill" ${s.cam === "A" ? "disabled" : ""}>${(reshoot() ? Object.entries(cfg.reshoot_angles || {}) : Object.entries(S.stills || {})).map(([k, v]) =>
        `<option value="${k}" ${k === S.cam_stills[s.cam] ? "selected" : ""}>angle: ${esc(v.label)}</option>`).join("")}</select>
      ${s.planned_cam !== s.cam ? `<button data-cam="${s.planned_cam}">↺ Reset</button>` : ""}
    </div>`;
  $$("button[data-cam]", el).forEach(b => b.onclick = async () => {
    S = await api(`/api/projects/${S.id}/shots`, {method: "PATCH", json: {edits: {[s.i]: {cam: b.dataset.cam}}}}); render();
    toast(b.dataset.cam === "A" ? "Insert dropped — hit Re-render (free)" : `Shot → CAM ${b.dataset.cam}${clips[b.dataset.cam] ? " — Re-render (free)" : " — no clip yet: re-roll to generate it"}`); });
  const rb = $('button[data-act="reroll"]', el);
  if (rb) rb.onclick = async () => {
    if (!canPay()) return payMissing();
    if (demoOut(1)) return needOwnKey(1);
    const still = $("#rerollStill")?.value; const cam = s.cam;
    const ok = await paidConfirm("Re-roll this shot?", `<p>1 ${reshoot() ? "LTX-2.3 CrossView re-shoot (next camera move)" : "LTX-2.3 ID LoRA"} job for <b>CAM ${cam}</b> on “${esc(s.words)}” with a new seed${still && still !== S.cam_stills[cam] ? ` and the <b>${esc(camLabel(still))}</b> angle` : ""}. About <b>$${jobUsd().toFixed(2)}</b>. The old version is kept.</p>`, "Re-roll");
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
      <div class="row gap"><a class="btn" href="${r.page}" target="_blank">Open page</a><a class="btn" href="${r.zip}" download>Download .zip</a></div>
      <a class="btn" href="${r.x_intent}" target="_blank" rel="noopener">Post on X · #OpenSourceHiggsfield</a>`;
  } catch (e) { toast(e.message, 4000); }
  b.disabled = false; b.textContent = "Share";
};

function renderBanner() {
  const b = $("#banner");
  $("#judgeBox").hidden = !cfg.judge_code;
  if (cfg.public && cfg.trial) {
    const t = cfg.trial, url = cfg.get_key_url || "https://platform.comfy.org/profile/api-keys";
    const open = t.runs_left > 0 && !t.used_by_you;
    if (cfg.max_upload_s) $("#dropHint").textContent = `Vertical selfie video, up to ${cfg.max_upload_s} s.`;
    $("#keyNote").innerHTML = `${open ? "<b>Optional while free runs last:</b> leave it empty to use your free run." : "<b>Bring your own key:</b> generation runs on <b>your</b> Comfy Cloud credits."} Get a key at <a href="${url}" target="_blank" rel="noopener">platform.comfy.org → API keys</a>. It's saved only in <b>this browser</b> (localStorage) and sent per request to this server, which forwards it to Comfy Cloud and <b>never stores or logs it</b>. <a href="#" id="keyForget">Forget key</a>`;
    $("#keyForget").onclick = e => { e.preventDefault(); localStorage.removeItem(KEY_LS); if (payPick() === "key") setPayPick(null); renderKey(); toast("Key removed from this browser"); };
    b.hidden = false; b.classList.toggle("out", !open && !t.used_by_you);
    b.innerHTML = open
      ? `<b>${t.runs_left} of ${t.runs_total} free runs left</b> · one per visitor · <a href="#keyCard" id="bannerKey">or use your own key</a>`
      : t.used_by_you
        ? `<b>You're using your free run</b> (${t.runs_left} of ${t.runs_total} left for others) · for more, <a href="#keyCard" id="bannerKey">use your own Comfy key</a>${cfg.judge_code ? " or a promo code" : ""}`
        : `<b>The free runs are used up (0 of ${t.runs_total} left).</b> <a href="#keyCard" id="bannerKey"><b>Paste your own Comfy Cloud key</b></a> (<a href="${url}" target="_blank" rel="noopener">get one</a>)${cfg.judge_code ? " or a promo code" : ""} to generate. Upload, analyze, plan, edit and render stay free, or <a href="${cfg.repo_url}" target="_blank" rel="noopener">run it yourself</a>. ${reqRunsBtn(RUNS_CTX.trial)}`;
    $("#bannerKey").onclick = e => { e.preventDefault(); openPay(); $("#key").focus(); };
    return;
  }
  if (cfg.public && cfg.byok_only) {
    const per = (cfg.cost_usd?.[cfg.engine] ?? cfg.cost_per_job_usd ?? 0.05), url = cfg.get_key_url || "https://platform.comfy.org/profile/api-keys";
    $("#keyNote").innerHTML = `<b>Bring your own key:</b> generation runs on <b>your</b> Comfy Cloud credits. Get a key at <a href="${url}" target="_blank" rel="noopener">platform.comfy.org → API keys</a>. It's saved only in <b>this browser</b> (localStorage) and sent per request to this server, which forwards it to Comfy Cloud and <b>never stores or logs it</b>. Free steps (transcribe, plan, edit, render) don't need it. <a href="#" id="keyForget">Forget key</a>`;
    $("#keyForget").onclick = e => { e.preventDefault(); localStorage.removeItem(KEY_LS); if (payPick() === "key") setPayPick(null); renderKey(); toast("Key removed from this browser"); };
    if (cfg.max_upload_s) $("#dropHint").textContent = `Vertical selfie video, up to ${cfg.max_upload_s} s.`;
    b.hidden = false; b.classList.remove("out");
    b.innerHTML = `<b>Bring your own Comfy Cloud key.</b> Try the free steps (upload, analyze, plan, edit, render) right away; to generate AI cameras,
      <a href="#keyCard" id="bannerKey"><b>paste your own key</b></a> (<a href="${url}" target="_blank" rel="noopener">get one</a>) — ≈ $${per.toFixed(3)} per clip, ≈ $${(4 * per).toFixed(2)} per video, billed by Comfy.
      ${cfg.max_upload_s ? `Uploads ≤ ${cfg.max_upload_s}s` : ""}${cfg.ttl_minutes ? ` · deleted after ${cfg.ttl_minutes} min` : ""} · or <a href="${cfg.repo_url}" target="_blank" rel="noopener">run it yourself</a>.`;
    $("#bannerKey").onclick = e => { e.preventDefault(); openPay(); $("#key").focus(); };
    return;
  }
  if (cfg.hosted) $("#keyNote").innerHTML = `${(cfg.hosted_jobs_left ?? 0) > 0 ? "Optional here: leave it empty to use the capped demo credits." : "<b>Required now</b>: the free demo credits are used up — get a key at <a href=\"https://platform.comfy.org/profile/api-keys\" target=\"_blank\" rel=\"noopener\">platform.comfy.org → API keys</a>."} If you paste your own key it's saved only in <b>this browser</b> (localStorage) and sent per request to this server, which forwards it to Comfy Cloud and <b>never stores or logs it</b>. <a href="#" id="keyForget">Forget key</a>`,
    $("#keyForget").onclick = e => { e.preventDefault(); localStorage.removeItem(KEY_LS); if (payPick() === "key") setPayPick(null); renderKey(); toast("Key removed from this browser"); };
  if (cfg.max_upload_s) $("#dropHint").textContent = `Vertical selfie video, up to ${cfg.max_upload_s} s.`;
  if (!cfg.hosted) { b.hidden = true; return; }
  const left = cfg.hosted_jobs_left ?? 0, tot = cfg.hosted_jobs_total || 1;
  b.hidden = false;
  b.classList.toggle("out", left <= 0);
  const per = (cfg.cost_usd?.[cfg.engine] ?? cfg.cost_per_job_usd ?? 0.05);
  if (left <= 0) {
    b.innerHTML = `<b>Free demo runs are used up.</b> <a href="#keyCard" id="bannerKey"><b>Paste your own Comfy Cloud key</b></a> to keep going
      (≈ $${per.toFixed(2)} per clip, ≈ $${(4 * per).toFixed(2)} per video, billed to you by Comfy; the key stays in your browser) —
      or <a href="${cfg.repo_url}" target="_blank" rel="noopener">run Multicam yourself</a>. Free steps (analyze, plan, edit, render) still work. ${reqRunsBtn(RUNS_CTX.demo)}`;
    $("#bannerKey").onclick = e => { e.preventDefault(); openPay(); $("#key").focus(); };
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
  if (S.busy && !S.stale) poll(); else { loadRecent(); if (cfg.hosted || cfg.trial) refreshCfg(); } }, 1200); }
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
  if (judge()) { $("#judge").value = judge(); checkPromo(judge(), false, true); }  // the saved code stays visible with its status
})();
