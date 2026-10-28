"""'Your recent runs' (web/history.js): capped, newest first, one row per link, junk-proof parse. Runs under node."""
import json, shutil, subprocess
from pathlib import Path
import pytest

JS = Path(__file__).resolve().parents[1] / "multicam" / "web" / "history.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def run(body: str):
    src = f"const H = require({json.dumps(str(JS))});\n{body}"
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_newest_first_dedupe_and_cap():
    r = run("""let l = [];
      for (let i = 0; i < 14; i++) l = H.add(l, {hash: 's=' + (1000 + i).toString(16).padStart(8, '0')}, i);
      l = H.add(l, {hash: 's=000003f2'}, 99);   // revisit an existing one -> moves to the top, no duplicate
      console.log(JSON.stringify(l));""")
    assert len(r) == 10
    assert r[0]["hash"] == "s=000003f2" and r[0]["t"] == 99 and r[0]["mode"] == "scene"
    assert len({x["hash"] for x in r}) == 10
    assert r[1]["hash"] == "s=000003f5"  # then the newest of the rest


def test_revisit_keeps_known_name_and_thumb():
    r = run("""let l = H.add([], {hash: 'p=abcdef12', mode: 'music', name: 'take 1', thumb: '/api/x.jpg'}, 1);
      l = H.add(l, {hash: 'p=abcdef12', mode: null, name: '', thumb: ''}, 5);
      console.log(JSON.stringify(l));""")
    assert r == [{"hash": "p=abcdef12", "mode": "music", "name": "take 1", "thumb": "/api/x.jpg", "t": 5, "t0": 1}]


def test_parse_rejects_junk_and_drop():
    r = run("""const l = H.parse(JSON.stringify([{hash: 'd=00ff00ff', t: 1}, {hash: 'javascript:alert(1)'}, null, 7, {hash: 'p=xyz'}]));
      console.log(JSON.stringify({l, bad: H.parse('{oops'), obj: H.parse('{}'), dropped: H.drop(l, ['d=00ff00ff']),
        paths: ['p=ab12cd', 's=ab12cd', 'd=ab12cd'].map(H.apiPath)}));""")
    assert [x["hash"] for x in r["l"]] == ["d=00ff00ff"]
    assert r["bad"] == [] and r["obj"] == [] and r["dropped"] == []
    assert r["paths"] == ["/api/projects/ab12cd", "/api/scene/ab12cd", "/api/dub/ab12cd"]
