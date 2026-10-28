// Dub mode — same take in another language, then a multicam edit. Uses helpers from app.js ($, $$, api, toast,
// confirmBox, esc, key, judge, canPay, file, mode).
(() => {
  let DB = null, dcfg = null, dpoll = null;
  const did = () => (location.hash.match(/d=([0-9a-f]+)/) || [])[1];

  async function loadCfg() {
    dcfg = await api("/api/dub-config").catch(() => null);
    if (!dcfg) return;
    $("#dubLang").innerHTML = Object.entries(dcfg.langs).map(([k, v]) =>
      `<option value="${k}">${esc(v.name)}${v.validated ? "" : " (experimental)"}</option>`).join("");
  }
  function syncOpts() { $("#dubOpts").hidden = mode !== "dub"; }
  $$("#mode button").forEach(b => b.addEventListener("click", () => setTimeout(syncOpts)));

  $("#uploadCard").addEventListener("click", async e => {
    if (mode !== "dub" || !e.target.closest("#start")) return;
    e.preventDefault(); e.stopPropagation();
    if (!file) return toast("Pick your video first");
    if (!$("#consent").checked) return toast("Dub is for your own face and voice — tick the box");
    const fd = new FormData();
    fd.append("file", file); fd.append("consent", "1"); fd.append("lang", $("#dubLang").value);
    fd.append("script", $("#script").value || "");
    const b = $("#start"); b.disabled = true; b.textContent = "Uploading…";
    try { DB = await api("/api/dub", {body: fd}); location.hash = `d=${DB.id}`; show(); }
    catch (err) { toast(err.message, 5000); }
    finally { b.textContent = "Analyze — free"; b.disabled = false; }
  }, true);

  function lineRow(w) {
    const f = w.fit, warn = f && !f.ok;
    return `<div class="db-line"><div class="db-src">${w.start.toFixed(1)}–${w.end.toFixed(1)}s · ${esc(w.text)}</div>
      <textarea class="db-text" maxlength="400" placeholder="translation (exact words to speak)">${esc(w.translation)}</textarea>
      <div class="db-fit ${warn ? "" : "ok"}">${f ? (warn ? `⚠ ${esc(f.hint)} (×${f.ratio})` : `fits (×${f.ratio})`) : ""}${w.dubbed ? " · dubbed ✓" : ""}</div></div>`;
  }
  const collect = () => $$("#dbLines .db-text").map(t => t.value.trim());
  function render() {
    $("#dubCard").hidden = !DB;
    if (!DB) return;
    $("#dbLangName").textContent = DB.lang_name || "…";
    $("#dbBusy").textContent = DB.busy ? `${DB.busy}…` : (DB.output ? "ready" : "edit");
    $("#dbBusy").className = "badge " + (DB.busy ? "warn" : DB.output ? "ok" : "");
    $("#dbWarn").hidden = DB.validated !== false;
    $("#dbWarn").textContent = `${DB.lang_name} is not one of LipDub's validated languages: expect an accent. Try it; listen before you share.`;
    $("#dbErr").hidden = !DB.error; $("#dbErr").textContent = DB.error || "";
    $("#dbLog").textContent = (DB.log || []).join("\n");
    const box = $("#dbLines"), sig = JSON.stringify((DB.windows || []).map(w => [w.translation, w.dubbed, w.fit?.ratio]));
    if (DB.windows && box.dataset.sig !== sig && !box.contains(document.activeElement)) {
      box.innerHTML = DB.windows.map(lineRow).join(""); box.dataset.sig = sig;
    }
    const e = DB.estimate;
    $("#dbEst").innerHTML = e ? `<span><b>${e.windows}</b> LipDub windows + <b>${e.inserts}</b> CrossView re-shoots ≈ <b>$${e.usd.toFixed(2)}</b></span>` : "";
    $("#dbGen").disabled = $("#dbTranslate").disabled = !!DB.busy || !DB.windows;
    const v = $("#dbPlayer"), want = $("#dbLabels").checked ? DB.output_labeled : DB.output;
    $("#dbResult").hidden = !DB.output;
    if (want && v.dataset.src !== want) { v.src = want; v.dataset.src = want; }
    if (DB.output) $("#dbDownload").href = DB.output;
    $("#dbTake").hidden = !DB.dubbed_take; if (DB.dubbed_take) $("#dbTake").href = DB.dubbed_take;
    if (!DB.busy && (DB.translated || DB.output)) wfLink($("#dbWf"), "dub", DB.id, `${DB.output || ""}|${(DB.windows || []).filter(w => w.dubbed).length}|${DB.translated}`);
  }
  async function refresh() {
    clearTimeout(dpoll);
    if (!did()) return;
    try { DB = await api(`/api/dub/${did()}`); render(); } catch (e) { if (e.status === 404) { DB = null; render(); return; } }
    if (DB && (DB.busy || !DB.windows)) dpoll = setTimeout(refresh, 2000);
  }
  function show() { $("#uploadCard").hidden = true; $(".hero").hidden = true; $("#dubCard").hidden = false; refresh();
    $("#dubCard").scrollIntoView({behavior: "smooth"}); }
  const ownKey = () => { toast("Paste your own Comfy Cloud key first (step 1) — get one at platform.comfy.org → API keys", 4500);
    $("#keyCard").classList.add("attention"); };
  async function save() { DB = await api(`/api/dub/${DB.id}/lines`, {json: {translations: collect()}}); $("#dbLines").dataset.sig = ""; render(); }

  $("#dbSave").onclick = () => save().then(() => toast("Saved")).catch(e => toast(e.message));
  $("#dbTranslate").onclick = async () => {
    if (!canPay()) return ownKey();
    try { DB = await api(`/api/dub/${DB.id}/translate`, {method: "POST", withKey: true}); render(); refresh(); }
    catch (e) { toast(e.message, 5000); }
  };
  $("#dbGen").onclick = async () => {
    try {
      await save();
      if (!(DB.windows || []).every(w => w.translation)) return toast("Translate (or type) every line first");
      if (!canPay()) return ownKey();
      const e = DB.estimate;
      const ok = await confirmBox(`Dub into ${DB.lang_name}?`, `<p><b>${e.windows}</b> LipDub windows (≈$${e.lipdub_usd.toFixed(2)}) + <b>${e.inserts}</b> CrossView re-shoots (≈$${e.reshoot_usd.toFixed(2)}) ≈ <b>$${e.usd.toFixed(2)}</b>.</p><p class="note">Only changed lines are re-dubbed on a second run. Then a free render.</p>`, "Dub");
      if (!ok) return;
      DB = await api(`/api/dub/${DB.id}/generate`, {json: {confirm: true}, withKey: true}); render(); refresh();
      if (cfg.trial) refreshCfg();
    } catch (err) { toast(err.message, 5000); }
  };
  $("#dbLabels").onchange = render;
  window.addEventListener("hashchange", () => { if (did()) show(); });
  loadCfg().then(() => { syncOpts(); if (did()) show(); });
})();
