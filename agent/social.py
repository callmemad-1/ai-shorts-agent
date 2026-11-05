"""Daily social pack for each Short: YouTube, Instagram and LinkedIn posts + a carousel.

    python -m agent.social outputs/<date>/<topic>/script.json

Everything is written ONLY from the Short's fact-checked claims and caveats, audited
like the script, and checked in code (length limits, clickbait, aggregators, sources).
The pack lands in <topic>/posts/: posts.md (copy-paste blocks), posts.json and
carousel_01.png ... (1080x1350, 4:5) for Instagram and LinkedIn.

Nothing is posted: you review and post it yourself.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .utils import log

SOCIAL_SYSTEM = """You write the daily social posts for a YouTube Shorts channel about AI news. You
may ONLY use the fact-checked claims and caveats given. No other facts, numbers, names
or predictions. Keep every attribution ("Tencent says"), keep the honest caveat, and
never make the story sound bigger than the facts.

Voice: a real person who follows AI closely - warm, curious, plain English, short lines,
an honest reaction where it fits. Never AI-sounding filler ("let's dive in",
"game-changer", "delve", "landscape", "unprecedented", "in today's fast-paced world").
No clickbait ("shocking", "you won't believe", ALL CAPS words). Neutral on politics.
Never name aggregator or digest sites; credit the original outlets.

Per platform:
- youtube: "title" (max 70 chars, key fact in the first 40), "description" (2-3 short
  sentences, then "Sources:" with one URL per line), "hashtags" (3-5, incl. #Shorts),
  "pinned_comment" (one friendly question that invites replies, max 150 chars).
- instagram: "caption" for the Reel - first line is a hook under 125 characters (it's
  all people see before "more"), then 3-5 short lines, then one line asking people to
  save or comment, then "Sources: <outlet names>". Max 1,000 characters. "hashtags":
  6-10 relevant tags. "alt_text": one plain sentence describing the video for
  screen readers.
- linkedin: "post" for professionals - a strong first line (the insight), 3-5 short
  paragraphs (what happened, why it matters for people's work, the caveat), one
  question to start a discussion, then "Sources:" with the outlet names and URLs.
  120-220 words. "hashtags": 3-4."""

SOCIAL_USER = """Story: {title}
The Short's script (what viewers hear):
{voiceover}

Fact-checked claims (use only these):
{claims}
Caveats:
{caveats}
Sources (outlet - URL):
{sources}
{credits}
Return one ```json block:
{{"youtube": {{"title": "...", "description": "...", "hashtags": ["#..."], "pinned_comment": "..."}},
  "instagram": {{"caption": "...", "hashtags": ["#..."], "alt_text": "..."}},
  "linkedin": {{"post": "...", "hashtags": ["#..."]}}}}
"""

CLICKBAIT = re.compile(r"\b(shocking|insane|unbelievable|you won'?t believe|mind[- ]blowing|game[- ]changer|delve|"
                       r"let's dive in|dive into|landscape|unprecedented|buckle up)\b", re.I)


def _facts(script: dict, fc: dict) -> tuple[str, str]:
    claims = "\n".join(f"- {c['id']}: {c['final_wording']}" for c in fc.get("usable_claims", script.get("claims", [])))
    caveats = "\n".join(f"- {c}" for c in fc.get("caveats", [])) or "- (none)"
    return claims, caveats


def check(posts: dict, cfg: dict) -> list[str]:
    """Code-level checks that don't depend on the model's judgement."""
    problems = []
    agg = cfg["research"].get("aggregator_domains", [])
    yt, ig, li = posts.get("youtube", {}), posts.get("instagram", {}), posts.get("linkedin", {})
    if not 0 < len(yt.get("title", "")) <= 70:
        problems.append(f"YouTube title is {len(yt.get('title', ''))} characters; need 1-70.")
    if "http" not in yt.get("description", ""):
        problems.append("YouTube description must list the source URLs.")
    if len(yt.get("pinned_comment", "")) > 150:
        problems.append("YouTube pinned comment is over 150 characters.")
    cap = ig.get("caption", "")
    if not cap or len(cap) > 1000:
        problems.append(f"Instagram caption is {len(cap)} characters; need 1-1000.")
    elif len(cap.split("\n")[0]) > 125:
        problems.append("Instagram's first line must be under 125 characters (it's all people see before 'more').")
    if not 6 <= len(ig.get("hashtags", [])) <= 10:
        problems.append(f"Instagram needs 6-10 hashtags, has {len(ig.get('hashtags', []))}.")
    words = len(li.get("post", "").split())
    if not 100 <= words <= 260:
        problems.append(f"LinkedIn post is {words} words; aim for 120-220.")
    if "Sources" not in li.get("post", "") or "Sources" not in cap:
        problems.append("Instagram and LinkedIn posts must name their sources.")
    for name, text in (("YouTube", yt.get("title", "") + yt.get("description", "")), ("Instagram", cap), ("LinkedIn", li.get("post", ""))):
        m = CLICKBAIT.search(text)
        if m:
            problems.append(f"{name} post uses clickbait or AI-sounding filler: {m.group(0)!r}.")
        bad = [a for a in agg if a.split(".")[0] in text.lower()]
        if bad:
            problems.append(f"{name} post names an aggregator ({', '.join(bad)}); credit the original outlet.")
    return problems


def write(claude, cfg: dict, script: dict, fc: dict) -> dict:
    from . import prompts

    claims, caveats = _facts(script, fc)
    srcs = "\n".join(f"- {s.get('publisher', '')} - {s['url']}" for s in script.get("sources", []))
    imgs = [im for sc in script.get("scenes", []) for im in (sc.get("images") or ([sc["image"]] if sc.get("image") else []))]
    credits = ("Image credits (must be listed in the YouTube description and Instagram caption):\n"
               + "\n".join(f"- {i['credit']} ({i['page']})" for i in imgs) + "\n") if imgs else ""
    user = SOCIAL_USER.format(title=script.get("story_title") or script.get("title", ""),
                              voiceover="\n".join(s["voiceover"] for s in script["scenes"]),
                              claims=claims, caveats=caveats, sources=srcs, credits=credits)
    model = cfg["models"]["script"]
    log("Writing social posts (YouTube, Instagram, LinkedIn)...")
    posts, _ = claude.ask_json(model, SOCIAL_SYSTEM, user, search=False)
    for round_ in range(2):
        issues = [{"problem": p} for p in check(posts, cfg)]
        audit, _ = claude.ask_json(cfg["models"]["factcheck"], prompts.AUDIT_SYSTEM, prompts.AUDIT_USER.format(
            claims=claims, caveats=caveats, titles=json.dumps({"youtube_title": posts.get("youtube", {}).get("title")}),
            scenes=json.dumps([{"id": 1, "voiceover": posts.get("youtube", {}).get("description", "")},
                               {"id": 2, "voiceover": posts.get("instagram", {}).get("caption", "")},
                               {"id": 3, "voiceover": posts.get("linkedin", {}).get("post", "")}], indent=1)), search=False)
        issues += [{**i, "where": {1: "YouTube", 2: "Instagram", 3: "LinkedIn"}.get(i.get("scene_id"), "title")}
                   for i in audit.get("issues", [])]
        if not issues:
            log("Social posts passed the audit")
            break
        log(f"Social posts: {len(issues)} issue(s); revising")
        posts, _ = claude.ask_json(model, SOCIAL_SYSTEM, user + "\n\nFix these issues and return the same JSON:\n"
                                   + json.dumps(issues, indent=1) + "\n\nCurrent posts:\n" + json.dumps(posts, indent=1), search=False)
    posts["remaining_issues"] = check(posts, cfg)
    if imgs:  # credits are required by the licences: make sure they're there whatever the model wrote
        lines = "\n".join(f"{i['credit']} ({i['page']})" for i in imgs)
        if "Image credits" not in posts["youtube"].get("description", ""):
            posts["youtube"]["description"] += "\n\nImage credits:\n" + lines
        if "Photo" not in posts["instagram"].get("caption", "") and "Image" not in posts["instagram"].get("caption", ""):
            posts["instagram"]["caption"] += "\n\nPhotos: " + "; ".join(i["credit"] for i in imgs)
    return posts


def carousel(cfg: dict, script: dict, voice: dict, out: Path, slides: int = 6) -> list[Path]:
    """4:5 slides (1080x1350) from the Short's scenes, drawn by the same template with no
    captions: the hook as the cover, the key scenes, the reality check, the ending."""
    from PIL import Image

    from .render import render_still

    scenes = script["scenes"]
    keep = [0] + [i for i, s in enumerate(scenes) if s["type"] in ("step", "setup", "twist", "stat", "example", "stakes")]
    keep += [i for i, s in enumerate(scenes) if s["type"] == "verify"] + [len(scenes) - 1]
    keep = list(dict.fromkeys(keep))
    if len(keep) > slides:
        middle = keep[1:-2][: slides - 3]
        keep = [keep[0]] + middle + keep[-2:]
    cfg = {**cfg, "video": {**cfg["video"], "template": script.get("template") or cfg["video"].get("template", "classic")}}
    silent = {**voice, "clips": [{**c, "words": []} for c in voice["clips"]]}  # no captions on slides
    paths = []
    for n, i in enumerate(keep, 1):
        f = out / f"carousel_{n:02d}.png"
        render_still(cfg, script, silent, i, max(2.5, silent["clips"][i]["duration"] - 0.4), f)
        im = Image.open(f).convert("RGB")
        top = 60  # keep the brand header, drop the area under the content where captions sit
        im.crop((0, top, 1080, top + 1350)).save(f)
        paths.append(f)
    return paths


def markdown(posts: dict, slides: list[Path]) -> str:
    yt, ig, li = posts["youtube"], posts["instagram"], posts["linkedin"]
    out = ["# Social posts", "", "Review before posting. Nothing here has been posted.", ""]
    if posts.get("remaining_issues"):
        out += ["**Check these first:** " + "; ".join(posts["remaining_issues"]), ""]
    out += ["## YouTube Shorts", "", "**Title**", "```", yt["title"], "```", "", "**Description**", "```",
            yt["description"] + "\n\n" + " ".join(yt.get("hashtags", [])), "```", "",
            "**Pinned comment** (post it yourself after uploading, then pin it)", "```", yt.get("pinned_comment", ""), "```", "",
            "## Instagram Reel", "", "Upload `short.mp4` as a Reel; `cover.png` as the cover.", "", "**Caption**", "```",
            ig["caption"] + "\n\n" + " ".join(ig.get("hashtags", [])), "```", "",
            "**Alt text** (Advanced settings → Accessibility)", "```", ig.get("alt_text", ""), "```", "",
            f"**Carousel** (optional second post): {', '.join(p.name for p in slides)}", "",
            "## LinkedIn", "", "Post the video, or the carousel slides as a document/images.", "", "```",
            li["post"] + "\n\n" + " ".join(li.get("hashtags", [])), "```", ""]
    return "\n".join(out)


def make(script_path: Path, cfg: dict, claude=None) -> Path:
    """Write the whole pack for one Short; returns the posts/ folder."""
    from .llm import make_claude

    folder = script_path.parent
    script = json.loads(script_path.read_text())
    fc_raw = json.loads((folder / "factcheck.json").read_text()) if (folder / "factcheck.json").exists() else {}
    fc = fc_raw.get("factcheck", fc_raw)
    posts = None
    for brain in ([claude] if claude else []) + ["default", "subscription"]:
        try:
            posts = write(brain if not isinstance(brain, str) else make_claude(cfg, None if brain == "default" else brain), cfg, script, fc)
            break
        except Exception as e:  # noqa: BLE001 - e.g. API credits ran out: try the subscription
            log(f"Couldn't write social posts ({str(e)[:80]}); trying another way")
    if posts is None:
        raise RuntimeError("Couldn't write the social posts with either the API or the subscription.")
    out = folder / "posts"
    out.mkdir(exist_ok=True)
    slides = []
    voice_p = folder / "voice" / "timings.json"
    if voice_p.exists():
        slides = carousel(cfg, script, json.loads(voice_p.read_text()), out)
    (out / "posts.json").write_text(json.dumps(posts, indent=2, ensure_ascii=False))
    (out / "posts.md").write_text(markdown(posts, slides), encoding="utf-8")
    log(f"Social pack ready: {out / 'posts.md'} (+ {len(slides)} carousel slides)")
    return out


if __name__ == "__main__":
    from .utils import load_config, load_dotenv

    load_dotenv()
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m agent.social outputs/<date>/<topic>/script.json")
    make(Path(sys.argv[1]), load_config())
