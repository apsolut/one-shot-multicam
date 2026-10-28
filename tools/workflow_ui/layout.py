"""layout.py NAME RAW_UI.json API.json BINDINGS.json OUT_UI.json OUT_IDMAP.json
Takes the frontend's own loadApiJson() serialization (correct widget order by construction) and only changes
presentation: integer node ids, left-to-right columns by dependency depth, stage / branch groups, a notes node,
highlighted app-set nodes. Widgets, inputs and links are left as the frontend wrote them."""
import json, re, sys
from collections import defaultdict

name, raw_p, api_p, bind_p, out_p, map_p = sys.argv[1:7]
ui = json.load(open(raw_p)); api = json.load(open(api_p)); braw = json.load(open(bind_p))
INFO = json.load(open(sys.argv[7])) if len(sys.argv) > 7 else {}

STAGES = [
    ("Inputs", "#3f789e", {"LoadImage", "LoadVideo", "LoadAudio", "PrimitiveInt", "PrimitiveFloat", "PrimitiveStringMultiline",
                           "PrimitiveBoolean", "GetVideoComponents", "ResizeImageMaskNode", "FluxKontextImageScale", "LTXVPreprocess",
                           "ComfyMathExpression", "SolidMask", "ComfySwitchNode"}),
    ("Models + LoRAs", "#8A8", {"CheckpointLoaderSimple", "LTXVAudioVAELoader", "LTXAVTextEncoderLoader", "LoraLoaderModelOnly",
                                "LTXICLoRALoaderModelOnly", "LatentUpscaleModelLoader", "UNETLoader", "CLIPLoader", "VAELoader",
                                "ModelSamplingAuraFlow", "CFGNorm"}),
    ("Prompt + conditioning", "#a1309b", {"CLIPTextEncode", "TextEncodeQwenImageEditPlus", "LTXVConditioning",
                                          "FluxKontextMultiReferenceLatentMethod", "LTXAddVideoICLoRAGuide", "LTXVSetAudioRefTokens",
                                          "LTXVAudioVAEEncode", "LTXVImgToVideoInplace", "EmptyLTXVLatentVideo", "LTXVEmptyLatentAudio",
                                          "LTXVConcatAVLatent", "VAEEncode", "LTXVReferenceAudio"}),
    ("Sampling", "#b06634", {"RandomNoise", "KSamplerSelect", "ManualSigmas", "CFGGuider", "SamplerCustomAdvanced", "KSampler",
                             "LTXVSeparateAVLatent", "LTXVCropGuides", "LTXVLatentUpsampler", "TextGenerate"}),
    ("Decode + output", "#88A", {"VAEDecode", "VAEDecodeTiled", "LTXVAudioVAEDecode", "CreateVideo", "SaveVideo", "SaveImage", "PreviewAny"}),
]
def stage_of(t):
    for i, (_, _, s) in enumerate(STAGES):
        if t in s: return i
    raise SystemExit(f"unclassified node type {t}")

# ---- ids: the frontend wrote "427_221_169" for API id "427:221:169"; make every id an integer
api_of = {}  # ui id (after renumber) -> api id
by_ui = {}
used = {n["id"] for n in ui["nodes"] if isinstance(n["id"], int)}
nxt = max(used | {0}) + 1
remap = {}
for n in ui["nodes"]:
    old = n["id"]; aid = str(old).replace("_", ":")
    assert aid in api, (old, aid)
    if not isinstance(old, int):
        remap[old] = nxt; n["id"] = nxt; nxt += 1
    api_of[n["id"]] = aid; by_ui[n["id"]] = n
for l in ui["links"]:
    l[1] = remap.get(l[1], l[1]); l[3] = remap.get(l[3], l[3])

# ---- tidy: drop null-valued slot fields the serializer emits
for n in ui["nodes"]:
    for k in ("inputs", "outputs"):
        n[k] = [{a: b for a, b in s.items() if b is not None} for s in n.get(k, [])]
    for k in [k for k, v in n.items() if v is None]: del n[k]
    n.setdefault("properties", {})["api_id"] = api_of[n["id"]]

# ---- seeds: "fixed" so the graph reproduces the submitted values when queued (the app sets seeds per job)
ORDER = json.load(open(sys.argv[8])) if len(sys.argv) > 8 else {}
for n in ui["nodes"]:
    names = ORDER.get(n["type"], [])
    for k, nm in enumerate(names):
        if nm == "control_after_generate" and k < len(n.get("widgets_values", [])):
            n["widgets_values"][k] = "fixed"

# ---- app bindings -> highlight + title
binds = defaultdict(list)
for key, targets in braw.items():
    if key.startswith("_"): continue
    for node, inp in targets: binds[node].append(f"{key}→{inp}")
for n in ui["nodes"]:
    b = binds.get(api_of[n["id"]])
    if b:
        n["title"] = f"{n['type']} · app sets: " + ", ".join(sorted(set(k.split('→')[0] for k in b)))
        n["color"], n["bgcolor"] = "#6b6b1a", "#3d3d12"

# ---- depth (longest path from sources) and lanes (API subgraph path, e.g. 427:221)
src = defaultdict(list)
for l in ui["links"]: src[l[3]].append(l[1])
depth = {}
def d(i, seen=()):
    if i in depth: return depth[i]
    depth[i] = 0 if not src[i] else 1 + max(d(j) for j in src[i])
    return depth[i]
for i in by_ui: d(i)
# sources (no inputs: loaders, noise, sigmas, primitives) sit one column left of their first consumer, not at x=0
for i in by_ui:
    if not src[i]:
        cons = [l[3] for l in ui["links"] if l[1] == i]
        if cons: depth[i] = max(0, min(depth[c] for c in cons) - 1)
lane_of = {i: api_of[i].rsplit(":", 1)[0] if ":" in api_of[i] else "" for i in by_ui}
lanes = sorted(set(lane_of.values()), key=lambda s: (s != "", [int(x) for x in s.split(":")] if s else []))
multi = sum(1 for s in lanes if s) >= 3

# lane labels from the SaveImage outputs each branch feeds (_outputs in the bindings)
outs = braw.get("_outputs", {})
down = defaultdict(list)
for l in ui["links"]: down[l[1]].append(l[3])
label = {}
for i in by_ui:
    if lane_of[i]: continue
    nm = outs.get(api_of[i])
    if not nm: continue
    stack, seen = [s for s in src[i]], set()
    while stack:
        j = stack.pop()
        if j in seen: continue
        seen.add(j)
        if lane_of[j]: label.setdefault(lane_of[j], nm)
        stack += src[j]
lane_title = INFO.get("lane_titles", {})

GAPX, GAPY, PAD, HEAD = 70, 36, 30, 50
cols = sorted(set(depth.values()))
colw = {c: max(by_ui[i]["size"][0] for i in by_ui if depth[i] == c) for c in cols}
colx, x = {}, 0
for c in cols: colx[c] = x; x += colw[c] + GAPX
total_w = x - GAPX

groups, y = [], 0
note_h = 0
if INFO.get("note"):
    note_h = INFO.get("note_h", 520)
    y = note_h + 80

def place(ids, title, color, y0):
    """columns by depth inside one band starting at y0 -> band height"""
    per = defaultdict(list)
    for i in ids: per[depth[i]].append(i)
    h = 0
    for c, lst in per.items():
        lst.sort(key=lambda i: (stage_of(by_ui[i]["type"]), api_of[i]))
        yy = y0 + HEAD
        for i in lst:
            by_ui[i]["pos"] = [colx[c], yy]; yy += by_ui[i]["size"][1] + GAPY
        h = max(h, yy - y0)
    xs = [by_ui[i]["pos"][0] for i in ids]; xe = [by_ui[i]["pos"][0] + by_ui[i]["size"][0] for i in ids]
    groups.append({"title": title, "bounding": [min(xs) - PAD, y0, max(xe) - min(xs) + 2 * PAD, h + PAD - GAPY],
                   "color": color, "font_size": 22, "flags": {}})
    return h + PAD

for lane in lanes:
    ids = [i for i in by_ui if lane_of[i] == lane]
    if multi and lane:
        nm = label.get(lane, lane)
        y += place(ids, lane_title.get(nm, f"Branch {lane} → {nm}"), "#444", y) + 40
        continue
    for si, (sname, color, _) in enumerate(STAGES):
        sids = [i for i in ids if stage_of(by_ui[i]["type"]) == si]
        if not sids: continue
        prefix = INFO.get("stage_titles", {}).get(sname, sname)
        y += place(sids, prefix if not lane or not multi else f"{prefix}", color, y) + 40

for k, g in enumerate(groups, 1): g["id"] = k
nodes = ui["nodes"]
if INFO.get("note"):
    nid = max(by_ui) + 1
    nodes.insert(0, {"id": nid, "type": "MarkdownNote", "pos": [0, 0], "size": [min(max(900, total_w), 1400), note_h],
                     "flags": {}, "order": 0, "mode": 0, "inputs": [], "outputs": [], "title": "About this workflow",
                     "properties": {}, "widgets_values": [INFO["note"]], "color": "#432", "bgcolor": "#653"})
ui["groups"] = groups
ui["last_node_id"] = max(n["id"] for n in nodes)
ui["last_link_id"] = max([l[0] for l in ui["links"]] or [0])
ui["version"] = 0.4
ui["id"] = str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, f"multicam/workflows/{name}_ui.json"))  # stable
ui["extra"] = {"ds": {"scale": 0.55, "offset": [60, 60]}}
for k in ("floatingLinks",):
    if not ui.get(k): ui.pop(k, None)
json.dump(ui, open(out_p, "w"), indent=1, ensure_ascii=False)
json.dump({a: str(i) for i, a in api_of.items()}, open(map_p, "w"))
print(f"{name}: {len(nodes)} nodes, {len(ui['links'])} links, {len(groups)} groups, lanes={len(lanes)} multi={multi}")
