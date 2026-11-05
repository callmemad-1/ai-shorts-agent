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


def _independent_domains(evidence: list[dict]) -> int:
    from urllib.parse import urlparse

    hosts = set()
    for e in evidence or []:
        host = urlparse(e.get("url", "")).netloc.lower().removeprefix("www.")
        if host:
            hosts.add(".".join(host.split(".")[-2:]))
    return len(hosts)


def enforce_rules(fc: dict) -> dict:
    """Don't take the model's word for 'publishable' - re-check in code."""
    usable = []
    for c in fc.get("claims", []):
        ok = c.get("verdict") in USABLE and _independent_domains(c.get("evidence")) >= 2
        if c.get("verdict") in USABLE and not ok:
            c["verdict"] = "unverified"
            c["note"] = (c.get("note", "") + " [downgraded: fewer than 2 independent source domains]").strip()
        if ok:
            usable.append(c)
    headline = fc.get("claims", [{}])[0] if fc.get("claims") else {}
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
    data, res = claude.ask_json(
        cfg["models"]["factcheck"],
        prompts.FACTCHECK_SYSTEM,
        user,
        max_searches=cfg["research"]["fact_check_max_searches"],
    )
    data["search_queries"] = res.queries
    data["title"] = story["title"]
    data = enforce_rules(data)
    v = sum(1 for c in data.get("claims", []) if c.get("verdict") in USABLE)
    log(f"  -> {v}/{len(data.get('claims', []))} claims usable, publishable={data['publishable']}")
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
