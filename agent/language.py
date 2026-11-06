"""The voiceover language. The script is always written and fact-checked in English;
for another language each scene's line is translated into natural spoken language
("spoken"), back-checked for meaning, and that is what's voiced and captioned.
Headlines, numbers and source chips on screen stay in English."""
from __future__ import annotations

import json

from .utils import log

LANGUAGES = {"en": "English", "ta": "Tamil"}

TRANSLATE_SYSTEM = """You translate a fact-checked YouTube Short's voiceover for a {name}-speaking
audience. Write the way a popular {name} tech YouTuber talks to friends: everyday spoken
{name}, warm and clear, never formal or literary, and English tech words people already
say in {name} conversation are fine ("AI", "download", "model").
Accuracy comes first:
- Keep the exact meaning of every line: same facts, same numbers, same names, same
  attribution ("Tencent says" stays "Tencent says"), same caveats. Add nothing, drop
  nothing, don't make anything stronger or weaker.
- Write company, product and people's names in {name} script as they are pronounced
  (so the voice says them right).
- Write numbers as {name} words, the way they're said aloud.
- Keep each scene's line about as long as the English, so the video timing still works.
- Keep the flow: each line still follows from the one before."""

TRANSLATE_USER = """Translate each scene's English line into spoken {name}.

{lines}

Return one ```json block: {{"scenes": [{{"id": 1, "spoken": "..."}}]}}"""

CHECK_SYSTEM = """You check a translation of a fact-checked news script. Translate each {name} line
back into English yourself, then compare it with the original English line. Report only
real meaning differences: a fact, number, name or attribution added, dropped or changed,
or a claim made stronger or weaker. Wording and style differences are fine."""

CHECK_USER = """Each scene: the original English, then the {name} translation.

{pairs}

Return one ```json block:
{{"ok": true, "issues": [{{"scene_id": 1, "back_translation": "...", "problem": "...", "fix": "..."}}]}}"""


def name(lang: str) -> str:
    return LANGUAGES.get(lang, lang)


def ask_language(cfg: dict) -> str:
    """Which language the voiceover is in. Enter keeps tts.language from config."""
    current = cfg["tts"].get("language", "en")
    codes = list(LANGUAGES)
    print("\nVoiceover language:")
    for i, code in enumerate(codes, 1):
        print(f"  {i}. {LANGUAGES[code]}{'  ← default' if code == current else ''}")
    try:
        ans = input(f"Pick 1-{len(codes)} · Enter = default: ").strip().lower()
    except EOFError:
        return current
    if ans.isdigit() and 1 <= int(ans) <= len(codes):
        return codes[int(ans) - 1]
    return ans if ans in LANGUAGES else current


def spoken(scene: dict) -> str:
    """What is actually said for this scene: the translation, or the English line."""
    return scene.get("spoken") or scene.get("voiceover", "")


def _check(claude, cfg: dict, script: dict, lang: str) -> list[dict]:
    pairs = "\n\n".join(f"[{s['id']}] English: {s['voiceover']}\n[{s['id']}] {name(lang)}: {s.get('spoken', '')}"
                        for s in script["scenes"])
    out, _ = claude.ask_json(cfg["models"]["factcheck"], CHECK_SYSTEM.format(name=name(lang)),
                             CHECK_USER.format(name=name(lang), pairs=pairs), search=False)
    return [x for x in out.get("issues", []) if isinstance(x, dict)]


def translate(claude, cfg: dict, script: dict, lang: str) -> dict:
    """Add scene["spoken"] in `lang`, back-checked for meaning (one fix round)."""
    if lang == "en":
        for s in script["scenes"]:
            s.pop("spoken", None)
        script["language"] = "en"
        return script
    if script.get("language") == lang and all(s.get("spoken") for s in script["scenes"]):
        return script  # already translated (re-render)
    log(f"Translating the voiceover into {name(lang)}...")
    lines = "\n".join(f"[{s['id']}] {s['voiceover']}" for s in script["scenes"])
    out, _ = claude.ask_json(cfg["models"]["script"], TRANSLATE_SYSTEM.format(name=name(lang)),
                             TRANSLATE_USER.format(name=name(lang), lines=lines), search=False)
    by_id = {x.get("id"): x.get("spoken", "") for x in out.get("scenes", []) if isinstance(x, dict)}
    missing = [s["id"] for s in script["scenes"] if not by_id.get(s["id"])]
    if missing:
        raise RuntimeError(f"Translation is missing scenes {missing}")
    for s in script["scenes"]:
        s["spoken"] = by_id[s["id"]].strip()
    issues = _check(claude, cfg, script, lang)
    if issues:
        log(f"Translation check: {len(issues)} meaning difference(s); fixing")
        fix, _ = claude.ask_json(
            cfg["models"]["script"], TRANSLATE_SYSTEM.format(name=name(lang)),
            "Fix these meaning differences in the translation. Change only what's needed.\n\n"
            f"Issues:\n{json.dumps(issues, indent=1, ensure_ascii=False)}\n\nCurrent translation:\n"
            + json.dumps([{"id": s["id"], "english": s["voiceover"], "spoken": s["spoken"]} for s in script["scenes"]],
                         indent=1, ensure_ascii=False)
            + '\n\nReturn one ```json block: {"scenes": [{"id": 1, "spoken": "..."}]}',
            search=False,
        )
        for x in fix.get("scenes", []):
            for s in script["scenes"]:
                if isinstance(x, dict) and s["id"] == x.get("id") and x.get("spoken"):
                    s["spoken"] = x["spoken"].strip()
        issues = _check(claude, cfg, script, lang)
    warn = [f"{name(lang)} scene {x.get('scene_id')}: {x.get('problem')}" for x in issues]
    script["audit_warnings"] = [w for w in script.get("audit_warnings", []) if not w.startswith(name(lang))] + warn
    for w in warn:
        log(f"⚠ Translation still differs - check before uploading: {w}")
    if not warn:
        log(f"{name(lang)} translation matches the English meaning.")
    script["language"] = lang
    return script
