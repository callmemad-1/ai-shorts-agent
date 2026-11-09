"""Stage 4: free text-to-speech voiceover, one audio file per scene.

Engines:
- "edge"   : Microsoft Edge neural voices via edge-tts (free, needs internet).
             Gives real word timings for captions.
- "kokoro" : Kokoro-82M ONNX, fully offline and free (model files ~330 MB,
             see README). Word timings are estimated.
- "mine"   : you read each line into the mic (agent/myvoice.py).
- "clone"  : your cloned voice from the local-voice tool, with a mood per scene
             (agent/clone.py). No one needs to be at the keyboard.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

from .utils import log, resolve

SR = 24000


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def estimate_word_times(text: str, duration: float, lead: float = 0.08, tail: float = 0.12):
    """Spread words over the clip by length, with extra weight on pauses."""
    words = text.split()
    if not words:
        return []
    weights = []
    for w in words:
        wt = len(re.sub(r"\W", "", w)) + 2
        if re.search(r"[.!?]$", w):
            wt += 6
        elif re.search(r"[,;:—-]$", w):
            wt += 3
        weights.append(wt)
    span = max(duration - lead - tail, 0.1)
    total, t, out = sum(weights), lead, []
    for w, wt in zip(words, weights):
        d = span * wt / total
        out.append({"word": w, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    return out


# ---------------------------------------------------------------- edge-tts
async def _edge(text: str, voice: str, rate: str, mp3: Path):
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words = []
    with open(mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append({"word": chunk["text"], "start": start, "end": start + chunk["duration"] / 1e7})
    return words


def synth_edge(text: str, wav: Path, cfg: dict, speed: float):
    mp3 = wav.with_suffix(".mp3")
    rate = f"{round((speed - 1) * 100):+d}%"
    words = asyncio.run(_edge(text, cfg["tts"]["edge_voice"], rate, mp3))
    if mp3.stat().st_size == 0:
        raise RuntimeError("edge-tts returned no audio")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(mp3), "-ac", "1", "-ar", str(SR), str(wav)], check=True)
    mp3.unlink(missing_ok=True)
    dur = _ffprobe_duration(wav)
    # edge gives timings for the spoken tokens; map them back onto our words so
    # captions keep punctuation and casing.
    if words and len(words) == len(text.split()):
        for w, orig in zip(words, text.split()):
            w["word"] = orig
    elif not words:
        words = estimate_word_times(text, dur)
    else:
        words = estimate_word_times(text, dur)  # token mismatch: fall back
    return dur, words


# ---------------------------------------------------------------- kokoro
_KOKORO = None


def synth_kokoro(text: str, wav: Path, cfg: dict, speed: float):
    global _KOKORO
    import soundfile as sf
    from kokoro_onnx import Kokoro

    if _KOKORO is None:
        model, voices = resolve(cfg["tts"]["kokoro_model"]), resolve(cfg["tts"]["kokoro_voices"])
        if not model.exists() or not voices.exists():
            raise FileNotFoundError(f"Kokoro model files missing: {model}, {voices} (see README)")
        _KOKORO = Kokoro(str(model), str(voices))
    samples, sr = _KOKORO.create(text, voice=cfg["tts"]["kokoro_voice"], speed=speed, lang="en-us")
    sf.write(str(wav), samples, sr)
    dur = len(samples) / sr
    return dur, estimate_word_times(text, dur)


ENGINES = {"edge": synth_edge, "kokoro": synth_kokoro}


def _synth_all(script: dict, cfg: dict, outdir: Path, engine: str, speed: float):
    from .language import spoken

    fn = ENGINES[engine]
    lang = script.get("language", "en")
    if lang != "en":  # e.g. a Tamil Edge voice for a Tamil Short
        cfg = {**cfg, "tts": {**cfg["tts"], "edge_voice": cfg["tts"].get(f"edge_voice_{lang}", cfg["tts"]["edge_voice"])}}
    clips = []
    for s in script["scenes"]:
        wav = outdir / f"scene_{s['id']:02d}.wav"
        dur, words = fn(spoken(s), wav, cfg, speed)
        clips.append({"scene_id": s["id"], "wav": str(wav), "duration": round(dur, 3), "words": words})
    return clips


def synthesize(script: dict, cfg: dict, outdir: Path) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    sc, gap = cfg["script"], cfg["tts"]["scene_gap_seconds"]
    lang = script.get("language", "en")
    if cfg["tts"]["engine"] == "mine" and lang != "en":
        from . import clone as _clone

        cfg = {**cfg, "tts": {**cfg["tts"], "engine": "clone" if _clone.available(cfg) else "edge"}}
        log(f"Recording yourself is English-only for now; using the {cfg['tts']['engine']} voice for this {lang} Short.")
    if cfg["tts"]["engine"] == "mine":
        if sys.stdin.isatty() or cfg["tts"].get("reuse_takes"):
            from .myvoice import record_all

            return record_all(script, cfg, outdir, reuse_takes=cfg["tts"].get("reuse_takes", False))
        log("Your-voice mode needs you at the keyboard; using the AI voice for this run.")
    if cfg["tts"]["engine"] == "clone":
        from . import clone

        if clone.available(cfg):
            try:
                return clone.synthesize(script, cfg, outdir)
            except clone.StopRun:
                raise  # you chose to stop: don't quietly swap in another voice
            except Exception as e:  # noqa: BLE001 - never lose the day's Short to the clone
                log(f"Cloned voice failed ({e}); using the AI voice for this run.")
        else:
            log(f"Cloned voice not found at {cfg['tts'].get('clone', {}).get('path')}; using the AI voice for this run.")
    engines = [cfg["tts"]["engine"]] if cfg["tts"]["engine"] not in ("mine", "clone") else []
    if cfg["tts"].get("fallback_engine") and cfg["tts"]["fallback_engine"] not in engines:
        engines.append(cfg["tts"]["fallback_engine"])
    if "edge" not in engines:  # always end on the online voice: it needs no model files
        engines.append("edge")
    if lang != "en":
        engines = [e for e in engines if e == "edge"]  # Kokoro is English-only

    last_err = None
    for engine in engines:
        try:
            speed = 1.0
            for attempt in range(3):
                clips = _synth_all(script, cfg, outdir, engine, speed)
                total = sum(c["duration"] for c in clips) + gap * len(clips)
                log(f"Voiceover ({engine}, speed {speed:.2f}): {total:.1f}s")
                in_range = sc["min_seconds"] <= total <= sc["max_seconds"]
                if in_range and (abs(total - sc["target_seconds"]) <= 3 or attempt == 2):
                    break
                speed = max(0.85, min(1.25, speed * total / sc["target_seconds"]))
            return {"engine": engine, "speed": speed, "total": round(total, 2), "clips": clips}
        except Exception as e:  # noqa: BLE001 - try the next engine
            last_err = e
            log(f"TTS engine '{engine}' failed: {e}")
    raise RuntimeError(f"All TTS engines failed: {last_err}")
