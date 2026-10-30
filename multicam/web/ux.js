// First-run UX layer (ux.js): the numbered 3-step flow, required-field reasons on the single primary
// button, the access choice (free run / own key / promo code), the sticky preview with example outputs,
// and "Try with sample clip". UI only: it calls the same app.js helpers and never hits a paid endpoint.
(() => {
  const MEDIA = "/gallery/media/";
  const EX = {  // precomputed outputs from docs/media (free to watch)
    // clean (unlabeled) exports; camera names are UI chips under the player, not burned-in text
    talk: {loop: "ex_talk.mp4", full: "ex_talk.mp4", wide: false, chips: [["A", "Real take"], ["B", "AI · side"], ["C", "AI · wide"]],
      cap: "Camera A is your real take. B and C are AI re-shoots of the same moment."},
    music: {loop: "ex_music.mp4", full: "ex_music.mp4", wide: false, chips: [["A", "AI base"], ["B", "AI · side"]],
      cap: "The base performance (camera A) was AI-generated, not a real phone take. B is a Multicam re-shoot of it, cut on the beat."},
    scene: {loop: "ex_scene.mp4", full: "ex_scene.mp4", wide: false, chips: [["A", "Real take"], ["A", "AI · side"], ["B", "AI · single"], ["B", "AI · over the shoulder"]],
      cap: "One take becomes a two-person scene: you, and your other self answering."},
    dub: {loop: "ex_dub.mp4", full: "ex_dub.mp4", wide: true, chips: [["EN", "Original"], ["DE", "Dubbed"], ["ES", "Dubbed"], ["HR", "Experimental"]],
      cap: "The same take in German, Spanish and Croatian. Your face and voice, new lip motion."},
  };
  const NOTE = {
    talk: "Talk: nothing to set, the defaults work. Add your exact script under Advanced for word-perfect cuts.",
    music: "Music: upload a clip with the song audible, like you singing or lip-syncing (5–30 s). Cuts land on the beat, your real audio stays. Lyrics under Advanced are optional.",
    scene: "Scene: choose who answers you. Everything else has a sensible default.",
    dub: "Dub: pick the language. Speak English in your take.",
  };
  const NAME = {talk: "Talk", music: "Music", scene: "Scene", dub: "Dub"};
  const sv = $("#stageVideo");
  let view = "ex", mine = null, mineFile = null, access = null, playingFull = false;

  // ---------- preview stage
  // finished Scene / Dub video in a project: shown big in the right column, cameras as chips under it.
  // (Talk keeps its own player: the cut-list playhead is synced to it.)
  function resultVideo() {
    if (!document.body.classList.contains("in-project")) return null;
    for (const [box, vid, edl] of [["#scResult", "#scPlayer", "#scEdl"], ["#dbResult", "#dbPlayer", null]]) {
      const b = $(box), v = $(vid);
      if (b && !b.hidden && !b.closest("[hidden]") && v && v.getAttribute("src")) return {v, edl: edl && $(edl)};
    }
    return null;
  }
  // the stage (right column) only exists on wide screens; on phones/tablets it is display:none, so a result must
  // never be loaded into it there (it used to autoplay with sound behind the visible player: double audio)
  const wideStage = window.matchMedia("(min-width: 981px)");
  function stageResult() {
    const r = resultVideo();
    document.body.classList.toggle("has-result", !!r);
    if (!r || !wideStage.matches) {
      if (sv.dataset.result) { sv.pause(); delete sv.dataset.result; sv.dataset.src = ""; sv.removeAttribute("src"); sv.load(); stage(); }
      else if (r && !sv.paused) sv.pause();
      return;
    }
    const src = r.v.getAttribute("src");
    if (sv.dataset.src !== src) {
      r.v.pause();  // the in-card copy is hidden on desktop: it must not keep playing
      sv.pause(); sv.autoplay = false;  // a result never starts by itself: the visitor presses play
      sv.dataset.src = src; sv.dataset.result = "1"; sv.src = src; sv.muted = false; sv.controls = true; sv.loop = false;
      $("#stageTag").textContent = "Your result"; $("#stageTabs").hidden = true; $("#stagePlay").hidden = true;
      $("#stageFrame").classList.toggle("wide", r.v.videoWidth > r.v.videoHeight);
      $("#stageCap").textContent = "Download it, or edit the lines and re-render for free.";
    }
    const cams = r.edl ? [...new Set($$(".sc-shot-chip", r.edl).map(c => c.textContent.trim()))] : [];
    const html = cams.map(c => { const [k, ...rest] = c.split(" "); return `<span class="chip"><b>${esc(k)}</b>${esc(rest.join(" ") || "camera")}</span>`; }).join("");
    if ($("#stageChips").innerHTML !== html) $("#stageChips").innerHTML = html;
    $("#stageChips").hidden = !html;
  }
  window.uxStageResult = stageResult;
  wideStage.addEventListener("change", () => stageResult());

  // ---------- one video with sound at a time (Scene, Talk, Dub, Music, examples)
  // a video that starts playing with sound pauses every other video; muted preview loops paused that way
  // resume once nothing with sound plays. Hidden tab: everything pauses.
  function soundPlaying(except) { return $$("video").some(v => v !== except && !v.paused && !v.muted); }
  function soloSound(e) {
    const v = e.target;
    if (!(v instanceof HTMLVideoElement) || v.paused || v.muted) return;
    $$("video").forEach(o => { if (o !== v && !o.paused) { if (o.muted && o.loop) o.dataset.resume = "1"; o.pause(); } });
  }
  function resumeLoops(e) {
    const v = e.target;
    if (!(v instanceof HTMLVideoElement) || v.muted || soundPlaying() || document.hidden) return;
    $$("video[data-resume]").forEach(o => { delete o.dataset.resume; if (o.offsetParent && o.muted) o.play().catch(() => {}); });
  }
  document.addEventListener("play", soloSound, true);
  document.addEventListener("volumechange", soloSound, true);
  document.addEventListener("pause", resumeLoops, true);
  document.addEventListener("ended", resumeLoops, true);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) $$("video").forEach(v => { if (!v.paused) { if (v.muted && v.loop) v.dataset.resume = "1"; v.pause(); } });
    else if (!soundPlaying()) $$("video[data-resume]").forEach(o => { delete o.dataset.resume; if (o.offsetParent && o.muted) o.play().catch(() => {}); });
  });
  window.uxSoundPlaying = soundPlaying;
  function stage() {
    if (sv.dataset.result) return;
    const ex = EX[mode] || EX.talk;
    $("#stageTabs").hidden = !mine;
    $$("#stageTabs button").forEach(b => { b.classList.toggle("on", b.dataset.v === view); b.setAttribute("aria-pressed", b.dataset.v === view); });
    const showMine = view === "mine" && mine;
    const src = showMine ? mine : MEDIA + (playingFull ? ex.full : ex.loop);
    $("#stageTag").textContent = showMine ? "Your clip" : `Example output · ${NAME[mode]}`;
    $("#stageCap").textContent = showMine ? "This is what you'll upload." : ex.cap;
    $("#stageChips").hidden = !!showMine;
    $("#stageChips").innerHTML = (ex.chips || []).map(([k, v]) => `<span class="chip"><b>${k}</b>${v}</span>`).join("");
    $("#stageFrame").classList.toggle("wide", !showMine && ex.wide);
    $("#stagePlay").hidden = !!showMine;
    if (sv.dataset.src !== src) { sv.dataset.src = src; sv.src = src; }
    sv.muted = !(playingFull && !showMine); sv.controls = !!(showMine || playingFull); sv.loop = !playingFull;
    // only the muted example loop starts by itself, and only while the stage is actually on screen
    if (sv.muted && !playingFull && $("#stageCard").offsetParent && !soundPlaying()) sv.play?.().catch(() => {});
    else if (!$("#stageCard").offsetParent) sv.pause();
  }
  function playExample() {
    view = "ex"; playingFull = true; stage(); sv.currentTime = 0; sv.muted = false; sv.play().catch(() => {});
    if (window.matchMedia("(max-width: 980px)").matches) $("#stage").scrollIntoView({behavior: "smooth", block: "center"});
  }
  $("#stagePlay").onclick = playExample;
  $("#exampleBtn").onclick = e => { e.preventDefault(); playExample(); };
  sv.addEventListener("ended", () => { if (playingFull) { playingFull = false; stage(); } });
  $$("#stageTabs button").forEach(b => b.onclick = () => { view = b.dataset.v; playingFull = false; stage(); });

  // ---------- header sheets: payment (pill) + recent runs (clock icon). Right drawer on desktop, bottom sheet on mobile.
  const back = $("#sheetBack");
  let lastFocus = null;
  function openSheet(id) {
    $$(".sheet").forEach(x => { if (x.id !== id) x.hidden = true; });
    const el = $("#" + id); lastFocus = document.activeElement; el.hidden = false; back.hidden = false;
    requestAnimationFrame(() => { el.classList.add("in"); back.classList.add("in"); });
    document.body.classList.add("sheet-open");
    setTimeout(() => (el.querySelector("input:not([hidden]):not([type=checkbox]), .sheet-x") || el).focus?.(), 60);
  }
  function closeSheets() {
    $$(".sheet").forEach(x => { x.classList.remove("in"); x.hidden = true; }); back.classList.remove("in"); back.hidden = true;
    document.body.classList.remove("sheet-open"); $("#payReason").hidden = true; lastFocus?.focus?.();
  }
  back.onclick = closeSheets;
  $$(".sheet-x").forEach(b => b.onclick = closeSheets);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !back.hidden) closeSheets(); });
  $("#paySlot").appendChild($("#keyCard"));   // the payment card is no longer in the main flow
  $("#keySlot").remove();
  window.uxOpenPay = reason => {
    const r = $("#payReason"); r.textContent = reason || ""; r.hidden = !reason;
    openSheet("paySheet");
  };
  $("#payPill").onclick = () => window.uxOpenPay();
  // subpages link here with /#pay or /#recent to open a sheet on the home page
  function sheetFromHash() {
    const h = location.hash;
    if (h === "#pay" || h === "#recent") {
      history.replaceState(null, "", location.pathname + location.search);
      setTimeout(() => (h === "#pay" ? window.uxOpenPay() : $("#histBtn").click()), 300);
    }
  }
  window.addEventListener("hashchange", sheetFromHash);
  setTimeout(sheetFromHash, 0);
  $("#runsLeft").onclick = () => window.uxOpenPay();
  $("#keyForget2").onclick = () => $("#keyForget")?.click();
  function payPill() {
    const t = cfg.trial, el = $("#payPillT"), pill = $("#payPill");
    // the chip names exactly what the next Generate will charge (payMode in app.js), or what is missing
    let txt, out = !canPay();
    const m = payMode();
    if (m === "key") txt = key() ? "Your key" : "Add a key";
    else if (m === "judge") txt = judge() ? `Promo code${judgeLeft != null ? ` · ${judgeLeft}` : ""}` : "Enter promo code";
    else if (t && trialPer() > 1) txt = t.used_by_you ? `Free runs · ${trialPer()}/${trialPer()} used` : trialMine() > 0 ? `Free runs · ${trialMine()}/${trialPer()} left` : trialUsed() ? "Free run · yours" : "Add a key";
    else if (t) txt = t.used_by_you ? "Free run · yours" : t.runs_left > 0 ? `Free run · ${t.runs_left}/${t.runs_total}` : "Add a key";
    else { const l = cfg.hosted_jobs_left ?? 0; txt = l > 0 ? `Demo · ${l} jobs` : "Add a key"; }
    pill.dataset.pay = m;
    el.textContent = txt; pill.classList.toggle("out", out);
    pill.setAttribute("aria-label", `Payment: ${txt}. Change`);
  }

  // ---------- pipeline "Details": one collapsed line under the progress panel (desktop) / above the flow (mobile)
  function placePipe() {
    const pc = $("#progressCard"), desk = matchMedia("(min-width:981px)").matches;
    const want = desk ? $("#stage") : $("#flow");
    if (desk && pc.parentElement !== want) $("#jobPanel").after(pc);
    if (!desk && pc.parentElement !== want) $("#flow").prepend(pc);
  }
  matchMedia("(min-width:981px)").addEventListener("change", placePipe);
  $("#pipeToggle").onclick = () => { const pc = $("#progressCard"), o = !pc.classList.contains("open"); pc.classList.toggle("open", o); $("#pipeToggle").setAttribute("aria-expanded", o); };

  // ---------- sample take (loads a bundled clip into the form; never starts anything)
  $("#sampleBtn").onclick = async () => {
    const b = $("#sampleBtn"); b.disabled = true; b.textContent = "Loading sample…";
    try {
      const r = await fetch(MEDIA + "selfie.mp4"); if (!r.ok) throw new Error("sample not available");
      const blob = await r.blob();
      setFile(new File([blob], "sample_selfie.mp4", {type: "video/mp4"}));
      toast("Sample clip loaded. Nothing runs until you press the button in step 3.", 4200);
    } catch (e) { toast(e.message, 4000); }
    b.disabled = false; b.textContent = "Try with sample clip";
  };

  // ---------- access choice: free run | own key | promo code (one compact control)
  const freeOk = () => !!(cfg.trial && trialOk()) || !!(cfg.hosted && (cfg.hosted_jobs_left ?? 0) > 0);
  function setAccess(a) {
    access = a;
    $$("#access button").forEach(b => { const on = b.dataset.a === a; b.classList.toggle("on", on); b.setAttribute("aria-checked", on); });
    $("#accFree").hidden = a !== "free"; $("#accKey").hidden = a !== "key"; $("#accJudge").hidden = a !== "judge";
  }
  window.uxAccess = setAccess;
  // a click on a segment is the visitor's explicit payment choice (persisted, it wins over the defaults);
  // until then the switch shows the default: saved key, else promo code, else the free run (payMode in app.js)
  const pick = a => { setPayPick(a); setAccess(a); renderKey(); };
  $$("#access button").forEach(b => b.onclick = () => pick(b.dataset.a));
  const keyEl = $("#key"), keyFocus = keyEl.focus.bind(keyEl);
  keyEl.focus = (...a) => { setAccess("key"); keyFocus(...a); };  // "paste your key" prompts land on a visible field
  const judgeEl = $("#judge"), judgeFocus = judgeEl.focus.bind(judgeEl);
  judgeEl.focus = (...a) => { setAccess("judge"); judgeFocus(...a); };
  document.addEventListener("click", e => { if (e.target.closest("#bannerKey")) setAccess("key"); }, true);
  function syncAccess() {
    const hasFree = !!(cfg.trial || cfg.hosted), t = cfg.trial;
    $('#access [data-a="free"]').hidden = !hasFree;
    $('#access [data-a="judge"]').hidden = !cfg.judge_code;
    $("#access").style.setProperty("--n", 1 + hasFree + !!cfg.judge_code);
    $("#accFreeN").textContent = t ? (trialPer() > 1 ? (t.used_by_you ? "all used" : `${trialMine()} of ${trialPer()} yours`) : `${t.used_by_you ? "in use" : `${t.runs_left} of ${t.runs_total} left`}`) : cfg.hosted ? `${cfg.hosted_jobs_left ?? "∞"} jobs left` : "";
    $('#access [data-a="free"]').disabled = hasFree && !freeOk();
    $("#accFreeNote").innerHTML = key()
      ? `Free run selected: your saved key stays in this browser and will <b>not</b> be charged. Pick “My Comfy key” to pay with it again.`
      : t ? (trialPer() > 1 ? `Nothing to paste. Up to ${trialPer()} free runs per visitor (${trialMine()} left for you): each is one video in any mode, up to ${t.jobs_per_run} Comfy jobs.`
               : `Nothing to paste. One free run per visitor: one video in any mode, up to ${t.jobs_per_run} Comfy jobs.`)
        : "Nothing to paste: uses the capped public demo credits.";
    // the switch always shows the mode that will be charged, unless a prompt just opened another pane
    if (!$("#paySheet").contains(document.activeElement) || access === null) setAccess(payMode());
    // the counter next to Generate
    const r = $("#runsLeft");
    r.hidden = false;
    const m = payMode();
    if (m === "key") r.textContent = key() ? "Pays with your key" : "Add your key to generate";
    else if (m === "judge") r.textContent = judge() ? `Pays with promo code${judgeLeft != null ? ` · ${judgeLeft} jobs left` : ""}` : "Enter your promo code";
    else if (t) r.textContent = t.used_by_you ? "Pays with your free run" : trialPer() > 1 ? `Free run · ${trialMine()} of ${trialPer()} left for you` : `Free run · ${t.runs_left} of ${t.runs_total} left`;
    else if (cfg.hosted) r.textContent = `Demo credits · ${cfg.hosted_jobs_left ?? "∞"} jobs left`;
    else r.hidden = true;
    r.classList.toggle("out", !canPay());
    payPill();
  }

  // ---------- step states + the reason under the one primary button
  const over = () => !!(cfg.max_upload_s && fileDur > cfg.max_upload_s + 0.5);
  const partner = () => $("#scPartner button.on")?.dataset.p || "clone";
  function step2Why() {
    if (mode === "dub" && !$("#dubLang").value) return "Pick a language in step 2";
    if (mode === "scene" && partner() === "photo") {
      if (!$("#scPhoto").files[0]) return "Add the partner photo in step 2";
      if (!$("#scPhotoConsent").checked) return "Confirm you have the photo person's consent (step 2)";
    }
    return "";
  }
  function setStep(el, st, label) { el.dataset.state = st; $(".step-st", el).textContent = label;
    $(".step-b", el).inert = st === "locked"; }
  function sync() {
    placeConsent();
    // the own-face box never locks a step or greys Analyze: an unticked tap shows the error on the box (app.js)
    const s1 = !!file && !over(), w2 = step2Why(), s2 = s1 && !w2;
    setStep($("#step1"), s1 ? "done" : "active", s1 ? "Done" : "");
    setStep($("#step2"), !s1 ? "locked" : w2 ? "active" : "done", !s1 ? "Finish step 1" : w2 ? "" : "Ready");
    setStep($("#step3"), s2 ? "active" : "locked", s2 ? "" : "Finish steps 1–2");
    $("#step3 .step-b").inert = false;  // keep the button + access choice readable; the button carries the reason
    const why = !file ? "Add a clip in step 1, or press “Try with sample clip”." : over() ? `Your clip is longer than ${cfg.max_upload_s} s. Trim it and add it again.`
      : w2 ? w2 + "." : "";
    const btn = $("#start");
    if (btn.textContent !== "Uploading…") btn.disabled = !!why;  // no file / too long / step 2 missing; never the consent box
    $("#startWhy").textContent = why ? "ⓘ " + why : "Ready. This step is free.";
    $("#startWhy").classList.toggle("ok", !why);
    $("#modeNote").textContent = NOTE[mode];
    $("#scAdv").hidden = mode !== "scene";
    $$("#mode button").forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === mode));
    if (file !== mineFile) { mineFile = file; if (mine) URL.revokeObjectURL(mine); mine = file ? URL.createObjectURL(file) : null; view = file ? "mine" : "ex"; playingFull = false; }
    syncAccess(); stage(); place();
  }
  window.uxSync = sync;

  // ---------- own-face box: in step 1 on desktop, directly above the Analyze button on phones/tablets (one column)
  const narrow = window.matchMedia("(max-width: 980px)");
  function placeConsent() {
    const row = $("#consentRow"), go = $("#step3 .go");
    if (narrow.matches) { if (row.nextElementSibling !== go) go.before(row); }
    else if (row.parentElement !== $("#step1 .step-b")) $("#step1 .step-b").append(row);
  }
  narrow.addEventListener("change", () => sync());

  // ---------- home vs project layout: the access card sits in step 3 on home, on top in a project
  function place() {
    const home = !$("#uploadCard").hidden;
    document.body.classList.toggle("in-project", !home);
    $("#intro").hidden = !home;
    placePipe();
    if (!home && !sv.paused && !document.body.classList.contains("stage-idle")) sv.pause();
  }
  new MutationObserver(place).observe($("#uploadCard"), {attributes: true, attributeFilter: ["hidden"]});

  $$("#mode button").forEach(b => b.addEventListener("click", () => { playingFull = false; if (!mine) view = "ex"; setTimeout(sync); }));
  $$("#scPartner button").forEach(b => b.addEventListener("click", () => setTimeout(sync)));
  ["#scPhoto", "#scPhotoConsent", "#dubLang"].forEach(s => $(s).addEventListener("change", sync));
  // partner photo drop zone: same pattern as the video upload; thumbnail + check once filled
  const pd = $("#scPhotoDrop"), pin = $("#scPhoto");
  ["dragover", "dragenter"].forEach(ev => pd.addEventListener(ev, e => { e.preventDefault(); pd.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => pd.addEventListener(ev, e => { e.preventDefault(); pd.classList.remove("over"); }));
  pd.addEventListener("drop", e => { if (e.dataTransfer.files[0]) { pin.files = e.dataTransfer.files; pin.dispatchEvent(new Event("change")); } });
  let photoUrl = null;
  pin.addEventListener("change", () => {
    const f = pin.files[0], box = $("#scPhotoFile");
    if (photoUrl) URL.revokeObjectURL(photoUrl); photoUrl = f ? URL.createObjectURL(f) : null;
    pd.classList.toggle("filled", !!f); $("#scPhotoEmpty").hidden = !!f; box.hidden = !f;
    box.innerHTML = f ? `<img src="${photoUrl}" alt="partner photo"><div style="flex:1;min-width:0"><b>${esc(f.name)}</b><br><small class="muted">${(f.size / 1e6).toFixed(1)} MB · click to replace</small></div><span class="ok-check" aria-label="added">✓</span>` : "";
  });
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) $$("#mode video").forEach(v => { v.removeAttribute("autoplay"); v.pause(); });
  // ---------- typed text on the home form (script, lyrics, scene descriptions) survives a refresh
  const DRAFT_IDS = ["script", "lyrics", "scChar", "scALook", "scLookCustom"];
  const saved = textDraft.get("new", "form") || {};
  DRAFT_IDS.forEach(id => { const el = $("#" + id); if (!el) return;
    if (saved[id]) { el.value = saved[id]; if (id === "script" || id === "lyrics") $("#advBox").open = true; }
    el.addEventListener("input", () => textDraft.set("new", "form", Object.fromEntries(DRAFT_IDS.map(i => [i, $("#" + i)?.value || ""])))); });
  if (saved.scLookCustom) $("#scLookCustom").hidden = false;

  // ---------- while a job runs: "you can leave this page, come back with this link"
  const CARDS = [["#progressCard", "#busyBadge"], ["#sceneCard", "#scBusy"], ["#dubCard", "#dbBusy"]];
  CARDS.forEach(([c]) => {
    const n = document.createElement("div"); n.className = "leave"; n.hidden = true;
    n.innerHTML = `<div class="leave-t"><b>You can leave this page.</b> Generation keeps running on the server. Come back with this link<span class="leave-ttl"></span>:</div>
      <div class="leave-row"><input class="leave-url" readonly aria-label="Link to this project"><button type="button" class="secondary leave-copy">Copy link</button></div>`;
    $(".card-h", $(c)).after(n);
    $(".leave-copy", n).onclick = async () => {
      const u = $(".leave-url", n);
      try { await navigator.clipboard.writeText(u.value); } catch { u.select(); document.execCommand("copy"); }
      toast("Link copied. Open it any time to see the progress and the result.");
    };
  });
  function leaveNotes() {
    CARDS.forEach(([c, b]) => {
      const card = $(c), n = $(".leave", card), busy = !card.hidden && $(b).classList.contains("warn") && /…$/.test($(b).textContent);
      n.hidden = !busy; if (!busy) return;
      const u = $(".leave-url", n); if (u.value !== location.href) u.value = location.href;
      $(".leave-ttl", n).textContent = cfg.ttl_minutes ? ` (projects are kept for ${cfg.ttl_minutes} min)` : "";
    });
  }
  setInterval(leaveNotes, 1000);

  // ---------- one big progress panel for every job (analyze, auto-suggest, generate, scene, dub)
  const TERM = ["success", "error", "non_retryable_error", "lost", "cancelled"];
  const JOB_LS = "multicam.job";
  const mmss = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const eta = s => s <= 15 ? "almost done" : s < 90 ? `about ${Math.round(s / 10) * 10} s left` : `about ${Math.round(s / 60)} min left`;
  const lastLog = o => (o?.log || []).slice(-1)[0] || "";
  const camOf = label => (String(label).match(/CAM ([A-D])/) || [])[1];
  const KIND_LABEL = {still: "partner stills", stills: "partner stills", b: "camera B", line: "camera B", b_line: "camera B",
    a: "your side angle", a_reshoot: "your side angle", reshoot: "your side angle"};
  function trackGet() { try { return JSON.parse(localStorage.getItem(JOB_LS) || "{}"); } catch { return {}; } }
  function trackSet(o) { localStorage.setItem(JOB_LS, JSON.stringify(o)); }
  // what is running right now, in plain language
  function currentJob() {
    const home = !$("#uploadCard").hidden;
    if (home) return $("#start").textContent === "Uploading…" ? {key: "upload", kind: "upload", title: "Uploading your clip…", eta: 20} : null;
    const SCN = window.uxScene, DBB = window.uxDub;
    if (SCN && !$("#sceneCard").hidden) {
      if (window.uxSuggesting) return {key: `s.${SCN.id}.suggest`, id: SCN.id, kind: "suggest", title: "Writing lines for camera B…", detail: "From your own words. Free, no Comfy job.", eta: 5};
      if (!SCN.busy) return {idle: true, id: SCN.id, kindOf: "s", error: SCN.error, obj: SCN};
      const t = trackGet()[`s.${SCN.id}`], base = t?.key === `s.${SCN.id}.${SCN.busy}` ? (t.jobs0 ?? 0) : (SCN.jobs || []).length;
      const js = (SCN.jobs || []).filter((x, i) => i >= base || !TERM.includes(x.status));
      if (SCN.busy === "analyze") return {key: `s.${SCN.id}.analyze`, id: SCN.id, kindOf: "s", name: "analyze", title: "Analyzing your take…", detail: lastLog(SCN), eta: 60};
      if (SCN.busy === "render") return {key: `s.${SCN.id}.render`, id: SCN.id, kindOf: "s", name: "render", title: "Rendering your scene…", detail: "Free, on this server (ffmpeg).", eta: 45};
      const total = Math.max(js.length, SCN.estimate?.n_jobs || 0), done = js.filter(j => TERM.includes(j.status)).length;
      const run = js.find(j => !TERM.includes(j.status));
      const kk = String(run?.kind || ""), nm = kk.split(":")[1] || "";
      const what = !run ? "the scene" : kk === "character" ? "the partner's look" : kk === "stills" ? "partner stills"
        : kk.startsWith("clip:") ? `camera B${nm ? ` (${nm})` : ""}` : kk.startsWith("reshoot:") ? `your side angle${nm ? ` (${nm})` : ""}` : (KIND_LABEL[kk] || kk);
      return {key: `s.${SCN.id}.${SCN.busy}`, id: SCN.id, kindOf: "s", name: SCN.busy, jobs0: base,
        title: total ? `Generating ${what}… step ${Math.min(done + 1, total)} of ${total}` : `Generating ${what}…`,
        frac: total ? done / total : null, detail: lastLog(SCN), eta: total ? (total - done) * 75 + 40 : 180};
    }
    if (DBB && !$("#dubCard").hidden) {
      if (!DBB.busy) return {idle: true, id: DBB.id, kindOf: "d", error: DBB.error, obj: DBB};
      const n = DBB.estimate?.jobs || ((DBB.estimate?.windows || 0) + (DBB.estimate?.inserts || 0));
      const T = {analyze: ["Analyzing your take…", 60], translate: [`Translating your lines into ${DBB.lang_name || "the new language"}…`, 30],
        generate: [`Dubbing into ${DBB.lang_name || "the new language"} + multicam…`, n ? n * 70 + 60 : 300]}[DBB.busy] || [`${DBB.busy}…`, 120];
      return {key: `d.${DBB.id}.${DBB.busy}`, id: DBB.id, kindOf: "d", name: DBB.busy, title: T[0], detail: lastLog(DBB), eta: T[1]};
    }
    if (S && !$("#progressCard").hidden) {
      if (!S.busy) return {idle: true, id: S.id, kindOf: "p", error: S.error, obj: S};
      const k = `p.${S.id}.${S.busy}`;
      if (S.busy === "analyze") {
        const tr = S.steps?.transcribe?.status, n = tr === "done" || tr === "skipped" ? 2 : 1;
        return {key: k, id: S.id, kindOf: "p", name: "analyze", title: `${n === 1 ? "Transcribing your words" : "Planning the cuts"}… step ${n} of 2`,
          frac: (n - 1) / 2, detail: (S.steps?.[n === 1 ? "transcribe" : "plan"]?.msg) || "Free, on this server.", eta: n === 1 ? 50 : 15};
      }
      if (S.busy === "render") return {key: k, id: S.id, kindOf: "p", name: "render", title: "Rendering your video…", detail: "Free, on this server (ffmpeg).", eta: 40};
      const tk = trackGet()[`p.${S.id}`], fresh = tk?.key === k, base = fresh ? (tk.jobs0 ?? 0) : (S.jobs || []).length;
      const js = (S.jobs || []).filter((j, i) => i >= base || !TERM.includes(j.status));
      const done = js.filter(j => TERM.includes(j.status)).length, run = js.find(j => !TERM.includes(j.status));
      const total = Math.max(js.length, fresh ? (tk.n0 || 0) : (S.estimate?.jobs || 0));
      if (!run && js.length && done === js.length) {
        const al = S.steps?.align?.status === "running";
        return {key: k, id: S.id, kindOf: "p", name: S.busy, title: al ? "Aligning lip-sync…" : "Rendering your video…", frac: .92, detail: "AI cameras are done. Free from here.", eta: 30};
      }
      const cam = run && camOf(run.label), what = !run ? "your cameras" : cam ? `camera ${cam}` : /angle|still/i.test(run.label) ? "angle stills" : run.label;
      const runEl = run ? Math.round(Date.now() / 1000 - (run.started || Date.now() / 1000)) : 0;
      return {key: k, id: S.id, kindOf: "p", name: S.busy, jobs0: fresh ? tk.jobs0 : (S.jobs || []).length, n0: S.estimate?.jobs || 0, title: total ? `Generating ${what}… step ${Math.min(done + 1, total)} of ${total}` : `Generating ${what}…`,
        frac: total ? done / total : null, detail: run ? `${run.label} · Comfy Cloud · ${run.status}` : "Submitting to Comfy Cloud…", eta: Math.max(20, 130 - runEl) + 30};
    }
    return null;
  }
  const RETRY = {p: {analyze: "#newProject", generate: "#generate", reroll: "#generate", angles: "#anglesBtn", render: "#renderFree"},
    s: {analyze: "#newProject", generate: "#scGen", render: "#scRender"}, d: {analyze: "#newProject", translate: "#dbTranslate", generate: "#dbGen"}};
  const LOADING = {p: {generate: ["#generate"], reroll: ["#generate"], angles: ["#anglesBtn"], render: ["#renderFree", "#rerender"]},
    s: {generate: ["#scGen"], render: ["#scRender"]}, d: {translate: ["#dbTranslate"], generate: ["#dbGen"]}};
  const BASE_TITLE = document.title;
  let prevKey = null, dismissed = null;
  window.addEventListener("beforeunload", e => { if (prevKey) { e.preventDefault(); e.returnValue = ""; } });
  function panel() {
    const j = currentJob(), P = $("#jobPanel"), tr = trackGet();
    $$(".is-loading[data-loading]").forEach(b => { if (b.id !== "scSuggest") b.classList.remove("is-loading"); });
    if (j && !j.idle) {
      // remember when it started (survives a reload) and which button started it
      const rec = tr[j.key.split(".").slice(0, 2).join(".")];
      if (!rec || rec.key !== j.key) { tr[j.key.split(".").slice(0, 2).join(".")] = {key: j.key, name: j.name, t0: Date.now(), jobs0: j.jobs0 ?? 0, n0: j.n0 || 0}; trackSet(tr); }
      if (prevKey !== j.key) document.title = BASE_TITLE;
      prevKey = j.key; dismissed = null;
      const t0 = (tr[j.key.split(".").slice(0, 2).join(".")] || {}).t0 || Date.now(), el = (Date.now() - t0) / 1000;
      (LOADING[j.kindOf]?.[j.name] || []).forEach(sel => { const b = $(sel); if (b) { b.classList.add("is-loading"); b.disabled = true; } });
      P.hidden = false; P.dataset.state = "running"; document.body.classList.remove("stage-idle");
      if (P.dataset.wasRunning !== j.key) { P.dataset.wasRunning = j.key; P.classList.remove("open"); }
      const frac = j.frac != null ? Math.max(.04, j.frac) : null, left = Math.max(0, (j.eta || 60) - (j.frac == null ? el : 0));
      const pv = $(".sc-pick.chosen img") || $("#scPhotoFile img") || null, prev = pv ? pv.getAttribute("src") : null;
      P.innerHTML = `${prev ? `<img class="jp-prev" src="${esc(prev)}" alt="">` : ""}<div class="jp-top"><span class="spin" aria-hidden="true"></span><span class="jp-k">Working</span><span class="jp-mini">${esc(j.title)}</span><span class="jp-time">${mmss(el)}<span class="jp-eta"> elapsed · ${eta(left)}</span></span><span class="jp-chev" aria-hidden="true"></span></div>
        <div class="jp-title">${esc(j.title)}</div>
        <div class="jp-bar ${frac == null ? "ind" : ""}"><i style="${frac == null ? "" : `width:${Math.round(frac * 100)}%`}"></i></div>
        ${j.detail ? `<div class="jp-detail">${esc(j.detail)}</div>` : ""}
        ${j.kindOf ? `<div class="jp-leave">You can leave this page. Come back with this link: <button type="button" class="ghost jp-copy">Copy link</button></div>` : ""}`;
      const c = $(".jp-copy", P); if (c) c.onclick = () => navigator.clipboard.writeText(location.href).then(() => toast("Link copied")).catch(() => toast(location.href, 6000));
      document.body.classList.add("job-running");
      return;
    }
    document.body.classList.remove("job-running");
    const was = prevKey; prevKey = null;
    if (j?.idle && j.error && dismissed !== j.error) {
      const name = was ? was.split(".")[2] : (tr[`${j.kindOf}.${j.id}`]?.name) || (j.kindOf === "p" ? (S.steps?.plan?.status === "done" ? "generate" : "analyze") : j.obj?.sentences || j.obj?.windows ? "generate" : "analyze");
      const sel = RETRY[j.kindOf]?.[name];
      P.hidden = false; P.dataset.state = "error";
      P.innerHTML = `<div class="jp-err-h">Something went wrong</div><div class="jp-err-m">${esc(j.error)}</div>
        <div class="jp-actions">${sel ? `<button type="button" class="jp-retry">${sel === "#newProject" ? "Upload again" : "Retry"}</button>` : ""}<button type="button" class="ghost jp-dismiss">Dismiss</button></div>
        <div class="jp-note">${sel && sel !== "#newProject" ? "Retry shows the cost again before anything is spent." : "Nothing was charged for this step."}</div>`;
      const r = $(".jp-retry", P); if (r) r.onclick = () => { const b = $(sel); if (b) { b.disabled = false; b.click(); } };
      $(".jp-dismiss", P).onclick = () => { dismissed = j.error; panel(); };
      if (was) toast("That job failed. See the red card for details.", 4500);
      return;
    }
    if (was && j?.idle) {
      document.title = "Done · Multicam";
      if (!was.endsWith("suggest")) toast("Done", 3500);
      P.hidden = false; P.dataset.state = "done";
      P.innerHTML = `<div class="jp-top"><span class="jp-k">Done</span></div><div class="jp-title">${was.endsWith("suggest") ? "Lines added" : "Finished"}</div>
        <div class="jp-detail">${was.includes(".analyze") ? "Your clip is analyzed. Check the cameras and cost, then generate." : was.endsWith("suggest") ? "Edit them if you like, then Generate." : "Your result is ready below."}</div>`;
      setTimeout(() => { if (!prevKey && P.dataset.state === "done") P.hidden = true; }, 8000);
      return;
    }
    if (P.dataset.state === "done" && !P.hidden) return;
    if (document.body.classList.contains("in-project")) {  // desktop right column when idle: cost summary + example clip
      document.body.classList.add("stage-idle");
      const e = j?.obj?.estimate, n = e ? (e.n_jobs ?? e.jobs ?? ((e.windows || 0) + (e.inserts || 0))) : 0;
      if (e && typeof e.usd === "number") {
        P.hidden = false; P.dataset.state = "idle";
        P.innerHTML = `<div class="jp-top"><span class="jp-k">Next step</span></div>
          <div class="jp-title sm">${n ? `${n} Comfy job${n === 1 ? "" : "s"} ≈ $${e.usd.toFixed(2)}` : "Everything generated · re-render is free"}</div>
          <div class="jp-detail">Nothing is spent until you confirm the cost.</div>`;
      } else P.hidden = true;
      // only the muted example loop is restarted here; never a result (sound) and never over a playing video
      if (sv.paused && sv.muted && !sv.dataset.result && !playingFull && matchMedia("(min-width:981px)").matches && !soundPlaying() && !document.hidden) sv.play().catch(() => {});
    } else { P.hidden = true; document.body.classList.remove("stage-idle"); }
  }
  window.addEventListener("focus", () => { if (!prevKey && document.title.startsWith("Done ·")) setTimeout(() => { document.title = BASE_TITLE; }, 4000); });
  setInterval(() => { panel(); stageResult(); }, 1000);
  // mobile: the sticky panel is one line (status + elapsed); tap to expand
  $("#jobPanel").addEventListener("click", e => {
    if (e.target.closest("button,a,input") || !matchMedia("(max-width:560px)").matches) return;
    const P = $("#jobPanel"); if (P.dataset.state !== "running") return;
    P.classList.toggle("open"); P.setAttribute("aria-expanded", P.classList.contains("open"));
  });

  // ---------- resume card: last project / scene / dub per mode, if it's recent (< ~60 min)
  const LAST_LS = "multicam.last";
  const lastAll = () => { try { return JSON.parse(localStorage.getItem(LAST_LS) || "{}"); } catch { return {}; } };
  function remember() {
    const m = location.hash.match(/([psd])=([0-9a-f]+)/); if (!m) return;
    const md = m[1] === "s" ? "scene" : m[1] === "d" ? "dub" : (S?.id === m[2] ? S.mode : null); if (!md) return;
    const all = lastAll(), cur = all[md];
    if (cur?.id === m[2]) return;
    const name = m[1] === "p" ? S?.name : m[1] === "d" ? window.uxDub?.lang_name && `into ${window.uxDub.lang_name}` : "";
    all[md] = {id: m[2], hash: `${m[1]}=${m[2]}`, t: Date.now(), name: name || ""}; localStorage.setItem(LAST_LS, JSON.stringify(all));
  }
  setInterval(remember, 1500);
  async function resumeCard() {
    if (location.hash.match(/[psd]=/)) return;
    const MAXAGE = ((cfg.ttl_minutes || 60) - 2) * 60e3, now = Date.now();
    const items = Object.entries(lastAll()).filter(([, v]) => now - v.t < MAXAGE).sort((a, b) => b[1].t - a[1].t);
    const ok = [];
    for (const [md, v] of items) {
      const path = v.hash[0] === "p" ? `/api/projects/${v.id}` : v.hash[0] === "s" ? `/api/scene/${v.id}` : `/api/dub/${v.id}`;
      const r = await fetch(path).catch(() => null);  // still on the server? (public projects are deleted after the TTL)
      if (r?.ok) { const d = await r.json().catch(() => ({})); ok.push([md, v, d]); }
    }
    if (!ok.length) return;
    const [md, v, d] = ok[0], mins = Math.max(1, Math.round((now - v.t) / 60e3));
    const st = d.busy ? `Still working (${esc(d.busy)})` : d.error ? "Stopped with an error" : (d.output || d.renders?.length) ? "Result ready" : "waiting for you";
    const C = $("#resumeCard"); C.hidden = false;
    C.innerHTML = `<div class="rs-main"><div class="rs-k">Welcome back</div><div class="rs-t">Resume your last project</div>
      <div class="rs-m"><b>${NAME[md] || md}</b>${v.name ? ` · ${esc(v.name)}` : ""} · started ${mins} min ago · ${st}</div>
      ${ok.length > 1 ? `<div class="rs-more">Also: ${ok.slice(1).map(([m2, v2]) => `<a href="#${v2.hash}">${NAME[m2] || m2}</a>`).join(" · ")}</div>` : ""}</div>
      <div class="rs-act"><a class="btn secondary rs-go" href="#${v.hash}">Resume →</a><button type="button" class="ghost rs-x" aria-label="Hide">Start fresh</button></div>`;
    $(".rs-x", C).onclick = () => { C.hidden = true; };
    window.addEventListener("hashchange", () => { C.hidden = true; }, {once: true});
  }
  setTimeout(resumeCard, 600);

  // ---------- "Your recent runs": every project / scene / dub link this browser opened or created (max 10)
  const H = window.MCHistory;
  const histGet = () => H.parse(localStorage.getItem(H.LS));
  const histSet = l => { try { localStorage.setItem(H.LS, JSON.stringify(l)); } catch { /* private mode / full */ } };
  function histRecord() {
    const m = location.hash.match(/([psd])=([0-9a-f]+)/); if (!m) return;
    const hash = `${m[1]}=${m[2]}`, list = histGet(), cur = list[0];
    const md = m[1] === "s" ? "scene" : m[1] === "d" ? "dub" : (S?.id === m[2] ? S.mode : null);
    const name = m[1] === "p" ? (S?.id === m[2] ? S.name : "") : m[1] === "d" ? (window.uxDub?.lang_name ? `into ${window.uxDub.lang_name}` : "") : "";
    const im = $(".sc-pick.chosen img") || $("#scPhotoFile img") || $("#cams .still img");
    const thumb = im && /^\/api\//.test(im.getAttribute("src") || "") ? im.getAttribute("src").split("?")[0] : "";
    if (cur?.hash === hash && (!md || cur.mode === md) && (!name || cur.name === name) && (!thumb || cur.thumb === thumb)) return;
    if (cur?.hash === hash && Date.now() - cur.t < 1500 && !md && !name && !thumb) return;
    histSet(H.add(list, {hash, mode: md, name, thumb}));
  }
  setInterval(histRecord, 1500);
  function histRender() {
    const list = histGet();
    $("#historyEmpty").hidden = !!list.length; $("#historyClear").hidden = !list.length;
    $("#historyList").innerHTML = list.map(x => `<li><a class="hist-row" href="#${x.hash}"${location.hash === "#" + x.hash ? ' aria-current="page"' : ""}>
      <span class="hist-th">${x.thumb ? `<img src="${esc(x.thumb)}" alt="" loading="lazy" onerror="this.remove()">` : `<i>${esc((NAME[x.mode] || x.mode || "?")[0])}</i>`}</span>
      <span class="hist-t"><b>${esc(x.name || NAME[x.mode] || "Project")}</b><small>${esc(NAME[x.mode] || x.mode || "")} · ${esc(H.ago(x.t))}${x.busy ? " · still working" : ""}</small></span>
      <svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg></a></li>`).join("");
    $("#histDot").hidden = !list.some(x => x.busy);
  }
  async function histPrune() {  // drop links whose project is gone (public projects expire); note unfinished runs
    const list = histGet(); if (!list.length) { histRender(); return; }
    const res = await Promise.all(list.map(x => fetch(H.apiPath(x.hash)).then(async r => r.status === 404 ? {gone: x.hash} : r.ok ? {hash: x.hash, busy: !!(await r.json().catch(() => ({}))).busy} : null).catch(() => null)));
    const gone = res.filter(r => r?.gone).map(r => r.gone), busy = Object.fromEntries(res.filter(r => r?.hash).map(r => [r.hash, r.busy]));
    histSet(H.drop(histGet(), gone).map(x => (x.hash in busy ? {...x, busy: busy[x.hash]} : x))); histRender();
  }
  $("#historyClear").onclick = () => { histSet([]); histRender(); };
  $("#histBtn").onclick = () => { histRender(); openSheet("histSheet"); histPrune(); };
  $("#historyList").addEventListener("click", e => { if (e.target.closest("a")) closeSheets(); });
  window.addEventListener("hashchange", () => { histRecord(); histRender(); });
  histRecord(); histPrune(); setInterval(histPrune, 45000);
  sync();
})();
