"""`multicam scene ...`: talk to your other self (shot / reverse-shot dialogue from one real take)."""
from __future__ import annotations

from pathlib import Path


def _confirm(a, est: dict) -> None:
    print(f"estimate: {est['n_jobs']} Comfy Cloud jobs {est['jobs']} ~ ${est['usd']:.2f}")
    if est["n_jobs"] and not a.yes:
        raise SystemExit("add --yes to spend the credits")


def _balance(tag: str):
    from .comfy import balance_usd
    b = balance_usd()
    if b is not None:
        print(f"Comfy balance {tag}: ${b:.4f}")
    return b


def cmd(a):
    from . import scene as S
    work = Path(a.workdir)
    if a.scmd == "init":
        sc = S.init(a.video, work, partner=a.partner, lines=a.line, look=a.look, photo=a.photo, character=a.character,
                    voice=a.voice, person=a.person, a_look=a.a_look, script=a.script, suggest=a.suggest,
                    transcript=a.transcript)
        if a.a_clips:
            S.import_a_clips(sc, work, a.a_clips)
        show(sc)
        return
    sc = S.load(work)
    if a.scmd == "show":
        show(sc); return
    if a.scmd == "lines":
        if a.suggest:
            from .util import load_json
            sc["lines"] = S.suggest_lines(load_json(sc["transcript"]), sc["partner"])
        elif a.line:
            from .util import load_json
            sc["lines"] = S.parse_lines(a.line, len(load_json(sc["transcript"])["sentences"]))
        S.save(work, sc); show(sc); return
    if a.scmd == "import-a":
        S.import_a_clips(sc, work, a.clips_json); show(sc); return
    if a.scmd == "voices":
        print("\n".join(S.render_voices(model_dir=a.model_dir))); return
    if a.scmd in ("stills", "clips", "a-clips", "run"):
        est = S.estimate(sc, "stills" if a.scmd == "stills" else "clips" if a.scmd == "clips" else "all")
        if a.scmd == "a-clips":
            est = {"n_jobs": 1, "jobs": {"reshoot": "1 per A turn without a covering clip"}, "usd": S.JOB_COST["reshoot"]}
        _confirm(a, est)
        b0 = _balance("before")
        if a.scmd == "a-clips" or (a.scmd == "run" and a.reshoot_a):
            S.make_a_clips(sc, work)
        if a.scmd in ("stills", "run") and (not sc["stills"] or getattr(a, "force", False)):
            S.make_stills(sc, work, seed=a.seed)
        if a.scmd in ("clips", "run"):
            S.make_clips(sc, work, only=a.only, seed=a.seed)
        b1 = _balance("after")
        if b0 is not None and b1 is not None:
            print(f"spent: ${b0 - b1:.4f}")
        if a.scmd != "run":
            return
    if a.scmd in ("render", "run"):
        r = S.render(S.load(work), work, out=a.out, labels=a.labels, react=not a.no_react)
        for s in r["shots"]:
            print(f"  {s['t']:6.2f}s +{s['dur']:.2f}  {s['cam']:10s} {s['words'] or ''}")
        print(f"wrote {r['output']} ({r['duration']:.1f}s)")


def show(sc):
    from .util import load_json
    tr = load_json(sc["transcript"])
    print(f"partner: {sc['partner']}  look: {sc.get('look')}  voice: {sc.get('voice')}")
    after = {}
    for k, ln in enumerate(sc["lines"]):
        after.setdefault(ln["after"], []).append((k, ln))
    for s in tr["sentences"]:
        print(f"  A [{s['i']}] {s['text']}")
        for k, ln in after.get(s["i"], []):
            clip = "  (clip ok)" if str(k) in sc["b_clips"] else ""
            print(f"      B ({k}) {ln['text']}{clip}")
    if sc["stills"]:
        print("stills:", ", ".join(sc["stills"]))
    if sc["a_clips"]:
        print("A angles:", ", ".join(f"{k}:{c['preset']}@{c['src_start']}" for k, c in sc["a_clips"].items()))


def add_parser(sub):
    p = sub.add_parser("scene", help="Scene mode: talk to your other self (shot/reverse-shot dialogue from one take)")
    ss = p.add_subparsers(dest="scmd", required=True)
    q = ss.add_parser("init", help="transcribe + write scene.json (free)")
    q.add_argument("video")
    q.add_argument("--partner", choices=["clone", "photo", "character"], default="clone")
    q.add_argument("--line", action="append", help="'N:text' = B answers after A's sentence N (0-based); repeatable")
    q.add_argument("--suggest", action="store_true", help="pre-fill B's lines from the transcript")
    q.add_argument("--look", default="night", help="clone look: night|noir|office|studio or free text")
    q.add_argument("--photo", help="partner photo (only photos of people who consented)")
    q.add_argument("--character", help="fictional partner description (partner=character)")
    q.add_argument("--voice", help="B voice: self (clone default), a Kokoro sample name (af_heart...) or a wav path")
    q.add_argument("--person", default="man", help="man|woman|person (used in prompts)")
    q.add_argument("--a-look", help="what A looks like from behind, for over-the-shoulder shots")
    q.add_argument("--script", help="exact words of your take (improves the transcript)")
    q.add_argument("--transcript", help="reuse an existing transcript.json")
    q.add_argument("--a-clips", help="reuse time-locked re-shoot clips (talk-mode clips.json) as A's angles")
    ss.add_parser("show", help="print the dialogue")
    q = ss.add_parser("lines", help="replace B's lines")
    q.add_argument("--line", action="append"); q.add_argument("--suggest", action="store_true")
    q = ss.add_parser("import-a", help="import talk-mode re-shoot clips as A's angles")
    q.add_argument("clips_json")
    q = ss.add_parser("voices", help="render the Kokoro-82M reference voices (needs kokoro-onnx)")
    q.add_argument("--model-dir", default=".")
    for name, hlp in (("stills", "1 Qwen job: partner single / over-the-shoulder / close-up stills"),
                      ("clips", "LTX-2.3 ID LoRA: one clip per B line + one listening clip"),
                      ("a-clips", "CrossView re-shoots of your take for A's side/wide angles"),
                      ("run", "stills + clips + render")):
        q = ss.add_parser(name, help=hlp)
        q.add_argument("--yes", action="store_true", help="confirm the spend")
        q.add_argument("--seed", type=int)
        q.add_argument("--only", action="append", help="clips: only these (line index or 'react')")
        q.add_argument("--force", action="store_true", help="stills: regenerate")
        q.add_argument("--reshoot-a", action="store_true", help="run: also re-shoot A's angles")
        if name == "run":
            q.add_argument("--out"); q.add_argument("--labels", action="store_true")
            q.add_argument("--no-react", action="store_true")
    q = ss.add_parser("render", help="free: cut the scene -> scene.mp4 (720x1280)")
    q.add_argument("--out"); q.add_argument("--labels", action="store_true", help="burn speaker/camera labels")
    q.add_argument("--no-react", action="store_true", help="no L-cut to B listening")
    p.set_defaults(fn=cmd)
