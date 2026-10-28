"""'Download ComfyUI workflow': submit() archives the exact graph; projects export + serve it (no Comfy calls)."""
import io
import json
import zipfile

from multicam import comfy
from multicam.util import save_json


class _Resp:
    ok = True

    def __init__(self, pid):
        self._pid = pid

    def json(self):
        return {"prompt_id": self._pid}


def test_submit_archives_graph_without_key(tmp_path, monkeypatch):
    import requests
    comfy.set_workflow_archive(tmp_path / "wf")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp("abc-123"))
    g = {"6": {"class_type": "LTXICLoRALoaderModelOnly", "inputs": {"lora_name": "LTX2.3-22B_IC-LoRA-CrossView.safetensors"}}}
    assert comfy.submit(g, key="secret-key-value") == "abc-123"
    f = comfy.archived_workflow("abc-123")
    assert f and json.loads(f.read_text()) == g and "secret-key-value" not in f.read_text()
    out = comfy.export_workflows(["abc-123", "abc-123", "missing"], tmp_path / "proj" / "workflows")
    assert [p.name for p in out] == ["01_crossview-reshoot_abc-123.json"]
    assert comfy.archived_workflow("../etc/passwd") is None


def test_dub_endpoints_list_single_and_zip(tmp_path):
    from fastapi.testclient import TestClient
    from multicam.server import App, create_app
    A = App(tmp_path / "data")
    client = TestClient(create_app(A))  # sets the archive to <data>/workflows
    arch = A.data / "workflows"; arch.mkdir(parents=True, exist_ok=True)
    (arch / "p1.json").write_text(json.dumps({"1": {"class_type": "TextGenerate", "inputs": {}}}))
    (arch / "p2.json").write_text(json.dumps({"7": {"class_type": "LoadVideo", "inputs": {"lora": "lipdub"}}}))
    d = A.data / "dubs" / "abc123"; d.mkdir(parents=True)
    save_json({"id": "abc123", "created": 0}, d / "web.json")
    save_json({"lang": "de", "windows": [], "prompt_ids": ["p1", "p2"]}, d / "dub.json")
    r = client.get("/api/dub/abc123/workflows").json()
    assert [w["prompt_id"] for w in r["workflows"]] == ["p1", "p2"] and r["zip"]
    one = client.get("/api/dub/abc123/workflow/p2")
    assert one.status_code == 200 and json.loads(one.content)["7"]["class_type"] == "LoadVideo"
    z = client.get(r["zip"])
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert z.status_code == 200 and len([n for n in names if n.endswith(".json")]) == 2
    assert (d / "workflows").is_dir() and len(list((d / "workflows").glob("*.json"))) == 2  # saved in the project
    assert client.get("/api/dub/nope00/workflows").status_code == 404
