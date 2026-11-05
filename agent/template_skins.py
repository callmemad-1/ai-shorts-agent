"""Three more looks on the tutorial engine (same scenes, graphics and fact rules):

- "news"      Breaking-news desk: navy and red, LIVE tag, red story labels, scrolling ticker
- "editorial" Magazine page: masthead, big serif headlines, thin accent rules
- "chat"      The story as phone messages: typing dots, pop-in bubbles, quick-reply chips

Each class only changes fonts, colours, background, header and finishing touches; the
scene layouts and animated graphics come from TutorialRenderer.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .render import CW, LAYOUT, hex_rgb, numeric_parts, prog, put, text_img, wrap
from .template_tutorial import ACCENT, KICKER, MOOD, X0, X1, TutorialRenderer, icon_img

SYS = Path("/System/Library/Fonts")
SUP = SYS / "Supplemental"
INTER = Path(__file__).resolve().parent.parent / "assets" / "fonts"


# ====================================================================== news
class NewsRenderer(TutorialRenderer):
    FACES = {
        "marker": [(SYS / "Avenir Next Condensed.ttc", 8), (INTER / "InterDisplay-Black.otf", 0)],
        "note": [(SYS / "Avenir Next.ttc", 5), (INTER / "Inter-Medium.otf", 0)],
        "note_bold": [(SYS / "Avenir Next Condensed.ttc", 0), (INTER / "InterDisplay-Bold.otf", 0)],
        "caption": [(SYS / "Avenir Next.ttc", 8), (INTER / "InterDisplay-ExtraBold.otf", 0)],
    }
    PALETTE = {"paper": "#0A1630", "ink": "#FFFFFF", "soft": "#AEB8CC", "rule": "#24365C", "highlight": "#E11D2E",
               "card": "#13254A", "good": "#22C55E", "warn": "#F43F5E", "margin": "#24365C",
               "cap_text": "#FFFFFF", "cap_hl": "#E11D2E", "cap_hl_text": "#FFFFFF", "cap_stroke": "#050B1A",
               "before": "#3A1520", "after": "#12321F", "bar_dim": "#5B6B8C"}
    KICKER = {"hook": "TOP STORY", "setup": "WHAT HAPPENED", "twist": "THE TWIST", "stat": "BY THE NUMBERS",
              "example": "EXPLAINER", "stakes": "WHY IT MATTERS", "verify": "FACT CHECK", "outro": "BOTTOM LINE",
              "point": "WHAT HAPPENED"}
    TEACHER = False
    CONTENT_BOTTOM = 1120
    HEADLINE_MARK = "underline"
    CAPTION_STROKE = 6
    RED = (225, 29, 46)

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.acc = self.RED  # news is always red

    def _background(self) -> Image.Image:
        top, bot = np.array((12, 28, 60), float), np.array((4, 10, 26), float)
        ys = np.linspace(0, 1, self.H)[:, None]
        col = (top * (1 - ys) + bot * ys).astype(np.uint8)
        im = Image.fromarray(np.repeat(col[:, None, :], self.W, axis=1), "RGB").convert("RGBA")
        stripes = Image.new("RGBA", im.size, (0, 0, 0, 0))  # own layer: faint lines must blend, not replace
        d = ImageDraw.Draw(stripes)
        for x in range(-self.H, self.W, 46):  # faint diagonal studio stripes
            d.line([(x, self.H), (x + self.H, 0)], fill=(255, 255, 255, 9), width=2)
        im.alpha_composite(stripes)
        glow = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((-200, -300, 900, 600), fill=(225, 29, 46, 40))
        im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
        return im

    def _chrome(self) -> Image.Image:
        layer = Image.new("RGBA", (self.W, 240), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        f = self.font("marker", 40)
        label = "AI NEWS"
        w = f.getlength(label)
        d.rectangle((X0 - 30, 88, X0 - 30 + w + 36, 142), fill=(*self.RED, 255))
        d.text((X0 - 12, 115), label, font=f, fill=(255, 255, 255, 255), anchor="lm")
        d.text((X0 + w + 28, 115), self.cfg["brand"]["name"].upper(), font=self.font("note_bold", 36), fill=(255, 255, 255, 255), anchor="lm")
        date = self.script.get("date_label", "")
        if date:
            d.text((X1 + 40, 115), date, font=self.font("note_bold", 30), fill=(*self.p["soft"], 255), anchor="rm")
        return layer

    def _progress(self, fr: Image.Image, idx: int, t: float):
        d = ImageDraw.Draw(fr)
        on = int((idx * 7 + t) * 1.6) % 2 == 0  # blinking LIVE dot
        d.ellipse((X0 - 30, 178, X0 - 10, 198), fill=(*(self.RED if on else (110, 20, 30)), 255))
        d.text((X0, 188), "LIVE", font=self.font("note_bold", 30), fill=(255, 255, 255, 255), anchor="lm")
        x0, x1, gap = X0 + 90, X1 + 40, 8
        seg = (x1 - x0 - gap * (self.n - 1)) / self.n
        for i in range(self.n):
            x = x0 + i * (seg + gap)
            d.rectangle((x, 184, x + seg, 192), fill=(*self.p["rule"], 255))
            p = 1.0 if i < idx else (min(t / self.durs[idx], 1.0) if i == idx else 0.0)
            if p > 0:
                d.rectangle((x, 184, x + seg * p, 192), fill=(*self.RED, 255))

    def _kicker_img(self, text: str) -> Image.Image:
        f = self.font("note_bold", 34)
        im = Image.new("RGBA", (int(f.getlength(text)) + 40, 56), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, im.width, 56), fill=(*self.RED, 255))
        d.text((20, 28), text, font=f, fill=(255, 255, 255, 255), anchor="lm")
        return im

    def _overlay(self, fr: Image.Image, idx: int, t: float, gt: float):
        """Scrolling news ticker above the captions."""
        y0, y1 = 1170, 1232
        d = ImageDraw.Draw(fr)
        d.rectangle((0, y0, self.W, y1), fill=(255, 255, 255, 255))
        f = self.font("note_bold", 34)
        msg = f"{self.script.get('story_title') or self.script.get('title', '')}   •   Fact-checked against independent sources   •   "
        w = f.getlength(msg)
        x = 250 - (gt * 140) % w
        while x < self.W:
            d.text((x, (y0 + y1) / 2), msg, font=f, fill=(10, 22, 48, 255), anchor="lm")
            x += w
        lab = self.cfg["brand"]["name"].upper()
        fl = self.font("marker", 34)
        lw = fl.getlength(lab) + 48
        d.rectangle((0, y0, lw, y1), fill=(*self.RED, 255))
        d.text((24, (y0 + y1) / 2), lab, font=fl, fill=(255, 255, 255, 255), anchor="lm")

    def _follow(self, fr: Image.Image, y: float, start: float, t: float, out: float):
        pp = prog(t, start, 0.3)
        if pp <= 0:
            return
        d = ImageDraw.Draw(fr)
        a = int(255 * pp * out)
        f = self.font("marker", 60)
        w = f.getlength("FOLLOW") + 80
        d.rectangle((X0, y, X0 + w, y + 110), fill=(*self.RED, a))
        d.text((X0 + w / 2, y + 55), "FOLLOW", font=f, fill=(255, 255, 255, a), anchor="mm")
        d.text((X0 + w + 30, y + 55), self.cfg["brand"]["handle"], font=self.font("note_bold", 40), fill=(255, 255, 255, a), anchor="lm")


# ================================================================= editorial
EDIT_ACCENT = {"launch": "#0F5C8C", "product": "#0F5C8C", "research": "#0F5C8C", "policy": "#B42318",
               "security": "#B42318", "debate": "#B45309", "business": "#6B3FA0"}


class EditorialRenderer(TutorialRenderer):
    FACES = {
        "marker": [(SUP / "Georgia Bold.ttf", 0), (SYS / "NewYork.ttf", 0), (INTER / "InterDisplay-Black.otf", 0)],
        "note": [(SUP / "Georgia Italic.ttf", 0), (INTER / "Inter-Medium.otf", 0)],
        "note_bold": [(SYS / "Avenir Next.ttc", 2), (INTER / "Inter-SemiBold.otf", 0)],
        "caption": [(SYS / "Avenir Next.ttc", 0), (INTER / "InterDisplay-ExtraBold.otf", 0)],
    }
    PALETTE = {"paper": "#F6F2EA", "ink": "#111111", "soft": "#5F5A52", "rule": "#D8D0C2", "highlight": "#F3D9A4",
               "card": "#FFFFFF", "good": "#2F7D4F", "warn": "#B42318", "margin": "#D8D0C2",
               "cap_text": "#111111", "cap_hl": "#F3D9A4", "cap_hl_text": "#111111", "cap_stroke": "#F6F2EA",
               "before": "#F6E3E1", "after": "#E3EEE6", "bar_dim": "#B9B1A3"}
    KICKER = {"hook": "THE STORY", "setup": "WHAT HAPPENED", "twist": "THE TURN", "stat": "THE NUMBER",
              "example": "IN PLAIN TERMS", "stakes": "WHY IT MATTERS", "verify": "THE FINE PRINT", "outro": "THE TAKEAWAY",
              "point": "WHAT HAPPENED"}
    TEACHER = False
    CONTENT_BOTTOM = 1130
    HEADLINE_MARK = "underline"
    STAT_CIRCLE = False

    def __init__(self, cfg, script, *a, **k):
        super().__init__(cfg, script, *a, **k)
        self.acc = hex_rgb(EDIT_ACCENT.get(script.get("category", ""), "#B42318"))

    def _background(self) -> Image.Image:
        im = Image.new("RGBA", (self.W, self.H), (*self.p["paper"], 255))
        d = ImageDraw.Draw(im)
        d.rectangle((28, 28, self.W - 28, 1250), outline=(*self.p["ink"], 255), width=2)
        d.rectangle((38, 38, self.W - 38, 1240), outline=(*self.p["rule"], 255), width=1)
        return im

    def _chrome(self) -> Image.Image:
        layer = Image.new("RGBA", (self.W, 260), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        name = " ".join(self.cfg["brand"]["name"].upper())  # letter-spaced masthead
        d.text((self.W / 2 - 20, 108), name, font=self.font("marker", 54), fill=(*self.p["ink"], 255), anchor="mm")
        d.line([(X0 - 20, 160), (X1 + 30, 160)], fill=(*self.p["ink"], 255), width=3)
        d.line([(X0 - 20, 168), (X1 + 30, 168)], fill=(*self.p["ink"], 255), width=1)
        date = self.script.get("date_label", "")
        f = self.font("note_bold", 26)
        d.text((X0 - 20, 194), (date.title() if date else "") + "  ·  The daily AI brief, fact-checked", font=f, fill=(*self.p["soft"], 255), anchor="lm")
        return layer

    def _progress(self, fr: Image.Image, idx: int, t: float):
        d = ImageDraw.Draw(fr)
        d.text((X1 + 30, 194), f"{idx + 1} / {self.n}", font=self.font("note_bold", 26), fill=(*self.acc, 255), anchor="rm")
        d.line([(X0 - 20, 226), (X1 + 30, 226)], fill=(*self.p["rule"], 255), width=2)
        done = (sum(self.durs[:idx]) + t) / max(sum(self.durs), 1e-6)
        d.line([(X0 - 20, 226), (X0 - 20 + (X1 + 50 - X0) * done, 226)], fill=(*self.acc, 255), width=4)

    def _kicker_img(self, text: str) -> Image.Image:
        f = self.font("note_bold", 30)
        spaced = " ".join(text)
        im = Image.new("RGBA", (int(f.getlength(spaced)) + 70, 50), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.line([(0, 25), (48, 25)], fill=(*self.acc, 255), width=4)
        d.text((64, 25), spaced, font=f, fill=(*self.acc, 255), anchor="lm")
        return im

    def _follow(self, fr: Image.Image, y: float, start: float, t: float, out: float):
        pp = prog(t, start, 0.3)
        if pp <= 0:
            return
        d = ImageDraw.Draw(fr)
        a = int(255 * pp * out)
        f = self.font("note_bold", 40)
        label = "F O L L O W"
        w = f.getlength(label) + 70
        d.rectangle((X0, y, X0 + w, y + 96), outline=(*self.p["ink"], a), width=3)
        d.text((X0 + w / 2, y + 48), label, font=f, fill=(*self.p["ink"], a), anchor="mm")
        d.text((X0 + w + 28, y + 48), self.cfg["brand"]["handle"], font=self.font("note", 38), fill=(*self.p["soft"], a), anchor="lm")


# ====================================================================== chat
class ChatRenderer(TutorialRenderer):
    FACES = {
        "marker": [(SYS / "Avenir Next.ttc", 0), (INTER / "InterDisplay-Bold.otf", 0)],
        "note": [(SYS / "Avenir Next.ttc", 5), (INTER / "Inter-Medium.otf", 0)],
        "note_bold": [(SYS / "Avenir Next.ttc", 2), (INTER / "Inter-SemiBold.otf", 0)],
        "caption": [(SYS / "Avenir Next.ttc", 8), (INTER / "InterDisplay-ExtraBold.otf", 0)],
    }
    PALETTE = {"paper": "#E9EEF5", "ink": "#16202E", "soft": "#6B7686", "rule": "#D3DAE5", "highlight": "#FFE45C",
               "card": "#FFFFFF", "good": "#22A55A", "warn": "#E5484D", "margin": "#D3DAE5",
               "cap_text": "#16202E", "cap_hl": "#2F6FED", "cap_hl_text": "#FFFFFF", "cap_stroke": "#E9EEF5",
               "before": "#FFECEC", "after": "#E8F8EC", "bar_dim": "#9AA6B8"}
    KICKER = {"hook": "", "setup": "", "twist": "", "stat": "", "example": "", "stakes": "", "verify": "", "outro": "", "point": ""}
    TEACHER = False
    CONTENT_BOTTOM = 1140
    HEADLINE_MARK = None
    STAT_CIRCLE = False
    BUBBLE_W = 760

    def _background(self) -> Image.Image:
        im = Image.new("RGBA", (self.W, self.H), (*self.p["paper"], 255))
        d = ImageDraw.Draw(im, "RGBA")
        rng = np.random.default_rng(7)  # soft confetti pattern like a chat wallpaper
        for _ in range(70):
            x, y, r = rng.uniform(0, self.W), rng.uniform(260, 1250), rng.uniform(6, 16)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(*self.p["rule"], 120))
        d.rectangle((0, 0, self.W, 250), fill=(255, 255, 255, 255))
        d.line([(0, 250), (self.W, 250)], fill=(*self.p["rule"], 255), width=2)
        return im

    def _chrome(self) -> Image.Image:
        layer = Image.new("RGBA", (self.W, 250), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.text((X0 - 20, 54), "9:41", font=self.font("note_bold", 32), fill=(*self.p["ink"], 255), anchor="lm")
        d.rounded_rectangle((X1 - 20, 42, X1 + 34, 66), radius=6, outline=(*self.p["ink"], 255), width=3)
        d.rectangle((X1 - 14, 48, X1 + 20, 60), fill=(*self.p["ink"], 255))
        d.text((X0 - 24, 160), "‹", font=self.font("marker", 70), fill=(*self.acc, 255), anchor="lm")
        d.ellipse((X0 + 26, 116, X0 + 114, 204), fill=(*self.acc, 255))
        layer.alpha_composite(icon_img("robot", 70, (255, 255, 255), (255, 255, 255)), (X0 + 35, 125))
        d.text((X0 + 136, 142), self.cfg["brand"]["name"], font=self.font("marker", 40), fill=(*self.p["ink"], 255), anchor="lm")
        d.ellipse((X0 + 138, 182, X0 + 152, 196), fill=(*self.p["good"], 255))
        d.text((X0 + 162, 189), "online · fact-checked", font=self.font("note", 28), fill=(*self.p["soft"], 255), anchor="lm")
        return layer

    def _progress(self, fr: Image.Image, idx: int, t: float):
        d = ImageDraw.Draw(fr)
        d.text((X1 + 30, 160), f"{idx + 1}/{self.n}", font=self.font("note_bold", 30), fill=(*self.p["soft"], 255), anchor="rm")

    def _bubble(self, lines: list, font, ink, fill, width: int | None = None) -> Image.Image:
        pad = 30
        lh = int(font.size * 1.3)
        w = width or int(max(font.getlength(l) for l in lines)) + pad * 2
        h = lh * len(lines) + pad * 2 - int(font.size * .3)
        im = Image.new("RGBA", (w + 16, h + 10), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((16, 0, w + 16, h), radius=34, fill=(*fill, 255))
        d.polygon([(16, h - 40), (0, h + 6), (44, h - 8)], fill=(*fill, 255))  # tail
        for i, ln in enumerate(lines):
            d.text((16 + pad, pad + i * lh), ln, font=font, fill=(*ink, 255))
        return im

    def build_scene(self, scene: dict):
        beat = scene.get("type", "point")
        st = LAYOUT.get(beat, beat)
        os_ = scene.get("on_screen", {}) or {}
        p, ink, acc = self.p, self.p["ink"], self.acc
        L = {"type": st, "beat": beat, "items": [], "marks": [], "visual": None, "stat": None,
             "follow_at": None, "mood": MOOD.get(beat, "neutral"), "point_at": None, "typing": (300, 0.0, 0.45)}
        items, y, t = L["items"], 300, 0.5
        white = (255, 255, 255)
        bw = self.BUBBLE_W - 60
        vis = os_.get("visual") if isinstance(os_.get("visual"), dict) else None

        def pop(img, at):
            items.append({"img": img, "x": X0 - 30, "y": y, "start": at, "dur": 0, "mode": "pop"})

        if st in ("hook", "point", "outro"):
            f = self.font("marker", 58 if st == "hook" else 52)
            im = self._bubble(wrap(os_.get("headline", ""), f, bw)[:3], f, ink, white)
            pop(im, t); y += im.height + 22; t += 0.7
            if os_.get("sub"):
                fs = self.font("note", 42)
                im = self._bubble(wrap(os_["sub"], fs, bw)[:3], fs, ink, white)
                pop(im, t); y += im.height + 22; t += 0.5
            if vis:
                L["visual"] = self._build_visual(vis, y + 20, t, items)
            if st == "outro":
                L["follow_at"] = (y + 20, t)
        elif st == "stat":
            stat = str(os_.get("stat", ""))
            fst, _ = self.fit(stat, "marker", 200, 90, bw - 40, 1)
            h = int(fst.size * 1.25) + 50
            L["marks"].append({"kind": "panel", "box": (X0 - 14, y, X0 + bw + 80, y + h), "fill": white, "radius": 34, "start": t})
            L.update(stat=stat, stat_font=fst, num=numeric_parts(stat), stat_y=y + 20, stat_t=t)
            y += h + 22; t += 0.6
            if os_.get("sub"):
                fs = self.font("note", 42)
                im = self._bubble(wrap(os_["sub"], fs, bw)[:2], fs, ink, white)
                pop(im, t + 0.4); y += im.height + 22
        elif st == "verify":
            fb = self.font("note_bold", 42)
            im = self._bubble(["Fact check"], self.font("marker", 46), white, acc)
            pop(im, t); y += im.height + 22; t += 0.6
            for b in (os_.get("bullets") or [])[:3]:
                lines = wrap(b, fb, bw - 70)
                pad = " " * 8  # room for the green tick
                im = self._bubble([pad + l for l in lines], fb, ink, white)
                d = ImageDraw.Draw(im)
                d.ellipse((50, 30, 94, 74), fill=(*p["good"], 255))
                d.line([(60, 52), (70, 62), (86, 42)], fill=(255, 255, 255, 255), width=6, joint="curve")
                pop(im, t); y += im.height + 18; t += 0.8
        return L

    def draw_scene(self, fr: Image.Image, L: dict, t: float, dur: float):
        ty, t0, t1 = L.get("typing", (0, 0, 0))
        if t0 <= t < t1:  # "typing..." bubble with bouncing dots
            d = ImageDraw.Draw(fr)
            d.rounded_rectangle((X0 - 14, ty, X0 + 150, ty + 76), radius=34, fill=(255, 255, 255, 255))
            for i in range(3):
                dy = 8 * math.sin(t * 12 - i * 0.9)
                cx = X0 + 30 + i * 36
                d.ellipse((cx - 10, ty + 38 - 10 + dy, cx + 10, ty + 38 + 10 + dy), fill=(*self.p["soft"], 255))
        super().draw_scene(fr, L, t, dur)

    def _follow(self, fr: Image.Image, y: float, start: float, t: float, out: float):
        d = ImageDraw.Draw(fr)
        f = self.font("note_bold", 38)
        x = X0
        for i, (label, filled) in enumerate(((f"Follow {self.cfg['brand']['handle']}", True), ("Share", False))):
            pp = prog(t, start + i * 0.25, 0.3)
            if pp <= 0:
                continue
            a = int(255 * pp * out)
            w = f.getlength(label) + 64
            if filled:
                d.rounded_rectangle((x, y, x + w, y + 84), radius=42, fill=(*self.acc, a))
                d.text((x + w / 2, y + 42), label, font=f, fill=(255, 255, 255, a), anchor="mm")
            else:
                d.rounded_rectangle((x, y, x + w, y + 84), radius=42, outline=(*self.acc, a), width=4)
                d.text((x + w / 2, y + 42), label, font=f, fill=(*self.acc, a), anchor="mm")
            x += w + 20
