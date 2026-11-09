"""The last check before a video counts as done: does the finished file match the script?
Writes qa.json next to the video. "needs_review" means don't upload until it's fixed."""
from __future__ import annotations

import json
from pathlib import Path

from .utils import log

SQUEEZED_WORD_SECONDS = 0.06  # a caption word on screen for less than this can't be read
MAX_SQUEEZED_SHARE = 0.15
MIN_WORDS_HEARD = 0.8  # share of the script's words Whisper must hear in the finished video
LONG_FORM_SECONDS = 120  # longer videos have their own review; the Short rules don't apply


def caption_problems(voice: dict) -> list[str]:
    """Word timings that were guessed badly show up as captions flashing past."""
    out = []
    for c in voice.get("clips", []):
        words = c.get("words", [])
        short = [w for w in words if w["end"] - w["start"] < SQUEEZED_WORD_SECONDS]
        if len(words) >= 5 and len(short) / len(words) > MAX_SQUEEZED_SHARE:
            out.append(f"Scene {c.get('scene_id')}: {len(short)} of {len(words)} caption words flash by too fast to read.")
    return out


def check(script: dict, voice: dict, cfg: dict, mp4: Path) -> dict:
    from .myvoice import _duration, _loudest_db, align

    mp4 = Path(mp4)
    problems = caption_problems(voice)
    dur = _duration(mp4)
    sc, lang = cfg["script"], script.get("language", "en")
    if dur <= LONG_FORM_SECONDS:
        if not sc["min_seconds"] - 2 <= dur <= sc["max_seconds"] + 2:
            problems.append(f"The video is {dur:.0f}s; a Short here should be {sc['min_seconds']}-{sc['max_seconds']}s.")
        if lang == "en":  # listen to the finished file, not the pieces it was built from
            text = " ".join(s.get("voiceover", "") for s in script.get("scenes", []))
            _, ratio, _, missing = align(text, mp4, dur)
            if ratio < MIN_WORDS_HEARD:
                problems.append(f"Only {ratio:.0%} of the script's words can be heard in the finished video "
                                f"(missing: {', '.join(missing[:12])}).")
        elif _loudest_db(mp4) < -40:
            problems.append("The finished video has no audible voice.")
    result = {"status": "needs_review" if problems else "ready", "problems": problems}
    (mp4.parent / "qa.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    if problems:
        log("⚠ NEEDS REVIEW - don't upload this yet:")
        for p in problems:
            log(f"   - {p}")
    else:
        log("Final check passed: the video matches the script.")
    return result
