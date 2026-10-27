"""`multicam analyze`: word-level transcription with faster-whisper (CPU, int8).

Optionally aligns a known script onto the recognized words, so the exact
words (and punctuation) used later in [SPEECH] prompts match what was said.
"""
from __future__ import annotations

import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from .util import need, probe, save_json

PHRASE_PUNCT = (",", ";", ":", "—", "–")
SENT_PUNCT = (".", "?", "!")


def decode_audio(path: str | Path, sr: int = 16000) -> np.ndarray:
    """Decode with the ffmpeg binary (avoids PyAV version issues)."""
    need("ffmpeg")
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def norm(tok: str) -> str:
    return re.sub(r"[^a-z0-9']", "", tok.lower().replace("’", "'"))


def whisper_words(video: str | Path, model: str = "small", language: str = "en", initial_prompt: str | None = None):
    from faster_whisper import WhisperModel

    m = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _info = m.transcribe(
        decode_audio(video), language=language, word_timestamps=True,
        initial_prompt=initial_prompt, beam_size=5,
        # deterministic: no temperature-fallback sampling (it once produced a hallucinated transcript
        # under heavy CPU load) and no conditioning loops on earlier text
        temperature=0.0, condition_on_previous_text=False,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"text": text, "start": round(float(w.start), 3), "end": round(float(w.end), 3),
                              "prob": round(float(w.probability), 3)})
    return words


def align_script(words: list[dict], script: str) -> list[dict]:
    """Map script tokens onto recognized word timings (difflib alignment)."""
    stoks = script.split()
    a = [norm(w["text"]) for w in words]
    b = [norm(t) for t in stoks]
    out: list[dict] = []
    sm = SequenceMatcher(a=a, b=b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                w = dict(words[i1 + k]); w["text"] = stoks[j1 + k]; w["aligned"] = "match"
                out.append(w)
        elif op in ("replace", "insert"):
            if i2 > i1:
                t0, t1 = words[i1]["start"], words[i2 - 1]["end"]
            else:  # script words with no recognized counterpart: squeeze into the gap
                t0 = words[i1 - 1]["end"] if i1 > 0 else 0.0
                t1 = words[i1]["start"] if i1 < len(words) else t0 + 0.3
            lens = [max(1, len(b[j])) for j in range(j1, j2)]
            total, acc = sum(lens), 0
            for j, ln in zip(range(j1, j2), lens):
                s = t0 + (t1 - t0) * acc / total
                acc += ln
                e = t0 + (t1 - t0) * acc / total
                out.append({"text": stoks[j], "start": round(s, 3), "end": round(e, 3), "prob": None,
                            "aligned": "replaced" if i2 > i1 else "inserted"})
        # "delete": recognized words not in the script are dropped
    return out


def group(words: list[dict], gap_phrase: float = 0.35):
    """Split words into sentences (.?!) and phrases (punctuation or pauses)."""
    sentences, phrases = [], []
    s0 = p0 = 0
    for i, w in enumerate(words):
        last = i == len(words) - 1
        gap = (words[i + 1]["start"] - w["end"]) if not last else 0.0
        end_sent = last or w["text"].endswith(SENT_PUNCT)
        end_phrase = end_sent or w["text"].endswith(PHRASE_PUNCT) or gap > gap_phrase
        if end_phrase:
            phrases.append({"w0": p0, "w1": i})
            p0 = i + 1
        if end_sent:
            sentences.append({"w0": s0, "w1": i})
            s0 = i + 1
    for coll in (sentences, phrases):
        for k, g in enumerate(coll):
            g["i"] = k
            g["start"] = words[g["w0"]]["start"]
            g["end"] = words[g["w1"]]["end"]
            g["text"] = " ".join(w["text"] for w in words[g["w0"]: g["w1"] + 1])
    for k, p in enumerate(phrases):
        p["sentence"] = next(s["i"] for s in sentences if s["w0"] <= p["w0"] <= s["w1"])
    return sentences, phrases


def analyze(video: str | Path, out: str | Path, model: str = "small", script: str | None = None,
            language: str = "en", initial_prompt: str | None = None) -> dict:
    video = Path(video).resolve()
    info = probe(video)
    if initial_prompt is None and script:
        initial_prompt = script  # biases spelling of names like "Claude Code"
    raw = whisper_words(video, model=model, language=language, initial_prompt=initial_prompt)
    if info["duration"] > 6 and len(raw) / info["duration"] < 0.8:
        # far too few words for the speech span: whisper occasionally truncates under heavy CPU load
        again = whisper_words(video, model=model, language=language, initial_prompt=initial_prompt)
        raw = again if len(again) > len(raw) else raw
    words = align_script(raw, script) if script else [dict(w, aligned="asr") for w in raw]
    for i, w in enumerate(words):
        w["i"] = i
    sentences, phrases = group(words)
    doc = {
        "source": str(video),
        "duration": info["duration"],
        "fps": info.get("fps"),
        "model": f"faster-whisper/{model}/cpu-int8",
        "language": language,
        "asr_text": " ".join(w["text"] for w in raw),
        "script_aligned": bool(script),
        "text": " ".join(w["text"] for w in words),
        "words": words,
        "sentences": sentences,
        "phrases": phrases,
    }
    save_json(doc, out)
    return doc
