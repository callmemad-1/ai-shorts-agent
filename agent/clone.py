"""Voiceover in your cloned voice, made on this Mac by your local-voice tool
(IndexTTS 2.5 for English, with a mood per scene). Runs unattended, so scheduled
runs can use your voice.

The agent talks to local-voice's own long-running worker (app.index_worker, in its
.venv-index) over JSON lines; nothing in local-voice is changed or imported.
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

from .utils import log

# what each scene type sounds like unless its tone note says otherwise
EMOTION_BY_TYPE = {"hook": "excited", "setup": "calm", "twist": "surprised", "stat": "excited", "example": "warm",
                   "step": "calm", "stakes": "serious", "verify": "serious", "outro": "warm"}
# words in the script's "tone" note -> mood
EMOTION_BY_TONE = [
    (r"excit|energ|punch|upbeat|big", "excited"),
    (r"surpris|curious|intrigu|wonder|lean", "surprised"),
    (r"serious|grave|concern|careful|honest|sober|weight|skeptic|doubt|flat|direct", "serious"),
    (r"warm|friend|smile|kind|light", "warm"),
    (r"calm|steady|plain|clear|even", "calm"),
]
PAUSE_SECONDS = 0.45  # a "||" beat in the script's reading cues


def available(cfg: dict) -> bool:
    c = cfg["tts"].get("clone", {})
    root = Path(c.get("path", "")).expanduser()
    return (root / ".venv-index" / "bin" / "python").is_file() and (root / c.get("reference", "data/voice.wav")).is_file()


def emotion_for(scene: dict) -> str:
    if scene.get("emotion") in set(EMOTION_BY_TYPE.values()):
        return scene["emotion"]
    tone = (scene.get("tone") or "").lower()
    for pattern, mood in EMOTION_BY_TONE:
        if re.search(pattern, tone):
            return mood
    return EMOTION_BY_TYPE.get(scene.get("type"), "calm")


def spoken_parts(scene: dict, pronounce: list[dict]) -> list[str]:
    """The text to send, split at "||" beats, with reading marks removed and names
    respelled the way they should be said ("Hy4" -> "aitch why four")."""
    read = scene.get("read") or ""
    parts = [p for p in re.split(r"\|\|", read)] if "||" in read else [scene.get("voiceover", "")]
    out = []
    for p in parts:
        p = re.sub(r"\s+", " ", p.replace("|", " ").replace("*", "")).strip()
        for x in pronounce or []:
            word, say = str(x.get("word") or ""), str(x.get("say") or "").rstrip("?").strip()
            if word and say:
                p = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", say.replace("-", "").lower(), p, flags=re.I)
        if p:
            out.append(p)
    return out


class Worker:
    """One local-voice IndexTTS process for the whole Short (the model loads once)."""

    def __init__(self, cfg: dict, logdir: Path):
        c = cfg["tts"]["clone"]
        self.root = Path(c["path"]).expanduser()
        self.reference = self.root / c.get("reference", "data/voice.wav")
        self.intensity = c.get("intensity", 0.8)
        self.log = open(logdir / "clone-worker.log", "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [str(self.root / ".venv-index" / "bin" / "python"), "-m", "app.index_worker"],
            cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1,
        )

    def speak(self, text: str, emotion: str, out: Path) -> None:
        req = {"text": text, "reference": str(self.reference), "emotion": emotion,
               "intensity": self.intensity, "output": str(out)}
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("The local-voice worker stopped; see voice/clone-worker.log")
            if line.startswith("RESULT "):
                reply = json.loads(line[len("RESULT "):])
                if not reply.get("ok"):
                    raise RuntimeError(f"local-voice: {reply.get('error')}")
                return

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=20)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        self.log.close()


def level(x, target_rms_db: float = -20.0, peak_db: float = -1.5):
    """Same loudness for every scene (the model's output level drifts by several dB)."""
    import numpy as np

    rms = float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0
    if rms <= 0:
        return x
    g = 10 ** (target_rms_db / 20) / rms
    peak = float(np.max(np.abs(x))) * g
    if peak > 10 ** (peak_db / 20):
        g *= 10 ** (peak_db / 20) / peak
    return (x * g).astype("float32")


def synthesize(script: dict, cfg: dict, outdir: Path) -> dict:
    import numpy as np
    import soundfile as sf

    from .myvoice import align
    from .tts import SR, _ffprobe_duration

    c = cfg["tts"]["clone"]
    min_match, retries = c.get("min_match", 0.8), c.get("retries", 2)
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = outdir / "clone_tmp"
    tmp.mkdir(exist_ok=True)
    log("Cloned voice: loading your local-voice model (about 30 s the first time)...")
    worker = Worker(cfg, outdir)
    clips, flagged = [], []
    try:
        for s in script["scenes"]:
            mood = emotion_for(s)
            parts = spoken_parts(s, script.get("pronounce", []))
            wav = outdir / f"scene_{s['id']:02d}.wav"
            best = None
            for attempt in range(retries + 1):
                pieces = []
                for k, text in enumerate(parts):
                    raw = tmp / f"{uuid.uuid4().hex[:10]}.wav"
                    worker.speak(text, mood, raw)
                    a, sr = sf.read(str(raw), dtype="float32", always_2d=False)
                    raw.unlink(missing_ok=True)
                    if sr != SR:
                        n = int(round(len(a) * SR / sr))
                        a = np.interp(np.linspace(0, len(a), n, endpoint=False), np.arange(len(a)), a).astype("float32")
                    pieces.append(a)
                    if k < len(parts) - 1:
                        pieces.append(np.zeros(int(SR * PAUSE_SECONDS), dtype="float32"))
                audio = level(np.concatenate(pieces))
                fade = min(len(audio), int(SR * 0.01))
                audio[:fade] *= np.linspace(0, 1, fade)
                audio[len(audio) - fade:] *= np.linspace(1, 0, fade)
                sf.write(str(wav), audio, SR, subtype="PCM_24")
                dur = _ffprobe_duration(wav)
                words, ratio, heard, missing = align(s["voiceover"], wav, dur)
                if best is None or ratio > best[1]:
                    best = (wav.read_bytes(), ratio, words, dur, missing)
                if ratio >= min_match:
                    break
                log(f"Scene {s['id']}: only {ratio:.0%} of words came out clearly ({', '.join(missing)}); "
                    f"generating again ({attempt + 1}/{retries})" if attempt < retries else
                    f"Scene {s['id']}: still {ratio:.0%} after {retries} retries; keeping the best take")
            data, ratio, words, dur, missing = best
            wav.write_bytes(data)
            if ratio < min_match:
                flagged.append(f"scene {s['id']} ({ratio:.0%}; listen for: {', '.join(missing)})")
            log(f"Scene {s['id']} [{mood}]: {dur:.1f}s, {ratio:.0%} of words clear")
            clips.append({"scene_id": s["id"], "wav": str(wav), "duration": round(dur, 3), "words": words,
                          "emotion": mood, "match": round(ratio, 3)})
    finally:
        worker.close()
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()
    gap, sc = cfg["tts"]["scene_gap_seconds"], cfg["script"]
    total = sum(x["duration"] for x in clips) + gap * len(clips)
    if total > sc["max_seconds"]:  # the model has no speed knob: tighten gently with ffmpeg (pitch kept)
        f = min(1.12, total / sc["target_seconds"])
        log(f"Voiceover is {total:.1f}s; speeding it up x{f:.2f} to fit")
        for x in clips:
            src = Path(x["wav"])
            fast = src.with_suffix(".fast.wav")
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-af", f"atempo={f:.3f}",
                            "-c:a", "pcm_s24le", str(fast)], check=True)
            fast.replace(src)
            x["duration"] = round(x["duration"] / f, 3)
            x["words"] = [{**w, "start": round(w["start"] / f, 3), "end": round(w["end"] / f, 3)} for w in x["words"]]
        total = sum(x["duration"] for x in clips) + gap * len(clips)
    log(f"Voiceover (cloned voice): {total:.1f}s")
    if flagged:
        log("⚠ Listen to these before uploading: " + "; ".join(flagged))
    return {"engine": "clone", "speed": 1.0, "total": round(total, 2), "clips": clips, "flagged": flagged,
            "synthetic": True}
