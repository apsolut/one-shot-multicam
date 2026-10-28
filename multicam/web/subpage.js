// Shared header for subpages (gallery, share pages): same markup + classes as home. Payment chip and
// recent-runs icon open their sheets on the home page (/#pay, /#recent); state comes from this browser only.
// A copy lives in docs/assets/ (scripts/sync_pages_assets.py). On the GitHub Pages copy of the gallery
// (*.github.io) there is no app, so home links point at the live app and a "Try it live" pill replaces the
// payment chip and recent-runs icon (their state lives on the app's origin).
(function () {
  const LIVE = "https://one-shot-multicam.fly.dev/";
  const PAGES = /\.github\.io$/.test(location.hostname);
  const HOME = PAGES ? LIVE : "/";
  const CLOCK = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3 4v4h4"/><path d="M12 7.5V12l3 2"/></svg>';
  const BACK = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>';
  const REPO = "https://github.com/apsolut/one-shot-multicam";
  function header(active, title) {
    const cur = k => (k === active ? ' class="active" aria-current="page"' : "");
    const app = PAGES
      ? `<a class="hdr-live" href="${LIVE}">Try it live</a>`
      : `<a class="hdr-pill" id="payPill" href="/#pay"><span class="pp-dot" aria-hidden="true"></span><span id="payPillT">Payment</span></a>
        <a class="hdr-ic" id="histBtn" href="/#recent" aria-label="Your recent runs">${CLOCK}<span class="hdr-lbl">Recent</span><span class="hdr-dot" id="histDot" hidden></span></a>`;
    return `<header class="top"><a class="brand" href="${HOME}" aria-label="Multicam home"><span class="logo"><i></i><i></i><i></i></span>Multicam</a>
      <nav><a href="${HOME}" class="nav-new">New project</a>
        ${app}
        <a href="${PAGES ? "./" : "/gallery/"}" class="nav-gal${active === "gallery" ? " active" : ""}"${active === "gallery" ? ' aria-current="page"' : ""}>Gallery</a><a id="repoLink" href="${REPO}" target="_blank" rel="noopener">GitHub</a></nav></header>
      <nav class="crumb" aria-label="Breadcrumb"><a href="${HOME}">${BACK}Back to Multicam</a>${title ? `<span aria-hidden="true">/</span><span>${title}</span>` : ""}</nav>`;
  }
  function state() {
    let txt = "Free run";
    try {
      if (localStorage.getItem("multicam.comfyKey")) txt = "Your key";
      else if (localStorage.getItem("multicam.judgeCode")) txt = "Promo code";
      const h = JSON.parse(localStorage.getItem("multicam.history") || "[]");
      const dot = document.getElementById("histDot"); if (dot) dot.hidden = !(Array.isArray(h) && h.some(x => x && x.busy));
    } catch (e) { /* storage blocked */ }
    const t = document.getElementById("payPillT"); if (t) t.textContent = txt;
  }
  window.MCSubpage = {home: HOME, pages: PAGES, mount(el, active, title) { el.outerHTML = header(active, title); state(); }};
})();
