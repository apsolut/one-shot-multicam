"""build.py NAME...: out/NAME_raw.json (from fe.py api2ui) -> workflows/NAME_ui.json with layout, groups and notes."""
import json, subprocess, sys
import os
HERE = os.path.dirname(os.path.abspath(__file__))
WF = os.path.join(HERE, "..", "..", "workflows")
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)
PY = sys.executable
def bt(name):
    b = json.load(open(f"{WF}/{name}_bindings.json"))
    rows = "\n".join(f"| `{k}` | " + ", ".join(f"`{n}.{i}`" for n, i in v) + " |" for k, v in b.items() if not k.startswith("_"))
    return "| App input | API node.input |\n|---|---|\n" + rows
COMMON = ("\n\n**Nodes in yellow are the ones the app sets on every job** (see `{n}_bindings.json`); every other value is "
          "exactly what the app submits. Each node keeps its API id in Properties → `api_id`.\n\n"
          "This is a view of `workflows/{n}_api.json` (what the app sends to Comfy Cloud). All models are open weights; "
          "file names and download links: `workflows/README.md`.")
INFO = {
 "dub": {"note": "# Dub · LipDub / DubIt (LTX-2.3)\n\nYour take is the IC-LoRA reference (downscale 1). Its audio only conditions the "
         "**voice**; new speech in the target language is generated from the prompt, with matching lips.\n\n"
         "- Models: LTX-2.3 22B dev fp8 + distilled 1.1 LoRA (0.5) + LipDub IC-LoRA 0.9 (1.0), Gemma 3 12B fp4 text encoder, x2 spatial upscaler\n"
         "- Stage 1 at half resolution (8 sigmas), latent x2 upscale, stage 2 at 704×1280 (3 sigmas, stage-1 speech frozen)\n\n"
         + bt("dub") + COMMON.format(n="dub"), "note_h": 640},
 "translate": {"note": "# Dub · translation (Gemma 3 12B it, bf16)\n\nThe core `TextGenerate` node translates numbered lines; the app "
         "builds this graph in `multicam/dub.py` and reads the reply from **Preview Any**. `sampling_mode: off` = greedy, so a re-run "
         "gives the same text. Replace the two placeholder lines in the prompt with your own.\n\n" + bt("translate") + COMMON.format(n="translate"),
         "note_h": 460},
 "scene_edit": {"note": "# Scene · partner stills (Qwen-Image-Edit 2511, 8 edits in one job)\n\nThe Qwen *Multiple Character Angles* "
         "template with all 8 branches exposed: each branch has its own image (`img1..8`, or `image` for all), prompt (`p1..8`), seed "
         "(`s1..8`) and Multiple-Angles LoRA strength (`l1..8`, `angle_lora` for all; 0 = plain Qwen-Image-Edit). Scene uses it for "
         "partner B single, over-the-shoulder and close-up framings. One group per branch, top to bottom.\n\n"
         "- Models: Qwen-Image-Edit 2511 bf16, Qwen 2.5 VL 7B fp8, Qwen-Image VAE, Lightning 4-step LoRA, Multiple-Angles LoRA\n\n"
         + COMMON.format(n="scene_edit"), "note_h": 520},
}
b = json.load(open(f"{WF}/scene_edit_bindings.json"))
INFO["scene_edit"]["lane_titles"] = {}
for k in range(1, 9):
    lane = b[f"p{k}"][0][0].rsplit(":", 1)[0]
    out = [o for o, nm in b["_outputs"].items()]
    INFO["scene_edit"].setdefault("_lane_prompt", {})[lane] = k
# output name -> title (outputs are e1..e8)
INFO["scene_edit"]["lane_titles"] = {f"e{k}": f"Edit {k} → SaveImage e{k}" for k in range(1, 9)}
for name in sys.argv[1:]:
    json.dump(INFO[name], open(f"{OUT}/{name}_info.json", "w"))
    subprocess.run([PY, os.path.join(HERE, "layout.py"), name, f"{OUT}/{name}_raw.json", f"{WF}/{name}_api.json",
                    f"{WF}/{name}_bindings.json", f"{WF}/{name}_ui.json", f"{OUT}/{name}_idmap.json", f"{OUT}/{name}_info.json", os.path.join(HERE, "..", "..", "tests", "fixtures", "comfy_widget_order.json")], check=True)
