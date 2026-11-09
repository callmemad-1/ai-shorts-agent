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
import sys
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
PAUSE_SECONDS = 0.3  # a "||" beat in the script's reading cues
SOFT_PAUSE_SECONDS = 0.1  # where a long line was split for the model
# IndexTTS turns long passages into noise (a 33-word line did, the same words in short pieces
# didn't). Words per piece on each attempt: normal, then smaller after a failed Whisper check.
PIECE_WORDS = [20, 12, 8]


class StopRun(Exception):
    """The user chose to stop rather than use a voiceover that failed its check."""


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


def _respell(text: str, pronounce: list[dict]) -> str:
    """Names the way they should be said ("Hy4" -> "high four"). Acronyms spelled letter by
    letter ("N-B-C") stay as written: squashed into "nbc" the model says something else."""
    for x in pronounce or []:
        word, say = str(x.get("word") or ""), str(x.get("say") or "").rstrip("?").strip()
        if word and say and not re.fullmatch(r"[A-Za-z](?:[- ][A-Za-z])+", say):
            text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", say.replace("-", "").lower(), text, flags=re.I)
    return text


def _clauses(part: str, max_words: int) -> list[str]:
    """Break a line at its "|" marks, then at sentence ends and commas, then every max_words."""
    out = []
    for clause in part.split("|"):
        clause = re.sub(r"\s+", " ", clause.replace("*", "")).strip()
        if len(clause.split()) <= max_words:
            out += [clause] if clause else []
            continue
        for bit in re.split(r"(?<=[.!?,;:])\s+", clause):
            words = bit.split()
            out += [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]
    return out


def speech_pieces(scene: dict, pronounce: list[dict], max_words: int = PIECE_WORDS[0]) -> list[tuple[str, float]]:
    """What to send to the model: (text, pause after it). Split at "||" beats, and again
    wherever a stretch would be longer than the model can say cleanly."""
    read = scene.get("read") or ""
    parts = re.split(r"\|\|", read) if "||" in read else [scene.get("voiceover", "")]
    out: list[tuple[str, float]] = []
    for part in parts:
        merged: list[str] = []
        for clause in _clauses(part, max_words):
            if merged and len(merged[-1].split()) + len(clause.split()) <= max_words:
                merged[-1] += " " + clause
            else:
                merged.append(clause)
        out += [(_respell(m, pronounce), SOFT_PAUSE_SECONDS) for m in merged]
        if out:
            out[-1] = (out[-1][0], PAUSE_SECONDS)
    return out


def spoken_parts(scene: dict, pronounce: list[dict]) -> list[str]:
    """The text split only at "||" beats, with reading marks removed and names respelled."""
    return [text for text, _ in speech_pieces(scene, pronounce, max_words=10 ** 6)]


def trim_silence(a, sr: int, floor_db: float = -38.0, margin: float = 0.03):
    """Cut the padding the model leaves around a clip, so split lines don't sound gappy."""
    import numpy as np

    if a.size == 0:
        return a
    hop = max(1, int(sr * 0.01))
    level_ = np.sqrt(np.convolve(a ** 2, np.ones(hop) / hop, mode="same"))
    on = np.where(level_ > float(np.max(np.abs(a))) * 10 ** (floor_db / 20))[0]
    if on.size == 0:
        return a
    edge = int(sr * margin)
    return a[max(0, on[0] - edge): min(a.size, on[-1] + edge)]


class Worker:
    """One local-voice process for the whole Short (the model loads once): IndexTTS for
    English (with a mood), or Maya 2 for Tamil (keeps the delivery of your recording)."""

    def __init__(self, cfg: dict, logdir: Path, language: str = "en"):
        c = cfg["tts"]["clone"]
        self.root = Path(c["path"]).expanduser()
        self.reference = self.root / c.get("reference", "data/voice.wav")
        self.intensity = c.get("intensity", 0.8)
        self.language = language
        self.log = open(logdir / "clone-worker.log", "a", encoding="utf-8")
        if language == "en":
            cmd = [str(self.root / ".venv-index" / "bin" / "python"), "-m", "app.index_worker"]
        else:
            cmd = [str(self.root / ".venv" / "bin" / "python"), str(Path(__file__).with_name("maya_worker.py")),
                   str(self.root)]
        self.proc = subprocess.Popen(cmd, cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.log, text=True, bufsize=1)

    def speak(self, text: str, emotion: str, out: Path) -> None:
        req = {"text": text, "reference": str(self.reference), "emotion": emotion,
               "intensity": self.intensity, "output": str(out), "language": self.language}
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


def _ask_failed(scene_id, ratio: float, missing: list[str]) -> str:
    """A scene is still unclear after the retries: never pass it on silently. With no one at
    the keyboard it's kept and the Short is marked for review."""
    if not sys.stdin.isatty():
        return "keep"
    print(f"\n⚠ Scene {scene_id}: only {ratio:.0%} of the words came out clearly (missing: {', '.join(missing)}).")
    ans = input("Enter = try again · a = AI voice for this scene · k = keep it anyway · q = stop: ").strip().lower()
    return {"a": "ai", "k": "keep", "q": "stop"}.get(ans, "again")


def synthesize(script: dict, cfg: dict, outdir: Path) -> dict:
    import numpy as np
    import soundfile as sf

    from .language import spoken
    from .myvoice import _loudest_db, align
    from .tts import SR, _ffprobe_duration, estimate_word_times, synth_edge

    c = cfg["tts"]["clone"]
    min_match, retries = c.get("min_match", 0.8), c.get("retries", 2)
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = outdir / "clone_tmp"
    tmp.mkdir(exist_ok=True)
    lang = script.get("language", "en")
    log(f"Cloned voice ({'IndexTTS' if lang == 'en' else 'Maya'}, {lang}): loading your local-voice model "
        "(about 30 s the first time)...")
    worker = Worker(cfg, outdir, lang)
    clips, flagged = [], []
    try:
        for s in script["scenes"]:
            mood = emotion_for(s)
            wav = outdir / f"scene_{s['id']:02d}.wav"
            best, choice = None, ""
            attempt = 0
            while True:
                size = PIECE_WORDS[min(attempt, len(PIECE_WORDS) - 1)]
                # Tamil: the translated line, whole (Maya has no mood; the English read marks don't apply)
                parts = speech_pieces(s, script.get("pronounce", []), size) if lang == "en" else [(spoken(s), 0.0)]
                pieces = []
                for k, (text, pause) in enumerate(parts):
                    raw = tmp / f"{uuid.uuid4().hex[:10]}.wav"
                    worker.speak(text, mood, raw)
                    a, sr = sf.read(str(raw), dtype="float32", always_2d=False)
                    raw.unlink(missing_ok=True)
                    if sr != SR:
                        n = int(round(len(a) * SR / sr))
                        a = np.interp(np.linspace(0, len(a), n, endpoint=False), np.arange(len(a)), a).astype("float32")
                    pieces.append(trim_silence(a, SR) if lang == "en" else a)
                    if k < len(parts) - 1:
                        pieces.append(np.zeros(int(SR * pause), dtype="float32"))
                audio = level(np.concatenate(pieces))
                fade = min(len(audio), int(SR * 0.01))
                audio[:fade] *= np.linspace(0, 1, fade)
                audio[len(audio) - fade:] *= np.linspace(1, 0, fade)
                sf.write(str(wav), audio, SR, subtype="PCM_24")
                dur = _ffprobe_duration(wav)
                if lang == "en":
                    words, ratio, heard, missing = align(s["voiceover"], wav, dur)
                else:  # Whisper here only knows English: check there's a voice, and estimate the timings
                    ok = _loudest_db(wav) > -40
                    words, ratio, missing = estimate_word_times(spoken(s), dur), 1.0 if ok else 0.0, [] if ok else ["(silent)"]
                if best is None or ratio > best[1]:
                    best = (wav.read_bytes(), ratio, words, dur, missing)
                if ratio >= min_match:
                    break
                attempt += 1
                if attempt <= retries:
                    log(f"Scene {s['id']}: only {ratio:.0%} of words came out clearly ({', '.join(missing)}); "
                        f"trying again in shorter pieces ({attempt}/{retries})")
                    continue
                choice = _ask_failed(s["id"], best[1], best[4])
                if choice == "again":
                    continue
                if choice == "stop":
                    raise StopRun(f"Stopped at scene {s['id']}: the cloned voice wasn't clear enough.")
                break
            data, ratio, words, dur, missing = best
            wav.write_bytes(data)
            if ratio < min_match and choice == "ai":
                try:
                    dur, words = synth_edge(s["voiceover"], wav, cfg, 1.0)
                    ratio, missing = 1.0, []
                    log(f"Scene {s['id']}: using the AI voice for this scene")
                except Exception as e:  # noqa: BLE001 - keep the best take and flag it
                    log(f"Scene {s['id']}: the AI voice failed too ({e}); keeping the best take")
            if ratio < min_match:
                flagged.append(f"scene {s['id']} ({ratio:.0%}; listen for: {', '.join(missing)})")
            log(f"Scene {s['id']} [{mood}]: {dur:.1f}s, {ratio:.0%} of words clear" if lang == "en" else
                f"Scene {s['id']}: {dur:.1f}s, {'voice present' if ratio else 'SILENT'} (Maya keeps your recorded delivery)")
            clips.append({"scene_id": s["id"], "wav": str(wav), "duration": round(dur, 3), "words": words,
                          "emotion": mood, "match": round(ratio, 3)})
    finally:
        worker.close()
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()
    gap, sc = cfg["tts"]["scene_gap_seconds"], cfg["script"]
    from .myvoice import update_pace

    # before any speed-up: the clone's natural pace (English only: it sizes the English script)
    wpm = update_pace(script, clips, voice="clone") if lang == "en" else None
    if wpm:
        log(f"Cloned voice pace: {wpm:.0f} wpm (the next script is sized to it)")
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
            "needs_review": bool(flagged), "synthetic": True}
