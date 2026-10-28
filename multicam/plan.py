"""`multicam plan`: generation chunks (<=5 s, sentence/phrase aligned) and the A/B/C cut list.

Editorial rule (defaults):
  * start on A (>= head_a seconds), end on A (>= tail_a seconds)
  * at most one insert per generation chunk, 1-2 s long, cut on word boundaries,
    preferring to ENTER mid-phrase and EXIT on a phrase boundary
  * never use the first `breath` seconds of a generated chunk (breath mismatch)
  * at least `min_a` seconds of A between inserts (no back-to-back inserts)
  * inserts alternate between the B/C angles (pins can force an angle)
  * angles listed in `silent_only` (pulled-back wides) are never put over speech: lips too small to read,
    the shot looks frozen (see continuity.py); every insert here covers words, so they go to the other angles
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .util import qt, save_json

ANGLE_NAMES = {"A": "real", "B": "profile", "C": "wide", "D": "closeup"}


@dataclass
class PlanParams:
    max_chunk: float = 5.0       # LTX-2.3 ID LoRA clip length (s)
    lead: float = 0.5            # pre-roll before the first word of a chunk (breath zone)
    breath: float = 0.5          # never use the first N s of a generated chunk
    tail_guard: float = 0.1      # never use the last N s of a generated chunk
    min_insert: float = 1.0
    max_insert: float = 2.0
    target_insert: float = 1.5
    min_a: float = 1.5           # min A time between inserts
    head_a: float = 1.0          # min A time at the start
    tail_a: float = 1.0          # min A time at the end
    cut_pad: float = 0.12        # max distance a cut sits before a word onset (inside a pause)
    angles: list = field(default_factory=lambda: ["B", "C"])
    fps: float = 30.0
    all_chunks: bool = False     # emit generation jobs for chunks without an insert too
    silent_only: list = field(default_factory=lambda: ["C"])  # wide angles: only over silence (C = wide by default)


def _split_long_phrases(words, phrases, limit):
    out = []
    for p in phrases:
        if words[p["w1"]]["end"] - words[p["w0"]]["start"] <= limit:
            out.append(dict(p)); continue
        w0 = p["w0"]
        for i in range(p["w0"], p["w1"] + 1):
            if words[i]["end"] - words[w0]["start"] > limit and i > w0:
                out.append({**p, "w0": w0, "w1": i - 1, "split": True}); w0 = i
        out.append({**p, "w0": w0, "w1": p["w1"], "split": True})
    for p in out:
        p["start"], p["end"] = words[p["w0"]]["start"], words[p["w1"]]["end"]
    return out


def make_chunks(tr: dict, P: PlanParams) -> list[dict]:
    words, dur = tr["words"], tr["duration"]
    sent_end = {s["w1"] for s in tr["sentences"]}
    phrases = _split_long_phrases(words, tr["phrases"], P.max_chunk - P.lead)
    n = len(phrases)

    def window(i, j):
        s = max(0.0, phrases[i]["start"] - P.lead)
        return s, min(dur, s + P.max_chunk)

    def cost(i, j):
        s, e = window(i, j)
        if phrases[j]["end"] > s + P.max_chunk:
            return None
        span = phrases[j]["end"] - phrases[i]["start"]
        c = 1.0
        if phrases[j]["w1"] not in sent_end:
            c += 0.6
        c += max(0.0, 2.0 - span) * 0.5
        return c

    best = [0.0] + [float("inf")] * n
    back = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j):
            c = cost(i, j - 1)
            if c is not None and best[i] + c < best[j]:
                best[j], back[j] = best[i] + c, i
    cuts, j = [], n
    while j > 0:
        cuts.append((back[j], j - 1)); j = back[j]
    chunks = []
    for k, (i, j) in enumerate(reversed(cuts)):
        s, e = window(i, j)
        w0, w1 = phrases[i]["w0"], phrases[j]["w1"]
        chunks.append({
            "id": f"c{k + 1:02d}", "start": round(s, 3), "end": round(e, 3),
            "speech_start": words[w0]["start"], "speech_end": words[w1]["end"],
            "w0": w0, "w1": w1,
            "words": " ".join(w["text"] for w in words[w0:w1 + 1]),
            "phrases": [p["i"] for p in phrases[i:j + 1]],
            "angle": None, "generate": False,
        })
    return chunks


def _boundaries(words, P: PlanParams):
    """Cut point just before word k (inside the preceding pause if there is one)."""
    b = []
    for k, w in enumerate(words):
        gap = w["start"] - (words[k - 1]["end"] if k else 0.0)
        b.append(w["start"] - min(max(gap, 0.0) * 0.5, P.cut_pad))
    return b


def pick_inserts(tr: dict, chunks: list[dict], P: PlanParams) -> list[dict]:
    words, dur = tr["words"], tr["duration"]
    B = _boundaries(words, P)
    end_b = words[-1]["end"] + 0.15
    phrase_first = {p["w0"] for p in tr["phrases"]}
    phrase_last = {p["w1"] for p in tr["phrases"]}
    inserts, last_end = [], None
    for ch in chunks:
        lo = ch["start"] + P.breath
        hi = ch["end"] - P.tail_guard
        best = None
        for a in range(ch["w0"], ch["w1"] + 1):
            t_in = B[a]
            if t_in < lo:
                continue
            if t_in < (P.head_a if last_end is None else last_end + P.min_a):
                continue
            for b in range(a + 1, ch["w1"] + 2):
                t_out = B[b] if b < len(words) else end_b
                d = t_out - t_in
                if d < P.min_insert:
                    continue
                if d > P.max_insert or t_out > hi or t_out > dur - P.tail_a:
                    break
                score = 0.0
                score += 1.0 if a not in phrase_first else 0.5     # enter mid-phrase (preferred)
                score += 1.0 if (b - 1) in phrase_last else 0.0    # exit on a phrase boundary
                score -= abs(d - P.target_insert) * 0.8
                if best is None or score > best[0]:
                    best = (score, a, b, t_in, t_out)
        if best:
            _, a, b, t_in, t_out = best
            inserts.append({"chunk": ch["id"], "start": qt(t_in, P.fps), "end": qt(t_out, P.fps),
                            "w0": a, "w1": b - 1,
                            "words": " ".join(w["text"] for w in words[a:b]),
                            "enter": "mid-phrase" if a not in phrase_first else "phrase-start",
                            "exit": "phrase-end" if (b - 1) in phrase_last else "word-boundary"})
            last_end = t_out
    return inserts


def assign_angles(inserts: list[dict], angles: list[str], pins: dict[str, str],
                  silent_only: list[str] | tuple = ()) -> None:
    """Viterbi: minimize same-angle repeats between consecutive inserts, honoring pins.
    Inserts that cover words never get a `silent_only` angle (a pin to one is ignored with a warning)."""
    if not inserts:
        return
    speech_ok = [a for a in angles if a not in silent_only]
    for ins in inserts:
        if ins.get("words") and ins["chunk"] in pins and pins[ins["chunk"]] in silent_only:
            print(f"warn: pin {ins['chunk']}={pins[ins['chunk']]} ignored: a wide can't go over speech "
                  f"(\"{ins['words']}\")")
            pins = {k: v for k, v in pins.items() if k != ins["chunk"]}
    if not speech_ok:
        speech_ok = angles
    INF = float("inf")
    n = len(inserts)
    cost = [{a: INF for a in angles} for _ in range(n)]
    back = [{a: None for a in angles} for _ in range(n)]
    for k, ins in enumerate(inserts):
        allowed = [pins[ins["chunk"]]] if ins["chunk"] in pins else (speech_ok if ins.get("words") else angles)
        for a in allowed:
            if k == 0:
                cost[0][a] = angles.index(a) * 0.01 if a in angles else 0.0
                continue
            for p in angles:
                c = cost[k - 1].get(p, INF) + (1.0 if p == a else 0.0)
                if c < cost[k].get(a, INF):
                    cost[k][a], back[k][a] = c, p
    a = min(cost[-1], key=lambda x: cost[-1][x])
    for k in range(n - 1, -1, -1):
        inserts[k]["angle"] = a
        a = back[k][a]


def resolve_pins(pin_args: list[str], chunks: list[dict]) -> dict[str, str]:
    """`c04=B` pins a chunk id; `@14.5=B` pins the chunk whose window contains t=14.5 s."""
    pins = {}
    for arg in pin_args or []:
        key, ang = arg.split("=")
        if key.startswith("@"):
            t = float(key[1:])
            hit = [c for c in chunks if c["start"] <= t < c["end"]]
            if not hit:
                raise SystemExit(f"pin {arg}: no chunk contains t={t}")
            key = min(hit, key=lambda c: abs((c["start"] + c["end"]) / 2 - t))["id"]
        pins[key] = ang.upper()
    return pins


def build_plan(tr: dict, P: PlanParams, pins: list[str] | None = None) -> dict:
    chunks = make_chunks(tr, P)
    inserts = pick_inserts(tr, chunks, P)
    pin_map = resolve_pins(pins, chunks)
    all_angles = sorted(set(P.angles) | set(pin_map.values()))
    assign_angles(inserts, P.angles if set(pin_map.values()) <= set(P.angles) else all_angles, pin_map,
                  silent_only=P.silent_only)
    by_chunk = {c["id"]: c for c in chunks}
    for ins in inserts:
        ch = by_chunk[ins["chunk"]]
        ch["angle"], ch["generate"] = ins["angle"], True
    if P.all_chunks:
        for k, ch in enumerate(chunks):
            if not ch["generate"]:
                ok = [x for x in P.angles if x not in P.silent_only] or P.angles
                ch["angle"], ch["generate"] = ok[k % len(ok)], True

    # shots: A everywhere except inserts
    dur_q = qt(tr["duration"], P.fps)
    shots, t = [], 0.0
    for ins in inserts:
        if ins["start"] > t:
            shots.append({"cam": "A", "start": t, "end": ins["start"]})
        shots.append({"cam": ins["angle"], "start": ins["start"], "end": ins["end"], "chunk": ins["chunk"],
                      "offset": 0.0, "words": ins["words"], "enter": ins["enter"], "exit": ins["exit"]})
        t = ins["end"]
    if t < dur_q:
        shots.append({"cam": "A", "start": t, "end": dur_q})
    for k, s in enumerate(shots):
        s["i"] = k
        s["angle_name"] = ANGLE_NAMES.get(s["cam"], s["cam"])
        s["duration"] = round(s["end"] - s["start"], 4)
        w = [x["text"] for x in tr["words"] if s["start"] <= (x["start"] + x["end"]) / 2 < s["end"]]
        s.setdefault("words", " ".join(w))
    return {
        "source": tr["source"], "duration": tr["duration"], "fps": P.fps,
        "params": asdict(P), "pins": pin_map,
        "angle_names": ANGLE_NAMES,
        "chunks": chunks, "shots": shots,
        "cuts": [s["start"] for s in shots[1:]],
    }


def plan(transcript: dict, out, params: PlanParams, pins=None) -> dict:
    doc = build_plan(transcript, params, pins)
    save_json(doc, out)
    return doc
