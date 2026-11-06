#!/usr/bin/env python3
"""AI Shorts Agent: research -> fact-check -> script -> voiceover -> 9:16 MP4.

Usage:
  python main.py                      # full daily run
  python main.py --from-script FILE   # re-render a script.json (in its own folder)
  python main.py --stop-after script  # research + fact-check + script only
  python main.py --from-research FILE --story s3   # skip research, make a Short of one chosen story
  python main.py --interactive        # research + fact-check all, show briefing, you pick
  python main.py --tts kokoro         # force the offline voice
  python main.py --tts mine           # record the voiceover yourself, scene by scene
  python main.py --tts clone          # your cloned voice (local-voice tool), no recording
  python main.py --from-script FILE --reuse-takes   # re-process your kept recordings (no re-recording)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

from agent import factcheck, render, research, scriptwriter, tts
from agent.utils import add_history, load_config, load_dotenv, log, read_json, save_json, today_dir, topic_dir


def write_script_md(script: dict, fc: dict | None, path: Path) -> None:
    lines = [f"# {script.get('youtube_title') or script.get('title')}", ""]
    if script.get("audit_warnings"):
        lines += ["> **Check before recording** - the fact audit still flags:", ""]
        lines += [f"> - {w}" for w in script["audit_warnings"]] + [""]
    lines += ["## As it will be heard", "", " ".join(s.get("spoken") or s.get("voiceover", "") for s in script["scenes"]), ""]
    lines += ["## Script", ""]
    for s in script["scenes"]:
        lines.append(f"**{s['id']}. {s['type'].upper()}** — {s['voiceover']}")
        if s.get("spoken"):
            lines.append(f"  - spoken ({script.get('language')}): {s['spoken']}")
        lines.append(f"  - on screen: {s.get('on_screen')}  · claims: {', '.join(s.get('claim_ids', []))}")
    if any(s.get("read") for s in script["scenes"]):
        from agent.scriptwriter import format_read

        lines += ["", "## Recording sheet", "",
                  "Press Enter, wait one beat, then speak. After the last word, stay silent a second, then press Enter.",
                  "`/` = short breath · `//` = long pause · CAPITALS = stress that word", ""]
        guide = [x for x in script.get("pronounce", []) if x.get("word") and x.get("say")]
        if guide:
            lines.append("**Say it like this:** " + " · ".join(
                f"{x['word']} = *{x['say']}*" + (" (double-check)" if x["say"].endswith("?") else "") for x in guide) + "\n")
        if script["scenes"][0].get("start"):
            lines.append(f"**How to begin:** {script['scenes'][0]['start']}\n")
        for s in script["scenes"]:
            lines.append(f"**{s['id']}. {s['type'].upper()}** · _{s.get('tone', '')}_  ")
            lines.append(f"{format_read(s.get('read') or s['voiceover'])}\n")
        if script["scenes"][-1].get("end"):
            lines.append(f"**How to end:** {script['scenes'][-1]['end']}")
    lines += ["", "## Fact-check", ""]
    for c in script.get("claims", []):
        lines.append(f"- **{c['id']}** ({c.get('verdict')}): {c.get('final_wording')}")
        for e in c.get("evidence", []):
            lines.append(f"    - {e.get('publisher', '')}: {e.get('url')}")
    if fc and fc.get("caveats"):
        lines += ["", "**Caveats:** " + "; ".join(fc["caveats"])]
    lines += ["", "## Upload", "", f"**Title:** {script.get('youtube_title', '')}", "",
              f"**Description:**\n{script.get('description', '')}", "", "**Hashtags:** " + " ".join(script.get("hashtags", []))]
    path.write_text("\n".join(lines), encoding="utf-8")


def add_homebrew_to_path() -> None:
    """Find Homebrew's ffmpeg even when the shell profile doesn't put it on PATH."""
    path = os.environ.get("PATH", "").split(os.pathsep)
    for d in ("/opt/homebrew/bin", "/usr/local/bin"):
        if os.path.isdir(d) and d not in path:
            path.append(d)
    os.environ["PATH"] = os.pathsep.join(path)


def main() -> int:
    add_homebrew_to_path()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--from-script", default=None, help="render an existing script.json")
    ap.add_argument("--from-research", default=None, help="reuse an existing research.json")
    ap.add_argument("--story", default=None, help="story id from research.json to use (e.g. s3)")
    ap.add_argument("--interactive", action="store_true", help="show today's fact-checked briefing and let me pick")
    ap.add_argument("--stop-after", choices=["research", "factcheck", "script", "tts"], default=None)
    ap.add_argument("--tts", choices=["edge", "kokoro", "mine", "clone"], default=None,
                    help="voiceover: AI voice, 'mine' to record yourself, or 'clone' for your cloned voice")
    ap.add_argument("--language", choices=["en", "ta"], default=None,
                    help="voiceover language (the script is fact-checked in English, then translated)")
    ap.add_argument("--reuse-takes", action="store_true", help="with --tts mine: re-process your kept recordings, no prompts")
    ap.add_argument("--template", choices=["tutorial", "terminal", "news", "editorial", "chat", "classic"], default=None, help="video look for this run")
    ap.add_argument("--brain", choices=["api", "subscription"], default=None, help="who researches and writes: API key or your Claude subscription")
    ap.add_argument("--pattern", choices=["story", "explainer"], default=None, help="script pattern (skips the question)")
    ap.add_argument("--no-social", action="store_true", help="don't write the YouTube/Instagram/LinkedIn posts")
    ap.add_argument("--no-images", action="store_true", help="don't offer to add real photos")
    ap.add_argument("--out", default=None, help="output folder (default outputs/YYYY-MM-DD/<topic>)")
    args = ap.parse_args()

    load_dotenv()
    cfg = load_config(args.config)
    if args.tts:
        cfg["tts"]["engine"] = args.tts
    if args.language:
        cfg["tts"]["language"] = args.language
    elif sys.stdin.isatty() and not args.interactive and args.stop_after not in ("research", "factcheck"):
        from agent.language import ask_language

        cfg["tts"]["language"] = ask_language(cfg)
    if args.template:
        cfg["video"]["template"] = args.template
    if args.no_images:
        cfg.setdefault("images", {})["enabled"] = False
    if args.reuse_takes:
        cfg["tts"]["engine"], cfg["tts"]["reuse_takes"] = "mine", True
    today = dt.date.today()
    day = Path(args.out) if args.out and args.interactive else today_dir(today)  # research + briefing
    folder = Path(args.out) if args.out else None  # this Short's own folder: outputs/<date>/<topic>/
    fc = None

    if args.interactive:
        from agent import briefing

        day.mkdir(parents=True, exist_ok=True)
        return briefing.run(cfg, today, day, args.from_research, args.brain)

    if args.from_script:
        script = read_json(Path(args.from_script))
        folder = folder or Path(args.from_script).parent  # re-render in place
        if not args.template:
            cfg["video"]["template"] = render.ask_template(cfg, script.get("category", ""))
    else:
        from agent.llm import ask_brain, make_claude

        claude = make_claude(cfg, args.brain or ask_brain(cfg))

        if args.from_research:
            res = read_json(Path(args.from_research))
        else:
            res = research.run(claude, cfg, today)
            save_json(res, day / "research.json")
        if args.story:
            res["stories"] = [s for s in res["stories"] if s["id"] == args.story]
            if not res["stories"]:
                log(f"No story with id {args.story} in research.json.")
                return 1
        if args.stop_after == "research" or not res["stories"]:
            return 0 if res["stories"] else 1

        chk = factcheck.run(claude, cfg, res, today)
        if not chk["chosen"]:
            save_json(chk, day / "factcheck.json")
            log(f"No story passed fact-check today. Nothing published (see {day / 'factcheck.json'}).")
            return 2
        fc = chk["chosen"]["factcheck"]
        folder = folder or topic_dir(today, chk["chosen"]["story"]["title"])
        save_json(chk, folder / "factcheck.json")
        if args.stop_after == "factcheck":
            return 0

        if not args.template and args.stop_after not in ("script", "tts"):
            cfg["video"]["template"] = render.ask_template(cfg, chk["chosen"]["story"].get("category", ""))
        cfg["script"]["pattern"] = args.pattern or scriptwriter.ask_pattern(cfg, chk["chosen"]["story"].get("category", ""))
        from agent.myvoice import fit_script_to_pace

        fit_script_to_pace(cfg)
        script = scriptwriter.run(claude, cfg, chk["chosen"])
        script["date_label"] = today.strftime("%b %d, %Y").upper()
        save_json(script, folder / "script.json")
        write_script_md(script, fc, folder / "script.md")
        if args.stop_after == "script":
            return 0

    folder.mkdir(parents=True, exist_ok=True)
    lang = cfg["tts"].get("language", "en")
    if lang != script.get("language", "en") or (lang != "en" and not all(s.get("spoken") for s in script["scenes"])):
        from agent.language import translate

        if args.from_script:
            from agent.llm import ask_brain, make_claude

            claude = make_claude(cfg, args.brain or ask_brain(cfg))
        script = translate(claude, cfg, script, lang)
        save_json(script, folder / "script.json")
        write_script_md(script, fc, folder / "script.md")
    if args.stop_after not in ("script",):  # offer real photos before recording/rendering
        from agent.images import maybe_add

        script = maybe_add(folder / "script.json", cfg) if (folder / "script.json").exists() else script
    voice = tts.synthesize(script, cfg, folder / "voice")
    save_json(voice, folder / "voice" / "timings.json")
    if args.stop_after == "tts":
        return 0

    script["template"] = cfg["video"].get("template", "classic")  # remembered for the social carousel
    save_json(script, folder / "script.json") if (folder / "script.json").exists() else None
    mp4 = render.render_video(cfg, script, voice, folder / "short.mp4")
    render.render_still(cfg, script, voice, 0, 2.5, folder / "cover.png")
    if cfg.get("social", {}).get("enabled", True) and not args.no_social and (folder / "script.json").exists():
        try:
            from agent.social import make as make_posts

            make_posts(folder / "script.json", cfg)
        except Exception as e:  # noqa: BLE001 - the video is done; posts can be redone later
            log(f"Social posts failed ({e}); run: python -m agent.social {folder / 'script.json'}")
    if not args.from_script:
        add_history(script.get("story_title", script.get("title", "")), [s["url"] for s in script.get("sources", [])], today)
    log(f"Done -> {mp4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
