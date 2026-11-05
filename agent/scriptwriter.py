"""Stage 3: write the 50-60s script from verified claims only, then audit it."""
from __future__ import annotations

import json
import re

from . import prompts
from .llm import Claude
from .utils import log

# story arc: hook -> setup -> twist/stat beats (any mix) -> stakes -> verify -> outro
ARC_ORDER = {"hook": 0, "setup": 1, "twist": 2, "stat": 2, "example": 2, "stakes": 3, "verify": 4, "outro": 5}
HEADLINE_WORDS = {"hook": 5, "setup": 5, "twist": 5, "example": 5, "stakes": 5, "outro": 4}
HOOK_MAX_WORDS = 12  # first spoken sentence, about 2.5 s
CLICKBAIT = re.compile(r"\b(shocking|insane|unbelievable|you won'?t believe|mind[- ]blowing|game[- ]changer)\b", re.I)


def word_count(script: dict) -> int:
    return sum(len(s.get("voiceover", "").split()) for s in script.get("scenes", []))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\"'“”‘’]", "", text.replace(",", ""))).lower()


def check_visual(scene: dict, facts: str) -> list[str]:
    """Every number, name and quote in an animated graphic must be in the verified facts."""
    v = (scene.get("on_screen") or {}).get("visual")
    if not v:
        return []
    sid, f = scene.get("id"), _norm(facts)
    if scene.get("type") not in ("setup", "twist", "example", "stakes"):
        return [f"Scene {sid}: visuals are only allowed on setup, twist, example and stakes scenes."]
    kind = v.get("kind") if isinstance(v, dict) else None
    if kind not in ("bars", "grid", "diff", "whois", "flow", "icons"):
        return [f"Scene {sid}: unknown visual kind {kind!r}."]
    if kind == "icons":
        from .template_tutorial import ICONS

        bad = [i.get("icon") for i in v.get("items", []) if i.get("icon") not in ICONS]
        if bad:
            return [f"Scene {sid}: unknown icons {bad}; use only: {', '.join(ICONS)}."]
    if kind in ("flow", "icons") and scene.get("type") != "example":
        # words in a flow/icons graphic state facts: any number in them must be verified
        texts = v.get("steps", []) if kind == "flow" else [i.get("label", "") for i in v.get("items", [])]
        f = _norm(facts)
        for n in re.findall(r"\d[\d.,]*", " ".join(map(str, texts))):
            if f and n.replace(",", "") not in f:
                return [f"Scene {sid}: number {n} in the graphic is not in the fact-checked claims."]
    if not f:
        return []  # no fact text supplied (e.g. rendering an existing script)
    nums = [r.get("value") for r in v.get("items", [])] if kind == "bars" else [v.get("percent")] if kind == "grid" else []
    problems = []
    for n in nums:
        if not isinstance(n, (int, float)):
            problems.append(f"Scene {sid}: visual value {n!r} is not a number.")
            continue
        txt = f"{n:g}"
        if not re.search(rf"(?<![\d.]){re.escape(txt)}(?![\d])", f):
            problems.append(f"Scene {sid}: visual number {txt} is not in the fact-checked claims.")
    for key in ("name", "quote") if kind == "whois" else ():
        if v.get(key) and _norm(v[key]) not in f:
            problems.append(f"Scene {sid}: visual {key} {v[key]!r} is not in the fact-checked claims.")
    return problems


def _bare(text: str) -> list[str]:
    """Words as letters and digits only, so punctuation and pause marks never count."""
    return [w for w in (re.sub(r"[^a-z0-9]", "", t.lower()) for t in text.split()) if w]


def read_words(read: str) -> list[str]:
    """The words of a marked-up 'read' line, without | pauses or *stress* marks."""
    return _bare(read.replace("|", " ").replace("*", ""))


def format_read(read: str) -> str:
    """'a | b || *c*' -> 'a / b // C' : the line as you read it aloud."""
    out = re.sub(r"\*([^*]+)\*", lambda m: m.group(1).upper(), read)
    return re.sub(r"\s*\|\|\s*", "  //  ", re.sub(r"\s*(?<!\|)\|(?!\|)\s*", " / ", out)).strip()


NUMBER_WORDS = ("two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
                "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand "
                "million billion trillion percent half quarter dozen").split()
MAX_SENTENCE_WORDS = 18


def spoken_numbers(text: str) -> list[str]:
    """Number phrases a listener has to hold in their head ("twenty-three percent",
    "about five point three", "2027"). 'one' alone is skipped: it's usually 'one lawmaker'."""
    raw = re.findall(r"[A-Za-z0-9.,%$'-]+", text)
    found, cur = [], []
    for i, tok in enumerate(raw):
        w = tok.lower()
        parts = w.strip(".,").split("-")
        named = any(c.isdigit() for c in w) and i > 0 and raw[i - 1][:1].isupper() and not cur  # "Hut 8", "GPT 5"
        is_num = not named and (any(c.isdigit() for c in w) or all(p in NUMBER_WORDS or p == "point" or (p == "one" and cur) for p in parts if p))
        if is_num:
            cur.append(w)
        else:
            if cur and cur != ["point"]:
                found.append(" ".join(cur).strip(".,"))
            cur = []
    if cur and cur != ["point"]:
        found.append(" ".join(cur).strip(".,"))
    return found


def check_speakability(scenes: list[dict], max_numbers: int = 3) -> list[str]:
    """Lines must be easy to read aloud: one breath per sentence, few numbers to hold."""
    problems = []
    for s in scenes:
        for sent in re.split(r"(?<=[.!?])\s+", s.get("voiceover", "")):
            n = len(sent.split())
            if n > MAX_SENTENCE_WORDS:
                problems.append(f"Scene {s.get('id')}: a {n}-word sentence is hard to say in one breath; "
                                f"split it (max {MAX_SENTENCE_WORDS}): {sent[:60]!r}...")
    spoken = [n for s in scenes if s.get("type") != "outro" for n in spoken_numbers(s.get("voiceover", ""))]
    if len(spoken) > max_numbers:
        problems.append(f"The voiceover says {len(spoken)} numbers ({', '.join(spoken)}); keep it to {max_numbers} "
                        "and put the rest on screen only.")
    return problems


def check_delivery(scenes: list[dict]) -> list[str]:
    """Recording cues must exist and must not change a single fact-checked word."""
    problems = []
    for i, s in enumerate(scenes):
        sid, read = s.get("id"), s.get("read", "")
        if not read:
            problems.append(f"Scene {sid} has no 'read' line with pause and stress marks.")
        elif read_words(read) != _bare(s.get("voiceover", "")):
            problems.append(f"Scene {sid} 'read' must use exactly the voiceover's words; only add | || and *stress* marks.")
        if not s.get("tone"):
            problems.append(f"Scene {sid} has no 'tone' cue.")
    if scenes and not scenes[0].get("start"):
        problems.append("Scene 1 needs a 'start' cue (how to begin the video).")
    if scenes and not scenes[-1].get("end"):
        problems.append("The last scene needs an 'end' cue (how to finish the video).")
    return problems


def validate(script: dict, usable_ids: set[str], cfg: dict, facts: str = "") -> list[str]:
    """Code-level checks that don't depend on the model's judgement."""
    problems = []
    for s in script.get("scenes", []):
        problems += check_visual(s, facts)
    scenes = script.get("scenes", [])
    if not 5 <= len(scenes) <= 9:
        problems.append(f"Script has {len(scenes)} scenes; need 6-8.")
    if scenes and scenes[0].get("type") != "hook":
        problems.append("First scene must be type 'hook'.")
    if scenes and scenes[-1].get("type") != "outro":
        problems.append("Last scene must be type 'outro'.")
    types = [s.get("type") for s in scenes]
    unknown = sorted({t for t in types if t not in ARC_ORDER})
    if unknown:
        problems.append(f"Unknown scene types {unknown}; use {list(ARC_ORDER)}.")
    for t in ("setup", "stakes", "verify"):
        if types.count(t) != 1:
            problems.append(f"Need exactly one '{t}' scene, found {types.count(t)}.")
    if not 1 <= types.count("twist") <= 2:
        problems.append(f"Need 1-2 'twist' scenes, found {types.count('twist')}.")
    if types.count("stat") > 2:
        problems.append(f"At most 2 'stat' scenes, found {types.count('stat')}.")
    if types.count("example") != 1:
        problems.append(f"Need exactly one 'example' scene (an everyday comparison), found {types.count('example')}.")
    problems += check_delivery(scenes)
    problems += check_speakability(scenes, cfg["script"].get("max_spoken_numbers", 3))
    for s in scenes:
        if s.get("type") == "verify" and not 2 <= len((s.get("on_screen") or {}).get("bullets") or []) <= 3:
            problems.append(f"Scene {s.get('id')}: the reality check needs 2-3 bullets (the screen shows at most 3).")
    ranks = [ARC_ORDER[t] for t in types if t in ARC_ORDER]
    if ranks != sorted(ranks):
        problems.append("Scenes out of story-arc order: hook, setup, twist/stat/example, stakes, verify, outro.")
    for s in scenes:
        if s.get("type") == "outro":
            continue
        ids = s.get("claim_ids") or []
        if not ids:
            problems.append(f"Scene {s.get('id')} has no claim_ids.")
        bad = [i for i in ids if i not in usable_ids]
        if bad:
            problems.append(f"Scene {s.get('id')} uses unverified claims {bad}.")
    for s in scenes:
        limit = HEADLINE_WORDS.get(s.get("type"))
        head = (s.get("on_screen") or {}).get("headline", "")
        if limit and len(head.split()) > limit:
            problems.append(f"Scene {s.get('id')} headline {head!r} is {len(head.split())} words; max {limit}.")
    hook = scenes[0].get("voiceover", "") if scenes else ""
    first = re.split(r"(?<=[.!?])\s", hook.strip(), maxsplit=1)[0]
    if len(first.split()) > HOOK_MAX_WORDS:
        problems.append(f"Hook's first sentence is {len(first.split())} words; keep it to {HOOK_MAX_WORDS} or fewer.")
    yt = script.get("youtube_title", "")
    if not 0 < len(yt) <= 70:
        problems.append(f"youtube_title is {len(yt)} characters; need 1-70.")
    for field in ("title", "youtube_title"):
        if CLICKBAIT.search(script.get(field, "")):
            problems.append(f"{field} uses clickbait wording: {script.get(field)!r}.")
    wc = word_count(script)
    sc = cfg["script"]
    if not sc["words_min"] - 10 <= wc <= sc["words_max"] + 10:
        problems.append(f"Voiceover is {wc} words; need {sc['words_min']}-{sc['words_max']}.")
    return problems


def run(claude: Claude, cfg: dict, chosen: dict) -> dict:
    story, fc = chosen["story"], chosen["factcheck"]
    usable = fc["usable_claims"]
    usable_ids = {c["id"] for c in usable}
    claims_txt = "\n".join(f"- {c['id']}: {c['final_wording']}" for c in usable)
    caveats_txt = "\n".join(f"- {c}" for c in fc.get("caveats", [])) or "- (none)"
    sc = cfg["script"]
    model = cfg["models"]["script"]

    user = prompts.SCRIPT_USER.format(
        words_min=sc["words_min"],
        words_max=sc["words_max"],
        seconds=sc["target_seconds"],
        title=story["title"],
        claims=claims_txt,
        caveats=caveats_txt,
    )
    log("Writing script...")
    script, _ = claude.ask_json(model, prompts.SCRIPT_SYSTEM, user, search=False)

    for round_ in range(3):
        issues = [{"problem": p} for p in validate(script, usable_ids, cfg, claims_txt + "\n" + caveats_txt)]
        audit, _ = claude.ask_json(
            cfg["models"]["factcheck"],
            prompts.AUDIT_SYSTEM,
            prompts.AUDIT_USER.format(
                claims=claims_txt,
                caveats=caveats_txt,
                titles=json.dumps({"title": script.get("title"), "youtube_title": script.get("youtube_title")}),
                scenes=json.dumps(
                    [{"id": s["id"], "voiceover": s["voiceover"], "on_screen": s.get("on_screen")} for s in script["scenes"]],
                    indent=1,
                ),
            ),
            search=False,
        )
        issues += audit.get("issues", [])
        if not issues:
            log(f"Script passed audit ({word_count(script)} words).")
            break
        log(f"Audit round {round_ + 1}: {len(issues)} issue(s); revising")
        script, _ = claude.ask_json(
            model,
            prompts.SCRIPT_SYSTEM,
            prompts.REVISE_USER.format(issues=json.dumps(issues, indent=1), script=json.dumps(script, indent=1)),
            search=False,
        )
    else:
        remaining = validate(script, usable_ids, cfg, claims_txt + "\n" + caveats_txt)
        if any("unverified" in p or "not in the fact-checked" in p for p in remaining):
            raise RuntimeError(f"Script still uses unverified claims after revisions: {remaining}")
        log(f"Proceeding with minor issues: {remaining}")

    # attach sources for the on-screen source line and description
    evidence = [e for c in usable for e in c.get("evidence", [])]
    seen, sources = set(), []
    for e in evidence:
        if any(a in e.get("url", "") for a in cfg["research"].get("aggregator_domains", [])):
            continue  # still counted by the fact-check, but never shown or cited as a source
        if e.get("url") and e["url"] not in seen:
            seen.add(e["url"])
            sources.append({"url": e["url"], "publisher": e.get("publisher", "")})
    script["sources"] = sources
    said = " ".join(s.get("voiceover", "") for s in script.get("scenes", [])).lower()
    script["pronounce"] = [x for x in script.get("pronounce", []) if isinstance(x, dict) and str(x.get("word", "")).lower() in said]
    script["story_title"] = story["title"]
    script["category"] = story.get("category", "")
    script["claims"] = usable
    return script
