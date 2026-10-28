// Scene mode — "talk to your other self". Uses helpers from app.js ($, api, toast, confirmBox, key, cfg, file, mode).
(() => {
  let SC = null, scfg = null, spoll = null, partner = "clone";
  const SHOT_LABEL = {single: "single", ots: "over-the-shoulder", close: "close-up"};
  const sid = () => (location.hash.match(/s=([0-9a-f]+)/) || [])[1];

  async function loadCfg() {
    scfg = await api("/api/scene-config").catch(() => null);
    if (!scfg) return;
    $("#scLook").innerHTML = Object.entries(scfg.looks).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("") +
      `<option value="custom">custom…</option>`;
    $("#scVoice").innerHTML = Object.entries(scfg.voices).map(([k, v]) => `<option value="${k}">${esc(v)} · ${k}</option>`).join("");
  }

  // ---- upload card: options when the Scene mode button is on
  function syncOpts() {
    const on = mode === "scene";
    $("#sceneOpts").hidden = !on;
    if (on) { $("#scriptBox").hidden = false; }
    $$("#scPartner button").forEach(b => b.classList.toggle("on", b.dataset.p === partner));
    $("#scCloneBox").hidden = partner !== "clone";
    $("#scPhotoBox").hidden = partner !== "photo";
    $("#scCharBox").hidden = partner !== "character";
    $("#scVoiceRow").hidden = partner === "clone";
  }
  $$("#mode button").forEach(b => b.addEventListener("click", () => setTimeout(syncOpts)));
  $$("#scPartner button").forEach(b => b.onclick = e => { e.preventDefault(); partner = b.dataset.p; syncOpts(); });
  $("#scLook").onchange = () => { $("#scLookCustom").hidden = $("#scLook").value !== "custom"; };

  // intercept "Analyze" in scene mode (capture phase on the card, before app.js's handler)
  $("#uploadCard").addEventListener("click", async e => {
    if (mode !== "scene" || !e.target.closest("#start")) return;
    e.preventDefault(); e.stopPropagation();
    if (!file) return toast("Pick your video first");
    const fd = new FormData();
    fd.append("file", file); fd.append("consent", $("#consent").checked ? "1" : "");
    fd.append("partner", partner); fd.append("person", $("#scPerson").value);
    fd.append("script", $("#script").value || "");
    fd.append("a_look", $("#scALook").value || "");
    fd.append("reshoot_a", $("#scReshootA").checked ? "1" : "");
    if (partner === "clone") { fd.append("voice", "self");
      fd.append("look", $("#scLook").value === "custom" ? $("#scLookCustom").value : $("#scLook").value); }
    else fd.append("voice", $("#scVoice").value);
    if (partner === "photo") {
      const ph = $("#scPhoto").files[0];
      if (!ph) return toast("Add the partner photo");
      if (!$("#scPhotoConsent").checked) return toast("Only photos of people who consented — tick the box");
      fd.append("photo", ph); fd.append("photo_consent", "1");
    }
    if (partner === "character") fd.append("character", $("#scChar").value || "");
    const b = $("#start"); b.disabled = true; b.textContent = "Uploading…";
    try {
      SC = await api("/api/scene", {body: fd});
      location.hash = `s=${SC.id}`;
      show();
    } catch (err) { toast(err.message, 5000); }
    finally { b.textContent = "Analyze my clip — free"; b.disabled = false; canStart(); }
  }, true);

  // ---- scene card
  function lineRow(ln, k) {
    const opts = (SC.sentences || []).map(s => `<option value="${s.i}" ${s.i === ln.after ? "selected" : ""}>after “${esc(s.text.slice(0, 38))}${s.text.length > 38 ? "…" : ""}”</option>`).join("");
    const shots = scfg.shots.map(s => `<option value="${s}" ${s === ln.shot ? "selected" : ""}>${SHOT_LABEL[s]}</option>`).join("");
    return `<div class="sc-line" data-k="${k}"><select class="sc-after">${opts}</select>
      <input class="sc-text" maxlength="160" value="${esc(ln.text)}" placeholder="B's line, exact words">
      <select class="sc-shot">${shots}</select>${ln.clip ? '<span class="badge ok">clip</span>' : ""}
      <button class="ghost sc-del" title="remove">✕</button></div>`;
  }
  function collect() {
    return $$("#scLines .sc-line").map(r => ({after: +$(".sc-after", r).value, text: $(".sc-text", r).value.trim(), shot: $(".sc-shot", r).value}))
      .filter(l => l.text);
  }
  function markEmpty() {  // CAM B field: REQUIRED, lime dashed while empty
    const rows = $$("#scLines .sc-line");
    $("#scEmpty").hidden = !!rows.length;
    $("#scField").classList.toggle("is-empty", !rows.some(r => $(".sc-text", r).value.trim()));
    rows.forEach(r => $(".sc-text", r).classList.toggle("empty", !$(".sc-text", r).value.trim()));
  }
  function render() {
    window.uxScene = SC;
    const c = $("#sceneCard"); c.hidden = !SC;
    if (!SC) return;
    $("#scBusy").textContent = SC.busy ? `${SC.busy}…` : (SC.output ? "ready" : "edit");
    $("#scBusy").className = "badge " + (SC.busy ? "warn" : SC.output ? "ok" : "");
    $("#scErr").hidden = !SC.error; $("#scErr").textContent = SC.error || "";
    $("#scLog").textContent = (SC.log || []).join("\n");
    const dlg = $("#scDialogue");
    if (SC.sentences && !dlg.dataset.done) {
      dlg.innerHTML = SC.sentences.map(s => `<div class="sc-a"><b>A</b> ${esc(s.text)}</div>`).join("");
      const draft = textDraft.get("s", SC.id);  // unsaved B lines typed before a refresh
      const rows = draft && JSON.stringify(draft) !== JSON.stringify((SC.lines || []).map(l => ({after: l.after, text: l.text, shot: l.shot}))) ? draft : null;
      $("#scLines").innerHTML = (rows || SC.lines || []).map(lineRow).join("");
      if (rows) toast("Restored the lines you typed before the refresh (not saved yet)", 3500);
      dlg.dataset.done = "1";
    }
    markEmpty();
    $("#scStills").innerHTML = Object.entries(SC.stills || {}).map(([k, u]) => `<figure><img src="${u}" alt="${k}"><figcaption>B · ${SHOT_LABEL[k] || k}</figcaption></figure>`).join("");
    const e = SC.estimate;
    $("#scEst").innerHTML = e ? (e.n_jobs ? `<b>${e.n_jobs}</b> Comfy jobs ≈ <b>$${e.usd.toFixed(2)}</b> <span class="muted small">(${Object.entries(e.jobs).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(", ")})</span>` : "everything generated — re-render is free") : "";
    $("#scGen").disabled = !!SC.busy || !SC.sentences;
    $("#scRender").disabled = !!SC.busy || !(SC.lines || []).every(l => l.clip) || !(SC.lines || []).length;
    const v = $("#scPlayer"), want = $("#scLabels").checked ? SC.output_labeled : SC.output;
    $("#scResult").hidden = !SC.output;
    if (want && v.dataset.src !== want) { v.src = want; v.dataset.src = want; }
    if (SC.output) $("#scDownload").href = SC.output;
    if (SC.output && !SC.busy) wfLink($("#scWf"), "scene", SC.id, `${SC.output}|${(SC.jobs || []).length}`);  // ComfyUI graphs it ran
    $("#scEdl").innerHTML = (SC.edl || []).map(s => `<span class="sc-shot-chip ${s.who}" title="${esc(s.words || "")}" style="flex:${s.dur}">${esc(s.cam)}</span>`).join("");
  }
  async function refresh() {
    clearTimeout(spoll);
    if (!sid()) return;
    try { SC = await api(`/api/scene/${sid()}`); render(); } catch (e) { if (e.status === 404) { SC = null; render(); return; } }
    if (SC && (SC.busy || !SC.sentences)) spoll = setTimeout(refresh, 2000);
  }
  function show() { $("#uploadCard").hidden = true; $("#sceneCard").hidden = false; $("#scDialogue").dataset.done = ""; refresh();
    $("#sceneCard").scrollIntoView({behavior: "smooth"}); }

  $("#scAdd").onclick = () => {
    const ls = collect(); if (ls.length >= (scfg?.max_lines || 4)) return toast(`max ${scfg.max_lines} lines`);
    const after = Math.min((SC.sentences || []).length - 1, ls.length ? ls[ls.length - 1].after + 1 : 0);
    $("#scLines").insertAdjacentHTML("beforeend", lineRow({after, text: "", shot: scfg.shots[ls.length % 3]}, ls.length));
    markEmpty(); $("#scLines .sc-line:last-child .sc-text")?.focus();
  };
  const rawLines = () => $$("#scLines .sc-line").map(r => ({after: +$(".sc-after", r).value, text: $(".sc-text", r).value, shot: $(".sc-shot", r).value}));
  ["input", "change"].forEach(ev => $("#scLines").addEventListener(ev, () => { markEmpty(); if (SC) textDraft.set("s", SC.id, rawLines()); }));
  $("#scLines").addEventListener("click", e => { if (e.target.classList.contains("sc-del")) { e.target.closest(".sc-line").remove(); markEmpty(); } });
  $("#scSuggest").onclick = async () => {  // free: written locally from your transcript, no Comfy job
    const b = $("#scSuggest"); b.disabled = true; b.classList.add("is-loading"); window.uxSuggesting = true; window.uxSync?.();
    try {
      const r = await api(`/api/scene/${SC.id}/suggest`, {method: "POST"});
      $("#scLines").innerHTML = r.lines.map((l, k) => lineRow({...l, shot: scfg.shots[k % 3]}, k)).join("");
      textDraft.set("s", SC.id, rawLines()); markEmpty();
      const f = $("#scField"); f.classList.remove("flash"); void f.offsetWidth; f.classList.add("flash");
      $("#scFilled").hidden = false; setTimeout(() => { $("#scFilled").hidden = true; }, 6000);
      toast("Lines added — edit if you like");
    } catch (e) { toast(e.message, 4000); }
    finally { b.disabled = false; b.classList.remove("is-loading"); window.uxSuggesting = false; window.uxSync?.(); }
  };
  async function saveLines() { SC = await api(`/api/scene/${SC.id}/lines`, {json: {lines: collect()}}); textDraft.clear("s", SC.id); $("#scDialogue").dataset.done = ""; render(); }
  $("#scSave").onclick = () => saveLines().then(() => toast("Saved")).catch(e => toast(e.message));
  $("#scGen").onclick = async () => {
    try {
      await saveLines();
      if (!SC.lines.length) return toast("Write at least one line for B");
      const e = SC.estimate;
      if (e.n_jobs) {
        if (!canPay()) return needOwnKey(e.n_jobs);
        const ok = await confirmBox("Generate the scene?", `<p><b>${e.n_jobs}</b> Comfy Cloud jobs ≈ <b>$${e.usd.toFixed(2)}</b>: ${Object.entries(e.jobs).filter(([, v]) => v).map(([k, v]) => `${v}× ${k}`).join(", ")}.</p><p class="note">Partner stills (Qwen-Image-Edit), B's lines (LTX-2.3 ID LoRA, exact words), your side angle (CrossView re-shoot). Then a free render.</p>`, "Generate");
        if (!ok) return;
      }
      SC = await api(`/api/scene/${SC.id}/generate`, {json: {confirm: true}, withKey: true}); render(); refresh();
    } catch (err) { toast(err.message, 5000); }
  };
  $("#scRender").onclick = async () => { try { await saveLines(); SC = await api(`/api/scene/${SC.id}/render`, {json: {}}); refresh(); } catch (e) { toast(e.message); } };
  $("#scLabels").onchange = render;
  window.addEventListener("hashchange", () => { if (sid()) show(); });
  loadCfg().then(() => { syncOpts(); if (sid()) show(); });
})();
