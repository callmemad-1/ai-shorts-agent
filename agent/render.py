"""Stage 5: render the 9:16 (1080x1920) YouTube Shorts video.

Every frame is drawn with Pillow at full size and streamed into ffmpeg, one
encoded file per scene (rendered in parallel), then the scenes are joined with
the concat demuxer (-c copy) and the voiceover (+ optional music) is added.

Layout keeps clear of the Shorts UI: nothing important in the bottom ~22% or
under the right-hand action buttons.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import remote
from .utils import ROOT, log, resolve

FONTS = ROOT / "assets" / "fonts"
F_BLACK = FONTS / "InterDisplay-Black.otf"
F_XBOLD = FONTS / "InterDisplay-ExtraBold.otf"
F_BOLD = FONTS / "InterDisplay-Bold.otf"
F_SEMI = FONTS / "Inter-SemiBold.otf"
F_MED = FONTS / "Inter-Medium.otf"

X0, X1 = 72, 960          # content column (right side kept clear of Shorts buttons)
CW = X1 - X0
CAPTION_Y = 1255          # caption centre line
SOURCES_Y = 1430
DEFAULT_KICKER = {"hook": "TODAY IN AI", "stat": "BY THE NUMBERS", "point": "WHAT HAPPENED", "setup": "WHAT HAPPENED",
                  "twist": "THE TWIST", "example": "THINK OF IT LIKE", "stakes": "WHY IT MATTERS", "verify": "REALITY CHECK"}
# story-arc beats that share a layout
LAYOUT = {"setup": "point", "twist": "point", "step": "point", "example": "point", "stakes": "point"}


def step_number(script: dict, scene: dict) -> int:
    """1-based position of a "step" scene among the script's steps (explainer pattern)."""
    steps = [s.get("id") for s in script.get("scenes", []) if s.get("type") == "step"]
    return steps.index(scene.get("id")) + 1 if scene.get("id") in steps else 1


# ------------------------------------------------------------------ helpers
def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


@lru_cache(maxsize=256)
def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def ease_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def prog(t: float, start: float, dur: float) -> float:
    return ease_out((t - start) / dur) if dur > 0 else float(t >= start)


def wrap(text: str, fnt, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        test = f"{cur} {w}".strip()
        if fnt.getlength(test) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(text: str, path: Path, max_size: int, min_size: int, max_w: int, max_lines: int):
    size = max_size
    while size > min_size:
        f = font(path, size)
        lines = wrap(text, f, max_w)
        if len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines):
            return f, lines
        size -= 4
    f = font(path, min_size)
    return f, wrap(text, f, max_w)


# Indic scripts the Latin fonts don't have: (unicode range, font file, face index for bold)
SCRIPT_FONTS = [
    (re.compile(r"[\u0B80-\u0BFF]"), Path("/System/Library/Fonts/Supplemental/Tamil Sangam MN.ttc"), 2),
]


def script_font(line: str, fnt):
    """Same size, in a font that has the line's letters (Tamil captions in a Tamil Short).
    Needs Pillow's raqm layout (brew install fribidi) so vowel signs land in the right place."""
    for pattern, path, index in SCRIPT_FONTS:
        if pattern.search(line) and path.exists():
            return ImageFont.truetype(str(path), int(fnt.size), index=index)
    return fnt


def text_img(line: str, fnt, fill, stroke=0, stroke_fill=(0, 0, 0)) -> Image.Image:
    fnt = script_font(line, fnt)
    l, t, r, b = fnt.getbbox(line, stroke_width=stroke)
    asc, desc = fnt.getmetrics()
    w, h = int(r - min(l, 0)) + 4, asc + desc + stroke * 2 + 4
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((-min(l, 0) + 2, stroke + 2), line, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return im


def gradient_text(line: str, fnt, c1, c2) -> Image.Image:
    mask = text_img(line, fnt, (255, 255, 255, 255)).getchannel("A")
    w, h = mask.size
    grad = Image.new("RGBA", (w, h))
    top, bot = np.array(c1, float), np.array(c2, float)
    ys = np.linspace(0, 1, h)[:, None]
    arr = (top * (1 - ys) + bot * ys).astype(np.uint8)
    grad = Image.fromarray(np.repeat(arr[:, None, :], w, axis=1).reshape(h, w, 3), "RGB").convert("RGBA")
    grad.putalpha(mask)
    return grad


def with_alpha(im: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return im
    out = im.copy()
    out.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
    return out


def put(frame: Image.Image, im: Image.Image, x: float, y: float, a: float = 1.0):
    if a <= 0.01:
        return
    frame.alpha_composite(with_alpha(im, a), (int(x), int(y)))


def glow_sprite(rgb, radius: int, strength: int) -> Image.Image:
    size = radius * 3
    im = Image.new("RGBA", (size, size), (*rgb, 0))
    ImageDraw.Draw(im).ellipse([radius, radius, radius * 2, radius * 2], fill=(*rgb, strength))
    return im.filter(ImageFilter.GaussianBlur(radius * 0.45))


def numeric_parts(stat: str):
    """'$1,279' -> ('$', 1279.0, '', 0 decimals, True)  - for the count-up."""
    m = re.fullmatch(r"([$€£₹]?)(\d[\d,]*(?:\.\d+)?)([A-Za-z%+×x]*)", stat.strip())
    if not m:
        return None
    num = m.group(2)
    decimals = len(num.split(".")[1]) if "." in num else 0
    return m.group(1), float(num.replace(",", "")), m.group(3), decimals, "," in num


# ------------------------------------------------------------------ captions
def caption_chunks(words: list[dict], max_words=4, max_chars=22) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or len(" ".join(x["word"] for x in cur + [w])) > max_chars):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?]$", w["word"]):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


# ------------------------------------------------------------------ renderer
class SceneRenderer:
    def __init__(self, cfg: dict, script: dict, n_scenes: int, scene_durations: list[float]):
        self.cfg = cfg
        self.W, self.H, self.fps = cfg["video"]["width"], cfg["video"]["height"], cfg["video"]["fps"]
        c = {k: hex_rgb(v) for k, v in cfg["brand"]["colors"].items()}
        self.c = c
        self.script = script
        self.n = n_scenes
        self.durs = scene_durations
        self.bg = self._background()
        self.glows = [glow_sprite(c["glow_1"], 420, 120), glow_sprite(c["glow_2"], 340, 90)]
        self.chrome = self._chrome()
        self.sources = self._sources_line()

    # -- static layers ---------------------------------------------------
    def _background(self) -> Image.Image:
        top, bot = np.array(self.c["bg_top"], float), np.array(self.c["bg_bottom"], float)
        ys = np.linspace(0, 1, self.H)[:, None]
        col = (top * (1 - ys) + bot * ys).astype(np.uint8)
        arr = np.repeat(col[:, None, :], self.W, axis=1).reshape(self.H, self.W, 3)
        im = Image.fromarray(arr, "RGB").convert("RGBA")
        grid = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(grid)
        for x in range(0, self.W, 90):
            d.line([(x, 0), (x, self.H)], fill=(255, 255, 255, 9), width=1)
        for y in range(0, self.H, 90):
            d.line([(0, y), (self.W, y)], fill=(255, 255, 255, 9), width=1)
        im.alpha_composite(grid)
        return im

    def _chrome(self) -> Image.Image:
        layer = Image.new("RGBA", (self.W, 240), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        name = self.cfg["brand"]["name"]
        f = font(F_XBOLD, 36)
        tw = f.getlength(name)
        d.rounded_rectangle([X0, 92, X0 + tw + 76, 148], radius=28, fill=(*self.c["accent"], 255))
        d.ellipse([X0 + 20, 112, X0 + 36, 128], fill=(*self.c["bg_bottom"], 255))
        d.text((X0 + 48, 120), name, font=f, fill=(*self.c["bg_bottom"], 255), anchor="lm")
        date = self.script.get("date_label", "")
        if date:
            d.text((X1, 120), date, font=font(F_SEMI, 32), fill=(*self.c["muted"], 255), anchor="rm")
        return layer

    def _sources_line(self):
        pubs = []
        for s in self.script.get("sources", []):
            p = (s.get("publisher") or "").strip()
            if p and p not in pubs:
                pubs.append(p)
        if not pubs:
            return None
        txt = "Sources: " + " · ".join(pubs[:3])
        f = font(F_MED, 32)
        while f.getlength(txt) > CW and len(pubs) > 1:
            pubs.pop()
            txt = "Sources: " + " · ".join(pubs)
        return text_img(txt, f, (*self.c["muted"], 230))

    # -- per-frame pieces ------------------------------------------------
    def _base(self, gt: float) -> Image.Image:
        fr = self.bg.copy()
        g1, g2 = self.glows
        fr.alpha_composite(g1, (int(-300 + 140 * math.sin(gt * 0.35)), int(80 + 120 * math.cos(gt * 0.27))))
        fr.alpha_composite(g2, (int(420 + 120 * math.cos(gt * 0.31)), int(820 + 160 * math.sin(gt * 0.23))))
        fr.alpha_composite(self.chrome, (0, 0))
        return fr

    def _progress(self, fr: Image.Image, idx: int, t: float):
        d = ImageDraw.Draw(fr)
        gap, y, h = 10, 182, 8
        seg = (CW - gap * (self.n - 1)) / self.n
        dim = tuple(int(a * 0.82 + b * 0.18) for a, b in zip(self.c["bg_top"], (255, 255, 255)))
        for i in range(self.n):
            x = X0 + i * (seg + gap)
            d.rounded_rectangle([x, y, x + seg, y + h], radius=4, fill=(*dim, 255))
            p = 1.0 if i < idx else (min(t / self.durs[idx], 1.0) if i == idx else 0.0)
            if p > 0:
                d.rounded_rectangle([x, y, x + max(seg * p, h), y + h], radius=4, fill=(*self.c["accent"], 255))

    @lru_cache(maxsize=512)
    def _caption_img(self, text_words: tuple, active: int) -> Image.Image:
        f = font(F_XBOLD, 72)
        space = f.getlength(" ")
        parts = [text_img(w, f, (*(self.c["accent"] if i == active else self.c["text"]), 255), stroke=7) for i, w in enumerate(text_words)]
        widths = [p.width for p in parts]
        total = sum(widths) + space * (len(parts) - 1)
        if total > CW:  # two lines if needed
            mid = max(1, len(parts) // 2)
            rows = [parts[:mid], parts[mid:]]
        else:
            rows = [parts]
        row_imgs = []
        for row in rows:
            w = int(sum(p.width for p in row) + space * (len(row) - 1))
            h = max(p.height for p in row)
            im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            x = 0
            for p in row:
                im.alpha_composite(p, (int(x), 0))
                x += p.width + space
            row_imgs.append(im)
        W = max(r.width for r in row_imgs)
        H = sum(r.height for r in row_imgs) - 10 * (len(row_imgs) - 1)
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        y = 0
        for r in row_imgs:
            out.alpha_composite(r, ((W - r.width) // 2, y))
            y += r.height - 10
        return out

    def _captions(self, fr: Image.Image, chunks, t: float):
        for ci, ch in enumerate(chunks):
            start = ch[0]["start"]
            end = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else ch[-1]["end"] + 0.4
            if start <= t < end:
                active = max([i for i, w in enumerate(ch) if w["start"] <= t] or [0])
                im = self._caption_img(tuple(w["word"] for w in ch), active)
                pop = prog(t, start, 0.12)
                y = CAPTION_Y - im.height / 2 + (1 - pop) * 18
                put(fr, im, (self.W - im.width) / 2 - 30, y, 0.4 + 0.6 * pop)
                return

    # -- scene layouts -----------------------------------------------------
    def _kicker(self, text: str):
        f = font(F_SEMI, 38)
        im = text_img(text.upper(), f, (*self.c["accent"], 255))
        out = Image.new("RGBA", (im.width + 34, im.height), (0, 0, 0, 0))
        ImageDraw.Draw(out).rectangle([0, 12, 8, im.height - 10], fill=(*self.c["accent"], 255))
        out.alpha_composite(im, (30, 0))
        return out

    def build_scene(self, scene: dict):
        """Pre-render the scene's static text so frames only composite."""
        beat = scene.get("type", "point")
        st = LAYOUT.get(beat, beat)
        os_ = scene.get("on_screen", {}) or {}
        L = {"type": st, "kicker": None, "lines": [], "sub": [], "stat": None, "bullets": []}
        kick = os_.get("label") or (self.cfg["brand"]["tagline"] if st == "outro" else
                                    f"STEP {step_number(self.script, scene)}" if beat == "step" else DEFAULT_KICKER.get(beat))
        if kick:
            L["kicker"] = self._kicker(kick)
        white = (*self.c["text"], 255)
        if st in ("hook", "point", "outro"):
            maxs = {"hook": 150, "point": 124, "outro": 132}[st]
            f, lines = fit(os_.get("headline", ""), F_BLACK, maxs, 70, CW, 4)
            L["lines"] = [text_img(l, f, white) for l in lines]
            L["line_h"] = int(f.size * 1.08)
            if os_.get("sub"):
                fs, sl = fit(os_["sub"], F_MED, 56, 40, CW, 3)
                L["sub"] = [text_img(l, fs, (*self.c["muted"], 255)) for l in sl]
                L["sub_h"] = int(fs.size * 1.3)
        elif st == "stat":
            stat = str(os_.get("stat", ""))
            f, _ = fit(stat, F_BLACK, 300, 110, CW, 1)
            L["stat_font"] = f
            L["stat"] = stat
            L["num"] = numeric_parts(stat)
            if os_.get("sub"):
                fs, sl = fit(os_["sub"], F_SEMI, 62, 42, CW, 3)
                L["sub"] = [text_img(l, fs, white) for l in sl]
                L["sub_h"] = int(fs.size * 1.3)
        elif st == "verify":
            bf = font(F_SEMI, 50)
            for b in (os_.get("bullets") or [])[:3]:
                lines = wrap(b, bf, CW - 120)
                card_h = 56 * len(lines) + 56
                card = Image.new("RGBA", (CW, card_h), (0, 0, 0, 0))
                d = ImageDraw.Draw(card)
                fill = tuple(int(a * 0.75 + b2 * 0.25) for a, b2 in zip(self.c["bg_top"], self.c["accent_2"]))
                d.rounded_rectangle([0, 0, CW - 1, card_h - 1], radius=26, fill=(*fill, 235))
                d.ellipse([34, card_h / 2 - 11, 56, card_h / 2 + 11], fill=(*self.c["verified"], 255))
                for i, ln in enumerate(lines):
                    d.text((86, 28 + i * 56), ln, font=bf, fill=white)
                L["bullets"].append(card)
            f = font(F_BLACK, 84)
            L["badge_text"] = text_img("REALITY CHECK", f, (*self.c["verified"], 255))
        return L

    def draw_scene(self, fr: Image.Image, L: dict, t: float, dur: float):
        out = 1 - prog(t, dur - 0.18, 0.18)  # fade content out at the end
        y = 400
        if L["kicker"] is not None:
            p = prog(t, 0.0, 0.3)
            put(fr, L["kicker"], X0 - (1 - p) * 40, y, p * out)
            y += L["kicker"].height + 30
        st = L["type"]

        if st in ("hook", "point", "outro"):
            for i, im in enumerate(L["lines"]):
                p = prog(t, 0.08 + i * 0.1, 0.35)
                put(fr, im, X0, y + (1 - p) * 50, p * out)
                y += L["line_h"]
            # accent bar under the headline
            p = prog(t, 0.35, 0.4)
            if p > 0:
                ImageDraw.Draw(fr, "RGBA").rounded_rectangle([X0, y + 18, X0 + 10 + 220 * p, y + 30], radius=6, fill=(*self.c["accent"], int(255 * out)))
            y += 70
            for i, im in enumerate(L["sub"]):
                p = prog(t, 0.45 + i * 0.08, 0.35)
                put(fr, im, X0, y + (1 - p) * 30, p * out)
                y += L["sub_h"]
            if st == "outro":
                self._follow_button(fr, y + 40, t, out)

        elif st == "stat":
            n = L["num"]
            p = prog(t, 0.05, 0.9)
            if n and p < 1:
                pre, val, suf, dec, comma = n
                v = val * p
                s = f"{v:,.{dec}f}" if comma else f"{v:.{dec}f}"
                txt = f"{pre}{s}{suf}"
            else:
                txt = L["stat"]
            im = self._stat_img_cached(L["stat"], txt)
            pin = prog(t, 0.0, 0.25)
            put(fr, im, X0 - 6, y - 10 + (1 - pin) * 40, pin * out)
            y += int(L["stat_font"].size * 1.15)
            for i, sim in enumerate(L["sub"]):
                pp = prog(t, 0.4 + i * 0.08, 0.35)
                put(fr, sim, X0, y + (1 - pp) * 30, pp * out)
                y += L["sub_h"]

        elif st == "verify":
            p = prog(t, 0.0, 0.35)
            self._check_badge(fr, X0, y, p, out)
            put(fr, L["badge_text"], X0 + 150, y + 14 + (1 - p) * 20, p * out)
            y += 180
            for i, card in enumerate(L["bullets"]):
                start = 0.35 + i * max(0.45, (dur - 1.2) / max(len(L["bullets"]), 1) * 0.6)
                pp = prog(t, start, 0.35)
                put(fr, card, X0 + (1 - pp) * 120, y, pp * out)
                y += card.height + 24

    @lru_cache(maxsize=1024)
    def _stat_img_cached(self, stat_key: str, txt: str):
        L = self._stat_font_for.get(stat_key)
        return gradient_text(txt, L, self.c["accent"], self.c["accent_2"])

    def _check_badge(self, fr, x, y, p, a):
        if p <= 0:
            return
        r = int(56 * (0.6 + 0.4 * p))
        cx, cy = x + 60, y + 62
        d = ImageDraw.Draw(fr, "RGBA")
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*self.c["verified"], int(255 * a)))
        if p > 0.5:
            d.line([(cx - 24, cy + 2), (cx - 6, cy + 20), (cx + 26, cy - 18)], fill=(*self.c["bg_bottom"], int(255 * a)), width=12, joint="curve")

    def _follow_button(self, fr, y, t, a):
        p = prog(t, 0.6, 0.35)
        if p <= 0:
            return
        pulse = 1 + 0.04 * math.sin(max(t - 0.95, 0) * 6)
        f = font(F_BLACK, 64)
        label = "FOLLOW"
        tw = f.getlength(label)
        w, h = int((tw + 140) * pulse), int(124 * pulse)
        d = ImageDraw.Draw(fr, "RGBA")
        x = X0
        d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=(*self.c["accent"], int(255 * a * p)))
        d.text((x + w / 2, y + h / 2), label, font=f, fill=(*self.c["bg_bottom"], int(255 * a * p)), anchor="mm")
        d.text((x + w + 36, y + h / 2), self.cfg["brand"]["handle"], font=font(F_SEMI, 46), fill=(*self.c["text"], int(255 * a * p)), anchor="lm")

    # -- one full frame ---------------------------------------------------
    def frame(self, idx: int, L: dict, chunks, t: float, global_t: float) -> Image.Image:
        fr = self._base(global_t)
        self._progress(fr, idx, t)
        self.draw_scene(fr, L, t, self.durs[idx])
        if self.sources is not None and L["type"] != "outro":
            put(fr, self.sources, (self.W - self.sources.width) / 2 - 30, SOURCES_Y, 1.0)
        self._captions(fr, chunks, t)
        return fr


TEMPLATES = {
    "tutorial": "Learning tutorial - notebook page, cartoon teacher, flow diagrams",
    "terminal": "Hacker terminal - green CRT text typed out",
    "news": "Breaking news - navy and red, LIVE tag, scrolling ticker",
    "editorial": "Editorial - magazine page, serif headlines, thin rules",
    "chat": "Chat - the story as phone messages with typing dots",
    "classic": "Classic - navy gradient, bold headlines",
}
SKINS = {"news": "NewsRenderer", "editorial": "EditorialRenderer", "chat": "ChatRenderer"}
# which look fits which kind of story best
RECOMMENDED = {"research": "tutorial", "policy": "tutorial", "business": "tutorial", "debate": "tutorial",
               "product": "tutorial", "launch": "terminal", "security": "terminal"}


def ask_template(cfg: dict, category: str = "", script: dict | None = None) -> str:
    """Ask which look to use before rendering. Enter keeps the config default; with no one
    at the keyboard (scheduled runs) the default is used without asking."""
    import sys

    default = cfg["video"].get("template", "classic")
    names = list(TEMPLATES)
    if remote.active:
        rec_ = RECOMMENDED.get(category or "")
        return remote.active.choose("Which look should this video use?",
                                    [(n, TEMPLATES[n].split(" - ")[0] + ("  ★ recommended" if n == rec_ else "")) for n in names], default)
    if not sys.stdin.isatty():
        return default
    rec = RECOMMENDED.get(category or "")
    print("\nWhich look should this video use?")
    for i, n in enumerate(names, 1):
        tags = [t for t, on in (("default", n == default), (f"recommended for {category} stories", n == rec)) if on]
        print(f"  {i}. {TEMPLATES[n]}" + (f"   ({', '.join(tags)})" if tags else ""))
    while True:
        try:
            ans = input(f"Pick 1-{len(names)} · Enter = {default}: ").strip().lower()
        except EOFError:  # input closed: keep the default rather than crash the run
            return default
        if not ans:
            return default
        if ans.isdigit() and 1 <= int(ans) <= len(names):
            return names[int(ans) - 1]
        if ans in names:
            return ans
        print("  Type a number from the list, or press Enter.")


def make_renderer(cfg: dict, script: dict, n_scenes: int, scene_durations: list[float]) -> SceneRenderer:
    """Pick the video template from config: one of TEMPLATES (default "classic")."""
    template = cfg["video"].get("template", "classic")
    if template == "terminal":
        from .template_terminal import TerminalRenderer

        return TerminalRenderer(cfg, script, n_scenes, scene_durations)
    if template == "tutorial":
        from .template_tutorial import TutorialRenderer

        return TutorialRenderer(cfg, script, n_scenes, scene_durations)
    if template in SKINS:
        from . import template_skins

        return getattr(template_skins, SKINS[template])(cfg, script, n_scenes, scene_durations)
    return SceneRenderer(cfg, script, n_scenes, scene_durations)


# ------------------------------------------------------------------ workers
def _render_scene(args) -> str:
    cfg, script, idx, durs, words, offset, out_path, wav = args
    r = make_renderer(cfg, script, len(durs), durs)
    if hasattr(r, "set_audio") and wav:
        r.set_audio(wav, r.fps)  # the tutorial teacher's mouth follows the voice
    scene = script["scenes"][idx]
    L = r.build_scene(scene)
    r._stat_font_for = {L["stat"]: L["stat_font"]} if L.get("stat") else {}
    chunks = caption_chunks(words)
    fps, W, H = r.fps, r.W, r.H
    n_frames = round(durs[idx] * fps)
    tmp = str(out_path).replace(".mp4", ".part.mp4")
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(cfg["video"]["crf"]), "-pix_fmt", "yuv420p",
        "-r", str(fps), "-movflags", "+faststart", tmp,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(n_frames):
        t = f / fps
        proc.stdin.write(r.frame(idx, L, chunks, t, offset + t).convert("RGB").tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed on scene {idx + 1}")
    os.replace(tmp, out_path)
    return str(out_path)


def render_still(cfg: dict, script: dict, tts: dict, idx: int, t: float, out_png: Path) -> Path:
    """Render a single frame (for previews / thumbnails)."""
    durs = scene_durations(cfg, tts)
    r = make_renderer(cfg, script, len(durs), durs)
    if hasattr(r, "set_audio") and tts["clips"][idx].get("wav"):
        r.set_audio(tts["clips"][idx]["wav"], r.fps)
    L = r.build_scene(script["scenes"][idx])
    r._stat_font_for = {L["stat"]: L["stat_font"]} if L.get("stat") else {}
    chunks = caption_chunks(tts["clips"][idx]["words"])
    r.frame(idx, L, chunks, t, sum(durs[:idx]) + t).convert("RGB").save(out_png)
    return out_png


def scene_durations(cfg: dict, tts: dict) -> list[float]:
    fps, gap = cfg["video"]["fps"], cfg["tts"]["scene_gap_seconds"]
    return [round((c["duration"] + gap) * fps) / fps for c in tts["clips"]]


def build_voice_track(tts: dict, durs: list[float], out_wav: Path) -> Path:
    parts, sr = [], None
    for clip, d in zip(tts["clips"], durs):
        data, sr_ = sf.read(clip["wav"], dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        sr = sr or sr_
        n = int(round(d * sr))
        data = data[:n] if len(data) >= n else np.concatenate([data, np.zeros(n - len(data), dtype="float32")])
        parts.append(data)
    track = np.concatenate(parts)
    peak = float(np.max(np.abs(track))) or 1.0
    sf.write(str(out_wav), track * (0.89 / peak), sr)  # normalise to about -1 dBFS
    return out_wav


LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"


def loudnorm_linear(src: Path, dst: Path) -> Path:
    """Two-pass loudness to -14 LUFS as ONE steady gain. Single-pass loudnorm rides the
    level like an automatic volume control, turning hiss up in every pause."""
    import json as _json

    probe = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(src), "-af", f"{LOUDNORM}:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    m = _json.loads(probe[probe.rindex("{"): probe.rindex("}") + 1])
    af = (f"{LOUDNORM}:linear=true:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-af", af, "-ar", "48000", str(dst)], check=True)
    return dst


def render_video(cfg: dict, script: dict, tts: dict, out_mp4: Path, workers: int | None = None) -> Path:
    durs = scene_durations(cfg, tts)
    work = Path(tempfile.mkdtemp(prefix="scenes_", dir=out_mp4.parent))
    offsets = [sum(durs[:i]) for i in range(len(durs))]
    jobs = [
        (cfg, script, i, durs, tts["clips"][i]["words"], offsets[i], work / f"scene_{i + 1:02d}.mp4", tts["clips"][i].get("wav", ""))
        for i in range(len(durs))
    ]
    workers = workers or max(1, min(len(jobs), (os.cpu_count() or 2)))
    log(f"Rendering {len(jobs)} scenes ({sum(durs):.1f}s) on {workers} worker(s)...")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        files = list(ex.map(_render_scene, jobs))

    concat = work / "list.txt"
    concat.write_text("".join(f"file '{Path(f).name}'\n" for f in files))
    video_only = work / "video.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(video_only)], check=True)

    voice = loudnorm_linear(build_voice_track(tts, durs, work / "voice_raw.wav"), work / "voice.wav")
    music = resolve(cfg["video"].get("music_file", "")) if cfg["video"].get("music_file") else None
    tmp_out = str(out_mp4).replace(".mp4", ".part.mp4")
    if music and music.exists():
        vol = cfg["video"].get("music_volume", 0.1)
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-i", str(video_only), "-i", str(voice), "-stream_loop", "-1", "-i", str(music),
            "-filter_complex", f"[2:a]volume={vol}[m];[1:a][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.84[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-ar", "48000", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", tmp_out,
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-i", str(video_only), "-i", str(voice),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-ar", "48000", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", tmp_out,
        ]
    subprocess.run(cmd, check=True)
    os.replace(tmp_out, out_mp4)
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    log(f"Video ready: {out_mp4}")
    return out_mp4
