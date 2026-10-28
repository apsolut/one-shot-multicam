"""Stub backend: serves the official ComfyUI frontend (pip comfyui-frontend-package) with Comfy Cloud node
definitions, so graphs can be loaded, converted and checked headless. Renders only, never runs anything."""
import json, os, sys
import comfyui_frontend_package as c
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn
STATIC = os.path.join(os.path.dirname(c.__file__), "static")
HERE = os.path.dirname(os.path.abspath(__file__))
OI = json.load(open(os.environ.get("COMFY_OBJECT_INFO", os.path.join(HERE, "object_info.json"))))
# the placeholder file names used in workflows/*_api.json must be valid choices on the loader nodes
_names = {"LoadVideo": set(), "LoadImage": set(), "LoadAudio": set()}
for _f in __import__("glob").glob(os.path.join(HERE, "..", "..", "workflows", "*_api.json")):
    for _n in json.load(open(_f)).values():
        if _n["class_type"] in _names:
            _names[_n["class_type"]].update(v for v in _n["inputs"].values() if isinstance(v, str))
for _t, _vals in _names.items():
    for _spec in OI[_t]["input"]["required"].values():
        if _spec[0] == "COMBO" and "options" in _spec[1]: _spec[1]["options"][:0] = sorted(_vals)
        elif isinstance(_spec[0], list): _spec[0][:0] = sorted(_vals)
app = FastAPI()
LOG = open(os.devnull, "a")

@app.middleware("http")
async def log(req: Request, call_next):
    r = await call_next(req)
    LOG.write(f"{r.status_code} {req.method} {req.url.path}?{req.url.query}\n"); LOG.flush()
    return r

@app.websocket("/ws")
async def ws(w: WebSocket):
    await w.accept()
    await w.send_json({"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 0}}, "sid": "x"}})
    try:
        while True:
            await w.receive_text()
    except Exception:
        pass

@app.get("/api/object_info")
def oi(): return OI
@app.get("/api/system_stats")
def ss(): return {"system": {"os": "posix", "comfyui_version": "0.3.70", "python_version": "3.13", "pytorch_version": "2", "embedded_python": False, "argv": [], "ram_total": 1, "ram_free": 1, "required_frontend_version": "1.58.1"}, "devices": []}
@app.get("/api/settings")
def st(): return {"Comfy.TutorialCompleted": True, "Comfy.Workflow.ShowMissingNodesWarning": False, "Comfy.Workflow.ShowMissingModelsWarning": False, "Comfy.ColorPalette": "dark", "Comfy.UseNewMenu": "Top", "Comfy.Minimap.Visible": False}
@app.post("/api/settings")
@app.post("/api/settings/{k}")
def sts(k: str = ""): return {}
@app.get("/api/settings/{k}")
def stk(k: str): return JSONResponse(None)
@app.get("/api/users")
def users(): return {"storage": "server", "migrated": True}
@app.get("/api/extensions")
def ext(): return []
@app.get("/api/embeddings")
def emb(): return []
@app.get("/api/features")
def feat(): return {}
@app.get("/api/i18n")
def i18n(): return {}
@app.get("/api/queue")
def q(): return {"queue_running": [], "queue_pending": []}
@app.get("/api/history")
def h(): return {}
@app.get("/api/userdata")
def ud(): return []
@app.get("/api/v2/userdata")
def ud2(): return []
@app.get("/api/userdata/{p:path}")
def udp(p: str): return Response(status_code=404)
@app.post("/api/userdata/{p:path}")
def udpp(p: str): return {}
@app.get("/api/workflow_templates")
def wt(): return {}
@app.get("/templates/index.json")
def ti(): return []
@app.get("/api/experiment/models")
def em(): return []
@app.get("/api/experiment/models/{f}")
def emf(f: str): return []
@app.get("/api/{rest:path}")
def any_api(rest: str): return JSONResponse({})
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8840, log_level="warning")
