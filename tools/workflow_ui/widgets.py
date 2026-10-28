import asyncio, json, sys
from playwright.async_api import async_playwright
async def main(files, out):
    res = {}
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome", args=["--no-sandbox"])
        pg = await b.new_page()
        await pg.goto("http://127.0.0.1:8841/", wait_until="networkidle")
        await pg.wait_for_function("window.app && window.app.graph", timeout=60000)
        await pg.wait_for_timeout(800)
        for f in files:
            r = await pg.evaluate("""async (wf) => { await window.app.loadGraphData(wf, true, true, 'x.json');
              return window.app.graph.nodes.map(n => [n.type, (n.widgets || []).map(w => w.name)]); }""", json.load(open(f)))
            for t, names in r:
                if t in ("MarkdownNote", "Note"): continue
                key = t
                if key in res and res[key] != names: raise SystemExit(f"{t}: widget lists differ between instances {res[key]} vs {names}")
                res[key] = names
    json.dump(dict(sorted(res.items())), open(out, "w"), indent=1)
    print(len(res), "classes")
asyncio.run(main(sys.argv[1:-1], sys.argv[-1]))
