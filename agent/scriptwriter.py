"""Stage 3: write the 50-60s script from verified claims only, then audit it."""
from __future__ import annotations

import json
import re
import sys
import textwrap

from . import prompts
from .llm import Claude
from .utils import log

# story arc: hook -> setup -> twist/stat beats (any mix) -> stakes -> verify -> outro
ARC_ORDER = {"hook": 0, "setup": 1, "twist": 2, "stat": 2, "example": 2, "stakes": 3, "verify": 4, "outro": 5}
HEADLINE_WORDS = {"hook": 5, "setup": 5, "twist": 5, "step": 5, "example": 5, "stakes": 5, "outro": 4}
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
    if scene.get("type") not in ("setup", "twist", "step", "example", "stakes"):
        return [f"Scene {sid}: visuals are only allowed on setup, twist, step, example and stakes scenes."]
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


# capitalised words that come before a real number, not a product name ("In 2027", "May 5")
NOT_NAMES = set("in by on at since until from to for about around over under nearly almost just only "
                "and but so the of after before between up down roughly some its it's this that "
                "january february march april may june july august september october november december "
                "jan feb mar apr jun jul aug sep sept oct nov dec".split())


def spoken_numbers(text: str) -> list[str]:
    """Number phrases a listener has to hold in their head ("twenty-three percent",
    "about five point three", "2027"). 'one' alone is skipped: it's usually 'one lawmaker'."""
    raw = re.findall(r"[A-Za-z0-9.,%$'-]+", text)
    found, cur = [], []
    for i, tok in enumerate(raw):
        w = tok.lower()
        parts = w.strip(".,").split("-")
        prev = raw[i - 1].strip(".,").lower() if i > 0 else ""
        named = (any(c.isdigit() for c in w) and i > 0 and raw[i - 1][:1].isupper() and not cur
                 and prev not in NOT_NAMES)  # "Hut 8", "GPT 5" - but not "In 2027" or "November 4"
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


EXPLAINER_ORDER = {"hook": 0, "step": 1, "example": 1, "stat": 1, "verify": 2, "outro": 3}
AI_FILLER = re.compile(r"\b(in today's fast[- ]paced world|let's dive in|dive into|delve|game[- ]changer|landscape|"
                       r"it's important to note|in conclusion|buckle up|revolutioni[sz]e|unprecedented|the future is here)\b", re.I)


# ---------------------------------------------------------------- flow
TEASE = re.compile(r"\b(here's the (catch|thing|twist|kicker)|but here's|here's why|it gets (weirder|stranger|worse|better)|"
                   r"then it got|wait\.|guess what)", re.I)
# "a, b, c and d": three or more short items in a row
_ITEM = r"[\w'’-]+(?: [\w'’-]+){0,3}"
FEATURE_LIST = re.compile(rf"\b(?:{_ITEM}, )+{_ITEM},? (?:and|or) {_ITEM}", re.I)
STOPWORDS = set("a an the and or but so to of in on at for with it its it's is are was be this that you your "
                "they them their there here what how why can will just not no yes we i our".split())
CHOPPY_WORDS, CHOPPY_SHARE = 7, 0.5


def sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x]


def check_flow(scenes: list[dict]) -> list[str]:
    """Code-level signs that a script reads as cuts and pieces instead of one story."""
    problems = []
    body = [s for s in scenes if s.get("type") != "outro"]
    sents = [x for s in body for x in sentences(s.get("voiceover", ""))]
    short = [x for x in sents if len(x.split()) < CHOPPY_WORDS]
    if len(sents) >= 6 and len(short) / len(sents) > CHOPPY_SHARE:
        problems.append(f"{len(short)} of {len(sents)} sentences are under {CHOPPY_WORDS} words, so it sounds choppy. "
                        "Join related ideas into flowing sentences (8-16 words) and keep short ones for punch.")
    # the same phrase in three or more scenes = repeating the point
    seen: dict[str, set] = {}
    for s in body:
        words = re.findall(r"[a-z0-9'’-]+", s.get("voiceover", "").lower())
        for i in range(len(words) - 2):
            gram = words[i:i + 3]
            if all(w in STOPWORDS for w in gram):
                continue
            seen.setdefault(" ".join(gram), set()).add(s.get("id"))
    reps = sorted(g for g, ids in seen.items() if len(ids) >= 3)
    if reps:
        problems.append(f"The phrase {reps[0]!r} comes up in {len(seen[reps[0]])} scenes; say the main point at most "
                        "twice (hook and outro) and make every other scene add something new.")
    for s in scenes:
        m = FEATURE_LIST.search(s.get("voiceover", ""))
        if m and not re.search(r"\d", m.group(0)):
            problems.append(f"Scene {s.get('id')} lists three or more things in a row ({m.group(0)[:60]!r}). "
                            "Pick the one that matters most to a normal person and explain it.")
    teases = [(i, m.group(0)) for i, s in enumerate(body) for m in TEASE.finditer(s.get("voiceover", ""))]
    if len(teases) > 2:
        problems.append(f"{len(teases)} teases ({', '.join(t for _, t in teases)}); keep it to 2 at most.")
    for a, b in zip(body, body[1:]):
        end = sentences(a.get("voiceover", ""))
        if end and TEASE.search(end[-1]) and b.get("type") == "example":
            problems.append(f"Scene {a.get('id')} ends on {end[-1]!r} but the next scene is the comparison, not the "
                            "payoff. Deliver the catch right after teasing it.")
    if body:
        last = sentences(body[-1].get("voiceover", ""))
        if last and TEASE.search(last[-1]):
            problems.append(f"Scene {body[-1].get('id')} ends on a tease ({last[-1]!r}) with no payoff after it.")
    return problems


def joined(script: dict) -> str:
    return " ".join(s.get("voiceover", "").strip() for s in script.get("scenes", []))


def check_structure(types: list[str], pattern: str) -> list[str]:
    """Each pattern has its own scene types and order."""
    problems = []
    if pattern == "explainer":
        unknown = sorted({t for t in types if t not in EXPLAINER_ORDER})
        if unknown:
            problems.append(f"Unknown scene types {unknown} for the explainer; use {list(EXPLAINER_ORDER)}.")
        if not 2 <= types.count("step") <= 4:
            problems.append(f"Need 2-4 'step' scenes, found {types.count('step')}.")
        for t in ("example", "verify"):
            if types.count(t) != 1:
                problems.append(f"Need exactly one '{t}' scene, found {types.count(t)}.")
        if types.count("stat") > 1:
            problems.append(f"At most 1 'stat' scene in the explainer, found {types.count('stat')}.")
        ranks = [EXPLAINER_ORDER[t] for t in types if t in EXPLAINER_ORDER]
        if ranks != sorted(ranks):
            problems.append("Scenes out of explainer order: hook (question), steps/example/stat, verify, outro.")
        return problems
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
    ranks = [ARC_ORDER[t] for t in types if t in ARC_ORDER]
    if ranks != sorted(ranks):
        problems.append("Scenes out of story-arc order: hook, setup, twist/stat/example, stakes, verify, outro.")
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
    pattern = script.get("pattern", "story")
    problems += check_structure(types, pattern)
    problems += check_delivery(scenes)
    problems += check_speakability(scenes, cfg["script"].get("max_spoken_numbers", 3))
    problems += check_flow(scenes)
    for s in scenes:
        if s.get("type") == "verify" and not 2 <= len((s.get("on_screen") or {}).get("bullets") or []) <= 3:
            problems.append(f"Scene {s.get('id')}: the reality check needs 2-3 bullets (the screen shows at most 3).")
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
        fill = AI_FILLER.search(s.get("voiceover", ""))
        if fill:
            problems.append(f"Scene {s.get('id')} uses AI-sounding filler {fill.group(0)!r}; say it like a person would.")
        limit = HEADLINE_WORDS.get(s.get("type"))
        if s.get("type") == "hook" and script.get("pattern") == "explainer":
            limit = 6
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
    pattern = sc.get("pattern", "story") if sc.get("pattern", "story") in prompts.PATTERNS else "story"

    ctx = {"usable_ids": usable_ids, "claims": claims_txt, "caveats": caveats_txt, "pattern": pattern, "model": model}

    log(f"Drafting the story as one paragraph ({pattern} pattern)...")
    draft, _ = claude.ask_json(
        model, prompts.SCRIPT_SYSTEM,
        prompts.DRAFT_USER.format(words_min=sc["words_min"], words_max=sc["words_max"], seconds=sc["target_seconds"],
                                  title=story["title"], claims=claims_txt, caveats=caveats_txt,
                                  shape=prompts.PATTERNS[pattern]["shape"]),
        search=False,
    )
    paragraph = (draft.get("paragraph") or "").strip()
    n = len(paragraph.split())
    if paragraph and not sc["words_min"] <= n <= sc["words_max"]:
        log(f"Draft is {n} words; fitting it to {sc['words_min']}-{sc['words_max']}...")
        fixed, _ = claude.ask_json(
            model, prompts.SCRIPT_SYSTEM,
            f"This paragraph is {n} words. Rewrite it to {sc['words_min']}-{sc['words_max']} words, keeping it one "
            f"flowing story with the same facts (no new ones). Cut the least important detail first.\n\n{paragraph}\n\n"
            'Return one ```json block: {"paragraph": "..."}',
            search=False,
        )
        paragraph = (fixed.get("paragraph") or paragraph).strip()
    user = prompts.SCRIPT_USER.format(
        words_min=sc["words_min"],
        words_max=sc["words_max"],
        seconds=sc["target_seconds"],
        title=story["title"],
        claims=claims_txt,
        caveats=caveats_txt,
        pattern=prompts.PATTERNS[pattern]["rules"],
        draft=paragraph or "(no draft: write the scenes directly, as one flowing story)",
    )
    log("Splitting it into scenes...")
    script, _ = claude.ask_json(model, prompts.SCRIPT_SYSTEM, user, search=False)
    script["pattern"] = pattern
    script = polish(claude, cfg, script, ctx)
    if sys.stdin.isatty() and sc.get("approve", True):
        script = approve(claude, cfg, script, ctx)

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
    attach_scene_sources(script, usable, cfg)
    for sc in script.get("scenes", []):  # the template already shows "STEP n": don't repeat it
        os_ = sc.get("on_screen") or {}
        if sc.get("type") == "step" and os_.get("headline"):
            os_["headline"] = re.sub(r"^\s*step\s*\d+\s*[:.\-–]\s*", "", os_["headline"], flags=re.I)
    script["claims"] = usable
    return script


def listen(claude: Claude, cfg: dict, script: dict) -> list[dict]:
    """The read-aloud review: a second model hears the joined voiceover and says where it
    stops sounding like one person telling one story."""
    text = "\n".join(f"[{s['id']}] {s.get('voiceover', '')}" for s in script.get("scenes", []))
    try:
        out, _ = claude.ask_json(cfg["models"]["factcheck"], prompts.FLOW_SYSTEM, prompts.FLOW_USER.format(text=text),
                                 search=False)
    except Exception as e:  # noqa: BLE001 - the review helps, but the facts audit is what's required
        log(f"Read-aloud review skipped ({e})")
        return []
    if out.get("ok") and not out.get("problems"):
        return []
    return [{**x, "problem": "Flow: " + str(x.get("problem", ""))} for x in out.get("problems", []) if isinstance(x, dict)]


def audit_facts(claude: Claude, cfg: dict, script: dict, ctx: dict) -> list[dict]:
    audit, _ = claude.ask_json(
        cfg["models"]["factcheck"],
        prompts.AUDIT_SYSTEM,
        prompts.AUDIT_USER.format(
            claims=ctx["claims"],
            caveats=ctx["caveats"],
            titles=json.dumps({"title": script.get("title"), "youtube_title": script.get("youtube_title")}),
            scenes=json.dumps(
                [{"id": s["id"], "voiceover": s["voiceover"], "on_screen": s.get("on_screen")} for s in script["scenes"]],
                indent=1,
            ),
        ),
        search=False,
    )
    return [x for x in audit.get("issues", []) if isinstance(x, dict)]


def polish(claude: Claude, cfg: dict, script: dict, ctx: dict, seed: list[dict] | None = None, rounds: int = 3) -> dict:
    """Code checks + facts audit + read-aloud review, revising the whole script until it passes."""
    pattern, facts = ctx["pattern"], ctx["claims"] + "\n" + ctx["caveats"]
    seed = list(seed or [])
    for round_ in range(rounds):
        issues = seed + [{"problem": p} for p in validate(script, ctx["usable_ids"], cfg, facts)]
        seed = []  # a producer's note is applied once
        issues += audit_facts(claude, cfg, script, ctx)
        if round_ < rounds - 1:  # the last revision is about facts only, so it can be re-audited cleanly
            issues += listen(claude, cfg, script)
        if not issues:
            log(f"Script passed audit and read-aloud review ({word_count(script)} words).")
            return script
        log(f"Audit round {round_ + 1}: {len(issues)} issue(s); revising")
        script, _ = claude.ask_json(
            ctx["model"],
            prompts.SCRIPT_SYSTEM,
            prompts.REVISE_USER.format(issues=json.dumps(issues, indent=1), script=json.dumps(script, indent=1))
            + f"\n\nLength: the voiceover is {word_count(script)} words now and must end up "
            f"{cfg['script']['words_min']}-{cfg['script']['words_max']} words. Every fix that adds words must cut "
            "the same number elsewhere: drop the least important detail, not the explanation."
            + "\n\nThe pattern to follow:\n" + prompts.PATTERNS[pattern]["rules"],
            search=False,
        )
        script["pattern"] = pattern
    remaining = validate(script, ctx["usable_ids"], cfg, facts)
    if any("unverified" in p or "not in the fact-checked" in p for p in remaining):
        raise RuntimeError(f"Script still uses unverified claims after revisions: {remaining}")
    # the last revision was never audited: check its facts once more before anyone hears it
    left = audit_facts(claude, cfg, script, ctx)
    if left:
        log(f"Last revision has {len(left)} fact issue(s); one more facts-only fix")
        script, _ = claude.ask_json(
            ctx["model"], prompts.SCRIPT_SYSTEM,
            prompts.REVISE_USER.format(issues=json.dumps(left, indent=1), script=json.dumps(script, indent=1))
            + "\n\nFix only these fact issues. Change as few words as possible; keep the length and the flow."
            + "\n\nThe pattern to follow:\n" + prompts.PATTERNS[pattern]["rules"],
            search=False,
        )
        script["pattern"] = pattern
        left = audit_facts(claude, cfg, script, ctx)
        remaining = validate(script, ctx["usable_ids"], cfg, facts)
        if any("unverified" in p or "not in the fact-checked" in p for p in remaining):
            raise RuntimeError(f"Script still uses unverified claims after revisions: {remaining}")
    script["audit_warnings"] = [f"Scene {x.get('scene_id')}: {x.get('problem')}" for x in left]
    for w in script["audit_warnings"]:
        log(f"⚠ Still flagged by the fact audit - check before recording: {w}")
    log(f"Proceeding with minor issues: {remaining}")
    return script


def approve(claude: Claude, cfg: dict, script: dict, ctx: dict) -> dict:
    """Show the whole voiceover as one paragraph, the way it will be heard, before recording.
    Enter approves; anything typed is a note for a rewrite ("simpler", "explain the weights")."""
    while True:
        print("\n── The script, as it will be heard " + "─" * 30)
        print(textwrap.fill(joined(script), 88))
        print(f"   ({word_count(script)} words)")
        for w in script.get("audit_warnings", []):
            print(f"   ⚠ Fact audit: {w}")
        try:
            note = input("Enter = approve · or type what to change (e.g. simpler, less dramatic): ").strip()
        except EOFError:
            return script
        if not note:
            return script
        log(f"Rewriting with your note: {note}")
        script = polish(claude, cfg, script, ctx, seed=[{"problem": f"Producer's note - apply it to the whole script: {note}"}])


def _outlet(name: str) -> str:
    """'Reuters via Yahoo Finance' -> 'Reuters'; 'SEC EDGAR (Apple 10-K ...)' -> 'SEC EDGAR'."""
    name = re.split(r"\s+(?:via|carried by|in a report)\b", name, flags=re.I)[0]
    return re.sub(r"\s*\(.*?\)\s*", " ", name).strip(" ,;-")


def attach_scene_sources(script: dict, usable: list[dict], cfg: dict) -> None:
    """scene["source"] = the outlets behind THAT scene's facts, best first (primary, then
    major outlets), max 2, aggregators never. Shown on screen so viewers see where each
    fact comes from, not just one sources line at the end."""
    agg = cfg["research"].get("aggregator_domains", [])
    by_id = {c["id"]: c for c in usable}
    rank = {"P": 0, "M": 1}
    for sc in script.get("scenes", []):
        names = []
        for cid in sc.get("claim_ids") or []:
            for e in sorted((by_id.get(cid) or {}).get("evidence", []), key=lambda e: rank.get(e.get("tier"), 2)):
                if any(a in e.get("url", "") for a in agg) or e.get("tier") not in ("P", "M"):
                    continue
                n = _outlet(e.get("publisher") or "")
                if n and n not in names:
                    names.append(n)
        if names and sc.get("type") != "outro":
            sc["source"] = " · ".join(names[:2])
        else:
            sc.pop("source", None)


def ask_pattern(cfg: dict, category: str = "") -> str:
    """Ask which script pattern to use, with one recommended for the story type. Enter
    keeps script.pattern from config; scheduled runs use it without asking."""
    import sys

    default = cfg["script"].get("pattern", "story")
    if not sys.stdin.isatty():
        return default
    rec = prompts.RECOMMENDED_PATTERN.get(category or "")
    names = list(prompts.PATTERNS)
    print("\nWhich script pattern?")
    for i, n in enumerate(names, 1):
        tags = [t for t, on in (("default", n == default), (f"recommended for {category} stories", n == rec)) if on]
        print(f"  {i}. {prompts.PATTERNS[n]['name']}" + (f"   ({', '.join(tags)})" if tags else ""))
    while True:
        try:
            ans = input(f"Pick 1-{len(names)} · Enter = {rec or default}: ").strip().lower()
        except EOFError:
            return rec or default
        if not ans:
            return rec or default
        if ans.isdigit() and 1 <= int(ans) <= len(names):
            return names[int(ans) - 1]
        if ans in prompts.PATTERNS:
            return ans
        print("  Type a number from the list, or press Enter.")
