# workflow_ui: API JSON → ComfyUI graph JSON, checked by the real frontend

This folder builds and checks `workflows/*_ui.json`. Widget order is never guessed. The official ComfyUI frontend
does the conversion itself (`app.loadApiJson`), using Comfy Cloud's own node definitions. These scripts only
change the presentation: integer node ids, columns by dependency depth, stage and branch groups, a notes node,
and yellow on the nodes the app sets. The graph is then loaded back (`app.loadGraphData` → `graphToPrompt`) and
compared with the original API JSON.

```bash
python -m venv .venv && .venv/bin/pip install comfyui-frontend-package fastapi uvicorn websockets playwright
.venv/bin/playwright install chromium     # or use an installed Chrome (fe.py uses channel="chrome")
COMFY_API_KEY=... .venv/bin/python fetch_object_info.py        # object_info.json (not committed)
.venv/bin/python stub.py 8841 &                                # frontend + node defs, renders only
.venv/bin/python fe.py api2ui ../../workflows/dub_api.json out/dub_raw.json out/dub_p1.json
.venv/bin/python build.py dub                                  # -> workflows/dub_ui.json
.venv/bin/python fe.py ui2api ../../workflows/dub_ui.json out/dub_p2.json
python cmp.py ../../workflows/dub_api.json out/dub_p2.json out/dub_idmap.json   # expect 0 differences
.venv/bin/python widgets.py ../../workflows/{crossview,dub,translate,scene_edit}_ui.json ../../tests/fixtures/comfy_widget_order.json
```

`widgets.py` writes the widget order per node class that the frontend uses. `tests/test_workflows_ui.py` then
checks every `*_ui.json` against its `*_api.json` offline, with no network and no ComfyUI.
