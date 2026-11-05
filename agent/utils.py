"""Shared helpers: config, paths, JSON extraction, run history."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | os.PathLike | None = None) -> dict:
    path = Path(path) if path else ROOT / "config.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader so the project has no extra dependency for it."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def resolve(path: str | os.PathLike) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


STOP_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with"}


def topic_slug(title: str, max_len: int = 55) -> str:
    """'Google will let schools turn off its AI tools, but...' -> 'google-will-let-schools-turn-off-its-ai-tools'."""
    words = re.sub(r"[^a-z0-9]+", " ", title.lower().replace("'", "").replace("’", "")).split()
    out = []
    for w in words:
        if len("-".join(out + [w])) > max_len:
            break
        out.append(w)
    while len(out) > 2 and out[-1] in STOP_WORDS:
        out.pop()
    return "-".join(out) or "story"


def topic_dir(date: dt.date, title: str) -> Path:
    """Every Short lives in outputs/<date>/<topic>/."""
    d = today_dir(date) / topic_slug(title)
    d.mkdir(parents=True, exist_ok=True)
    return d


def today_dir(date: dt.date | None = None) -> Path:
    date = date or dt.date.today()
    d = ROOT / "outputs" / date.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)


def extract_json(text: str):
    """Pull the last JSON object/array out of a model reply.

    Tries fenced ```json blocks first (last one wins), then the widest
    {...} span. Raises ValueError if nothing parses.
    """
    candidates = [m.group(1) for m in _FENCE.finditer(text)][::-1]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            # tolerate trailing commas, a common model slip
            try:
                return json.loads(re.sub(r",\s*([}\]])", r"\1", c))
            except json.JSONDecodeError:
                continue
    raise ValueError("No valid JSON found in model output")


# ---- history: avoid making a Short about the same story twice ----------

HISTORY = ROOT / "outputs" / "history.json"


def load_history(days: int) -> list[dict]:
    if not HISTORY.exists():
        return []
    cutoff = dt.date.today() - dt.timedelta(days=days)
    items = read_json(HISTORY)
    return [h for h in items if dt.date.fromisoformat(h["date"]) >= cutoff]


def add_history(title: str, urls: list[str], date: dt.date | None = None) -> None:
    items = read_json(HISTORY) if HISTORY.exists() else []
    items.append({"date": (date or dt.date.today()).isoformat(), "title": title, "urls": urls[:5]})
    save_json(items, HISTORY)


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)
