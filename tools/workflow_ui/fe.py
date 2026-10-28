"""Drive the official ComfyUI frontend (stub backend on :8841, Comfy Cloud node defs) headless.
fe.py api2ui IN_API.json OUT_UI_RAW.json OUT_PROMPT.json   loadApiJson -> graph.serialize(), graphToPrompt()
fe.py ui2api IN_UI.json OUT_PROMPT.json                     loadGraphData -> graphToPrompt()"""
import asyncio, json, os, sys
from playwright.async_api import async_playwright

async def main(cmd, args):
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome", args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1600, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
        pg.on("console", lambda m: errs.append(m.text[:300]) if m.type == "error" else None)
        await pg.goto("http://127.0.0.1:8841/", wait_until="networkidle")
        await pg.wait_for_function("window.app && window.app.graph", timeout=60000)
        await pg.wait_for_timeout(1000)
        wf = json.load(open(args[0]))
        for o in args[1:]: os.makedirs(os.path.dirname(os.path.abspath(o)), exist_ok=True)
        if cmd == "api2ui":
            res = await pg.evaluate("""async (wf) => { await window.app.loadApiJson(wf, 'x.json');
                const ui = window.app.graph.serialize(); const pr = await window.app.graphToPrompt();
                return {ui, prompt: pr.output}; }""", wf)
            json.dump(res["ui"], open(args[1], "w"), indent=1)
            json.dump(res["prompt"], open(args[2], "w"), indent=1)
        else:
            res = await pg.evaluate("""async (wf) => { await window.app.loadGraphData(wf, true, true, 'x.json');
                const pr = await window.app.graphToPrompt(); return {prompt: pr.output,
                missing: [...new Set(window.app.graph.nodes.filter(n => n.has_errors || n.type === undefined).map(n => n.type))]}; }""", wf)
            json.dump(res["prompt"], open(args[1], "w"), indent=1)
            if res["missing"]: errs.append("nodes with errors: " + ",".join(map(str, res["missing"])))
        await b.close()
        for e in errs: print("  [frontend]", e)

asyncio.run(main(sys.argv[1], sys.argv[2:]))
