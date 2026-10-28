"""The ComfyUI workflows the app runs ship in workflows/: bindings point at real nodes, the exported translate
graph matches the one dub.py builds, nothing private (paths, keys) is in them, and the README covers each one."""
import json
import re
from pathlib import Path

from multicam.comfy import load_bindings, patch_workflow
from multicam.dub import translate_workflow

WF = Path(__file__).resolve().parent.parent / "workflows"
APIS = sorted(WF.glob("*_api.json"))


def _pairs(v):
    return [v] if isinstance(v, list) and v and isinstance(v[0], str) else v


def test_every_workflow_has_bindings_that_point_at_real_inputs():
    assert {p.name for p in APIS} >= {"crossview_api.json", "idlora_api.json", "dub_api.json", "qwen_angles_api.json",
                                      "scene_edit_api.json", "translate_api.json"}
    for api in APIS:
        wf = json.loads(api.read_text())
        b = load_bindings(WF / api.name.replace("_api.json", "_bindings.json"))
        assert b, api.name
        for key, targets in b.items():
            for node, inp in _pairs(targets):
                assert node in wf, f"{api.name}: {key} -> missing node {node}"
                assert inp in wf[node]["inputs"], f"{api.name}: {key} -> {node}.{inp} missing"


def test_translate_export_matches_the_graph_dub_builds():
    exported = json.loads((WF / "translate_api.json").read_text())
    built = translate_workflow(["a", "b"], "de")
    strip = lambda w: {k: {"class_type": n["class_type"], "inputs": {a: v for a, v in n["inputs"].items() if a != "prompt"}}
                       for k, n in w.items()}
    assert strip(exported) == strip(built)
    patched = patch_workflow(exported, load_bindings(WF / "translate_bindings.json"), {"prompt": "x"})
    assert patched["2"]["inputs"]["prompt"] == "x"


def test_no_private_paths_or_keys_in_workflows():
    bad = re.compile(r"(/home/|/workspace/|/Users/|[A-Za-z]:\\\\|comfyui-[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{16,}|"
                     r"api[_-]?key\"\s*:\s*\"[^\"]+)", re.I)
    for f in WF.glob("*.json"):
        assert not bad.search(f.read_text()), f.name


def test_readme_covers_every_workflow():
    readme = (WF / "README.md").read_text()
    for api in APIS:
        assert api.name in readme, api.name
    main = (WF.parent / "README.md").read_text()
    assert "workflows/README.md" in main
