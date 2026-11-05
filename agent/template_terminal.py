"""Terminal / hacker video template (config: video.template = "terminal").

Black CRT screen, phosphor monospace text typed out with a block cursor, a
terminal window frame, scanlines and a short glitch on each cut. Same scene
types, timing and Shorts-safe layout as the classic template in render.py.
"""
from __future__ import annotations

import math
import random
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .render import CW, FONTS, LAYOUT, SOURCES_Y, X0, X1, SceneRenderer, glow_sprite, hex_rgb, numeric_parts, prog, put, text_img, wrap

# first font that exists wins; drop JetBrainsMono-*.ttf into assets/fonts to bundle one
MONO = {
    False: [(FONTS / "JetBrainsMono-Regular.ttf", 0), (Path("/System/Library/Fonts/Menlo.ttc"), 0),
            (Path("C:/Windows/Fonts/consola.ttf"), 0), (Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"), 0)],
    True: [(FONTS / "JetBrainsMono-Bold.ttf", 0), (Path("/System/Library/Fonts/Menlo.ttc"), 1),
           (Path("C:/Windows/Fonts/consolab.ttf"), 0), (Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"), 0)],
}
DEFAULT_COLORS = {
    "bg": "#030703", "accent": "#39FF6A", "text": "#D9FFE2", "muted": "#6FB884",
    "warn": "#FFB000", "red": "#FF5F56", "frame": "#123A1E",
}
# phosphor colour per story category (config video.theme: "auto" or a theme name)
THEMES = {
    "green": {"accent": "#39FF6A", "text": "#D9FFE2", "muted": "#6FB884", "frame": "#123A1E", "warn": "#FFB000"},
    "amber": {"accent": "#FFB000", "text": "#FFF1D6", "muted": "#C9A25E", "frame": "#3A2A08", "warn": "#FF5F56"},
    "cyan": {"accent": "#3DF5FF", "text": "#DFFCFF", "muted": "#6DB8C2", "frame": "#0E3238", "warn": "#FFB000"},
    "red": {"accent": "#FF4D5E", "text": "#FFE3E6", "muted": "#C07A82", "frame": "#3A1015", "warn": "#FFB000"},
    "purple": {"accent": "#C08BFF", "text": "#F1E6FF", "muted": "#9C85C2", "frame": "#2A1A40", "warn": "#FFB000"},
}
THEME_BY_CATEGORY = {"launch": "green", "product": "green", "policy": "amber", "research": "cyan",
                     "security": "red", "business": "purple", "debate": "amber"}
TRAFFIC = ((255, 95, 86), (255, 189, 46), (39, 201, 63))  # window buttons
COMMANDS = {
    "hook": "./breaking --today", "setup": "cat setup.log", "twist": "grep -i 'but' story.log",
    "stat": "wc --numbers", "example": "explain --like-im-five", "stakes": "echo $WHY_IT_MATTERS", "verify": "factcheck --strict",
    "outro": "exit", "point": "cat story.log",
}
WIN = (40, 232, 1000, 1540)  # terminal window box
GLOW_PAD = 18


@lru_cache(maxsize=256)
def mono(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path, index in MONO[bold]:
        if path.exists():
            return ImageFont.truetype(str(path), size, index=index)
    raise FileNotFoundError("No monospace font found; add assets/fonts/JetBrainsMono-Regular.ttf and -Bold.ttf")


def fit_mono(text: str, bold: bool, max_size: int, min_size: int, max_w: int, max_lines: int):
    size = max_size
    while size > min_size:
        f = mono(size, bold)
        lines = wrap(text, f, max_w)
        if len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines):
            return f, lines
        size -= 4
    f = mono(min_size, bold)
    return f, wrap(text, f, max_w)


def glow_text(line: str, fnt, rgb, glow: int = 10, strength: float = 0.85) -> Image.Image:
    """Crisp text over a blurred copy of itself (phosphor glow), padded by GLOW_PAD."""
    crisp = text_img(line, fnt, (*rgb, 255))
    out = Image.new("RGBA", (crisp.width + GLOW_PAD * 2, crisp.height + GLOW_PAD * 2), (0, 0, 0, 0))
    out.alpha_composite(crisp, (GLOW_PAD, GLOW_PAD))
    halo = out.filter(ImageFilter.GaussianBlur(glow))
    halo.putalpha(halo.getchannel("A").point(lambda v: int(min(255, v * 1.6) * strength)))
    halo.alpha_composite(out)
    return halo


class TerminalRenderer(SceneRenderer):
    def __init__(self, cfg: dict, script: dict, n_scenes: int, scene_durations: list[float]):
        theme = cfg["video"].get("theme", "auto")
        if theme == "auto":
            by_cat = {**THEME_BY_CATEGORY, **cfg["video"].get("theme_by_category", {})}
            theme = by_cat.get(script.get("category", ""), "green")
        colors = {**DEFAULT_COLORS, **cfg["brand"].get("terminal_colors", {}), **THEMES.get(theme, {})}
        self.k = {k: hex_rgb(v) for k, v in colors.items()}
        super().__init__(cfg, script, n_scenes, scene_durations)
        self.glows = [glow_sprite(self.k["accent"], 380, 34)]
        self.scan = self._scanlines()
        self.total = sum(scene_durations)

    # -- static layers ---------------------------------------------------
    def _background(self) -> Image.Image:
        k = self.k
        ys, xs = np.mgrid[0 : self.H, 0 : self.W].astype(np.float32)
        r = np.sqrt(((xs - self.W / 2) / (self.W * 0.75)) ** 2 + ((ys - self.H * 0.45) / (self.H * 0.7)) ** 2)
        lift = np.clip(1 - r, 0, 1)[..., None] * 0.06  # faint green bloom in the middle, dark corners
        arr = np.array(k["bg"], np.float32) * (1 - lift) + np.array(k["accent"], np.float32) * lift
        im = Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")
        d = ImageDraw.Draw(im)
        x0, y0, x1, y1 = WIN
        d.rounded_rectangle(WIN, radius=22, fill=(*k["bg"], 255), outline=(*k["frame"], 255), width=3)
        d.rounded_rectangle([x0, y0, x1, y0 + 64], radius=22, fill=(*k["frame"], 255))
        d.rectangle([x0, y0 + 40, x1, y0 + 64], fill=(*k["frame"], 255))
        for i, c in enumerate(TRAFFIC):
            cx = x0 + 38 + i * 36
            d.ellipse([cx - 10, y0 + 22, cx + 10, y0 + 42], fill=(*c, 255))
        title = f"{self.cfg['brand']['name'].lower().replace(' ', '_')}@shorts: ~"
        d.text(((x0 + x1) / 2, y0 + 32), title, font=mono(28), fill=(*k["muted"], 255), anchor="mm")
        return im

    def _chrome(self) -> Image.Image:
        layer = Image.new("RGBA", (self.W, 240), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        f = mono(38, True)
        name = f"[ {self.cfg['brand']['name']} ]"
        d.text((X0, 120), name, font=f, fill=(*self.k["accent"], 255), anchor="lm")
        date = self.script.get("date_label", "")
        if date:
            d.text((X1, 120), date, font=mono(30), fill=(*self.k["muted"], 255), anchor="rm")
        return layer

    def _scanlines(self) -> Image.Image:
        a = np.zeros((self.H, self.W, 4), np.uint8)
        a[::4, :, 3] = 70
        a[1::4, :, 3] = 25
        return Image.fromarray(a, "RGBA")

    def _sources_line(self):
        pubs = []
        for s in self.script.get("sources", []):
            p = (s.get("publisher") or "").strip()
            if p and p not in pubs:
                pubs.append(p)
        if not pubs:
            return None
        f = mono(28)
        txt = "// src: " + " · ".join(pubs[:3])
        while f.getlength(txt) > CW and len(pubs) > 1:
            pubs.pop()
            txt = "// src: " + " · ".join(pubs)
        return text_img(txt, f, (*self.k["muted"], 220))

    # -- per-frame pieces ------------------------------------------------
    def _base(self, gt: float) -> Image.Image:
        fr = self.bg.copy()
        g = self.glows[0]
        fr.alpha_composite(g, (int(-120 + 160 * math.sin(gt * 0.3)), int(500 + 220 * math.cos(gt * 0.21))))
        fr.alpha_composite(self.chrome, (0, 0))
        return fr

    def _progress(self, fr: Image.Image, idx: int, t: float):
        d = ImageDraw.Draw(fr)
        f = mono(28, True)
        label = f"{idx + 1:02d}/{self.n:02d}"
        lw = f.getlength(label) + 24
        cell, gap, y, h = 14, 5, 176, 18
        n_cells = int((CW - lw + gap) // (cell + gap))
        done = (sum(self.durs[:idx]) + t) / self.total * n_cells
        for i in range(n_cells):
            x = X0 + i * (cell + gap)
            on = i < int(done)
            d.rectangle([x, y, x + cell, y + h], fill=(*(self.k["accent"] if on else self.k["frame"]), 255))
        d.text((X1, y + h / 2), label, font=f, fill=(*self.k["accent"], 255), anchor="rm")

    @lru_cache(maxsize=512)
    def _caption_img(self, text_words: tuple, active: int) -> Image.Image:
        f = mono(60, True)
        space = f.getlength(" ")
        parts = []
        for i, w in enumerate(text_words):
            if i == active:  # inverse video on the spoken word
                im = text_img(w, f, (*self.k["bg"], 255))
                box = Image.new("RGBA", (im.width + 16, im.height), (*self.k["accent"], 255))
                box.alpha_composite(im, (8, 0))
                parts.append(box)
            else:
                parts.append(text_img(w, f, (*self.k["text"], 255), stroke=6, stroke_fill=(*self.k["bg"],)))
        total = sum(p.width for p in parts) + space * (len(parts) - 1)
        rows = [parts[: max(1, len(parts) // 2)], parts[max(1, len(parts) // 2) :]] if total > CW else [parts]
        row_imgs = []
        for row in rows:
            im = Image.new("RGBA", (int(sum(p.width for p in row) + space * (len(row) - 1)), max(p.height for p in row)), (0, 0, 0, 0))
            x = 0
            for p in row:
                im.alpha_composite(p, (int(x), 0))
                x += p.width + space
            row_imgs.append(im)
        out = Image.new("RGBA", (max(r.width for r in row_imgs), sum(r.height + 6 for r in row_imgs)), (0, 0, 0, 0))
        y = 0
        for r in row_imgs:
            out.alpha_composite(r, ((out.width - r.width) // 2, y))
            y += r.height + 6
        return out

    # -- scene layouts -----------------------------------------------------
    def _typed(self, items: list, img: Image.Image, x: int, y: int, text: str, adv: float, start: float, cps: float, pad: int = 0):
        """Queue a line that is revealed character by character; returns when it finishes."""
        items.append({"img": img, "x": x, "y": y, "n": len(text), "adv": adv, "start": start, "cps": cps, "pad": pad})
        return start + len(text) / cps

    def build_scene(self, scene: dict):
        """Lay out every line up front so frames only crop and composite."""
        beat = scene.get("type", "point")
        st = LAYOUT.get(beat, beat)
        os_ = scene.get("on_screen", {}) or {}
        k = self.k
        L = {"type": st, "items": [], "stat": None, "follow_at": None}
        items, y, t = L["items"], 360, 0.0

        # prompt line: "$ cat setup.log"
        cmd = os_.get("label") or COMMANDS.get(beat, COMMANDS["point"])
        f = mono(36, True)
        prompt = Image.new("RGBA", (CW, 56), (0, 0, 0, 0))
        dollar = text_img("$ ", f, (*k["accent"], 255))
        prompt.alpha_composite(dollar, (0, 0))
        prompt.alpha_composite(text_img(cmd, f, (*k["muted"], 255)), (int(f.getlength("$ ")), 0))
        t = self._typed(items, prompt, X0, y, "$ " + cmd, f.getlength("M"), 0.0, 60)
        y += 56 + 34
        t += 0.08

        if st in ("hook", "point", "outro"):
            vis = os_.get("visual") if st == "point" and isinstance(os_.get("visual"), dict) else None
            maxs = {"hook": 104, "point": 76 if vis else 88, "outro": 96}[st]
            fh, lines = fit_mono(os_.get("headline", "").upper(), True, maxs, 52, CW, 2 if vis else 4)
            cps = max(sum(map(len, lines)) / 0.9, 26)
            for ln in lines:
                t = self._typed(items, glow_text(ln, fh, k["accent"]), X0 - GLOW_PAD, y - GLOW_PAD, ln, fh.getlength("M"), t, cps, GLOW_PAD)
                y += int(fh.size * 1.16)
            y += 28 if vis else 36
            if os_.get("sub"):
                fs, sl = fit_mono("> " + os_["sub"], False, 40 if vis else 46, 32, CW, 2 if vis else 3)
                for ln in sl:
                    t = self._typed(items, text_img(ln, fs, (*k["text"], 255)), X0, y, ln, fs.getlength("M"), t + 0.05, 70)
                    y += int(fs.size * 1.4)
            if vis:
                L["visual"] = self._build_visual(vis, y + 22, t + 0.15, items)
            if st == "outro":
                L["follow_at"] = (y + 30, t + 0.15)

        elif st == "stat":
            stat = str(os_.get("stat", ""))
            fst, _ = fit_mono(stat, True, 250, 100, CW, 1)
            L.update(stat=stat, stat_font=fst, num=numeric_parts(stat), stat_y=y, stat_t=t)
            y += int(fst.size * 1.12) + 20
            if os_.get("sub"):
                fs, sl = fit_mono("> " + os_["sub"], False, 52, 36, CW, 3)
                for ln in sl:
                    t = self._typed(items, text_img(ln, fs, (*k["text"], 255)), X0, y, ln, fs.getlength("M"), t + 0.6, 60)
                    y += int(fs.size * 1.4)

        elif st == "verify":
            fb = mono(56, True)
            badge = "FACT-CHECK: PASSED"
            t = self._typed(items, glow_text(badge, fb, k["accent"]), X0 - GLOW_PAD, y - GLOW_PAD, badge, fb.getlength("M"), t, 40, GLOW_PAD)
            y += 110
            bf = mono(44)
            tag = "[!] "
            bullets = (os_.get("bullets") or [])[:3]
            step = max(0.45, (self.durs[self.script['scenes'].index(scene)] - 1.4) / max(len(bullets), 1) * 0.6) if bullets else 0
            for i, b in enumerate(bullets):
                start = t + 0.15 + i * step
                for j, ln in enumerate(wrap(b, bf, CW - int(bf.getlength(tag)))):
                    pre = tag if j == 0 else " " * len(tag)
                    im = Image.new("RGBA", (CW, 64), (0, 0, 0, 0))
                    im.alpha_composite(text_img(pre, bf, (*k["warn"], 255)), (0, 0))
                    im.alpha_composite(text_img(ln, bf, (*k["text"], 255)), (int(bf.getlength(pre)), 0))
                    start = self._typed(items, im, X0, y, pre + ln, bf.getlength("M"), start, 55)
                    y += 64
                y += 22
        return L

    def draw_scene(self, fr: Image.Image, L: dict, t: float, dur: float):
        out = 1 - prog(t, dur - 0.15, 0.15)
        k = self.k
        cursor_at = None
        if L.get("visual"):  # first, so card backgrounds sit under the typed text
            self._draw_visual(fr, L["visual"], t, out)
        for it in L["items"]:
            if t < it["start"]:
                break
            shown = min(it["n"], int((t - it["start"]) * it["cps"]) + 1)
            w = int(it["pad"] + 2 + shown * it["adv"] + (it["pad"] if shown == it["n"] else 0))
            im = it["img"] if shown == it["n"] else it["img"].crop((0, 0, min(w, it["img"].width), it["img"].height))
            put(fr, im, it["x"], it["y"], out)
            cursor_at = (it["x"] + it["pad"] + 2 + shown * it["adv"], it["y"] + it["pad"], it["img"].height - 2 * it["pad"], it["adv"])

        if L["stat"] is not None and t >= L["stat_t"]:
            n, p = L["num"], prog(t, L["stat_t"], 0.9)
            if n and p < 1:
                pre, val, suf, dec, comma = n
                s = f"{val * p:,.{dec}f}" if comma else f"{val * p:.{dec}f}"
                txt = f"{pre}{s}{suf}"
            else:
                txt = L["stat"]
            im = _glow_cached(txt, L["stat_font"].size, k["accent"])
            put(fr, im, X0 - GLOW_PAD - 4, L["stat_y"] - GLOW_PAD, out)

        if L["follow_at"] and t >= L["follow_at"][1]:
            y, start = L["follow_at"]
            self._follow_button(fr, y, t - start, out)
            cursor_at = None

        # block cursor: solid while typing, blinking once the line is done
        if cursor_at:
            x, y, h, adv = cursor_at
            typing = any(it["start"] <= t < it["start"] + it["n"] / it["cps"] for it in L["items"])
            if typing or int(t * 2.4) % 2 == 0:
                ImageDraw.Draw(fr, "RGBA").rectangle([x + 4, y + h * 0.18, x + 4 + adv * 0.9, y + h * 0.86], fill=(*k["accent"], int(230 * out)))

    def _follow_button(self, fr, y, t, a):
        p = prog(t, 0.0, 0.25)
        if p <= 0:
            return
        k = self.k
        d = ImageDraw.Draw(fr, "RGBA")
        f = mono(60, True)
        label = "[ FOLLOW ]"
        w, h = int(f.getlength(label) + 60), 118
        on = int(t * 2.4) % 2 == 0
        fill, ink = (k["accent"], k["bg"]) if on else (k["bg"], k["accent"])
        d.rectangle([X0, y, X0 + w, y + h], fill=(*fill, int(255 * a * p)), outline=(*k["accent"], int(255 * a * p)), width=4)
        d.text((X0 + w / 2, y + h / 2), label, font=f, fill=(*ink, int(255 * a * p)), anchor="mm")
        d.text((X0 + w + 32, y + h / 2), self.cfg["brand"]["handle"], font=mono(42), fill=(*k["text"], int(255 * a * p)), anchor="lm")

    def _glitch(self, fr: Image.Image, idx: int, t: float):
        """A few frames of shifted bands right after each cut (video.glitch_cuts, off by default)."""
        if not self.cfg["video"].get("glitch_cuts", False) or idx == 0 or t > 0.12:
            return fr
        rng = random.Random(idx * 1000 + int(t * 100))
        arr = np.array(fr)
        for _ in range(5):
            y0 = rng.randrange(0, self.H - 120)
            h = rng.randrange(20, 120)
            arr[y0 : y0 + h] = np.roll(arr[y0 : y0 + h], rng.randrange(-40, 40), axis=1)
        return Image.fromarray(arr, "RGBA")

    def frame(self, idx: int, L: dict, chunks, t: float, global_t: float) -> Image.Image:
        fr = self._base(global_t)
        self._progress(fr, idx, t)
        self.draw_scene(fr, L, t, self.durs[idx])
        if self.sources is not None and L["type"] != "outro":
            put(fr, self.sources, X0, SOURCES_Y + 10, 1.0)
        self._captions(fr, chunks, t)
        fr.alpha_composite(self.scan)
        return self._glitch(fr, idx, t)


    # -- animated fact graphics (on_screen.visual) ------------------------
    # Every number/name in a visual is checked against the fact-checked
    # claims in scriptwriter.validate before it can reach the renderer.
    def _build_visual(self, v: dict, y: int, t0: float, items: list) -> dict | None:
        kind = v.get("kind")
        V = {"kind": kind, "y": y, "t0": t0}
        k = self.k
        if kind == "bars":
            rows = [r for r in v.get("items", []) if isinstance(r.get("value"), (int, float))][:4]
            if not rows:
                return None
            lf, vf = mono(34, True), mono(36, True)
            V.update(rows=rows, lf=lf, vf=vf, pre=v.get("prefix", ""), suf=v.get("suffix", ""),
                     lw=int(max(lf.getlength(str(r["label"])[:10]) for r in rows)) + 24, top=max(r["value"] for r in rows) or 1)
        elif kind == "grid":
            pct = v.get("percent")
            if not isinstance(pct, (int, float)):
                return None
            fl, sub = fit_mono(str(v.get("label", "")), False, 38, 30, CW - 360, 3)
            V.update(pct=max(0.0, min(100.0, float(pct))), label=[text_img(l, fl, (*k["text"], 255)) for l in sub], label_h=int(fl.size * 1.35))
        elif kind == "diff":
            f = mono(38)
            adv = f.getlength("M")
            yy, t = y, t0
            for sign, key, color in (("-", "removed", k["red"]), ("+", "added", k["accent"])):
                for line in (v.get(key) or [])[:3]:
                    for j, ln in enumerate(wrap(str(line), f, CW - int(adv * 2))):
                        txt = (sign if j == 0 else " ") + " " + ln
                        im = Image.new("RGBA", (CW, 56), (*color, 28))
                        im.alpha_composite(text_img(txt, f, (*color, 255)), (8, 2))
                        t = self._typed(items, im, X0, yy, txt, adv, t, 55)
                        yy += 60
                t += 0.25
        elif kind == "whois":
            f = mono(36)
            fb = mono(36, True)
            adv = f.getlength("M")
            V.update(avatar_y=y + 24)
            rows = [("name", v.get("name")), ("role", v.get("role")), ("org", v.get("org"))]
            rows = [(a, str(b)) for a, b in rows if b]
            yy, t = y + 22, t0 + 0.2
            for label, val in rows:
                txt = f"{label + ':':<6}{val}"[:30]
                im = Image.new("RGBA", (CW - 170, 50), (0, 0, 0, 0))
                im.alpha_composite(text_img(f"{label + ':':<6}", fb, (*k["muted"], 255)), (0, 0))
                im.alpha_composite(text_img(txt[6:], f, (*k["text"], 255)), (int(adv * 6), 0))
                t = self._typed(items, im, X0 + 170, yy, txt, adv, t, 60)
                yy += 52
            V["card_h"] = max(yy - y + 18, 150)
            yy = y + V["card_h"] + 26
            if v.get("quote"):
                fq, ql = fit_mono(f'> "{v["quote"]}"', False, 40, 32, CW, 3)
                V["q_start"] = t + 0.2
                for ln in ql:
                    t = self._typed(items, text_img(ln, fq, (*k["accent"], 255)), X0, yy, ln, fq.getlength("M"), t + 0.05, 22)
                    yy += int(fq.size * 1.4)
                V["q_end"] = t
        else:
            return None
        return V

    AVATAR = ("..XXXX..", ".XXXXXX.", ".XoXXoX.", ".XXXXXX.", "..XXXX..", "...XX...", ".XXXXXX.", "XXXXXXXX")

    def _draw_visual(self, fr: Image.Image, V: dict, t: float, out: float):
        k, d = self.k, ImageDraw.Draw(fr, "RGBA")
        a = int(255 * out)
        y, t0 = V["y"], V["t0"]
        if t < t0:
            return
        if V["kind"] == "bars":
            x_bar, x_val = X0 + V["lw"], X1 - 170
            cell, gap = 12, 4
            full = int((x_val - 20 - x_bar) // (cell + gap))
            for i, r in enumerate(V["rows"]):
                p = prog(t, t0 + i * 0.3, 0.9)
                ry = y + i * 70
                d.text((X0, ry + 22), str(r["label"])[:10], font=V["lf"], fill=(*k["muted"], a), anchor="lm")
                n = max(1, round(r["value"] / V["top"] * full)) if r["value"] > 0 else 0
                lit = int(n * p)
                col = k["accent"] if r["value"] == V["top"] else k["text"]
                for c in range(full):
                    cx = x_bar + c * (cell + gap)
                    on = c < lit
                    if on or c < n:
                        d.rectangle([cx, ry + 6, cx + cell, ry + 38], fill=(*(col if on else k["frame"]), a))
                val = r["value"] * p
                s = f"{val:,.0f}" if float(r["value"]).is_integer() else f"{val:,.1f}"
                d.text((X1, ry + 22), f"{V['pre']}{s}{V['suf']}", font=V["vf"], fill=(*col, a), anchor="rm")
        elif V["kind"] == "grid":
            p = prog(t, t0, 1.3)
            lit = round(V["pct"] * p)
            cell, gap = 24, 6
            for i in range(100):
                cx, cy = X0 + (i % 10) * (cell + gap), y + (i // 10) * (cell + gap)
                d.rectangle([cx, cy, cx + cell, cy + cell], fill=(*(k["accent"] if i < lit else k["frame"]), a))
            pct = V["pct"] * p
            big = _glow_cached(f"{pct:.0f}%" if float(V["pct"]).is_integer() else f"{pct:.1f}%", 110, k["accent"])
            put(fr, big, X0 + 330 - GLOW_PAD, y - GLOW_PAD, out)
            ly = y + 150
            for im in V["label"]:
                put(fr, im, X0 + 330, ly, out * prog(t, t0 + 0.4, 0.4))
                ly += V["label_h"]
        elif V["kind"] == "whois":
            p = prog(t, t0, 0.3)
            d.rounded_rectangle([X0, y, X1, y + V["card_h"]], radius=14, outline=(*k["frame"], int(a * p)), width=3, fill=(*k["frame"], int(60 * out * p)))
            blink = int(t * 1.3) % 4 == 3
            cell, ax, ay = 14, X0 + 28, V["avatar_y"]
            for r, row in enumerate(self.AVATAR):
                for c, ch in enumerate(row):
                    if ch == "X" or (ch == "o" and blink):
                        d.rectangle([ax + c * cell, ay + r * cell, ax + c * cell + cell - 2, ay + r * cell + cell - 2], fill=(*k["accent"], int(a * p)))
            if "q_start" in V and V["q_start"] <= t < V["q_end"] + 0.3:  # "speaking" meter
                for i in range(5):
                    h = 10 + 26 * abs(math.sin(t * 9 + i * 1.3))
                    bx = X1 - 30 - i * 16
                    d.rectangle([bx, y + 40 - h / 2, bx + 9, y + 40 + h / 2], fill=(*k["accent"], a))


@lru_cache(maxsize=1024)
def _glow_cached(txt: str, size: int, rgb: tuple) -> Image.Image:
    return glow_text(txt, mono(size, True), rgb, glow=16)
