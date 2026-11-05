"""Learning-tutorial video template (config: video.template = "tutorial").

A notebook page with marker headlines, a lesson tracker ("Step 3 of 8"), hand-drawn
graphics and a cartoon robot teacher who talks with the voiceover, blinks, changes
expression with the scene and points at each graphic. The teacher is clearly a cartoon:
it never depicts a real person.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .render import CW, FONTS, LAYOUT, SceneRenderer, hex_rgb, numeric_parts, prog, put, step_number, text_img, wrap

SYS = Path("/System/Library/Fonts")
FACES = {  # first that exists wins; Inter (bundled) keeps it working off macOS
    "marker": [(SYS / "MarkerFelt.ttc", 1), (FONTS / "InterDisplay-Black.otf", 0)],
    "note": [(SYS / "Supplemental" / "ChalkboardSE.ttc", 1), (FONTS / "Inter-SemiBold.otf", 0)],
    "note_bold": [(SYS / "Supplemental" / "ChalkboardSE.ttc", 2), (SYS / "Supplemental" / "ChalkboardSE.ttc", 1), (FONTS / "InterDisplay-Bold.otf", 0)],
    "caption": [(SYS / "Avenir Next.ttc", 8), (SYS / "Avenir Next.ttc", 0), (FONTS / "InterDisplay-ExtraBold.otf", 0)],
}
PAPER = {"paper": "#FBF7EE", "rule": "#D9E4F2", "margin": "#F2A7A7", "ink": "#1F2A44", "soft": "#6B7280",
         "card": "#FFFFFF", "highlight": "#FFE45C", "good": "#2BA84A", "warn": "#E5484D"}
ACCENT = {"launch": "#2F6FED", "product": "#2F6FED", "policy": "#E8590C", "debate": "#E8590C",
          "research": "#0E9F9A", "security": "#E5484D", "business": "#7C3AED"}
KICKER = {"hook": "TODAY'S QUESTION", "setup": "THE BASICS", "twist": "PLOT TWIST", "stat": "BY THE NUMBERS",
          "example": "THINK OF IT LIKE", "stakes": "WHY YOU SHOULD CARE", "verify": "REALITY CHECK",
          "outro": "LESSON IN ONE LINE", "point": "WHAT HAPPENED"}
MOOD = {"hook": "curious", "twist": "surprised", "stat": "surprised", "verify": "serious", "outro": "happy"}
X0, X1 = 84, 948            # writing column inside the page (right side clear of Shorts buttons)
CARD = (44, 238, 1036, 1190)
CONTENT_BOTTOM = 985        # graphics stop above the teacher
CAPTION_Y = 1318
SOURCES_Y = 1452
TEACHER = (70, 1010)        # teacher's top-left; it sits on the page's bottom edge


@lru_cache(maxsize=512)
def load_font(cands: tuple, size: int) -> ImageFont.FreeTypeFont:
    for path, index in cands:
        if Path(path).exists():
            try:
                return ImageFont.truetype(str(path), size, index=index)
            except OSError:
                continue
    return ImageFont.load_default()


@lru_cache(maxsize=256)
def face(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path, index in FACES[kind]:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size, index=index)
            except OSError:
                continue
    return ImageFont.load_default()


def fit(text: str, kind: str, max_size: int, min_size: int, max_w: int, max_lines: int):
    size = max_size
    while size > min_size:
        f = face(kind, size)
        lines = wrap(text, f, max_w)
        if len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines):
            return f, lines
        size -= 4
    f = face(kind, min_size)
    return f, wrap(text, f, max_w)


def wobble_line(d: ImageDraw.ImageDraw, pts, fill, width: int, seed: int = 0):
    """A slightly hand-drawn line through pts."""
    rng = np.random.default_rng(seed)
    out = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        n = max(2, int(math.hypot(x2 - x1, y2 - y1) // 18))
        for i in range(n):
            t = i / n
            out.append((x1 + (x2 - x1) * t + rng.normal(0, 0.8), y1 + (y2 - y1) * t + rng.normal(0, 0.8)))
    out.append(pts[-1])
    d.line(out, fill=fill, width=width, joint="curve")


# ------------------------------------------------------------------ icons
# Simple line icons drawn in code (no downloads, no licences). Each draws into a
# size x size box in the given ink colour.
def _icon(d: ImageDraw.ImageDraw, name: str, x: int, y: int, s: int, ink, acc):
    w = max(3, s // 22)
    c = (x + s / 2, y + s / 2)
    def L(*p): d.line(list(p), fill=ink, width=w, joint="curve")
    def E(b, fill=None): d.ellipse(b, outline=ink, width=w, fill=fill)
    def R(b, fill=None, r=8): d.rounded_rectangle(b, radius=r, outline=ink, width=w, fill=fill)
    if name == "cloud":
        for b in ((x + s*.1, y + s*.4, x + s*.5, y + s*.8), (x + s*.3, y + s*.2, x + s*.75, y + s*.65), (x + s*.55, y + s*.4, x + s*.92, y + s*.8)):
            d.ellipse(b, fill=(*acc, 70))
        for b in ((x + s*.1, y + s*.4, x + s*.5, y + s*.8), (x + s*.3, y + s*.2, x + s*.75, y + s*.65), (x + s*.55, y + s*.4, x + s*.92, y + s*.8)):
            E(b)
    elif name == "rain":
        _icon(d, "cloud", x, y - s*.15, s, ink, acc)
        for i in range(3):
            L((x + s*(.3 + .2*i), y + s*.75), (x + s*(.25 + .2*i), y + s*.92))
    elif name == "sun":
        E((x + s*.3, y + s*.3, x + s*.7, y + s*.7), fill=(*acc, 90))
        for k in range(8):
            a = k * math.pi / 4
            L((c[0] + math.cos(a)*s*.3, c[1] + math.sin(a)*s*.3), (c[0] + math.cos(a)*s*.45, c[1] + math.sin(a)*s*.45))
    elif name == "house":
        d.polygon([(x + s*.1, y + s*.5), (c[0], y + s*.12), (x + s*.9, y + s*.5)], outline=ink, width=w, fill=(*acc, 60))
        R((x + s*.2, y + s*.5, x + s*.8, y + s*.9), r=4)
        R((x + s*.42, y + s*.65, x + s*.58, y + s*.9), r=2)
    elif name == "boards":
        _icon(d, "house", x, y, s, ink, acc)
        L((x + s*.18, y + s*.6), (x + s*.82, y + s*.78)); L((x + s*.18, y + s*.78), (x + s*.82, y + s*.6))
    elif name == "building":  # government / institution
        d.polygon([(x + s*.1, y + s*.32), (c[0], y + s*.1), (x + s*.9, y + s*.32)], outline=ink, width=w, fill=(*acc, 60))
        for i in range(4):
            L((x + s*(.22 + .19*i), y + s*.38), (x + s*(.22 + .19*i), y + s*.8))
        L((x + s*.1, y + s*.86), (x + s*.9, y + s*.86))
    elif name == "chip":
        R((x + s*.25, y + s*.25, x + s*.75, y + s*.75), fill=(*acc, 60))
        for i in range(3):
            o = s*(.35 + .15*i)
            L((x + o, y + s*.1), (x + o, y + s*.25)); L((x + o, y + s*.75), (x + o, y + s*.9))
            L((x + s*.1, y + o), (x + s*.25, y + o)); L((x + s*.75, y + o), (x + s*.9, y + o))
    elif name == "server":  # data centre
        for i in range(3):
            R((x + s*.15, y + s*(.12 + .26*i), x + s*.85, y + s*(.32 + .26*i)), fill=(*acc, 50), r=5)
            d.ellipse((x + s*.24, y + s*(.19 + .26*i), x + s*.3, y + s*(.25 + .26*i)), fill=ink)
    elif name == "chart_down":
        L((x + s*.12, y + s*.12), (x + s*.12, y + s*.88), (x + s*.9, y + s*.88))
        L((x + s*.2, y + s*.3), (x + s*.42, y + s*.5), (x + s*.58, y + s*.42), (x + s*.84, y + s*.78))
        d.polygon([(x + s*.84, y + s*.78), (x + s*.72, y + s*.76), (x + s*.84, y + s*.64)], fill=(*acc, 255))
    elif name == "chart_up":
        L((x + s*.12, y + s*.12), (x + s*.12, y + s*.88), (x + s*.9, y + s*.88))
        L((x + s*.2, y + s*.75), (x + s*.42, y + s*.55), (x + s*.58, y + s*.63), (x + s*.84, y + s*.25))
        d.polygon([(x + s*.84, y + s*.25), (x + s*.72, y + s*.27), (x + s*.84, y + s*.4)], fill=(*acc, 255))
    elif name == "dollar":
        E((x + s*.12, y + s*.12, x + s*.88, y + s*.88), fill=(*acc, 60))
        f = face("marker", int(s * .55))
        d.text(c, "$", font=f, fill=ink, anchor="mm")
    elif name == "law":  # document / rule
        R((x + s*.22, y + s*.1, x + s*.78, y + s*.9), fill=(*acc, 40), r=6)
        for i in range(4):
            L((x + s*.32, y + s*(.28 + .14*i)), (x + s*(.68 if i < 3 else .5), y + s*(.28 + .14*i)))
    elif name == "warning":
        d.polygon([(c[0], y + s*.1), (x + s*.92, y + s*.88), (x + s*.08, y + s*.88)], outline=ink, width=w, fill=(*acc, 80))
        L((c[0], y + s*.38), (c[0], y + s*.64)); d.ellipse((c[0] - w, y + s*.74 - w, c[0] + w, y + s*.74 + w), fill=ink)
    elif name == "globe":
        E((x + s*.12, y + s*.12, x + s*.88, y + s*.88), fill=(*acc, 50))
        d.ellipse((x + s*.35, y + s*.12, x + s*.65, y + s*.88), outline=ink, width=w)
        L((x + s*.12, c[1]), (x + s*.88, c[1]))
    elif name == "school":
        d.polygon([(x + s*.08, y + s*.4), (c[0], y + s*.15), (x + s*.92, y + s*.4), (c[0], y + s*.62)], outline=ink, width=w, fill=(*acc, 70))
        L((x + s*.28, y + s*.5), (x + s*.28, y + s*.75)); L((x + s*.72, y + s*.5), (x + s*.72, y + s*.75))
        d.arc((x + s*.28, y + s*.6, x + s*.72, y + s*.9), 0, 180, fill=ink, width=w)
    elif name == "search":
        E((x + s*.12, y + s*.12, x + s*.62, y + s*.62), fill=(*acc, 50))
        L((x + s*.56, y + s*.56), (x + s*.88, y + s*.88))
    elif name == "lock":
        d.arc((x + s*.28, y + s*.12, x + s*.72, y + s*.6), 180, 360, fill=ink, width=w)
        L((x + s*.28, y + s*.36), (x + s*.28, y + s*.45)); L((x + s*.72, y + s*.36), (x + s*.72, y + s*.45))
        R((x + s*.18, y + s*.45, x + s*.82, y + s*.9), fill=(*acc, 70), r=6)
    elif name == "code":
        L((x + s*.36, y + s*.28), (x + s*.14, y + s*.5), (x + s*.36, y + s*.72))
        L((x + s*.64, y + s*.28), (x + s*.86, y + s*.5), (x + s*.64, y + s*.72))
        L((x + s*.56, y + s*.2), (x + s*.44, y + s*.8))
    elif name == "person":
        E((x + s*.35, y + s*.1, x + s*.65, y + s*.4), fill=(*acc, 60))
        d.arc((x + s*.18, y + s*.48, x + s*.82, y + s*1.1), 180, 360, fill=ink, width=w)
    elif name == "phone":
        R((x + s*.3, y + s*.08, x + s*.7, y + s*.92), fill=(*acc, 40), r=10)
        L((x + s*.44, y + s*.82), (x + s*.56, y + s*.82))
    elif name == "robot":
        R((x + s*.2, y + s*.25, x + s*.8, y + s*.75), fill=(*acc, 60), r=12)
        d.ellipse((x + s*.33, y + s*.42, x + s*.43, y + s*.52), fill=ink); d.ellipse((x + s*.57, y + s*.42, x + s*.67, y + s*.52), fill=ink)
        L((c[0], y + s*.25), (c[0], y + s*.1)); d.ellipse((c[0] - w*1.5, y + s*.06, c[0] + w*1.5, y + s*.12), fill=(*acc, 255))
    elif name == "check":
        E((x + s*.12, y + s*.12, x + s*.88, y + s*.88), fill=(*acc, 60))
        L((x + s*.3, y + s*.52), (x + s*.45, y + s*.66), (x + s*.72, y + s*.36))
    else:  # unknown name: a labelled dot, never a crash
        E((x + s*.3, y + s*.3, x + s*.7, y + s*.7), fill=(*acc, 90))


ICONS = ("cloud", "rain", "sun", "house", "boards", "building", "chip", "server", "chart_down", "chart_up", "dollar",
         "law", "warning", "globe", "school", "search", "lock", "code", "person", "phone", "robot", "check")


@lru_cache(maxsize=128)
def icon_img(name: str, size: int, ink: tuple, acc: tuple) -> Image.Image:
    big = size * 2  # draw large, scale down for smooth lines
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    _icon(ImageDraw.Draw(im, "RGBA"), name, 0, 0, big, (*ink, 255), acc)
    return im.resize((size, size), Image.LANCZOS)


# ------------------------------------------------------------------ teacher
def draw_teacher(fr: Image.Image, x: int, y: int, mood: str, mouth: float, blink: bool, point: float, t: float, acc, ink):
    """Cartoon robot teacher, about 230 x 230. mouth 0..1 opens with the voice;
    point 0..1 raises the right arm toward the board."""
    s = 1.0
    d = ImageDraw.Draw(fr, "RGBA")
    bob = math.sin(t * 2.2) * 3
    hx, hy = x + 20, y + 24 + bob                      # head box top-left
    # antenna
    d.line([(hx + 95, hy), (hx + 95 - 6 * math.sin(t * 3), hy - 30)], fill=(*ink, 255), width=5)
    d.ellipse((hx + 84 - 6 * math.sin(t * 3), hy - 44, hx + 106 - 6 * math.sin(t * 3), hy - 22), fill=(*acc, 255), outline=(*ink, 255), width=4)
    # body + bow tie
    d.rounded_rectangle((hx + 30, hy + 150, hx + 160, hy + 215), radius=24, fill=(240, 244, 250, 255), outline=(*ink, 255), width=5)
    d.polygon([(hx + 95, hy + 162), (hx + 72, hy + 150), (hx + 72, hy + 176)], fill=(*acc, 255))
    d.polygon([(hx + 95, hy + 162), (hx + 118, hy + 150), (hx + 118, hy + 176)], fill=(*acc, 255))
    # arms: left resting, right pointing up-right toward the board when point > 0
    d.line([(hx + 34, hy + 168), (hx + 4, hy + 206)], fill=(*ink, 255), width=8)
    ang = math.radians(25 + 70 * point)
    ex, ey = hx + 156 + math.cos(ang) * 70, hy + 168 - math.sin(ang) * 70
    d.line([(hx + 156, hy + 168), (ex, ey)], fill=(*ink, 255), width=8)
    d.ellipse((ex - 9, ey - 9, ex + 9, ey + 9), fill=(*acc, 255))
    # head
    d.rounded_rectangle((hx, hy, hx + 190, hy + 150), radius=40, fill=(255, 255, 255, 255), outline=(*ink, 255), width=6)
    d.rounded_rectangle((hx + 18, hy + 22, hx + 172, hy + 128), radius=30, fill=(232, 240, 252, 255))
    # eyes
    for ex0 in (hx + 62, hx + 128):
        if blink:
            d.line([(ex0 - 14, hy + 64), (ex0 + 14, hy + 64)], fill=(*ink, 255), width=6)
        else:
            r = 15 if mood != "surprised" else 18
            d.ellipse((ex0 - r, hy + 64 - r, ex0 + r, hy + 64 + r), fill=(*ink, 255))
            d.ellipse((ex0 - r / 2.6 + 4, hy + 64 - r / 1.6, ex0 + r / 2.6 + 4, hy + 64 - r / 6), fill=(255, 255, 255, 255))
    # brows by mood
    brow = {"curious": (-10, 6), "surprised": (-14, -14), "serious": (8, -8), "happy": (-6, -6)}.get(mood, (-4, -4))
    d.line([(hx + 46, hy + 40 + brow[1] * .4), (hx + 78, hy + 38 + brow[0] * .4)], fill=(*ink, 255), width=5)
    d.line([(hx + 112, hy + 38 + brow[0] * .4 * (1 if mood != "curious" else -1)), (hx + 144, hy + 40 + brow[1] * .4)], fill=(*ink, 255), width=5)
    # mouth: a smile when quiet, opens with the voice
    mx, my = hx + 95, hy + 104
    if mouth > 0.08:
        h = 8 + 26 * mouth
        d.rounded_rectangle((mx - 22, my - h / 2, mx + 22, my + h / 2), radius=int(min(14, h / 2)), fill=(*ink, 255))
        d.rounded_rectangle((mx - 12, my + h / 2 - 9, mx + 12, my + h / 2 - 2), radius=4, fill=(*hex_rgb(PAPER["warn"]), 255))
    else:
        d.arc((mx - 24, my - 18, mx + 24, my + 14), 20, 160, fill=(*ink, 255), width=6)
    # cheeks when happy
    if mood == "happy":
        for cx in (hx + 40, hx + 150):
            d.ellipse((cx - 12, hy + 92, cx + 12, hy + 108), fill=(255, 150, 160, 120))
    # thought bubble on the question
    if mood == "curious":
        f = face("marker", 54)
        bx, by = hx + 200, hy - 30 + bob
        d.ellipse((bx, by, bx + 70, by + 70), fill=(255, 255, 255, 255), outline=(*ink, 255), width=4)
        d.text((bx + 35, by + 37), "?", font=f, fill=(*acc, 255), anchor="mm")


# ------------------------------------------------------------------ photos
def load_cover(path: str, w: int, h: int, over: float = 1.12) -> Image.Image | None:
    """The photo scaled to cover a w x h box with room to zoom (Ken Burns)."""
    try:
        im = Image.open(path).convert("RGB")
    except Exception:  # noqa: BLE001 - a missing or broken file just means no photo
        return None
    s = max(w * over / im.width, h * over / im.height)
    return im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)


def kenburns(cover: Image.Image, w: int, h: int, p: float) -> Image.Image:
    """A slow push-in: p 0..1 over the scene, zoom 1.0 -> 1.08, drifting slightly up."""
    p = max(0.0, min(1.0, p))
    z = 1.0 + 0.08 * p
    cw = cover.width / z / 1.06  # the cover has 12% spare; start just inside it
    ch = cw * h / w
    if ch > cover.height:
        ch = cover.height
        cw = ch * w / h
    x0 = (cover.width - cw) / 2
    y0 = (cover.height - ch) / 2 * (1 - 0.3 * p)
    return cover.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((w, h), Image.BILINEAR)


def scene_photos(scene: dict) -> list[dict]:
    """The scene's approved photos (one or several), existing files only."""
    photos = scene.get("images") or ([scene["image"]] if scene.get("image") else [])
    return [p for p in photos if p.get("path") and Path(p["path"]).exists()]


def slide(n: int, t: float, t0: float, dur: float) -> tuple[int, float, float]:
    """Slideshow timing: which photo is up, how far through its own zoom, and its
    fade-in (0..1) so each photo cross-fades in over 0.3 s."""
    span = max(dur - t0, 0.1) / max(n, 1)
    i = min(n - 1, int(max(t - t0, 0) / span))
    local = (t - t0 - i * span) / span
    return i, local, min(1.0, (t - t0 - i * span) / 0.3) if i else 1.0


class TutorialRenderer(SceneRenderer):
    # --- the skin: other templates (template_skins.py) override these ---
    FACES = FACES
    PALETTE = {**PAPER, "cap_text": PAPER["ink"], "cap_hl": PAPER["highlight"], "cap_hl_text": PAPER["ink"],
               "cap_stroke": PAPER["paper"], "before": "#FFECEC", "after": "#E8F8EC", "bar_dim": "#96A0B4"}
    KICKER = KICKER
    TEACHER = True
    CONTENT_BOTTOM = CONTENT_BOTTOM
    HEADLINE_MARK = "highlight"   # "highlight" | "underline" | None
    CAPTION_STROKE = 0
    STAT_CIRCLE = True            # marker circle drawn around a big number

    def font(self, kind: str, size: int) -> ImageFont.FreeTypeFont:
        return load_font(tuple(self.FACES[kind]), size)

    def fit(self, text: str, kind: str, max_size: int, min_size: int, max_w: int, max_lines: int):
        size = max_size
        while size > min_size:
            f = self.font(kind, size)
            lines = wrap(text, f, max_w)
            if len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines):
                return f, lines
            size -= 4
        f = self.font(kind, min_size)
        return f, wrap(text, f, max_w)

    def _attach_image(self, L: dict, scene: dict, y: int, t0: float):
        photos = scene_photos(scene)
        if not photos:
            return
        im, path = photos[0], photos[0]["path"]
        V = L.get("visual")
        if V and V.get("kind") == "whois":  # a real photo instead of the cartoon avatar
            V["photo"] = load_cover(path, 150, 150, 1.0)
            return
        if V:  # keep the graphic; the photo goes behind it as a soft backdrop
            x0, y0, x1, y1 = self.BACKDROP_BOX
            covers = [(c, p.get("credit", "")) for p in photos if (c := load_cover(p["path"], x1 - x0, y1 - y0)) is not None]
            if covers:
                L["backdrop"] = {"covers": covers, "box": self.BACKDROP_BOX, "t0": 0.0}
            return
        if self.TEACHER:  # beside the teacher: the page is free to its bottom on the right
            x, bottom, max_w = 360, 1110, X1 - 360
        else:
            x, bottom, max_w = X0, self.CONTENT_BOTTOM - 60, CW
        h = min(520, bottom - y)
        if h < 180:
            return
        w = min(max_w, int(h * 1.6))
        h = min(h, int(w / 1.3))
        covers = [(c, p.get("credit", "")) for p in photos if (c := load_cover(p["path"], w, h)) is not None]
        if covers:
            L["image"] = {"covers": covers, "x": x, "y": y, "w": w, "h": h, "t0": t0}

    BACKDROP_BOX = (60, 254, 1020, 1174)  # inside the notebook page
    BACKDROP_ALPHA = 0.16

    def _draw_backdrop(self, fr: Image.Image, B: dict, t: float, dur: float, out: float):
        x0, y0, x1, y1 = B["box"]
        i, local, fade = slide(len(B["covers"]), t, B["t0"], dur)
        cover, credit = B["covers"][i]
        photo = kenburns(cover, x1 - x0, y1 - y0, local).convert("RGBA")
        put(fr, photo, x0, y0, self.BACKDROP_ALPHA * out * prog(t, 0, 0.5) * fade)
        f = self.font("note", 22)
        ImageDraw.Draw(fr).text((x1 - 12, y1 - 14), credit, font=f, fill=(*self.p["soft"], int(220 * out)), anchor="rd")

    def _frame_image(self, photo: Image.Image, credit: str) -> Image.Image:
        """Tutorial: a Polaroid taped onto the page, credit written on the white strip."""
        pad, foot = 18, 64
        card = Image.new("RGBA", (photo.width + pad * 2, photo.height + pad + foot), (255, 255, 255, 255))
        card.paste(photo, (pad, pad))
        d = ImageDraw.Draw(card)
        d.rectangle((0, 0, card.width - 1, card.height - 1), outline=(220, 220, 220, 255), width=2)
        d.text((pad + 4, photo.height + pad + foot / 2), credit, font=self.font("note", 26), fill=(*self.p["soft"], 255), anchor="lm")
        tape = Image.new("RGBA", (160, 44), (255, 236, 150, 190))
        card.alpha_composite(tape.rotate(4, expand=True), (card.width // 2 - 80, 0))
        return card.rotate(-1.5, resample=Image.BICUBIC, expand=True)

    def _draw_image(self, fr: Image.Image, I: dict, t: float, dur: float, out: float):
        if t < I["t0"]:
            return
        pp = prog(t, I["t0"], 0.4)
        i, local, fade = slide(len(I["covers"]), t, I["t0"], dur)
        cover, credit = I["covers"][i]
        if fade < 1 and i:  # cross-fade: the previous photo stays underneath
            prev = self._frame_image(kenburns(I["covers"][i - 1][0], I["w"], I["h"], 1.0), I["covers"][i - 1][1])
            put(fr, prev, I["x"] - 12, I["y"], out)
        framed = self._frame_image(kenburns(cover, I["w"], I["h"], local), credit)
        put(fr, framed, I["x"] - 12, I["y"] + (1 - pp) * 30, out * pp * fade)

    def _kicker_img(self, text: str) -> Image.Image:
        return text_img(text, self.font("note_bold", 34), (*self.acc, 255))

    def _source_chip(self, source: str) -> Image.Image | None:
        """Small tag naming the scene's source: 'Source: Reuters'."""
        f = self.font("note_bold", 24)
        label = f"Source: {source}"
        w = int(f.getlength(label)) + 28
        im = Image.new("RGBA", (w, 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((1, 1, w - 2, 38), radius=19, outline=(*self.p["soft"], 255), width=2, fill=(*self.p["paper"], 255))
        d.text((w / 2, 20), label, font=f, fill=(*self.p["ink"], 255), anchor="mm")
        return im

    def _headline_mark(self, L: dict, x: float, y: float, w: float, size: int, start: float):
        if self.HEADLINE_MARK == "highlight":
            L["marks"].append({"kind": "highlight", "x": x - 8, "y": y + size * .55, "w": w + 16, "h": size * .5, "start": start})
        elif self.HEADLINE_MARK == "underline":
            L["marks"].append({"kind": "underline", "x": x, "y": y + size * 1.08, "w": min(w, 260), "h": 8, "start": start})

    def _overlay(self, fr: Image.Image, idx: int, t: float, gt: float):
        """Per-frame extras drawn over the scene (a news ticker, say)."""

    def __init__(self, cfg: dict, script: dict, n_scenes: int, scene_durations: list[float]):
        self.p = {k: hex_rgb(v) for k, v in self.PALETTE.items()}
        self.acc = hex_rgb(cfg["video"].get("tutorial_accent") or ACCENT.get(script.get("category", ""), "#2F6FED"))
        self.env = None  # loudness per frame of this scene's voice, for the teacher's mouth
        super().__init__(cfg, script, n_scenes, scene_durations)
        self.glows = []

    def set_audio(self, wav: str, fps: int):
        try:
            import soundfile as sf

            x, sr = sf.read(wav, dtype="float32")
            if x.ndim > 1:
                x = x.mean(1)
            n = int(sr / fps)
            r = np.sqrt(np.mean(x[: len(x) // n * n].reshape(-1, n) ** 2, 1))
            self.env = np.clip(r / (np.percentile(r, 95) + 1e-6), 0, 1)
        except Exception:  # noqa: BLE001 - no audio: mouth follows word timings
            self.env = None

    # -- static layers ---------------------------------------------------
    def _background(self) -> Image.Image:
        p = self.p
        im = Image.new("RGBA", (self.W, self.H), (*p["paper"], 255))
        d = ImageDraw.Draw(im)
        # the page: a white card with a soft shadow, notebook rules and a margin line
        sh = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((CARD[0] + 6, CARD[1] + 10, CARD[2] + 6, CARD[3] + 12), radius=28, fill=(60, 50, 30, 60))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))
        d.rounded_rectangle(CARD, radius=28, fill=(*p["card"], 255))
        for yy in range(CARD[1] + 120, CARD[3] - 20, 56):
            d.line([(CARD[0] + 20, yy), (CARD[2] - 20, yy)], fill=(*p["rule"], 255), width=2)
        d.line([(X0 - 18, CARD[1] + 20), (X0 - 18, CARD[3] - 20)], fill=(*p["margin"], 255), width=3)
        for yy in (CARD[1] + 200, CARD[1] + 470, CARD[1] + 740):  # binder holes
            d.ellipse((CARD[0] + 8, yy, CARD[0] + 26, yy + 18), fill=(*p["paper"], 255))
        return im

    def _chrome(self) -> Image.Image:
        layer = Image.new("RGBA", (self.W, 240), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        f = self.font("note_bold", 34)
        label = "LESSON"
        tw = f.getlength(label)
        d.rounded_rectangle((X0 - 30, 88, X0 - 30 + tw + 40, 140), radius=26, fill=(*self.acc, 255))
        d.text((X0 - 10, 114), label, font=f, fill=(255, 255, 255, 255), anchor="lm")
        d.text((X0 + tw + 30, 114), self.cfg["brand"]["name"], font=self.font("note_bold", 36), fill=(*self.p["ink"], 255), anchor="lm")
        date = self.script.get("date_label", "")
        if date:
            d.text((X1 + 40, 114), date.title(), font=self.font("note", 30), fill=(*self.p["soft"], 255), anchor="rm")
        return layer

    def _sources_line(self):
        pubs = []
        for s in self.script.get("sources", []):
            p = (s.get("publisher") or "").strip()
            if p and p not in pubs:
                pubs.append(p)
        if not pubs:
            return None
        f = self.font("note", 28)
        txt = "Sources: " + " · ".join(pubs[:3])
        while f.getlength(txt) > CW and len(pubs) > 1:
            pubs.pop()
            txt = "Sources: " + " · ".join(pubs)
        return text_img(txt, f, (*self.p["soft"], 255))

    # -- per-frame pieces ------------------------------------------------
    def _base(self, gt: float) -> Image.Image:
        fr = self.bg.copy()
        fr.alpha_composite(self.chrome, (0, 0))
        return fr

    def _progress(self, fr: Image.Image, idx: int, t: float):
        d = ImageDraw.Draw(fr)
        d.text((X0 - 30, 196), f"Step {idx + 1} of {self.n}", font=self.font("note_bold", 30), fill=(*self.p["ink"], 255), anchor="lm")
        x = X0 + 190
        for i in range(self.n):
            cx = x + i * 40
            if i < idx:
                d.ellipse((cx - 11, 185, cx + 11, 207), fill=(*self.acc, 255))
            elif i == idx:
                r = 11 + 3 * math.sin(t * 5)
                d.ellipse((cx - r, 196 - r, cx + r, 196 + r), fill=(*self.acc, 255))
            else:
                d.ellipse((cx - 11, 185, cx + 11, 207), outline=(*self.p["rule"], 255), width=4)

    @lru_cache(maxsize=512)
    def _caption_img(self, text_words: tuple, active: int) -> Image.Image:
        f = self.font("caption", 62)
        space = f.getlength(" ")
        parts = []
        for i, w in enumerate(text_words):
            on = i == active
            im = text_img(w, f, (*self.p["cap_hl_text" if on else "cap_text"], 255),
                          stroke=0 if on else self.CAPTION_STROKE, stroke_fill=(*self.p["cap_stroke"],))
            if on:  # highlighter behind the spoken word
                hl = Image.new("RGBA", (im.width + 16, im.height), (0, 0, 0, 0))
                ImageDraw.Draw(hl).rounded_rectangle((0, im.height * .28, im.width + 16, im.height * .92), radius=10, fill=(*self.p["cap_hl"], 255))
                hl.alpha_composite(im, (8, 0))
                im = hl
            parts.append(im)
        total = sum(p.width for p in parts) + space * (len(parts) - 1)
        rows = [parts[: max(1, len(parts) // 2)], parts[max(1, len(parts) // 2):]] if total > CW else [parts]
        row_imgs = []
        for row in rows:
            im = Image.new("RGBA", (int(sum(p.width for p in row) + space * (len(row) - 1)), max(p.height for p in row)), (0, 0, 0, 0))
            x = 0
            for p in row:
                im.alpha_composite(p, (int(x), 0))
                x += p.width + space
            row_imgs.append(im)
        out = Image.new("RGBA", (max(r.width for r in row_imgs), sum(r.height for r in row_imgs)), (0, 0, 0, 0))
        y = 0
        for r in row_imgs:
            out.alpha_composite(r, ((out.width - r.width) // 2, y))
            y += r.height
        return out

    def _captions(self, fr: Image.Image, chunks, t: float):
        for ci, ch in enumerate(chunks):
            start = ch[0]["start"]
            end = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else ch[-1]["end"] + 0.4
            if start <= t < end:
                active = max([i for i, w in enumerate(ch) if w["start"] <= t] or [0])
                im = self._caption_img(tuple(w["word"] for w in ch), active)
                pop = prog(t, start, 0.12)
                put(fr, im, (self.W - im.width) / 2 - 30, CAPTION_Y - im.height / 2 + (1 - pop) * 14, 0.4 + 0.6 * pop)
                return

    # -- scene layouts -----------------------------------------------------
    def _write(self, items, img, x, y, start, dur):
        """Queue an image that is 'written on' left to right."""
        items.append({"img": img, "x": x, "y": y, "start": start, "dur": dur})
        return start + dur

    def build_scene(self, scene: dict):
        beat = scene.get("type", "point")
        st = LAYOUT.get(beat, beat)
        os_ = scene.get("on_screen", {}) or {}
        p, ink, acc = self.p, self.p["ink"], self.acc
        L = {"type": st, "beat": beat, "items": [], "marks": [], "visual": None, "stat": None,
             "follow_at": None, "mood": MOOD.get(beat, "neutral"), "point_at": None}
        items, y, t = L["items"], 300, 0.0

        kick = f"STEP {step_number(self.script, scene)}" if beat == "step" and self.KICKER.get("hook") else self.KICKER.get(beat, "")
        if scene.get("source") and st != "outro":  # where this scene's fact comes from, always visible
            chip = self._source_chip(scene["source"])
            if chip is not None:
                self._write(items, chip, 1004 - chip.width, y + 2, 0.15, 0.25)
        if kick:
            im = self._kicker_img(kick)
            t = self._write(items, im, X0, y, 0.0, 0.3)
            y += im.height + 22

        vis = os_.get("visual") if isinstance(os_.get("visual"), dict) else None
        room = vis or (scene.get("image") or {}).get("path")  # a graphic or photo needs space below
        if st in ("hook", "point", "outro"):
            maxs = {"hook": 84 if room else 104, "point": 84 if room else 96, "outro": 96}[st]
            fh, lines = self.fit(os_.get("headline", ""), "marker", maxs, 56, CW, 2 if room else 3)
            for i, ln in enumerate(lines):
                im = text_img(ln, fh, (*ink, 255))
                if i == len(lines) - 1:  # highlighter stroke (or underline) under the last line
                    self._headline_mark(L, X0, y, im.width, fh.size, t + 0.35)
                t = self._write(items, im, X0, y, t, max(0.25, len(ln) / 30))
                y += int(fh.size * 1.12)
            y += 26
            if os_.get("sub"):
                fs, sl = self.fit(os_["sub"], "note", 46 if not room else 40, 32, CW, 2)
                for ln in sl:
                    t = self._write(items, text_img(ln, fs, (*p["soft"], 255)), X0, y, t + 0.05, max(0.2, len(ln) / 45))
                    y += int(fs.size * 1.35)
            if vis:
                L["visual"] = self._build_visual(vis, y + 26, t + 0.15, items)
                L["point_at"] = t + 0.15
            if st != "outro":
                self._attach_image(L, scene, y + 26, t + 0.15)
                if L.get("image"):
                    L["point_at"] = t + 0.15
            if st == "outro":
                L["follow_at"] = (y + 30, t + 0.2)

        elif st == "stat":
            stat = str(os_.get("stat", ""))
            fst, _ = self.fit(stat, "marker", 240, 100, CW - 60, 1)
            L.update(stat=stat, stat_font=fst, num=numeric_parts(stat), stat_y=y + 10, stat_t=t)
            y += int(fst.size * 1.2) + 30
            if os_.get("sub"):
                fs, sl = self.fit(os_["sub"], "note_bold", 52, 36, CW, 2)
                for ln in sl:
                    t = self._write(items, text_img(ln, fs, (*ink, 255)), X0, y, t + 0.6, max(0.2, len(ln) / 40))
                    y += int(fs.size * 1.35)
            self._attach_image(L, scene, y + 24, t + 0.3)
            L["point_at"] = 0.3

        elif st == "verify":
            fb = self.font("note_bold", 46)
            for i, b in enumerate((os_.get("bullets") or [])[:3]):
                lines = wrap(b, fb, CW - 110)
                h = int(fb.size * 1.3) * len(lines) + 20
                im = Image.new("RGBA", (CW, h), (0, 0, 0, 0))
                dd = ImageDraw.Draw(im)
                dd.rounded_rectangle((4, 8, 60, 64), radius=10, outline=(*ink, 255), width=5)
                for j, ln in enumerate(lines):
                    dd.text((86, 10 + j * int(fb.size * 1.3)), ln, font=fb, fill=(*ink, 255))
                start = t + 0.2 + i * 0.9
                L["marks"].append({"kind": "tick", "x": X0 + 4, "y": y + 8, "start": start + 0.35})
                self._write(items, im, X0, y, start, 0.45)
                y += h + 26
            L["point_at"] = 0.4
        return L

    def _build_visual(self, v: dict, y: int, t0: float, items: list):
        kind = v.get("kind")
        ink, acc = self.p["ink"], self.acc
        V = {"kind": kind, "y": y, "t0": t0}
        if kind == "flow":
            steps = [str(s) for s in (v.get("steps") or [])][:4]
            if not steps:
                return None
            f = self.font("note_bold", 40 if len(steps) <= 3 else 36)
            room = self.CONTENT_BOTTOM - y
            gap = 44 if len(steps) <= 3 else 34
            bh = max(60, min(124, (room - gap * (len(steps) - 1)) // len(steps)))
            if len(steps) * bh + gap * (len(steps) - 1) > room:  # too tight: smaller text, smaller boxes
                f = self.font("note_bold", 32)
                gap, bh = 28, max(52, (room - 28 * (len(steps) - 1)) // len(steps))
            boxes = []
            for i, s in enumerate(steps):
                lines = wrap(s, f, CW - 150)[:2]
                by = y + i * (bh + gap)
                boxes.append({"lines": [text_img(l, f, (*ink, 255)) for l in lines], "y": by, "start": t0 + i * 0.7})
            V.update(boxes=boxes, bh=bh, gap=gap)
        elif kind == "icons":
            its = [i for i in (v.get("items") or []) if isinstance(i, dict)][:3]
            if not its:
                return None
            n = len(its)
            size = 190 if n <= 2 else 170
            f = self.font("note_bold", 36)
            span = CW - 40
            xs = [X0 + 20 + (span - size) * (i / (n - 1) if n > 1 else 0.5) for i in range(n)]
            V.update(items=[{"img": icon_img(str(it.get("icon", "")), size, ink, acc),
                             "label": [text_img(l, f, (*ink, 255)) for l in wrap(str(it.get("label", "")), f, size + 70)[:2]],
                             "x": x, "start": t0 + k * 0.6} for k, (it, x) in enumerate(zip(its, xs))], size=size)
        elif kind == "bars":
            rows = [r for r in v.get("items", []) if isinstance(r.get("value"), (int, float))][:4]
            if not rows:
                return None
            V.update(rows=rows, top=max(r["value"] for r in rows) or 1, pre=v.get("prefix", ""), suf=v.get("suffix", ""))
        elif kind == "grid":
            if not isinstance(v.get("percent"), (int, float)):
                return None
            V.update(pct=max(0.0, min(100.0, float(v["percent"]))), label=str(v.get("label", "")))
        elif kind == "diff":
            V.update(before=[str(x) for x in (v.get("removed") or [])][:3], after=[str(x) for x in (v.get("added") or [])][:3])
        elif kind == "whois":
            V.update(name=str(v.get("name", "")), role=str(v.get("role", "")), org=str(v.get("org", "")), quote=str(v.get("quote", "")))
        else:
            return None
        return V

    def _draw_visual(self, fr: Image.Image, V: dict, t: float, out: float):
        ink, acc, p = self.p["ink"], self.acc, self.p
        d = ImageDraw.Draw(fr, "RGBA")
        a = int(255 * out)
        y, t0 = V["y"], V["t0"]
        if t < t0:
            return
        if V["kind"] == "flow":
            bh, gap = V["bh"], V["gap"]
            for i, b in enumerate(V["boxes"]):
                pp = prog(t, b["start"], 0.35)
                if pp <= 0:
                    break
                x0, y0 = X0 + 10, b["y"] + (1 - pp) * 20
                self._tint(fr, (x0, y0, X1 - 10, y0 + bh), acc, int(40 * out * pp))
                d.rounded_rectangle((x0, y0, X1 - 10, y0 + bh), radius=22, outline=(*ink, int(a * pp)), width=4)
                d.ellipse((x0 + 22, y0 + bh / 2 - 26, x0 + 74, y0 + bh / 2 + 26), fill=(*acc, int(a * pp)))
                d.text((x0 + 48, y0 + bh / 2), str(i + 1), font=self.font("note_bold", 34), fill=(255, 255, 255, int(a * pp)), anchor="mm")
                th = sum(im.height for im in b["lines"])
                ty = y0 + (bh - th) / 2
                for im in b["lines"]:
                    put(fr, im, x0 + 96, ty, out * pp)
                    ty += im.height
                if i + 1 < len(V["boxes"]):  # arrow drawing down to the next box
                    ap = prog(t, b["start"] + 0.35, 0.3)
                    if ap > 0:
                        ax, ay1 = (x0 + X1) / 2 - 5, y0 + bh + 6
                        ay2 = ay1 + (gap - 12) * ap
                        wobble_line(d, [(ax, ay1), (ax, ay2)], (*ink, a), 5, seed=i)
                        if ap > 0.95:
                            d.polygon([(ax - 14, ay2 - 14), (ax + 14, ay2 - 14), (ax, ay2 + 4)], fill=(*ink, a))
        elif V["kind"] == "icons":
            size = V["size"]
            items = V["items"]
            for k, it in enumerate(items):
                pp = prog(t, it["start"], 0.4)
                if pp <= 0:
                    break
                sc = 0.6 + 0.4 * pp + 0.06 * math.sin(max(0, t - it["start"] - 0.4) * 3) * (pp > 0.99)
                im = it["img"].resize((max(1, int(size * sc)), max(1, int(size * sc))))
                put(fr, im, it["x"] + (size - im.width) / 2, y + (size - im.height) / 2, out * pp)
                ly = y + size + 18
                for lab in it["label"]:
                    put(fr, lab, it["x"] + (size - lab.width) / 2, ly, out * pp)
                    ly += lab.height
                if k + 1 < len(items):  # arrow to the next picture
                    ap = prog(t, it["start"] + 0.4, 0.25)
                    if ap > 0:
                        ax1, ax2 = it["x"] + size + 6, items[k + 1]["x"] - 6
                        ay = y + size / 2
                        xe = ax1 + (ax2 - ax1) * ap
                        wobble_line(d, [(ax1, ay), (xe, ay)], (*ink, a), 5, seed=k + 7)
                        if ap > 0.95:
                            d.polygon([(xe - 4, ay - 14), (xe - 4, ay + 14), (xe + 14, ay)], fill=(*ink, a))
        elif V["kind"] == "bars":
            f, fv = self.font("note_bold", 36), self.font("marker", 44)
            lw = int(max(f.getlength(str(r["label"])[:10]) for r in V["rows"])) + 24
            full = X1 - 170 - (X0 + lw)
            for i, r in enumerate(V["rows"]):
                pp = prog(t, t0 + i * 0.3, 0.9)
                ry = y + i * 90
                d.text((X0, ry + 30), str(r["label"])[:10], font=f, fill=(*ink, a), anchor="lm")
                w = full * (r["value"] / V["top"]) * pp
                col = acc if r["value"] == V["top"] else p["bar_dim"]
                if w > 2:
                    d.rounded_rectangle((X0 + lw, ry + 6, X0 + lw + w, ry + 56), radius=12, fill=(*col, a), outline=(*ink, a), width=4)
                val = r["value"] * pp
                s = f"{val:,.0f}" if float(r["value"]).is_integer() else f"{val:,.1f}"
                d.text((X1, ry + 30), f"{V['pre']}{s}{V['suf']}", font=fv, fill=(*ink, a), anchor="rm")
        elif V["kind"] == "grid":
            pp = prog(t, t0, 1.3)
            lit = round(V["pct"] * pp)
            cell, gp = 26, 8
            for i in range(100):
                cx, cy = X0 + (i % 10) * (cell + gp), y + (i // 10) * (cell + gp)
                d.ellipse((cx, cy, cx + cell, cy + cell), fill=(*(acc if i < lit else p["rule"]), a))
            pct = V["pct"] * pp
            d.text((X0 + 380, y + 70), f"{pct:.0f}%", font=self.font("marker", 120), fill=(*acc, a), anchor="lm")
            for j, ln in enumerate(wrap(V["label"], self.font("note_bold", 38), CW - 390)[:3]):
                d.text((X0 + 380, y + 170 + j * 50), ln, font=self.font("note_bold", 38), fill=(*ink, a), anchor="lm")
        elif V["kind"] == "diff":
            f = self.font("note_bold", 36)
            colw = (CW - 30) // 2
            for c, (title, rows, col) in enumerate((("Before", V["before"], p["before"]), ("After", V["after"], p["after"]))):
                pp = prog(t, t0 + c * 0.7, 0.4)
                if pp <= 0 or not rows:
                    continue
                x0 = X0 + c * (colw + 30)
                lines = [l for r in rows for l in wrap(r, f, colw - 40)]
                h = 80 + len(lines) * 48
                rot = Image.new("RGBA", (colw + 20, h + 20), (0, 0, 0, 0))
                rd = ImageDraw.Draw(rot)
                rd.rectangle((10, 10, colw + 10, h + 10), fill=(*col, 255), outline=(*ink, 255), width=3)
                rd.text((30, 30), title, font=self.font("marker", 44), fill=(*(p["warn"] if c == 0 else p["good"]), 255))
                for j, ln in enumerate(lines):
                    rd.text((30, 88 + j * 48), ln, font=f, fill=(*ink, 255))
                rot = rot.rotate(-2 if c == 0 else 2, resample=Image.BICUBIC, expand=True)
                put(fr, rot, x0 - 10, y + (1 - pp) * 30, out * pp)
        elif V["kind"] == "whois":
            pp = prog(t, t0, 0.4)
            self._tint(fr, (X0, y, X1, y + 200), acc, int(36 * out * pp), 24)
            d.rounded_rectangle((X0, y, X1, y + 200), radius=24, outline=(*ink, int(a * pp)), width=4)
            if V.get("photo") is not None:  # real photo, cropped round
                ph = V["photo"].crop((0, 0, 150, 150))
                mask = Image.new("L", (150, 150), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, 149, 149), fill=255)
                disc = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
                disc.paste(ph, (0, 0), mask)
                put(fr, disc, X0 + 22, y + 26, out * pp)
            else:
                put(fr, icon_img("person", 150, ink, acc), X0 + 22, y + 26, out * pp)
            f1, f2 = self.font("marker", 54), self.font("note", 36)
            d.text((X0 + 200, y + 62), V["name"], font=f1, fill=(*ink, int(a * pp)), anchor="lm")
            d.text((X0 + 200, y + 128), ", ".join(x for x in (V["role"], V["org"]) if x), font=f2, fill=(*p["soft"], int(a * pp)), anchor="lm")
            if V["quote"]:
                qp = prog(t, t0 + 0.6, 0.5)
                fq = self.font("note_bold", 42)
                for j, ln in enumerate(wrap(f"“{V['quote']}”", fq, CW - 40)[:3]):
                    d.text((X0 + 10, y + 250 + j * 56), ln, font=fq, fill=(*acc, int(a * qp)))

    def _tint(self, fr: Image.Image, box, rgb, alpha: int, radius: int = 22):
        layer = Image.new("RGBA", fr.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=(*rgb, alpha))
        fr.alpha_composite(layer)

    # -- drawing ----------------------------------------------------------
    def draw_scene(self, fr: Image.Image, L: dict, t: float, dur: float):
        out = 1 - prog(t, dur - 0.15, 0.15)
        d = ImageDraw.Draw(fr, "RGBA")
        if L.get("backdrop"):  # faint photo first, everything else on top
            self._draw_backdrop(fr, L["backdrop"], t, dur, out)
        for m in L["marks"]:  # highlighter strokes, underlines and panels sit under the writing
            if t < m["start"]:
                continue
            pp = prog(t, m["start"], 0.4)
            if m["kind"] == "highlight":
                d.rounded_rectangle((m["x"], m["y"], m["x"] + m["w"] * pp, m["y"] + m["h"]), radius=12, fill=(*self.p["highlight"], int(200 * out)))
            elif m["kind"] == "underline":
                d.rectangle((m["x"], m["y"], m["x"] + m["w"] * pp, m["y"] + m["h"]), fill=(*self.acc, int(255 * out)))
            elif m["kind"] == "panel":
                self._panel(fr, m, prog(t, m["start"], 0.25) * out)
        if L.get("visual"):
            self._draw_visual(fr, L["visual"], t, out)
        if L.get("image"):
            self._draw_image(fr, L["image"], t, dur, out)
        for it in L["items"]:
            if t < it["start"]:
                break
            if it.get("mode") == "pop":  # bubbles fade and rise in instead of being written on
                pp = prog(t, it["start"], 0.25)
                put(fr, it["img"], it["x"], it["y"] + (1 - pp) * 24, out * pp)
                continue
            pp = min(1.0, (t - it["start"]) / it["dur"]) if it["dur"] > 0 else 1.0
            im = it["img"] if pp >= 1 else it["img"].crop((0, 0, max(1, int(it["img"].width * pp)), it["img"].height))
            put(fr, im, it["x"], it["y"], out)
        for m in L["marks"]:
            if m["kind"] == "tick" and t >= m["start"]:
                pp = prog(t, m["start"], 0.3)
                x, y = m["x"], m["y"]
                pts = [(x + 10, y + 30), (x + 24, y + 46), (x + 52, y + 8)]
                seg = pts[:2] if pp < 0.5 else pts
                wobble_line(d, seg, (*self.p["good"], int(255 * out)), 8, seed=3)
        if L["stat"] is not None and t >= L["stat_t"]:
            n, pp = L["num"], prog(t, L["stat_t"], 0.9)
            if n and pp < 1:
                pre, val, suf, dec, comma = n
                s = f"{val * pp:,.{dec}f}" if comma else f"{val * pp:.{dec}f}"
                txt = f"{pre}{s}{suf}"
            else:
                txt = L["stat"]
            f = L["stat_font"]
            d.text((X0, L["stat_y"]), txt, font=f, fill=(*self.acc, int(255 * out)))
            w = f.getlength(L["stat"])
            cp = prog(t, L["stat_t"] + 0.9, 0.5)  # a marker circle around the number
            if cp > 0 and self.STAT_CIRCLE:
                bx = (X0 - 30, L["stat_y"] - 10, X0 + w + 30, L["stat_y"] + f.size * 1.15)
                d.arc(bx, -100, -100 + 360 * cp, fill=(*self.p["ink"], int(255 * out)), width=6)
        if L["follow_at"] and t >= L["follow_at"][1]:
            self._follow(fr, *L["follow_at"], t, out)

    def _panel(self, fr: Image.Image, m: dict, a: float):
        """A rounded background box (chat bubbles); m: box, fill, radius."""
        if a <= 0:
            return
        layer = Image.new("RGBA", fr.size, (0, 0, 0, 0))
        x0, y0, x1, y1 = m["box"]
        dy = (1 - min(1.0, a)) * 24
        ImageDraw.Draw(layer).rounded_rectangle((x0, y0 + dy, x1, y1 + dy), radius=m.get("radius", 28), fill=(*m["fill"], int(255 * a)))
        fr.alpha_composite(layer)

    def _follow(self, fr: Image.Image, y: float, start: float, t: float, out: float):
        if True:
            pp = prog(t, start, 0.3)
            note = Image.new("RGBA", (520, 150), (0, 0, 0, 0))
            nd = ImageDraw.Draw(note)
            nd.rectangle((10, 10, 510, 140), fill=(*self.p["highlight"], 255), outline=(*self.p["ink"], 255), width=3)
            nd.text((260, 56), "FOLLOW", font=self.font("marker", 62), fill=(*self.p["ink"], 255), anchor="mm")
            nd.text((260, 112), self.cfg["brand"]["handle"], font=self.font("note_bold", 32), fill=(*self.p["ink"], 255), anchor="mm")
            note = note.rotate(-3 + 2 * math.sin(max(0, t - start) * 4), resample=Image.BICUBIC, expand=True)
            put(fr, note, X0 + 40, y + (1 - pp) * 40, pp * out)

    def _mouth(self, t: float, chunks) -> float:
        if self.env is not None:
            i = int(t * self.fps)
            return float(self.env[i]) if 0 <= i < len(self.env) else 0.0
        for ch in chunks:  # no audio (previews): open while a word is being said
            for w in ch:
                if w["start"] <= t < w["end"]:
                    return 0.45 + 0.45 * abs(math.sin((t - w["start"]) * 14))
        return 0.0

    def frame(self, idx: int, L: dict, chunks, t: float, global_t: float) -> Image.Image:
        fr = self._base(global_t)
        self._progress(fr, idx, t)
        self.draw_scene(fr, L, t, self.durs[idx])
        blink = (global_t % 3.7) < 0.12
        point = prog(t, L["point_at"], 0.4) if L.get("point_at") is not None else 0.0
        if self.TEACHER:
            draw_teacher(fr, *TEACHER, L["mood"], self._mouth(t, chunks), blink, point, global_t, self.acc, self.p["ink"])
        self._overlay(fr, idx, t, global_t)
        if self.sources is not None and L["type"] != "outro":
            put(fr, self.sources, X0 - 30, SOURCES_Y, 1.0)
        self._captions(fr, chunks, t)
        return fr
