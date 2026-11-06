"""Stage 2: independently verify the top stories' claims.

Each story is checked in a fresh conversation that never sees the researcher's
reasoning - only the claims and the researcher's sources (as leads). A story is
used only if the fact-checker marks it publishable AND the code-level rules
below agree.
"""
from __future__ import annotations

import datetime as dt
import json

from . import prompts
from .llm import Claude
from .utils import log

USABLE = {"verified", "corrected"}


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url or "").netloc.lower().removeprefix("www.")


def _base(host: str) -> str:
    """news.example.co.uk -> example.co.uk, blogs.nvidia.com -> nvidia.com"""
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "gov", "ac", "ne", "or") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _independent_domains(evidence: list[dict]) -> int:
    return len({_base(_host(e.get("url", ""))) for e in evidence or [] if _host(e.get("url", ""))})


def source_tier(url: str, about: str, tiers: dict) -> str:
    """P = primary (the source of the news itself), M = major outlet, o = other, x = blocked.

    Primary: government/court/regulator sites, filings, papers, configured primaries, a
    person's own post on X when the claim is about that post, and a company's own site
    when the claim or story names that company (conductor.build for "Conductor")."""
    host = _host(url)
    if not host:
        return "o"
    base = _base(host)
    if any(a in host for a in tiers.get("blocked", [])):
        return "x"
    if base in tiers.get("major", []) or host in tiers.get("major", []):
        return "M"
    if base in tiers.get("not_primary", []):
        return "o"
    if (host in tiers.get("primary", []) or base in tiers.get("primary", [])
            or host.endswith((".gov", ".gov.uk", ".gov.in", ".europa.eu")) or base == "europa.eu"):
        return "P"
    text = about.lower()
    if base in ("x.com", "twitter.com"):
        return "P" if (" on x" in text or "posted" in text or "tweet" in text) else "o"
    name = base.split(".")[0]
    squashed = "".join(ch for ch in text if ch.isalnum())
    if len(name) >= 4 and name in squashed:  # the organisation's own site
        return "P"
    return "o"


def evidence_ok(tiers_found: list[tuple[str, str]]) -> bool:
    """The balanced rule. Pass if ANY of:
      1. a primary source + one independent site (claims stay attributed: "OpenAI says")
      2. two different major outlets
      3. one major outlet + one other independent site
    Only 'other' sites, however many, is not enough."""
    by = {"P": set(), "M": set(), "o": set()}
    for t, base in tiers_found:
        if t in by:
            by[t].add(base)
    every = by["P"] | by["M"] | by["o"]
    return bool(
        (by["P"] and len(every - by["P"]) >= 1)
        or len(by["M"]) >= 2
        or (by["M"] and len(every - by["M"]) >= 1)
    )


def enforce_rules(fc: dict, cfg: dict | None = None, story: dict | None = None) -> dict:
    """Don't take the model's word for 'publishable' - re-check in code."""
    tiers = (cfg or {}).get("research", {}).get("source_tiers")
    agg = (cfg or {}).get("research", {}).get("aggregator_domains", [])
    usable = []
    for c in fc.get("claims", []):
        if tiers:
            about = f"{c.get('final_wording', '')} {(story or {}).get('title', '')} {fc.get('title', '')}"
            found = []
            for e in c.get("evidence", []) or []:
                e["tier"] = source_tier(e.get("url", ""), about, {**tiers, "blocked": agg})
                found.append((e["tier"], _base(_host(e.get("url", "")))))
            c["source_tiers"] = sorted({t for t, _ in found})
            ok = c.get("verdict") in USABLE and evidence_ok(found)
            why = "needs a primary source or a major outlet, plus one more independent site"
        else:
            ok = c.get("verdict") in USABLE and _independent_domains(c.get("evidence")) >= 2
            why = "fewer than 2 independent source domains"
        if c.get("verdict") in USABLE and not ok:
            c["verdict"] = "unverified"
            c["note"] = (c.get("note", "") + f" [downgraded: {why}]").strip()
        if ok:
            usable.append(c)
    # the headline is the story's first key claim, matched by id: don't trust the reply's order
    claims = fc.get("claims") or []
    head_id = ((story or {}).get("key_claims") or [{}])[0].get("id")
    headline = next((c for c in claims if head_id and c.get("id") == head_id), claims[0] if claims else {})
    fc["usable_claims"] = usable
    fc["publishable"] = bool(
        fc.get("overall") == "publishable"
        and fc.get("event_in_window", False)
        and headline.get("verdict") in USABLE
        and len(usable) >= 3
    )
    return fc


def check_story(claude: Claude, cfg: dict, story: dict, today: dt.date) -> dict:
    claims_txt = "\n".join(f"- {c['id']}: {c['claim']}" for c in story["key_claims"])
    user = prompts.FACTCHECK_USER.format(
        today=today.isoformat(),
        title=story["title"],
        event_date=story.get("event_date", "?"),
        summary=story.get("summary", ""),
        sources=json.dumps([s.get("url") for s in story.get("sources", [])]),
        claims=claims_txt,
        story_id=story["id"],
    )
    log(f"Fact-checking: {story['title']}")
    rc = cfg["research"]
    # the checker may not lean on digests or encyclopedias: originals and real outlets only
    blocked = sorted(set(rc.get("blocked_domains", []) + rc.get("aggregator_domains", []) + rc.get("factcheck_blocked_domains", [])))
    data, res = claude.ask_json(
        cfg["models"]["factcheck"],
        prompts.FACTCHECK_SYSTEM,
        user,
        max_searches=rc["fact_check_max_searches"],
        blocked_domains=blocked or None,
        max_fetches=rc.get("fact_check_max_fetches", 4),
    )
    data["search_queries"] = res.queries
    data["pages_read"] = res.fetched
    data["title"] = story["title"]
    data = enforce_rules(data, cfg, story)
    v = sum(1 for c in data.get("claims", []) if c.get("verdict") in USABLE)
    log(f"  -> {v}/{len(data.get('claims', []))} claims usable, publishable={data['publishable']} "
        f"({len(res.queries)} searches, {len(res.fetched)} pages read)")
    return data


def run(claude: Claude, cfg: dict, research: dict, today: dt.date) -> dict:
    results = []
    chosen = None
    for story in research["stories"][: cfg["research"]["stories_to_factcheck"]]:
        fc = check_story(claude, cfg, story, today)
        results.append(fc)
        if fc["publishable"]:
            chosen = {"story": story, "factcheck": fc}
            break  # stories are ranked; first publishable one wins
    return {"date": today.isoformat(), "checked": results, "chosen": chosen}
