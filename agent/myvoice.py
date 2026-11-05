"""Your own voice: record each scene's line from the mic, then time every word.

    python main.py --tts mine ...     (or tts.engine = "mine" in config.json)

For each scene the terminal shows the line to read. Press Enter to start, Enter
to stop, then keep / redo / play it back. Takes are cleaned up (rumble filter,
silence trimmed) and Whisper finds when each word was spoken so the captions stay
in sync. If what you said differs a lot from the fact-checked script, you're
warned before it's used.

You can also record elsewhere (phone, QuickTime) and drop the files into
<output>/voice/mine/ as scene_01.m4a, scene_02.m4a ... - they're picked up.
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
from pathlib import Path

from .utils import log

SR = 48000  # keep the mic's full bandwidth; 24 kHz (the AI voice's rate) sounds dull
MATCH_WARN = 0.8  # below this share of matching words, ask before using a take
WHISPER_MODEL = "base.en"  # fast; misheard names are listed so you can tell them from real misreads
_WHISPER = None


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


# ---------------------------------------------------------------- audio
# Broadcast voice chain, in order: click repair, hiss removal (non-local means: strong on hiss, gentle on
# soft consonants), less boxiness, more presence and air, de-esser, compressor for an even
# level. No noise gate: on these takes it chopped word endings, which sounds like the voice
# sticking. tts.voice_enhance = false keeps just the rumble filter and fades.
ENHANCE = ("adeclick=w=10:o=75:t=2,anlmdn=s=0.0002:p=0.002:r=0.006,"
           "equalizer=f=250:t=q:w=1:g=-2,equalizer=f=3500:t=q:w=1.2:g=4,highshelf=f=9000:g=4,"
           "deesser=i=0.35,acompressor=threshold=0.08:ratio=3:attack=8:release=120:makeup=2,")


def _clean(src: Path, dst: Path, enhance: bool = True) -> None:
    """Mono 48 kHz voice: filtered by ffmpeg, then silence trimmed with 15/40 ms fades so
    takes don't click in or out. Trimming is done here, not with ffmpeg's areverse:
    ffmpeg 9.0.2 crashes (SIGTRAP) on adeclick followed by areverse."""
    import numpy as np
    import soundfile as sf

    af = f"highpass=f=80{',' + ENHANCE.rstrip(',') if enhance else ''}"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", af, "-ac", "1", "-ar", str(SR),
                    "-c:a", "pcm_f32le", str(dst)], check=True)
    x, sr = sf.read(str(dst), dtype="float32")
    if x.ndim > 1:
        x = x.mean(1)
    w = int(sr * 0.01)
    db = 20 * np.log10(np.sqrt(np.mean(x[: len(x) // w * w].reshape(-1, w) ** 2, 1)) + 1e-9)
    voiced = np.where(db > -45)[0]
    if len(voiced):
        a = max(0, voiced[0] * w - int(sr * 0.08))              # keep 80 ms before the first word
        b = min(len(x), (voiced[-1] + 1) * w + int(sr * 0.08))  # and after the last
        x = x[a:b].copy()
    fi, fo = min(len(x), int(sr * 0.015)), min(len(x), int(sr * 0.04))
    x[:fi] *= np.linspace(0, 1, fi)
    x[len(x) - fo:] *= np.linspace(1, 0, fo)
    x = np.concatenate([x, np.zeros(int(sr * 0.12), dtype="float32")])
    sf.write(str(dst), x, sr, subtype="PCM_24")


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def _find_mic(device: str):
    """sounddevice index for a mic name (or index); None = system default input."""
    import sounddevice as sd

    if device.isdigit():
        return int(device)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and device.lower() in d["name"].lower():
            return i
    names = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
    raise RuntimeError(f"Mic {device!r} not found. Is it plugged in? Available: {', '.join(names)}")


def _record(raw: Path, device: str) -> bool:
    """Record through CoreAudio (sounddevice) at the mic's own sample rate. ffmpeg's
    avfoundation capture dropped/repeated small buffers on these Macs, which sounds like
    crackly radio breakup; here any dropped buffer is counted and reported."""
    import queue

    import sounddevice as sd
    import soundfile as sf

    try:
        idx = _find_mic(device)
    except RuntimeError as e:
        print(f"   {e}")
        input("   Plug it in (or fix tts.mic_device in config.json), then press Enter to try again. ")
        return False
    rate = int(sd.query_devices(idx)["default_samplerate"])
    q: queue.Queue = queue.Queue()
    glitches = [0]

    def callback(indata, frames, time, status):  # noqa: ARG001 - sounddevice signature
        if status.input_overflow:
            glitches[0] += 1
        q.put(indata.copy())

    input("   Press Enter, then read the line. ")
    with sf.SoundFile(str(raw), "w", samplerate=rate, channels=1, subtype="PCM_24") as f, \
            sd.InputStream(device=idx, channels=1, samplerate=rate, dtype="float32", latency="high",
                           blocksize=2048, callback=callback):
        stop = __import__("threading").Event()
        waiter = __import__("threading").Thread(target=lambda: (input("   ● Recording... press Enter when you're done. "), stop.set()))
        waiter.start()
        while not stop.is_set() or not q.empty():
            try:
                f.write(q.get(timeout=0.1))
            except queue.Empty:
                pass
        waiter.join()
    if not raw.exists() or raw.stat().st_size < 2000:
        print("   No audio was captured. If macOS asked for microphone access, allow it and try again.")
        return False
    if glitches[0]:
        print(f"   ⚠ {glitches[0]} audio glitch(es) while recording (the Mac was busy). Consider a redo.")
    return True


# ---------------------------------------------------------------- timings
def _whisper():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel

        log("Loading Whisper (first time downloads ~150 MB)...")
        _WHISPER = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _WHISPER


def align(text: str, wav: Path, duration: float):
    """Word timings for the SCRIPT's words (captions show what was fact-checked),
    taken from Whisper where the words match and interpolated where they don't.
    Returns (words, match_ratio, heard_text, script words that weren't heard)."""
    from .tts import estimate_word_times

    script_words = text.split()
    try:
        import numpy as np

        # decode with ffmpeg rather than faster-whisper's PyAV path (breaks across PyAV versions)
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                             capture_output=True, check=True).stdout
        audio = np.frombuffer(pcm, dtype=np.float32)
        segs, _ = _whisper().transcribe(audio, language="en", word_timestamps=True, beam_size=5,
                                        condition_on_previous_text=False)
        heard = [w for s in segs for w in (s.words or [])]
    except Exception as e:  # noqa: BLE001 - fall back to estimated timings
        log(f"Whisper unavailable ({e}); estimating word timings")
        return estimate_word_times(text, duration), 1.0, text, []
    a, b = [_norm(w) for w in script_words], [_norm(w.word) for w in heard]
    # near-misses ("codecs" for Codex) count as the script's word
    b = [x if x in a else next((y for y in a if y and difflib.SequenceMatcher(None, x, y).ratio() >= 0.8), x) for x in b]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(script_words)
    for blk in sm.get_matching_blocks():
        for i in range(blk.size):
            w = heard[blk.b + i]
            times[blk.a + i] = (float(w.start), float(w.end))
    hit = {i for i, tm in enumerate(times) if tm}
    est = estimate_word_times(text, duration)
    out = []
    for i, w in enumerate(script_words):
        if times[i] is None:  # between the nearest matched neighbours
            prev = next((times[j][1] for j in range(i - 1, -1, -1) if times[j]), None)
            nxt = next((times[j][0] for j in range(i + 1, len(times)) if times[j]), None)
            s = prev if prev is not None else est[i]["start"]
            e = nxt if nxt is not None else est[i]["end"]
            times[i] = (s, max(e, s + 0.12))
        out.append({"word": w, "start": round(times[i][0], 3), "end": round(times[i][1], 3)})
    matched = sum(blk.size for blk in sm.get_matching_blocks())
    missing = [script_words[i].strip(".,!?:;\"“”") for i in range(len(script_words)) if i not in hit]
    return out, matched / max(len(a), 1), " ".join(w.word.strip() for w in heard), missing


# ---------------------------------------------------------------- pace
PACE_FILE = Path(__file__).resolve().parent.parent / "voice_profile.json"


def update_pace(script: dict, clips: list[dict]) -> float | None:
    """Words per minute from this session's takes (first to last word of each line, so
    leading and trailing silence don't count); kept as an average of the last 5 sessions."""
    words = secs = 0.0
    by_id = {c["scene_id"]: c for c in clips}
    for sc in script["scenes"]:
        c = by_id.get(sc["id"])
        if c and c.get("words"):
            words += len(c["words"])
            secs += max(0.5, c["words"][-1]["end"] - c["words"][0]["start"])
    if secs < 10:
        return None
    prof = json.loads(PACE_FILE.read_text()) if PACE_FILE.exists() else {"sessions": []}
    prof["sessions"] = (prof["sessions"] + [round(words / secs * 60, 1)])[-5:]
    prof["wpm"] = round(sum(prof["sessions"]) / len(prof["sessions"]), 1)
    PACE_FILE.write_text(json.dumps(prof, indent=1))
    return prof["wpm"]


def fit_script_to_pace(cfg: dict) -> None:
    """Size the next script to how fast YOU speak (only when recording your own voice).
    Room is left for the pauses between scenes and the // beats in the read marks."""
    if cfg["tts"]["engine"] != "mine" or not PACE_FILE.exists():
        return
    wpm = json.loads(PACE_FILE.read_text()).get("wpm")
    if not wpm:
        return
    sc = cfg["script"]
    speaking = sc["target_seconds"] - 8 * cfg["tts"]["scene_gap_seconds"] - 4  # gaps + long pauses
    sc["words_max"] = max(90, int(wpm * speaking / 60))
    sc["words_min"] = sc["words_max"] - 20
    log(f"Script sized to your pace ({wpm:.0f} wpm): {sc['words_min']}-{sc['words_max']} words")


# ---------------------------------------------------------------- session
def _dropped(folder: Path, sid: int) -> Path | None:
    for ext in ("wav", "m4a", "mp3", "aiff", "caf"):
        p = folder / f"scene_{sid:02d}.{ext}"
        if p.exists():
            return p
    return None


def record_all(script: dict, cfg: dict, outdir: Path, reuse_takes: bool = False) -> dict:
    """reuse_takes: re-process the takes you already kept (e.g. after changing the voice
    chain) without asking anything; scenes with no take are still recorded."""
    mine = outdir / "mine"
    mine.mkdir(parents=True, exist_ok=True)
    meta_path = mine / "takes.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    device = str(cfg["tts"].get("mic_device", "0"))
    enhance = cfg["tts"].get("voice_enhance", True)
    scenes = script["scenes"]
    from .scriptwriter import format_read

    print(f"\nRecording your voiceover: {len(scenes)} lines. Speak like you're telling a friend.\n"
          "  When to start: press Enter, wait one beat (about a second), then speak.\n"
          "  When to stop:  after your last word, stay silent for a second, then press Enter.\n"
          "  How to read:   /  = short breath    //  = long pause    CAPITALS = stress that word\n"
          "Tip: about a hand's width from the mic, slightly to the side.\n")
    if scenes and scenes[0].get("start"):
        print(f"  How to begin the video: {scenes[0]['start']}\n")
    clips = []
    for n, s in enumerate(scenes, 1):
        sid, text = s["id"], s["voiceover"]
        wav = outdir / f"scene_{sid:02d}.wav"
        print(f"── {n}/{len(scenes)} · {s['type'].upper()} " + "─" * 40)
        if s.get("tone"):
            print(f"   Tone: {s['tone']}")
        say = [x for x in script.get("pronounce", []) if x.get("word") and x["word"].lower() in text.lower()]
        if say:
            print("   Say: " + " · ".join(f"{x['word']} = {x['say']}" for x in say))
        print(f"   “{format_read(s['read']) if s.get('read') else text}”")
        if n == len(scenes) and s.get("end"):
            print(f"   How to end: {s['end']}")
        print()
        src = _dropped(mine, sid)
        reuse = src is not None and meta.get(str(sid), {}).get("text", text) == text
        if reuse and reuse_takes:
            _clean(src, wav, enhance)
            dur = _duration(wav)
            words, ratio, _, missing = align(text, wav, dur)
            print(f"   reused {src.name} · {dur:.1f}s · {ratio:.0%} of words matched")
            clips.append({"scene_id": sid, "wav": str(wav), "duration": round(dur, 3), "words": words})
            continue
        while True:
            if reuse:
                ans = input(f"   Found a take ({src.name}). Enter = use it · r = record again: ").strip().lower()
                reuse = False
                if ans != "r":
                    take = src
                else:
                    continue
            else:
                take = mine / f"scene_{sid:02d}.wav"
                if not _record(take, device):
                    continue
            _clean(take, wav, enhance)
            dur = _duration(wav)
            words, ratio, heard, missing = align(text, wav, dur)
            if ratio < MATCH_WARN:
                print(f"   ⚠ That may differ from the fact-checked line ({ratio:.0%} of words matched).\n"
                      f"     Not heard: {', '.join(missing)}\n"
                      f"     Heard: “{heard}”\n"
                      "     (Names are often misheard; if you said them right, keep the take.)")
            ans = input(f"   {dur:.1f}s · Enter = keep · r = redo · p = play back: ").strip().lower()
            while ans == "p":
                subprocess.run(["afplay", str(wav)])
                ans = input("   Enter = keep · r = redo · p = play again: ").strip().lower()
            if ans == "r":
                continue
            if ratio < MATCH_WARN and input("   Use it anyway? Captions will show the script's words. [y/N] ").strip().lower() != "y":
                continue
            meta[str(sid)] = {"text": text, "file": take.name}
            meta_path.write_text(json.dumps(meta, indent=1))
            clips.append({"scene_id": sid, "wav": str(wav), "duration": round(dur, 3), "words": words})
            break
    gap = cfg["tts"]["scene_gap_seconds"]
    total = sum(c["duration"] for c in clips) + gap * len(clips)
    log(f"Voiceover (your voice): {total:.1f}s")
    wpm = update_pace(script, clips)
    if wpm:
        log(f"Your speaking pace: about {wpm:.0f} words a minute (next scripts are sized to it)")
    if total > cfg["script"]["max_seconds"]:
        log(f"Note: that's over {cfg['script']['max_seconds']}s. Re-record a little faster, or it's fine if the Short "
            "still feels tight (Shorts can run up to 3 minutes).")
    return {"engine": "mine", "speed": 1.0, "total": round(total, 2), "clips": clips}


if __name__ == "__main__":  # quick mic check: python -m agent.myvoice
    import sys

    from .utils import load_config

    out = Path.home() / "Desktop" / "mic_check.wav"
    device = sys.argv[1] if len(sys.argv) > 1 else str(load_config()["tts"].get("mic_device", "0"))
    print(f"Mic check on {device!r}: record a sentence or two, it plays back after.")
    if _record(out, device):
        subprocess.run(["afplay", str(out)])
        print(f"Saved: {out}")
