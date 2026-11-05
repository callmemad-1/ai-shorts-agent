"""Real images for a Short, from sources that are safe for a monetized channel.

    python -m agent.images outputs/<date>/<topic>/script.json

1. Claude suggests what each scene could show (a person, a place, a thing) - one search
   per scene at most, and only where a real photo beats the scene's graphic.
2. Wikimedia Commons (official portraits, government photos) and Openverse
   (openverse.org) are searched for each idea, keeping only licences that allow
   commercial use AND changes (we crop and zoom): public domain, CC0 and CC BY.
   Share-alike (SA), no-derivatives (ND) and non-commercial (NC) licences are skipped.
3. The primary source's own preview image (og:image) is offered too, marked "press
   image - check terms": it's the original's, used for editorial commentary.
4. You approve: thumbnails open in Preview, you pick one per scene or skip. Nothing is
   used without your OK. Chosen images are saved with their credit, and the credit is
   drawn on screen and listed in the YouTube description.

Never pulls photos from news articles (Reuters, AP, Getty...): those are licensed for
money and a common cause of copyright claims.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from .utils import log

UA = "ai-shorts-agent/1.0 (personal YouTube channel tool)"
SAFE_LICENSES = "cc0,pdm,by"
NEWS_PHOTO_HOSTS = ("reuters", "apnews", "gettyimages", "bloomberg", "afp", "shutterstock", "alamy", "ap.org")

IDEAS_SYSTEM = """You pick real photos for a fact-checked YouTube Short. For each scene, decide
whether a real photo would help viewers more than the scene's text or graphic, and if so
write a short image-search query (2-5 words) for a free photo library. Good: the named
person ("Yassamin Ansari official portrait"), the real place ("US Capitol"), the real kind
of thing ("data center server racks", "Seoul stock exchange board"). Never suggest a
photo that would imply something the scene doesn't say (no random company's building
captioned as the company in the story). Skip the outro and the "verify" (reality check)
scene. Scenes that already have a graphic can still get a photo: it's shown as a soft
backdrop behind the graphic, so pick a scene-setting photo (a place, a building, a
trading floor) rather than a close-up."""

IDEAS_USER = """Aim for real photos in about {target} of these {total} scenes.

Scenes:
{scenes}

Return one ```json block:
{{"ideas": [{{"scene_id": 1, "query": "...", "why": "one short reason"}}]}}
"""


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def openverse(query: str, n: int = 4) -> list[dict]:
    """Safe-licence photos for a query, landscape-ish and big enough for 1080p."""
    q = urllib.parse.urlencode({"q": query, "license": SAFE_LICENSES, "page_size": 12, "mature": "false"})
    try:
        data = json.loads(_get(f"https://api.openverse.org/v1/images/?{q}"))
    except Exception as e:  # noqa: BLE001 - no images is fine
        log(f"Openverse search failed for {query!r}: {e}")
        return []
    out = []
    for r in data.get("results", []):
        if (r.get("width") or 0) < 640 or any(h in (r.get("url") or "") for h in NEWS_PHOTO_HOSTS):
            continue
        lic = f"{'CC ' if r['license'] not in ('pdm', 'cc0') else ''}{r['license'].upper()} {r.get('license_version') or ''}".strip()
        lic = {"PDM": "Public domain", "CC0 1.0": "CC0 (public domain)"}.get(lic, lic)
        out.append({"url": r["url"], "thumb": r.get("thumbnail") or r["url"], "title": r.get("title") or "",
                    "creator": r.get("creator") or "unknown", "license": lic, "license_url": r.get("license_url", ""),
                    "page": r.get("foreign_landing_url") or r.get("detail_url", ""), "source": "openverse",
                    "credit": f"{r.get('creator') or 'Unknown'} / {lic}"})
        if len(out) >= n:
            break
    return out


SAFE_COMMONS = re.compile(r"^(public domain|pd|cc0|cc by \d|cc-by-\d|attribution)", re.I)


def commons(query: str, n: int = 3) -> list[dict]:
    """Wikimedia Commons: official portraits and government photos (often public domain).
    Only public domain, CC0 and CC BY files; share-alike, ND and NC are skipped."""
    q = urllib.parse.urlencode({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                                "gsrsearch": f"filetype:bitmap {query}", "gsrlimit": 12, "prop": "imageinfo",
                                "iiprop": "url|size|extmetadata", "iiurlwidth": 1280})
    try:
        pages = json.loads(_get(f"https://commons.wikimedia.org/w/api.php?{q}")).get("query", {}).get("pages", {})
    except Exception as e:  # noqa: BLE001
        log(f"Wikimedia Commons search failed for {query!r}: {e}")
        return []
    out = []
    for pg in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        info = (pg.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if not SAFE_COMMONS.match(lic) or re.search(r"\bSA\b|NC|ND", lic) or (info.get("width") or 0) < 400:
            continue
        artist = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip() or "Wikimedia Commons"
        out.append({"url": info.get("thumburl") or info.get("url"), "thumb": info.get("thumburl") or info.get("url"),
                    "title": pg.get("title", "").removeprefix("File:"), "creator": artist[:60], "license": lic,
                    "license_url": (meta.get("LicenseUrl") or {}).get("value", ""), "page": info.get("descriptionurl", ""),
                    "source": "wikimedia", "credit": f"{artist[:40]} / {lic}"})
        if len(out) >= n:
            break
    return out


def og_image(page_url: str) -> dict | None:
    """The primary source's own preview image (editorial use; you check the terms)."""
    try:
        html = _get(page_url, timeout=15).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html) or \
        re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', html)
    if not m:
        return None
    host = urllib.parse.urlparse(page_url).netloc.removeprefix("www.")
    return {"url": urllib.parse.urljoin(page_url, m.group(1)), "thumb": urllib.parse.urljoin(page_url, m.group(1)),
            "title": f"Preview image from {host}", "creator": host, "license": "press image - check terms",
            "license_url": "", "page": page_url, "source": "primary", "credit": f"Image: {host}"}


def primary_pages(script: dict, cfg: dict) -> list[str]:
    """Evidence pages the source tiers marked primary (the company's own post etc.)."""
    seen, out = set(), []
    for c in script.get("claims", []):
        for e in c.get("evidence", []):
            if e.get("tier") == "P" and e.get("url") and e["url"] not in seen and "x.com" not in e["url"]:
                seen.add(e["url"]); out.append(e["url"])
    return out[:2]


def suggest(claude, cfg: dict, script: dict) -> list[dict]:
    scenes = [{"id": s["id"], "type": s["type"], "voiceover": s["voiceover"],
               "has_graphic": bool((s.get("on_screen") or {}).get("visual"))} for s in script["scenes"] if s["type"] != "outro"]
    total = len(script["scenes"])
    target = max(1, round(cfg.get("images", {}).get("target_share", 0.6) * total))
    data, _ = claude.ask_json(cfg["models"]["script"], IDEAS_SYSTEM,
                              IDEAS_USER.format(target=target, total=total, scenes=json.dumps(scenes, indent=1)), search=False)
    ok = {s["id"] for s in script["scenes"] if s["type"] not in ("outro", "verify")}
    return [i for i in data.get("ideas", []) if i.get("query") and i.get("scene_id") in ok][: target + 1]


def _show(files: list[Path]):
    if files:
        subprocess.run(["open", "-a", "Preview", *map(str, files)])


def choose(script_path: Path, cfg: dict, claude=None, auto: bool = False) -> dict:
    """Find, show and let you pick images; saves them into script.json as scene['image']."""
    from .llm import make_claude

    script = json.loads(script_path.read_text())
    folder = script_path.parent / "images"
    folder.mkdir(exist_ok=True)
    ideas = []
    for brain in ([claude] if claude else []) + ["default", "subscription"]:
        try:
            ideas = suggest(brain if not isinstance(brain, str) else make_claude(cfg, None if brain == "default" else brain), cfg, script)
            break
        except Exception as e:  # noqa: BLE001 - e.g. API credits ran out: try the next brain
            log(f"Couldn't get photo ideas ({str(e)[:80]}); trying another way")
    if not ideas:  # last resort: the named people on profile cards, and the hook
        for sc in script["scenes"]:
            v = (sc.get("on_screen") or {}).get("visual") or {}
            if v.get("kind") == "whois" and v.get("name"):
                ideas.append({"scene_id": sc["id"], "query": f"{v['name']} official portrait", "why": "the named person"})
        hook = script["scenes"][0]
        ideas.insert(0, {"scene_id": hook["id"], "query": (hook.get("on_screen") or {}).get("headline", ""), "why": "the hook"})
    press = [x for u in primary_pages(script, cfg) if (x := og_image(u))]
    by_id = {s["id"]: s for s in script["scenes"]}
    credits = []
    for idea in ideas:
        sc = by_id.get(idea["scene_id"])
        if not sc or sc.get("image"):
            continue
        opts = commons(idea["query"]) + openverse(idea["query"]) + (press if sc["type"] in ("hook", "setup") else [])
        print(f"\n── Scene {sc['id']} · {sc['type'].upper()}: “{sc['voiceover'][:80]}”")
        print(f"   Idea: {idea['query']} ({idea.get('why', '')})")
        if not opts:
            print("   No safely licensed images found; skipping.")
            continue
        thumbs = []
        for k, o in enumerate(opts, 1):
            t = folder / f"scene{sc['id']:02d}_option{k}.jpg"
            try:
                t.write_bytes(_get(o["thumb"]))
                thumbs.append(t)
            except Exception:  # noqa: BLE001
                continue
            print(f"   {k}. {o['title'][:60]}  ·  {o['credit']}")
        if auto:
            pick = ""
        else:
            _show(thumbs)
            pick = input(f"   Pick one or more (e.g. 1 or 1,3) · Enter = skip: ").strip()
        nums = list(dict.fromkeys(int(n) for n in re.findall(r"\d+", pick) if 1 <= int(n) <= len(opts)))
        if not nums:
            continue
        chosen = []
        for k, n in enumerate(nums, 1):
            o = opts[n - 1]
            dst = folder / (f"scene{sc['id']:02d}.jpg" if k == 1 else f"scene{sc['id']:02d}_{k}.jpg")
            try:
                dst.write_bytes(_get(o["url"], timeout=40))
            except Exception as e:  # noqa: BLE001
                print(f"   Couldn't download option {n} ({e}); skipping it.")
                continue
            chosen.append({"file": str(dst.relative_to(script_path.parent)), "path": str(dst.resolve()), "credit": o["credit"],
                           "license": o["license"], "license_url": o["license_url"], "page": o["page"], "source": o["source"]})
        if not chosen:
            continue
        sc["image"], sc["images"] = chosen[0], chosen  # "image" = the first, for one-photo layouts
        credits += chosen
        print(f"   ✓ Using {len(chosen)} photo{'s' if len(chosen) > 1 else ''}"
              + (" (shown as a slideshow)" if len(chosen) > 1 else "") + ": " + "; ".join(c["credit"] for c in chosen))
    for t in folder.glob("scene*_option*.jpg"):
        t.unlink()
    have = sum(1 for x in script["scenes"] if x.get("image"))
    print(f"\nPhotos in {have} of {len(script['scenes'])} scenes ({have / len(script['scenes']):.0%}; "
          f"target {cfg.get('images', {}).get('target_share', 0.6):.0%})")
    add_credits(script)
    script_path.write_text(json.dumps(script, indent=2, ensure_ascii=False))
    fc_path = script_path.parent / "factcheck.json"
    if (script_path.parent / "script.md").exists() and fc_path.exists():
        from main import write_script_md  # keep the upload notes (credits) in step

        fc = json.loads(fc_path.read_text())
        write_script_md(script, fc.get("factcheck", fc), script_path.parent / "script.md")
    return script


def maybe_add(script_path: Path, cfg: dict, claude=None, asked: bool | None = None) -> dict:
    """The photo step inside a normal run: ask once (unless already answered), then pick.
    Skipped with no one at the keyboard, when images.enabled is false, or when the script
    already has photos."""
    script = json.loads(script_path.read_text())
    if not cfg.get("images", {}).get("enabled", True) or not sys.stdin.isatty():
        return script
    share = sum(1 for s in script["scenes"] if s.get("image")) / max(len(script["scenes"]), 1)
    if share >= cfg.get("images", {}).get("target_share", 0.6) - 0.05:
        return script
    if asked is None:
        try:
            asked = input("\nAdd real photos (free-licence; you approve each one)? [Y/n] ").strip().lower() not in ("n", "no")
        except EOFError:
            asked = False
    return choose(script_path, cfg, claude) if asked else script


def add_credits(script: dict) -> None:
    """Licences like CC BY require credit: list every image in the YouTube description."""
    desc = re.sub(r"\n\nImage credits:.*", "", script.get("description", ""), flags=re.S)
    lines = [f"- Scene {s['id']}: {im['credit']} ({im['page']})"
             for s in script["scenes"] for im in (s.get("images") or ([s["image"]] if s.get("image") else []))]
    script["description"] = desc + ("\n\nImage credits:\n" + "\n".join(lines) if lines else "")


if __name__ == "__main__":
    from .utils import load_config, load_dotenv

    load_dotenv()
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m agent.images outputs/<date>/<topic>/script.json")
    s = choose(Path(sys.argv[1]), load_config())
    print(f"\nSaved {sum(1 for x in s['scenes'] if x.get('image'))} image(s) into {sys.argv[1]}")
