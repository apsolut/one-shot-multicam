"""cmp.py API.json PROMPT.json [idmap.json]: compare a frontend graphToPrompt() output with the original API JSON."""
import json, sys
def norm(v): return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
def compare(api, pr, idmap):
    inv = {v: k for k, v in idmap.items()}  # prompt id -> api id
    pr = {inv.get(k, k): v for k, v in pr.items()}
    diffs, extras = [], []
    if set(api) != set(pr): diffs.append(f"node sets differ: missing {set(api)-set(pr)} extra {set(pr)-set(api)}")
    for nid, n in api.items():
        q = pr.get(nid)
        if not q: continue
        if q["class_type"] != n["class_type"]: diffs.append(f"{nid}: class {n['class_type']} vs {q['class_type']}")
        for k, v in n["inputs"].items():
            w = q["inputs"].get(k, "<missing>")
            if isinstance(v, list) and isinstance(w, list): w = [inv.get(str(w[0]), str(w[0])), w[1]]; v = [str(v[0]), v[1]]
            if norm(v) != norm(w): diffs.append(f"{nid} {n['class_type']}.{k}: {str(v)[:60]!r} -> {str(w)[:60]!r}")
        for k in set(q["inputs"]) - set(n["inputs"]): extras.append(f"{n['class_type']}.{k}={str(q['inputs'][k])[:40]}")
    return diffs, sorted(set(extras))
if __name__ == "__main__":
    api = json.load(open(sys.argv[1])); pr = json.load(open(sys.argv[2]))
    idmap = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else {k: k.replace(":", "_") for k in api}
    d, e = compare(api, pr, idmap)
    print(f"  {len(d)} differences, {len(e)} extra default inputs")
    for x in d[:30]: print("   DIFF", x)
    for x in e: print("   extra", x)
