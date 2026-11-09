"""Interactive mode: research -> fact-check every story -> briefing + recommendation
-> you choose -> script, voiceover and video for each story you picked.

    python main.py --interactive

Today's briefing is saved, so running it again the same day can skip straight to
choosing without paying for research and fact-checking twice.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import factcheck, prompts, remote, render, research, scriptwriter, tts
from .llm import Claude
from .utils import add_history, log, read_json, save_json, topic_slug

LINE = "─" * 72


def factcheck_all(claude: Claude, cfg: dict, res: dict, today: dt.date) -> list[dict]:
    stories = res["stories"][: cfg["research"].get("interactive_factcheck", len(res["stories"]))]
    log(f"Fact-checking all {len(stories)} stories in parallel...")
    with ThreadPoolExecutor(max_workers=cfg["research"].get("factcheck_workers", 4)) as ex:
        return list(ex.map(lambda s: factcheck.check_story(claude, cfg, s, today), stories))


def recommend(claude: Claude, cfg: dict, stories: list[dict], checks: list[dict]) -> dict:
    brief = []
    for st, fc in zip(stories, checks):
        brief.append({
            "story_id": st["id"], "title": st["title"], "category": st.get("category"),
            "scores": st.get("scores"), "why_it_matters": st.get("why_it_matters"),
            "publishable": fc["publishable"], "fact_check_reason": fc.get("reason"),
            "usable_facts": [c["final_wording"] for c in fc["usable_claims"]],
            "caveats": fc.get("caveats", []),
        })
    log("Asking the producer for a recommendation...")
    rec, _ = claude.ask_json(cfg["models"]["factcheck"], prompts.RECOMMEND_SYSTEM,
                             prompts.RECOMMEND_USER.format(stories=json.dumps(brief, indent=1)), search=False)
    ok = {st["id"] for st, fc in zip(stories, checks) if fc["publishable"]}
    rec["recommended"] = [i for i in rec.get("recommended", []) if i in ok]
    return rec


def _publishers(fc: dict, aggregators: list[str]) -> list[str]:
    pubs = []
    for c in fc["usable_claims"]:
        for e in c.get("evidence", []):
            if any(a in e.get("url", "") for a in aggregators):
                continue
            p = (e.get("publisher") or "").strip()
            if p and p not in pubs:
                pubs.append(p)
    return pubs


BADGE = {"make": "  ★ MAKE THIS", "maybe": "  ? MAYBE", "skip": "  ✗ SKIP"}


def picks_for(b: dict) -> list[str]:
    """Producer's 'make' stories in its preferred order (falls back to its first pick)."""
    notes = {n["story_id"]: n for n in b["recommendation"].get("notes", [])}
    rec = b["recommendation"]["recommended"]
    return [i for i in rec if notes.get(i, {}).get("verdict") == "make"] or rec[:1]


def briefing_text(b: dict, full: bool, aggregators: list[str]) -> str:
    notes = {n["story_id"]: n for n in b["recommendation"].get("notes", [])}
    out = [f"# AI news briefing · {b['date']}", ""]
    for i, (st, fc) in enumerate(zip(b["stories"], b["checks"]), 1):
        n = notes.get(st["id"], {})
        star = BADGE.get(n.get("verdict", ""), "") if fc["publishable"] else "  ✗ FAILED FACT-CHECK"
        status = "PASSED" if fc["publishable"] else "FAILED"
        out.append(f"## {i}. {st['title']}{star}")
        out.append(f"{st.get('category', '?')} · fact-check {status} · {len(fc['usable_claims'])} verified facts"
                   f" · hook {st.get('scores', {}).get('hook', '?')}/10 · impact {st.get('scores', {}).get('significance', '?')}/10")
        if n:
            out.append(f"Producer: {n.get('verdict', '').upper()} — {n.get('why', '')}")
            if fc["publishable"] and n.get("angle"):
                out.append(f"Angle: {n['angle']}")
        if not fc["publishable"]:
            out.append(f"Why it failed: {fc.get('reason', '')}")
        TAG = {"P": "primary", "M": "major outlet"}
        for c in fc["usable_claims"] if full else fc["usable_claims"][:3]:
            tags = [TAG[t] for t in c.get("source_tiers", []) if t in TAG]
            out.append(f"  ✓ {c['final_wording']}" + (f"  [{' + '.join(tags)}]" if tags else ""))
        for c in (fc.get("caveats") or [])[: None if full else 2]:
            out.append(f"  ! {c}")
        pubs = _publishers(fc, aggregators)
        if pubs:
            out.append("  Sources: " + " · ".join(pubs[: None if full else 4]))
        if full:
            for c in fc["usable_claims"]:
                for e in c.get("evidence", []):
                    if not any(a in e.get("url", "") for a in aggregators):
                        tier = {"P": " [primary]", "M": " [major]"}.get(e.get("tier", ""), "")
                        out.append(f"    - {e.get('publisher', '')}{tier}: {e.get('url', '')}")
        out.append("")
    return "\n".join(out)


def build(claude: Claude, cfg: dict, today: dt.date, out: Path, from_research: str | None) -> dict:
    path = out / "briefing.json"
    if path.exists() and not from_research:
        b = read_json(path)
        question = f"Today's briefing already exists ({len(b['stories'])} stories). Reuse it?"
        if remote.active:
            reuse = remote.active.confirm(question)
        else:
            reuse = input(f"{question} [Y/n] ").strip().lower() not in ("n", "no")  # research costs money
        if reuse:
            return b
    if from_research:
        res = read_json(Path(from_research))
    else:
        res = research.run(claude, cfg, today)
        save_json(res, out / "research.json")
    if not res["stories"]:
        raise SystemExit("Research found no stories today.")
    checks = factcheck_all(claude, cfg, res, today)
    stories = res["stories"][: len(checks)]
    b = {"date": today.isoformat(), "stories": stories, "checks": checks,
         "recommendation": recommend(claude, cfg, stories, checks)}
    save_json(b, path)
    return b


def ask_choice(b: dict) -> list[int]:
    ids = [s["id"] for s in b["stories"]]
    rec = [ids.index(i) + 1 for i in picks_for(b)]
    ok = [i for i, fc in enumerate(b["checks"], 1) if fc["publishable"]]
    if not ok:
        print("No story passed fact-check today, so there is nothing safe to publish.")
        if remote.active:
            remote.active.say("No story passed fact-check today, so there is nothing safe to publish.")
        return []
    default = rec or ok[:1]
    if remote.active:
        options = [(i, f"{i}. {b['stories'][i - 1]['title']}") for i in ok]
        return remote.active.pick_many("Which stories should I turn into Shorts? My picks are ticked.", options, default,
                                       done_label="Make these")
    print(f"\nProducer's pick: {', '.join(map(str, default))} (press Enter to make {'these' if len(default) > 1 else 'it'}).")
    while True:
        ans = input("Which stories should I turn into Shorts? Numbers like 1,3 · Enter = producer's pick"
                    " · all · q = quit: ").strip().lower()
        if ans in ("q", "quit"):
            return []
        if ans == "":
            return default
        if ans == "all":
            return ok
        picks = [int(x) for x in re.findall(r"\d+", ans)]
        bad = [p for p in picks if p not in ok]
        if picks and not bad:
            return list(dict.fromkeys(picks))
        if bad:
            print(f"Story {', '.join(map(str, bad))} didn't pass fact-check, so it can't be made. Pick from {ok}.")


def generate(claude: Claude, cfg: dict, story: dict, fc: dict, folder: Path, today: dt.date) -> Path:
    from main import write_script_md  # keeps one copy of the script.md writer

    folder.mkdir(parents=True, exist_ok=True)
    save_json({"story": story, "factcheck": fc}, folder / "factcheck.json")
    from .myvoice import fit_script_to_pace

    fit_script_to_pace(cfg)
    script = scriptwriter.run(claude, cfg, {"story": story, "factcheck": fc})
    script["date_label"] = today.strftime("%b %d, %Y").upper()
    from .language import translate

    script = translate(claude, cfg, script, cfg["tts"].get("language", "en"))
    save_json(script, folder / "script.json")
    write_script_md(script, fc, folder / "script.md")
    if cfg.get("images", {}).get("_want"):  # photos before recording, so the first render has them
        from .images import maybe_add

        script = maybe_add(folder / "script.json", cfg, claude, asked=True)
    voice = tts.synthesize(script, cfg, folder / "voice")
    save_json(voice, folder / "voice" / "timings.json")
    script["template"] = cfg["video"].get("template", "classic")  # remembered for the social carousel
    save_json(script, folder / "script.json")
    mp4 = render.render_video(cfg, script, voice, folder / "short.mp4")
    render.render_still(cfg, script, voice, 0, 2.5, folder / "cover.png")
    from . import qa

    qa.check(script, voice, cfg, mp4)
    if cfg.get("social", {}).get("enabled", True):
        try:
            from .social import make as make_posts

            make_posts(folder / "script.json", cfg, claude)
        except Exception as e:  # noqa: BLE001 - the video is done; posts can be redone later
            log(f"Social posts failed ({e}); run: python -m agent.social {folder / 'script.json'}")
    add_history(script.get("story_title", ""), [s["url"] for s in script.get("sources", [])], today)
    if remote.active:
        remote.active.deliver(folder, mp4)
    return mp4


def ask_voice(cfg: dict) -> str:
    """Who speaks the voiceover. Enter keeps tts.engine from config."""
    if remote.active:
        return "edge"  # recording yourself needs you at the Mac
    lang = cfg["tts"].get("language", "en")
    options = [("edge", "AI voice (Edge, free)")]
    if lang == "en":  # recording yourself is English-only for now
        options.append(("mine", "Record my own voice now"))
    if len(options) == 1:  # nothing to choose (Tamil is AI voice only)
        return options[0][0]
    current = cfg["tts"]["engine"] if cfg["tts"]["engine"] in dict(options) else "edge"
    print("\nVoiceover:")
    for i, (key, label) in enumerate(options, 1):
        print(f"  {i}. {label}{'  ← default' if key == current else ''}")
    try:
        ans = input(f"Pick 1-{len(options)} · Enter = default: ").strip()
    except EOFError:
        return current
    if ans.isdigit() and 1 <= int(ans) <= len(options):
        return options[int(ans) - 1][0]
    return current


def run(cfg: dict, today: dt.date, out: Path, from_research: str | None = None, brain: str | None = None) -> int:
    from .llm import ask_brain, make_claude

    brain = brain or ask_brain(cfg)
    if brain == "subscription":  # parallel Claude Code sessions eat plan limits fast
        cfg["research"]["factcheck_workers"] = min(cfg["research"].get("factcheck_workers", 4), 2)
    claude = make_claude(cfg, brain)
    b = build(claude, cfg, today, out, from_research)
    agg = cfg["research"].get("aggregator_domains", [])
    (out / "briefing.md").write_text(briefing_text(b, True, agg), encoding="utf-8")
    print("\n" + LINE + "\n" + briefing_text(b, False, agg) + LINE)
    print(f"Full briefing with every fact and link: {out / 'briefing.md'}")
    if remote.active:
        remote.active.doing = "waiting for your answers"
        remote.active.say(briefing_text(b, False, agg))
        remote.active.send_file(out / "briefing.md", "Full briefing with every fact and link")
    picks = ask_choice(b)
    if picks:
        from .language import ask_language

        cfg["tts"]["language"] = ask_language(cfg)
        cfg["tts"]["engine"] = ask_voice(cfg)
    if picks:
        # each story gets its own template and pattern, recommended for its category
        looks = {}
        for n in picks:
            st = b["stories"][n - 1]
            if len(picks) > 1:
                print(f"\n── Short {n}: {st['title']}")
            looks[n] = (render.ask_template(cfg, st.get("category", "")), scriptwriter.ask_pattern(cfg, st.get("category", "")))
        want_photos = False
        if cfg.get("images", {}).get("enabled", True):
            try:
                question = "Add real photos (free-licence; you approve each one)?"
                want_photos = (remote.active.confirm(question) if remote.active
                               else input(f"{question} [Y/n] ").strip().lower() not in ("n", "no"))
            except EOFError:
                pass
        cfg.setdefault("images", {})["_want"] = want_photos
    made = []
    for n in picks:
        st, fc = b["stories"][n - 1], b["checks"][n - 1]
        cfg["video"]["template"], cfg["script"]["pattern"] = looks[n]
        log(f"Making Short {n}: {st['title']}")
        if remote.active:
            remote.active.doing = f"making: {st['title']}"
            remote.active.check()
            remote.active.say(f"Making: {st['title']}")
        try:
            made.append(generate(claude, cfg, st, fc, out / topic_slug(st["title"]), today))
        except Exception as e:  # one bad story shouldn't stop the others
            log(f"Short {n} failed: {e}")
            if remote.active:
                remote.active.say(f"Short {n} failed: {str(e)[:300]}")
    for m in made:
        qa_file = Path(m).parent / "qa.json"
        bad = qa_file.exists() and json.loads(qa_file.read_text()).get("problems")
        print(f"  {'⚠ NEEDS REVIEW' if bad else '✓'} {m}")
    return 0 if made or not picks else 1
