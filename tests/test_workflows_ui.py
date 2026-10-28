"""workflows/*_ui.json (the graph you drag into ComfyUI) must describe exactly the graph in *_api.json (what the app
submits): same nodes and classes, every literal input in the right widget slot, every link wired to the same
source output. Widget order per node class comes from the official ComfyUI frontend loaded with Comfy Cloud's node
definitions (tests/fixtures/comfy_widget_order.json, see tools/workflow_ui/). No network, no ComfyUI needed."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / "workflows"
ORDER = json.loads((ROOT / "tests" / "fixtures" / "comfy_widget_order.json").read_text())
FLAT = ["crossview", "dub", "translate", "scene_edit"]      # flat graphs: checked node by node
SUBGRAPH = ["idlora", "qwen_angles"]                        # official Comfy templates (subgraphs), checked by the frontend tool
NOTES = {"MarkdownNote", "Note"}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def test_every_api_workflow_has_a_ui_version():
    for api in WF.glob("*_api.json"):
        assert (WF / api.name.replace("_api.json", "_ui.json")).is_file(), api.name


@pytest.mark.parametrize("name", FLAT)
def test_ui_matches_api(name):
    api = json.loads((WF / f"{name}_api.json").read_text())
    ui = json.loads((WF / f"{name}_ui.json").read_text())
    assert ui.get("version") == 0.4 and ui["last_node_id"] >= max(n["id"] for n in ui["nodes"])
    assert ui["last_link_id"] >= max([l[0] for l in ui["links"]] or [0])
    nodes = [n for n in ui["nodes"] if n["type"] not in NOTES]
    api_id = {n["id"]: str(n.get("properties", {}).get("api_id", n["id"])) for n in nodes}
    by_api = {api_id[n["id"]]: n for n in nodes}
    assert set(by_api) == set(api), f"node sets differ: {set(api) ^ set(by_api)}"
    links = {l[0]: l for l in ui["links"]}
    n_api_links = 0
    for aid, a in api.items():
        n = by_api[aid]
        assert n["type"] == a["class_type"], aid
        names = ORDER[n["type"]]
        wv = n.get("widgets_values", [])
        assert len(wv) == len(names), f"{name} {aid} {n['type']}: {len(wv)} widget values for {names}"
        widgets = dict(zip(names, wv))
        slots = {s["name"]: s for s in n.get("inputs", [])}
        for k, v in a["inputs"].items():
            if isinstance(v, list):  # a link: [source api id, output slot]
                n_api_links += 1
                s = slots.get(k)
                assert s and s.get("link") is not None, f"{name} {aid}.{k} not linked"
                lk = links[s["link"]]
                assert api_id[lk[1]] == str(v[0]) and lk[2] == v[1], f"{name} {aid}.{k}: wired to {lk[1]}:{lk[2]}, want {v}"
                assert lk[3] == n["id"] and n["inputs"][lk[4]]["name"] == k
            else:
                assert k in widgets, f"{name} {aid} {n['type']}.{k}: no widget"
                assert _num(widgets[k]) == _num(v), f"{name} {aid} {n['type']}.{k}: {widgets[k]!r} != {v!r}"
        if "control_after_generate" in widgets and name != "crossview":
            assert widgets["control_after_generate"] == "fixed"  # queued as-is, the graph reproduces the submitted seeds
    assert len(ui["links"]) == n_api_links


@pytest.mark.parametrize("name", SUBGRAPH)
def test_template_ui_files_are_subgraph_graphs(name):
    ui = json.loads((WF / f"{name}_ui.json").read_text())
    assert ui.get("version") == 0.4 and ui["nodes"] and ui["definitions"]["subgraphs"]


def test_ui_files_have_no_private_paths_or_keys():
    import re
    bad = re.compile(r"(/home/|/workspace/|/Users/|comfyui-[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{16,}|api[_-]?key\"\s*:\s*\"[^\"]+)", re.I)
    for f in WF.glob("*_ui.json"):
        assert not bad.search(f.read_text()), f.name
