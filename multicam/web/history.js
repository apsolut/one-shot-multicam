// "Your recent runs": a capped, newest-first history (in this browser only) of every project / scene / dub
// link opened or created here. Pure helpers, shared by ux.js and tests/test_history.py (run under node).
(function (root) {
  const LS = "multicam.history", MAX = 10, HASH = /^[psd]=[0-9a-f]{6,64}$/;
  const MODE = {p: null, s: "scene", d: "dub"};
  function parse(raw) {
    let v; try { v = JSON.parse(raw || "[]"); } catch { return []; }
    return Array.isArray(v) ? v.filter(x => x && typeof x.hash === "string" && HASH.test(x.hash)).slice(0, MAX) : [];
  }
  // newest first, one row per link; a later visit keeps what we already knew (name, thumb) unless it has better
  function add(list, item, now) {
    if (!item || !HASH.test(item.hash || "")) return list.slice(0, MAX);
    const prev = list.find(x => x.hash === item.hash) || {};
    const merged = {...prev};
    for (const k of ["mode", "name", "thumb"]) if (item[k]) merged[k] = item[k];
    merged.hash = item.hash; merged.t = now ?? Date.now(); merged.t0 = prev.t0 || merged.t;
    if (!merged.mode) merged.mode = MODE[item.hash[0]] || "talk";
    return [merged, ...list.filter(x => x.hash !== item.hash)].slice(0, MAX);
  }
  const drop = (list, hashes) => list.filter(x => !hashes.includes(x.hash));
  const apiPath = h => (h[0] === "p" ? "/api/projects/" : h[0] === "s" ? "/api/scene/" : "/api/dub/") + h.slice(2);
  function ago(t, now) {
    const s = Math.max(0, ((now ?? Date.now()) - t) / 1000);
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return new Date(t).toLocaleDateString(undefined, {month: "short", day: "numeric"});
  }
  const api = {LS, MAX, parse, add, drop, apiPath, ago};
  root.MCHistory = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
