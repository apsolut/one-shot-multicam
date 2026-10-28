// First-run UX layer (ux.js): the numbered 3-step flow, required-field reasons on the single primary
// button, the access choice (free run / own key / judge code), the sticky preview with example outputs,
// and "Try with sample clip". UI only: it calls the same app.js helpers and never hits a paid endpoint.
(() => {
  const MEDIA = "/gallery/media/";
  const EX = {  // precomputed outputs from docs/media (free to watch)
    talk: {loop: "loop_talk.mp4", full: "demo_v6_labeled.mp4", wide: false,
      cap: "Your real take is CAM A. CAM B and C are AI re-shoots of the same moment, cut on word boundaries over your real audio."},
    music: {loop: "loop_music.mp4", full: "demo_v5_labeled.mp4", wide: false,
      cap: "Same cut engine as Talk, timed to your lyrics. (Example shows a Talk cut; a Music sample is coming.)"},
    scene: {loop: "loop_scene.mp4", full: "scene_clone_v2_labeled.mp4", wide: false,
      cap: "One take becomes a two-person scene: you, and your other self answering between your sentences, shot / reverse-shot."},
    dub: {loop: "loop_dub.mp4", full: "dub_4lang_v2.mp4", wide: true,
      cap: "The same take dubbed into German, Spanish and Croatian: your face and voice, new lip motion, then a multicam edit."},
  };
  const NOTE = {
    talk: "Talk: nothing to set, the defaults work. Add your exact script under Advanced for word-perfect cuts.",
    music: "Music: add your lyrics with times under Advanced for the best cuts (recommended).",
    scene: "Scene: choose who answers you. Everything else has a sensible default.",
    dub: "Dub: pick the language. Speak English in your take.",
  };
  const NAME = {talk: "Talk", music: "Music", scene: "Scene", dub: "Dub"};
  const sv = $("#stageVideo");
  let view = "ex", mine = null, mineFile = null, access = null, playingFull = false;

  // ---------- preview stage
  function stage() {
    const ex = EX[mode] || EX.talk;
    $("#stageTabs").hidden = !mine;
    $$("#stageTabs button").forEach(b => b.classList.toggle("on", b.dataset.v === view));
    const showMine = view === "mine" && mine;
    const src = showMine ? mine : MEDIA + (playingFull ? ex.full : ex.loop);
    $("#stageTag").textContent = showMine ? "Your clip" : `Example output · ${NAME[mode]}`;
    $("#stageCap").textContent = showMine ? "This is what you'll upload. The example on the other tab shows what you get back." : ex.cap;
    $("#stageFrame").classList.toggle("wide", !showMine && ex.wide);
    $("#stagePlay").hidden = !!showMine;
    if (sv.dataset.src !== src) { sv.dataset.src = src; sv.src = src; }
    sv.muted = !(playingFull && !showMine); sv.controls = !!(showMine || playingFull); sv.loop = !playingFull;
    sv.play?.().catch(() => {});
  }
  function playExample() {
    view = "ex"; playingFull = true; stage(); sv.currentTime = 0; sv.muted = false; sv.play().catch(() => {});
    if (window.matchMedia("(max-width: 980px)").matches) $("#stage").scrollIntoView({behavior: "smooth", block: "center"});
  }
  $("#stagePlay").onclick = playExample;
  $("#exampleBtn").onclick = e => { e.preventDefault(); playExample(); };
  sv.addEventListener("ended", () => { if (playingFull) { playingFull = false; stage(); } });
  $$("#stageTabs button").forEach(b => b.onclick = () => { view = b.dataset.v; playingFull = false; stage(); });

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

  // ---------- access choice: free run | own key | judge code (one compact control)
  const freeOk = () => !!(cfg.trial && trialOk()) || !!(cfg.hosted && (cfg.hosted_jobs_left ?? 0) > 0);
  function setAccess(a) {
    access = a;
    $$("#access button").forEach(b => { const on = b.dataset.a === a; b.classList.toggle("on", on); b.setAttribute("aria-checked", on); });
    $("#accFree").hidden = a !== "free"; $("#accKey").hidden = a !== "key"; $("#accJudge").hidden = a !== "judge";
  }
  window.uxAccess = setAccess;
  let picked = false;  // until the visitor picks, follow the config (free run while it lasts)
  $$("#access button").forEach(b => b.onclick = () => { picked = true; setAccess(b.dataset.a); });
  const keyEl = $("#key"), keyFocus = keyEl.focus.bind(keyEl);
  keyEl.focus = (...a) => { picked = true; setAccess("key"); keyFocus(...a); };  // "paste your key" prompts land on a visible field
  document.addEventListener("click", e => { if (e.target.closest("#bannerKey")) { picked = true; setAccess("key"); } }, true);
  function syncAccess() {
    const hasFree = !!(cfg.trial || cfg.hosted), t = cfg.trial;
    $('#access [data-a="free"]').hidden = !hasFree;
    $('#access [data-a="judge"]').hidden = !cfg.judge_code;
    $("#access").style.setProperty("--n", 1 + hasFree + !!cfg.judge_code);
    $("#accFreeN").textContent = t ? `${t.used_by_you ? "in use" : `${t.runs_left} of ${t.runs_total} left`}` : cfg.hosted ? `${cfg.hosted_jobs_left ?? "∞"} jobs left` : "";
    $('#access [data-a="free"]').disabled = hasFree && !freeOk();
    $("#accFreeNote").innerHTML = key()
      ? `A key is saved in this browser, so it will be used instead. <a href="#" id="accForget">Forget the key</a> to use the free run.`
      : t ? `Nothing to paste. One free run per visitor: one video in any mode, up to ${t.jobs_per_run} Comfy jobs.`
        : "Nothing to paste: uses the capped public demo credits.";
    const fg = $("#accForget"); if (fg) fg.onclick = e => { e.preventDefault(); localStorage.removeItem(KEY_LS); renderKey(); toast("Key removed from this browser"); };
    if (!picked || access === null || (access === "free" && !freeOk() && !key()) || (access === "judge" && !cfg.judge_code))
      setAccess(key() ? "key" : judge() && cfg.judge_code ? "judge" : freeOk() ? "free" : "key");
    // the counter next to Generate
    const r = $("#runsLeft");
    r.hidden = false;
    if (key()) r.textContent = "🔑 using your key";
    else if (judge()) r.textContent = `🎟 judge code${judgeLeft != null ? ` · ${judgeLeft} jobs left` : ""}`;
    else if (t) r.textContent = t.used_by_you ? "🎁 your free run" : `🎁 ${t.runs_left} of ${t.runs_total} free runs left`;
    else if (cfg.hosted) r.textContent = `🎁 ${cfg.hosted_jobs_left ?? "∞"} demo jobs left`;
    else r.hidden = true;
    r.classList.toggle("out", !!t && !trialOk() && !key() && !judge());
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
    const s1 = !!file && $("#consent").checked && !over(), w2 = step2Why(), s2 = s1 && !w2;
    setStep($("#step1"), s1 ? "done" : "active", s1 ? "✓ Done" : "");
    setStep($("#step2"), !s1 ? "locked" : w2 ? "active" : "done", !s1 ? "Finish step 1" : w2 ? "" : "✓ Ready");
    setStep($("#step3"), s2 ? "active" : "locked", s2 ? "" : "Finish steps 1–2");
    $("#step3 .step-b").inert = false;  // keep the button + access choice readable; the button carries the reason
    const why = !file ? "Add a clip in step 1, or press “Try with sample clip”." : over() ? `Your clip is longer than ${cfg.max_upload_s} s. Trim it and add it again.`
      : !$("#consent").checked ? "Tick the consent box in step 1." : w2 ? w2 + "." : "";
    const btn = $("#start");
    if (btn.textContent !== "Uploading…") btn.disabled = !!why;
    $("#startWhy").textContent = why ? "ⓘ " + why : "Ready. This step is free.";
    $("#startWhy").classList.toggle("ok", !why);
    $("#modeNote").textContent = NOTE[mode];
    $("#scAdv").hidden = mode !== "scene";
    $$("#mode button").forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === mode));
    if (file !== mineFile) { mineFile = file; if (mine) URL.revokeObjectURL(mine); mine = file ? URL.createObjectURL(file) : null; view = file ? "mine" : "ex"; playingFull = false; }
    syncAccess(); stage(); place();
  }
  window.uxSync = sync;

  // ---------- home vs project layout: the access card sits in step 3 on home, on top in a project
  function place() {
    const home = !$("#uploadCard").hidden;
    document.body.classList.toggle("in-project", !home);
    $("#intro").hidden = !home;
    const kc = $("#keyCard");
    if (home && kc.parentElement !== $("#keySlot")) $("#keySlot").appendChild(kc);
    if (!home && kc.parentElement === $("#keySlot")) $("#uploadCard").after(kc);
    if (!home && !sv.paused) sv.pause();
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
    n.innerHTML = `<div class="leave-t">⏳ <b>You can leave this page.</b> Generation keeps running on the server. Come back with this link<span class="leave-ttl"></span>:</div>
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
  sync();
})();
