"""Stage 1: find today's AI stories and extract checkable claims."""
from __future__ import annotations

import datetime as dt

from . import prompts
from .llm import Claude
from .utils import load_history, log


def score(story: dict) -> float:
    s = story.get("scores", {})
    # hook matters most for Shorts, but a weak-sourced story is useless
    return 0.45 * s.get("hook", 0) + 0.35 * s.get("significance", 0) + 0.20 * s.get("verifiability", 0)


def run(claude: Claude, cfg: dict, today: dt.date) -> dict:
    rc = cfg["research"]
    since = dt.datetime.combine(today, dt.time()) - dt.timedelta(hours=rc["window_hours"] - 24)
    history = load_history(rc["avoid_repeat_days"])
    history_txt = "\n".join(f"- {h['date']}: {h['title']}" for h in history) or "- (none)"

    user = prompts.RESEARCH_USER.format(
        today=today.isoformat(),
        n=rc["candidate_stories"],
        hours=rc["window_hours"],
        since=since.strftime("%Y-%m-%d %H:%M"),
        history=history_txt,
    )
    log(f"Researching AI news (last {rc['window_hours']}h, up to {rc['max_searches']} searches)...")
    data, res = claude.ask_json(
        cfg["models"]["research"],
        prompts.RESEARCH_SYSTEM,
        user,
        max_searches=rc["max_searches"],
        blocked_domains=rc.get("blocked_domains") or None,
    )
    stories = data.get("stories", [])
    for st in stories:
        st["rank_score"] = round(score(st), 2)
    stories.sort(key=lambda s: s["rank_score"], reverse=True)
    log(f"Found {len(stories)} stories; top: {stories[0]['title'] if stories else '-'}")
    return {
        "date": today.isoformat(),
        "stories": stories,
        "search_queries": res.queries,
        "pages_seen": res.result_urls,
    }
