"""把一张照片 + 一段想法，排成一页手账 PNG。"""
from __future__ import annotations

import math
import os
import random

import numpy as np
from functools import lru_cache
from dataclasses import dataclass

from PIL import Image, ImageDraw

from . import decor, doodles, fonts, palettes, sticker
from .cutout import from_mask_png, remove_background
from .planner import Plan

BUBBLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "assets", "bubbles")
STICKER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "assets", "stickers")


@dataclass
class Options:
    width: int = 1200
    height: int = 1800
    palette: str = "ink"          # 预设名 / #RRGGBB / auto
    photo_style: str = "card"     # card | polaroid | cutout | none
    layout: str = "center"        # center | top
    decor: str = "medium"         # minimal | medium | rich
    title: str | None = None
    date: str | None = None
    seed: int = 0
    tolerance: float = 30.0
    rotate: float | None = None
    doodles: bool = True
    font: str = "lemi"
    emoji: bool = True
    cutout_force: bool = False
    tag_style: str = "sticky"
    stickers: tuple[str, ...] = ()
    font_scale: float = 1.5
    bubble: str = ""
    mask_png: bytes | None = None


def _cover(img: Image.Image, w: int, h: int, bias: float = 0.5) -> Image.Image:
    """等比裁切成目标比例（bias 控制裁切时保留的纵向位置）。"""
    src = img.convert("RGB")
    sw, sh = src.size
    scale = max(w / sw, h / sh)
    nw, nh = max(w, int(sw * scale + 0.5)), max(h, int(sh * scale + 0.5))
    src = src.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = int((nh - h) * bias)
    return src.crop((left, top, left + w, top + h))


def _rounded(img: Image.Image, radius: int) -> Image.Image:
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, img.size[0] - 1, img.size[1] - 1), radius=radius, fill=255)
    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", img.size, 0), mask))
    return img


def _target_aspect(img: Image.Image) -> float:
    ar = img.width / img.height
    if ar > 1.15:
        return 4 / 3
    if ar < 0.85:
        return 4 / 5
    return 1.0


def _fit(ar: float, max_w: float, max_h: float) -> tuple[float, float]:
    w, h = max_h * ar, max_h
    if w > max_w:
        w, h = max_w, max_w / ar
    return w, h


def render(plan: Plan | None, image: Image.Image | None, opts: Options | None = None):
    opts = opts or Options()
    if plan is None:
        plan = Plan(title=None, lines=["今天也值得记一笔"], tags=["生活记录"], emoji="✨",
                    palette="ink", date="", weekday="", raw="")

    layout = opts.layout
    if layout == "auto":
        n = len(plan.intro) + len(plan.feel)
        layout = "corner" if n <= 3 else ("split" if n >= 6 else "center")
    opts.layout = layout
    if image is not None and layout == "corner":
        return _render_corner(plan, image, opts)
    if image is not None and layout == "split":
        return _render_split(plan, image, opts)
    return _render_stack(plan, image, opts)


@lru_cache(maxsize=64)
def _load_bubble(name: str):
    path = os.path.join(BUBBLE_DIR, name + ".png")
    if not os.path.exists(path):
        return None
    img = Image.open(path).convert("RGBA")
    box = img.getbbox()
    if box:
        img = img.crop(box)
    # 四周留一圈透明边：后面要从 (0,0) 往里 flood fill 找「外面」，
    # 裁得贴边的话会找不到起点，整块被填成背景色（就是气泡底下那块色）
    pad = max(4, int(max(img.size) * 0.06))
    canvas = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
    canvas.alpha_composite(img, (pad, pad))
    return canvas


def _bubble_tile(name: str, w: int, h: int, pal):
    """气泡图：把轮廓里的空白填成背景色，再把描边盖回去。"""
    base = _load_bubble(name)
    if base is None:
        return None
    b = base.resize((max(1, w), max(1, h)), Image.LANCZOS)
    a = np.asarray(b.getchannel("A")) > 20
    transparent = ~a
    # 从四边向内膨胀，能走到的是「外面」；剩下的是气泡内部 + 描边
    outside = np.zeros_like(transparent)
    outside[0, :] = transparent[0, :]
    outside[-1, :] = transparent[-1, :]
    outside[:, 0] |= transparent[:, 0]
    outside[:, -1] |= transparent[:, -1]
    for _ in range(500):
        nxt = outside.copy()
        nxt[1:, :] |= outside[:-1, :]
        nxt[:-1, :] |= outside[1:, :]
        nxt[:, 1:] |= outside[:, :-1]
        nxt[:, :-1] |= outside[:, 1:]
        nxt &= transparent
        if nxt.sum() == outside.sum():
            break
        outside = nxt
    inside = ~outside
    fill = Image.new("RGBA", (w, h), (*pal.bg, 248))
    filled = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    filled.paste(fill, (0, 0), Image.fromarray((inside * 255).astype(np.uint8), "L"))
    filled.alpha_composite(b)
    return filled


@lru_cache(maxsize=64)
def _load_sticker(name: str):
    for ext in (".png", ".webp"):
        path = os.path.join(STICKER_DIR, name + ext)
        if os.path.exists(path):
            img = Image.open(path).convert("RGBA")
            box = img.getbbox()
            return img.crop(box) if box else img
    return None


def _tint_sticker(img: Image.Image, color) -> Image.Image:
    out = Image.new("RGBA", img.size, (*color[:3], 0))
    out.putalpha(img.getchannel("A").point(lambda v: v * color[3] // 255))
    return out


def _place_stickers(canvas, boxes, names, pal, opts, W, H, S, rng, margin, photo_box, top_y):
    """把选中的贴纸贴到空白处：先压图片角，再找四角。"""
    if not names:
        return 0
    placed = 0
    slots = []
    if photo_box:
        px, py, pw, ph = photo_box
        slots.append((px - 0.10 * W, py + ph - 0.34 * W))
        slots.append((px + pw - 0.26 * W, py + ph - 0.30 * W))
    slots += [
        (W - margin * 0.42 - 0.16 * W, top_y + 0.02 * H),
        (margin * 0.42, top_y + 0.02 * H),
        (margin * 0.40, H - margin * 1.55),
        (W - margin * 0.40 - 0.16 * W, H - margin * 1.55),
    ]
    for name in names[:3]:
        base = _load_sticker(name)
        if base is None:
            continue
        size = int(0.092 * W)
        ar = base.width / max(1, base.height)
        w = size if ar >= 1 else int(size * ar)
        h = int(w / ar) if ar >= 1 else size
        img = base.resize((max(1, w), max(1, h)), Image.LANCZOS)

        arr = np.asarray(img)
        solid = arr[..., 3] > 20
        if solid.any():
            rgb = arr[..., :3][solid].astype(np.float32)
            sat = float(((rgb.max(1) - rgb.min(1)) / np.maximum(rgb.max(1), 1)).mean())
        else:
            sat = 0.0
        if sat < 0.16:                      # 线稿类贴纸，跟着配色走
            img = _tint_sticker(img, (*pal.ink, 190))

        img = img.rotate(rng.uniform(-12, 12), expand=True, resample=Image.BICUBIC)
        for x, y in slots:
            x = max(6, min(x, W - 6 - img.width))
            y = max(6, min(y, H - 6 - img.height))
            if _hits(boxes, x, y, img.width, img.height, pad=14):
                continue
            canvas.alpha_composite(img, (int(x), int(y)))
            boxes.append((x, y, x + img.width, y + img.height))
            slots = [sl for sl in slots if sl != (x, y)]
            placed += 1
            break
    return placed


def _emoji_sticker(canvas, ch, boxes, photo_box, pal, S, rng):
    """把一个 emoji 当贴纸贴：先在图片四角找不压字的位置。"""
    if not ch:
        return
    size = int(72 * S)
    f = fonts.font("emoji", size)
    tile = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    try:
        ImageDraw.Draw(tile).text((size, size), ch, font=f, fill=(255, 255, 255, 255),
                                  anchor="mm", embedded_color=True)
    except Exception:
        return
    tile = tile.crop(tile.getbbox() or (0, 0, size * 2, size * 2))
    tile = tile.rotate(rng.uniform(-12, -4), expand=True, resample=Image.BICUBIC)
    cands = []
    if photo_box:
        px, py, pw, ph = photo_box
        cands += [(px + pw - tile.width * 0.4, py - tile.height * 0.25),
                  (px - tile.width * 0.55, py + ph * 0.18),
                  (px + pw - tile.width * 0.45, py + ph - tile.height * 0.5)]
    cands += [(80 * S, 150 * S), (1200 * S - 80 * S - tile.width, 150 * S)]
    for x, y in cands:
        x = max(8, min(x, 1200 * S - 8 - tile.width))
        y = max(8, min(y, 1800 * S - 8 - tile.height))
        if _hits(boxes, x, y, tile.width, tile.height, pad=10):
            continue
        canvas.alpha_composite(tile, (int(x), int(y)))
        boxes.append((x, y, x + tile.width, y + tile.height))
        return


def _draw_tag(canvas, plan, boxes, photo_box, pal, S, opts):
    """标签最后画：先量出宽度，再从图片四角里挑一个不压文字的位置。"""
    if not plan.tag or not photo_box:
        return
    px, py, pw, ph = photo_box
    f = fonts.font("hand", int(33 * S), opts.font)
    tw = fonts.measure(plan.tag, f, 2 * S) + 46 * S
    th = 56 * S
    cands = [
        (px + pw * 0.02, py + ph * 0.68),          # 左下（压在图上）
        (px + pw * 0.02, py + ph * 0.06),          # 左上
        (px + pw - tw * 0.92, py + ph * 0.68),     # 右下
        (px - tw * 0.28, py + ph * 0.40),          # 左侧外挂
        (px + pw - tw * 0.72, py + ph * 0.06),     # 右上
    ]
    pick = None
    for x, y in cands:
        if not _hits(boxes, x, y, tw, th, pad=6):
            pick = (x, y)
            break
    x, y = pick or cands[0]
    _tag_sticker(canvas, plan.tag, x, y, pal, S, opts, angle=-3.0)


def _tag_sticker(canvas, text, x, y, pal, S, opts, anchor=None, angle=-3.0):
    """标签：便利贴 / 手写 / 胶带，三种样式，可带一条指向主体的引线。"""
    style = getattr(opts, "tag_style", "sticky")
    if not text or style == "none":
        return None
    f = fonts.font("hand", int(33 * S), opts.font)
    tw = fonts.measure(text, f, 2 * S)

    if style == "frame" and opts.bubble and _load_bubble(opts.bubble) is not None:
        base = _load_bubble(opts.bubble)
        bw = int(tw + 96 * S)
        bh = int(bw * base.height / base.width)
        tile = _bubble_tile(opts.bubble, bw, bh, pal)
        fonts.draw_line(tile, bw / 2, bh * 0.56, text, f, (*pal.ink, 255),
                        tracking=2 * S, seed=5, thin=fonts.erode_for(opts.font))
        w, h = bw, bh
    elif style == "hand":
        w, h = int(tw + 40 * S), int(98 * S)
        tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        fonts.draw_line(tile, w / 2, h * 0.56, text, f, (*pal.ink, 255),
                        tracking=2.4 * S, seed=5, thin=fonts.erode_for(opts.font))
        decor.scribble_underline(ImageDraw.Draw(tile), w * 0.14, w * 0.86, h * 0.68,
                                 pal.accent, max(1, int(1.8 * S)), seed=6, amp=1.4 * S)
    elif style == "bubble":
        w, h = int(tw + 84 * S), int(96 * S)
        tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(tile)
        bh = int(h * 0.74)
        d.rounded_rectangle((0, 0, w - 1, bh), radius=int(bh * 0.46),
                            fill=(*pal.bg, 242), outline=(*pal.ink, 225),
                            width=max(2, int(2.4 * S)))
        tail = [(w * 0.58, bh - 2), (w * 0.76, bh - 2), (w * 0.70, h - 2)]
        d.polygon(tail, fill=(*pal.bg, 242))
        d.line([(w * 0.58, bh - 1), (w * 0.70, h - 2), (w * 0.76, bh - 1)],
               fill=(*pal.ink, 225), width=max(2, int(2.4 * S)), joint="curve")
        fonts.draw_line(tile, w / 2, bh * 0.66, text, f, (*pal.ink, 255),
                        tracking=2 * S, seed=5, thin=fonts.erode_for(opts.font))
    elif style == "tape":
        w, h = int(tw + 72 * S), int(66 * S)
        tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        strip = decor.tape(int(w - 16 * S), int(h - 8 * S),
                           palettes.mix(pal.accent, pal.bg, 0.10), alpha=232, seed=13)
        tile.alpha_composite(strip, (int(8 * S), int(4 * S)))
        fonts.draw_line(tile, w / 2, h * 0.64, text, f, (*pal.ink, 255),
                        tracking=2 * S, seed=5, thin=fonts.erode_for(opts.font))
    else:
        w, h = int(tw + 46 * S), int(56 * S)
        tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(tile)
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=int(8 * S),
                            fill=(*palettes.mix(pal.bg, pal.ink, 0.10), 235),
                            outline=(*pal.ink, 90), width=max(1, int(1.4 * S)))
        fonts.draw_line(tile, w / 2, h * 0.70, text, f, (*pal.ink, 255),
                        tracking=1.5 * S, seed=7, thin=fonts.erode_for(opts.font))

    if anchor is not None:
        ax, ay = anchor
        sx, sy = x + w / 2, y + h / 2
        pts = []
        for i in range(17):
            t = i / 16
            pts.append((sx + (ax - sx) * t,
                        sy + (ay - sy) * t + math.sin(t * math.pi) * 18 * S))
        ImageDraw.Draw(canvas).line(pts, fill=(*pal.ink, 110),
                                    width=max(1, int(1.4 * S)), joint="curve")
        r = 4 * S
        ImageDraw.Draw(canvas).ellipse((ax - r, ay - r, ax + r, ay + r),
                                       fill=(*pal.accent, 220))

    tile = tile.rotate(angle, expand=True, resample=Image.BICUBIC)
    canvas.alpha_composite(tile, (int(x - tile.width * 0.12), int(y - tile.height * 0.12)))
    return tile


def _prep_photo(image, opts):
    """决定这张图用什么形态：原图卡片 / 抠图 / 贴纸。"""
    style = opts.photo_style
    photo = image
    meta = {"photo": style, "cutout": None}
    if image is None:
        return "none", None, {**meta, "photo": "none"}
    if style in ("cutout", "sticker"):
        if opts.mask_png:
            cut, score, info = from_mask_png(image, opts.mask_png)
            meta["cutout"] = {**info, "score": 1.0}
            usable = cut is not None
        else:
            cut, score, info = remove_background(image, opts.tolerance)
            meta["cutout"] = {**info, "score": round(score, 3)}
            too_wide = cut is not None and cut.width / cut.height > 1.7
            usable = cut is not None and not too_wide and (score >= 0.45 or opts.cutout_force)
            if cut is not None:
                meta["cutout"]["fallback"] = not usable
        if style == "sticker":
            photo = cut if usable else image
            meta["sticker"] = "silhouette" if usable else "oval"
        elif not usable:
            style = "card"
            photo = image
        else:
            photo = cut
    meta["photo"] = style
    return style, photo, meta


def _render_corner(plan: Plan, image: Image.Image, opts: Options):
    """图放一侧，介绍在另一侧，感受压在下角。"""
    W, H = opts.width, opts.height
    S = W / 1200.0
    rng = random.Random(opts.seed or 7)
    pal = palettes.get(opts.palette, image)
    style, photo, meta = _prep_photo(image, opts)

    canvas = Image.new("RGBA", (W, H), (*pal.bg, 255))
    margin = 104 * S
    f_sans = fonts.font("sans", int(26 * S))
    f_tag = fonts.font("sans", int(23 * S))
    f_body = fonts.font("hand", int(44 * S), opts.font)

    boxes = []
    boxes.append((0, 0, W, margin * 1.9))
    date_txt = opts.date or plan.date
    if plan.weekday and date_txt:
        date_txt = f"{date_txt}   {plan.weekday}"
    if date_txt:
        fonts.draw_line(canvas, W / 2, margin * 1.15, date_txt, f_sans, pal.sub,
                        tracking=7 * S, seed=opts.seed)

    # 标题放左上
    f_title = fonts.font("hand", int(54 * S), opts.font)
    if plan.title:
        tlines = fonts.wrap(plan.title, f_title, W * 0.46)[:2]
        for i, ln in enumerate(tlines):
            fonts.draw_line(canvas, margin, margin * 2.55 + i * 74 * S, ln, f_title,
                            (*pal.ink, 255), tracking=6 * S, align="left", jitter=0.8,
                            seed=opts.seed + 9, thin=fonts.erode_for(opts.font))
        boxes.append((margin, margin * 1.9, margin + W * 0.46, margin * 2.6 + len(tlines) * 74 * S))

    # 图片放右侧
    pw = W * 0.48
    ph = min(H * 0.52, pw / max(0.6, photo.width / photo.height))
    px = int(W - margin - pw)
    py = int(max(margin * 3.3, (H - ph - 220 * S) / 2))
    layer = _card_layer(photo, int(pw), int(ph), style if style in ("card", "polaroid") else "card",
                        pal, opts, rng)
    _lx = int(px + (pw - layer.width) / 2)
    canvas.alpha_composite(layer, (_lx, int(py)))
    meta["photo_box"] = (_lx, int(py), layer.width, layer.height)



    # 左栏：介绍
    col_x = margin
    col_w = px - margin - 60 * S
    if plan.intro:
        txt = "，".join(plan.intro)
        lines = fonts.wrap(txt, f_body, col_w)[:6]
        y = py + 40 * S
        for i, ln in enumerate(lines):
            fonts.draw_line(canvas, col_x, y + i * 78 * S + 55 * S, ln, f_body,
                            (*pal.ink, 255), tracking=4 * S, align="left",
                            jitter=0.8, seed=opts.seed + i, thin=fonts.erode_for(opts.font))
        boxes.append((col_x, y, col_x + col_w, y + len(lines) * 78 * S))

    # 感受：右下 / 左下
    if plan.feel:
        txt = "".join(plan.feel)
        f_feel = fonts.font("hand", int(40 * S), opts.font)
        lines = fonts.wrap(txt, f_feel, W * 0.72)[:3]
        y = py + ph + 130 * S
        half = W * 0.11
        decor.scribble_underline(ImageDraw.Draw(canvas), W / 2 - half, W / 2 + half,
                                 y - 30 * S, pal.accent, max(1, int(1.6 * S)), seed=3)
        for i, ln in enumerate(lines):
            fonts.draw_line(canvas, W / 2, y + i * 74 * S + 46 * S, ln, f_feel,
                            (*pal.accent, 255), tracking=5 * S, jitter=0.9,
                            seed=opts.seed + 40 + i, thin=fonts.erode_for(opts.font))
        boxes.append((W / 2 - W * 0.36, y, W / 2 + W * 0.36, y + len(lines) * 74 * S))

    _footer(canvas, plan, pal, opts, W, H, S, margin, f_tag, boxes, rng)
    _draw_tag(canvas, plan, boxes, meta.get("photo_box"), pal, S, opts)
    return canvas.convert("RGB"), {**meta, "layout": "corner", "size": (W, H)}


def _render_split(plan: Plan, image: Image.Image, opts: Options):
    """左图右文：介绍在上，感受在下，中间一道虚线。"""
    W, H = opts.width, opts.height
    S = W / 1200.0
    rng = random.Random(opts.seed or 7)
    pal = palettes.get(opts.palette, image)
    style, photo, meta = _prep_photo(image, opts)

    canvas = Image.new("RGBA", (W, H), (*pal.bg, 255))
    draw = ImageDraw.Draw(canvas)
    margin = 104 * S
    f_sans = fonts.font("sans", int(26 * S))
    f_tag = fonts.font("sans", int(23 * S))
    f_body = fonts.font("hand", int(42 * S), opts.font)

    boxes = []
    date_txt = opts.date or plan.date
    if plan.weekday and date_txt:
        date_txt = f"{date_txt}   {plan.weekday}"
    if date_txt:
        fonts.draw_line(canvas, margin, margin * 1.15, date_txt, f_sans, pal.sub,
                        tracking=7 * S, align="left", seed=opts.seed)
    boxes.append((0, 0, W, margin * 1.9))

    # 左图
    pw = W * 0.44
    ph = min(H * 0.62, pw / max(0.62, photo.width / photo.height))
    layer = _card_layer(photo, int(pw), int(ph), "card", pal, opts, rng)
    px = int(margin)
    py = int(max(margin * 2.3, (H - ph) / 2 - 40 * S))
    canvas.alpha_composite(layer, (px, py))
    meta["photo_box"] = (px, py, layer.width, layer.height)



    # 右栏
    col_x = margin + pw + 56 * S
    col_w = W - margin - col_x
    y = py + 10 * S
    if plan.intro:
        txt = "，".join(plan.intro)
        lines = fonts.wrap(txt, f_body, col_w)[:7]
        for i, ln in enumerate(lines):
            fonts.draw_line(canvas, col_x, y + i * 76 * S + 52 * S, ln, f_body,
                            (*pal.ink, 255), tracking=4 * S, align="left", jitter=0.8,
                            seed=opts.seed + i, thin=fonts.erode_for(opts.font))
        y += len(lines) * 76 * S + 60 * S
        boxes.append((col_x, py, col_x + col_w, y))

    if plan.feel:
        dline_y = y + 10 * S
        draw.line((col_x, dline_y, col_x + col_w * 0.72, dline_y),
                  fill=(*pal.accent, 180), width=max(1, int(1.6 * S)))
        y += 56 * S
        f_feel = fonts.font("hand", int(38 * S), opts.font)
        lines = fonts.wrap("，".join(plan.feel), f_feel, col_w)[:4]
        for i, ln in enumerate(lines):
            fonts.draw_line(canvas, col_x, y + i * 70 * S + 44 * S, ln, f_feel,
                            (*pal.accent, 255), tracking=4 * S, align="left", jitter=0.8,
                            seed=opts.seed + 50 + i, thin=fonts.erode_for(opts.font))
        boxes.append((col_x, y, col_x + col_w, y + len(lines) * 70 * S))

    _footer(canvas, plan, pal, opts, W, H, S, margin, f_tag, boxes, rng)
    _draw_tag(canvas, plan, boxes, meta.get("photo_box"), pal, S, opts)
    return canvas.convert("RGB"), {**meta, "layout": "split", "size": (W, H)}


def _footer(canvas, plan, pal, opts, W, H, S, margin, f_tag, boxes, rng):
    draw = ImageDraw.Draw(canvas)
    _place_stickers(canvas, boxes, getattr(opts, "stickers", ()), pal, opts,
                    W, H, S, rng, margin, None, margin * 1.6)
    draw.line((margin, H - margin * 0.62, W - margin, H - margin * 0.62),
              fill=pal.faint, width=max(1, int(1.2 * S)))
    _place_doodles(canvas, boxes, None, plan, pal, opts, W, H, S, rng, margin, margin * 1.6)


def _render_stack(plan: Plan, image: Image.Image | None, opts: Options):
    W, H = opts.width, opts.height
    S = W / 1200.0
    rng = random.Random(opts.seed or 7)

    pal = palettes.get(opts.palette, image)
    meta: dict = {"palette": pal.to_dict(), "photo": "none", "cutout": None}

    canvas = Image.new("RGBA", (W, H), (*pal.bg, 255))
    draw = ImageDraw.Draw(canvas)

    # ---------- 准备照片 ----------
    style = opts.photo_style
    photo = image
    if image is not None and style in ("cutout", "sticker"):
        if opts.mask_png:
            cut, score, info = from_mask_png(image, opts.mask_png)
            meta["cutout"] = {**info, "score": 1.0}
            usable = cut is not None
        else:
            cut, score, info = remove_background(image, opts.tolerance)
            meta["cutout"] = {**info, "score": round(score, 3)}
            too_wide = cut is not None and cut.width / cut.height > 1.7
            usable = cut is not None and not too_wide and (score >= 0.45 or opts.cutout_force)
            if cut is not None:
                meta["cutout"]["fallback"] = not usable

        if style == "sticker":
            photo = cut if usable else image
            meta["sticker"] = "silhouette" if usable else "oval"
        elif not usable:
            style = "card"
            photo = image
        else:
            photo = cut
    if image is None:
        style = "none"
    meta["photo"] = style

    # ---------- 字号 ----------
    margin = 104 * S
    fs = max(1.0, float(getattr(opts, "font_scale", 1.5) or 1.0))
    date_size = int(26 * S * fs)
    tag_size = int(23 * S * fs)
    body_size = int(46 * S * fs)
    title_size = int(62 * S * fs)
    title_lead = int(84 * S * fs)

    f_sans = fonts.font("sans", date_size)
    f_tag = fonts.font("sans", tag_size)
    f_title = fonts.font("hand", title_size, opts.font)
    text_w = W - margin * 2

    title_lines: list[str] = []
    if plan.title:
        title_lines = fonts.wrap(plan.title, f_title, text_w * 0.92)
        if len(title_lines) > 2:
            title_lines = title_lines[:2]

    f_body_pre = fonts.font("hand", body_size, opts.font)
    def _rewrap(lines, max_w):
        out = []
        for ln in lines:
            out.extend(fonts.wrap(ln, f_body_pre, max_w))
        return out

    intro_lines = _rewrap(plan.intro, text_w * 0.98)[:6]
    feel_lines = _rewrap(plan.feel, text_w * 0.98)[:4]
    body_lines = intro_lines + feel_lines

    date_txt = opts.date or plan.date
    if plan.weekday and date_txt:
        date_txt = f"{date_txt}   {plan.weekday}"

    date_h = int(32 * S)
    title_h = len(title_lines) * title_lead
    tag_h = 0

    gap_date_title = int(58 * S) if title_lines else 0
    gap_title_photo = int(78 * S) if title_lines else int(138 * S)
    if title_lines and opts.decor == "rich":
        gap_title_photo += int(44 * S)
    gap_photo_body = int(78 * S)
    gap_body_tag = int(28 * S)
    divider_h = int(86 * S) if (intro_lines and feel_lines) else 0

    fixed_base = (date_h + gap_date_title + title_h + gap_title_photo
                  + gap_photo_body + gap_body_tag + tag_h + divider_h)

    # 矮画布（比如 1:1）放不下时：先缩正文字号，再砍行，避免溢出画面
    body_lead = int(67 * S * fs)
    f_body = fonts.font("hand", body_size, opts.font)
    f_emoji_small = fonts.font("emoji", int(body_size * 0.92))
    min_body = int(34 * S * fs)
    limit = H - margin * 2 + 1

    def _measure(body_n: int, lead: int):
        fixed = fixed_base + body_n * lead
        ph = 0.0
        if style != "none" and photo is not None:
            ph = max(min(H - margin * 2 - fixed, H * 0.50), H * 0.24)
        return fixed + ph

    while _measure(len(body_lines), body_lead) > limit:
        if body_size > min_body:
            body_size = max(min_body, body_size - int(3 * S))
            body_lead = int(body_size * 1.45)
            f_body = fonts.font("hand", body_size, opts.font)
            f_emoji_small = fonts.font("emoji", int(body_size * 0.92))
        elif len(body_lines) > 2:
            body_lines = body_lines[:-1]
        else:
            break

    body_h = len(body_lines) * body_lead
    fixed = fixed_base + body_h

    photo_h = 0.0
    photo_w = 0.0
    if style != "none" and photo is not None:
        avail = H - margin * 2 - fixed
        photo_h = max(min(avail, H * 0.44), H * 0.26)
        max_w = W - margin * 2 - 40 * S
        if style == "cutout":
            ar = photo.width / photo.height
            photo_w, photo_h = _fit(ar, max_w * 0.92, min(photo_h, H * 0.52))
        elif style == "sticker":
            ar = photo.width / photo.height
            ch = photo_h * 0.76
            cw = ch * ar
            if cw > max_w * 0.94:
                cw = max_w * 0.94
                ch = cw / ar
                photo_h = ch / 0.76
            photo_w = cw
        else:
            photo_w, photo_h = _fit(_target_aspect(photo), max_w, photo_h)
        _before = photo_h
        fixed += photo_h
        over = fixed - (H - margin * 2)
        if over > 0:                       # 内容超高：先把图压下来，别把日期盖住
            photo_h = max(H * 0.18, photo_h - over)
            if _before > 0:
                photo_w = photo_w * (photo_h / _before)
            fixed -= over

    top_mode = opts.layout == "top"
    y0 = margin if top_mode else margin + (H - margin * 2 - fixed) / 2
    y0 = max(y0, margin * 1.02)

    # ---------- 日期 ----------
    boxes: list[tuple[float, float, float, float]] = []
    y = y0
    if date_txt:
        dw = fonts.measure(date_txt, f_sans, 7 * S)
        boxes.append((W / 2 - dw / 2, y, W / 2 + dw / 2, y + date_h))
        fonts.draw_line(canvas, W / 2, y + date_h * 0.72, date_txt, f_sans,
                        pal.sub, tracking=7 * S, seed=opts.seed)
    y += date_h + gap_date_title

    # ---------- 标题 ----------
    if title_lines:
        twid = max(fonts.measure(ln, f_title, 10 * S) for ln in title_lines)
        boxes.append((W / 2 - twid / 2, y, W / 2 + twid / 2, y + title_h))
        for i, ln in enumerate(title_lines):
            fonts.draw_line(canvas, W / 2, y + (i + 1) * title_lead - 20 * S, ln, f_title,
                            (*pal.ink, 255), tracking=10 * S, jitter=1.0, seed=opts.seed + i,
                            thin=fonts.erode_for(opts.font))
        if opts.decor != "minimal":
            longest = max(title_lines, key=lambda s: fonts.measure(s, f_title, 10 * S))
            tw = fonts.measure(longest, f_title, 10 * S) * 0.88
            decor.scribble_underline(draw, W / 2 - tw / 2, W / 2 + tw / 2,
                                     y + title_h - 4 * S, pal.accent, max(2, int(2.6 * S)),
                                     seed=opts.seed + 3, amp=1.8 * S)
        y += title_h + gap_title_photo

    # ---------- 照片 ----------
    if style != "none" and photo is not None:
        if style == "cutout":
            layer = _cutout_layer(photo, int(photo_w), int(photo_h), pal)
            px = int(W / 2 - layer.width / 2)
            py = int(y + (photo_h - layer.height) / 2)
            canvas.alpha_composite(layer, (px, py))
            meta["photo_box"] = (px, py, layer.width, layer.height)
        elif style == "sticker":
            layer = _sticker_layer(photo, int(photo_w), int(photo_h), pal, opts,
                                   (plan.title or plan.tag or "").strip(), S)
            px = int(W / 2 - layer.width / 2)
            py = int(y)
            canvas.alpha_composite(layer, (px, py))
            meta["photo_box"] = (px, py, layer.width, layer.height)
        else:
            layer = _card_layer(photo, int(photo_w), int(photo_h), style, pal, opts, rng)
            px = int(W / 2 - layer.width / 2)
            py = int(y + photo_h / 2 - layer.height / 2)
            py = max(py, int(margin * 2.12))          # 任何情况下都不盖住日期
            canvas.alpha_composite(layer, (px, py))
            if opts.decor == "rich" and style == "polaroid":
                _tape(canvas, layer, px, py, pal, S, opts)
            meta["photo_box"] = (px, py, layer.width, layer.height)

        if style == "sticker":
            y += layer.height + gap_photo_body * 0.55
        else:
            y += photo_h + gap_photo_body

    # ---------- 正文：介绍 ----------
    bw = max((fonts.measure(ln, f_body, 6.4 * S) for ln in body_lines), default=0)
    boxes.append((W / 2 - bw / 2, y, W / 2 + bw / 2, y + body_h))
    for i, ln in enumerate(intro_lines):
        fonts.draw_line(canvas, W / 2, y + (i + 1) * body_lead - 24 * S, ln, f_body,
                        (*pal.ink, 255), tracking=6.4 * S, jitter=0.9,
                        seed=opts.seed + 100 + i, thin=fonts.erode_for(opts.font))
    y += len(intro_lines) * body_lead

    # ---------- 分隔线 ----------
    if intro_lines and feel_lines:
        dy = y + divider_h * 0.42
        half = text_w * 0.13
        decor.scribble_underline(draw, W / 2 - half, W / 2 + half, dy,
                                 pal.accent, max(1, int(1.6 * S)), seed=opts.seed + 7,
                                 amp=0.8 * S)
        y += divider_h

    # ---------- 正文：感受 ----------
    f_feel = fonts.font("hand", int(body_size * 0.94), opts.font)
    for i, ln in enumerate(feel_lines):
        text = ln
        if (i == len(feel_lines) - 1 and opts.emoji
                and (plan.emojis or [plan.emoji]) and opts.decor != "minimal"):
            cand = f"{ln} {(plan.emojis or [plan.emoji])[0]}"
            if fonts.measure(cand, f_feel, 6.4 * S, f_emoji_small) <= text_w * 0.96:
                text = cand
        fonts.draw_line(canvas, W / 2, y + (i + 1) * body_lead - 24 * S, text, f_feel,
                        (*pal.accent, 255), tracking=6.4 * S, jitter=0.9,
                        seed=opts.seed + 300 + i, emoji_f=f_emoji_small,
                        thin=fonts.erode_for(opts.font))
    y += len(feel_lines) * body_lead + gap_body_tag

    content_bottom = y

    # ---------- 点缀 ----------
    with_doodles = opts.doodles and opts.decor != "minimal"
    if not with_doodles:
        if opts.decor == "rich":
            for (dx, dy, r) in ((0.06, 0.10, 1.0), (0.93, 0.20, 0.65), (0.10, 0.88, 0.55)):
                decor.sparkle(draw, W * dx, H * dy, 12 * S * r, pal.accent, 170)
        elif opts.decor == "medium":
            decor.sparkle(draw, W * 0.905, H * 0.115, 11 * S, pal.accent, 150)

    # ---------- 标签 ----------
    _draw_tag(canvas, plan, boxes, meta.get("photo_box"), pal, S, opts)

    # ---------- emoji 贴纸 ----------
    if opts.emoji and opts.decor != "minimal":
        extra = (plan.emojis or [])[1:]
        if extra:
            _emoji_sticker(canvas, extra[0], boxes, meta.get("photo_box"), pal, S, rng)

    # ---------- 贴纸 ----------
    _place_stickers(canvas, boxes, getattr(opts, "stickers", ()), pal, opts,
                    W, H, S, rng, margin, meta.get("photo_box"), y0)

    # ---------- 简笔画 ----------
    _place_doodles(canvas, boxes, meta.get("photo_box"), plan, pal, opts,
                   W, H, S, rng, margin, y0)

    # 页脚细线
    draw.line((margin, H - margin * 0.62, W - margin, H - margin * 0.62),
              fill=pal.faint, width=max(1, int(1.2 * S)))

    out = canvas.convert("RGB")
    meta["size"] = (W, H)
    meta["style"] = style
    meta["layout"] = opts.layout
    return out, meta


def _hits(boxes, x, y, w, h, pad=18):
    for bx0, by0, bx1, by1 in boxes:
        if x < bx1 + pad and x + w > bx0 - pad and y < by1 + pad and y + h > by0 - pad:
            return True
    return False


def _place_doodles(canvas, boxes, photo_box, plan, pal, opts, W, H, S, rng, margin, top_y):
    """在空白处贴几张手绘简笔画，避开所有文字块。"""
    if not opts.doodles or opts.decor == "minimal":
        return 0
    want = 4 if opts.decor == "rich" else 2
    names = doodles.pick(plan.categories, want + 3, rng)
    small = 0.085 * W
    big = 0.14 * W

    slots: list[tuple[float, float, float, bool, bool]] = []
    bottom = H - margin * 0.98 - small
    jit = 12 * S
    slots += [
        (W - margin * 0.45 - small + rng.uniform(-jit, jit), top_y - small * 0.30 + rng.uniform(0, jit), small, True, True),
        (margin * 0.36 + rng.uniform(-jit, jit), bottom + rng.uniform(-jit, 0), small, True, True),
        (margin * 0.45 + rng.uniform(-jit, jit), top_y - small * 0.30 + rng.uniform(0, jit), small, True, True),
        (W - margin * 0.36 - small + rng.uniform(-jit, jit), bottom + rng.uniform(-jit, 0), small, True, True),
    ]

    placed = 0
    for x, y, size, test, allow_label in slots:
        if placed >= want:
            break
        name = names[placed % len(names)]
        labeled = allow_label and opts.decor == "rich" and size > 96 * S
        img = doodles.render(name, int(size), (*pal.ink, 240), label=labeled,
                             seed=rng.randint(0, 99999))
        img = img.rotate(rng.uniform(-7, 7), expand=True, resample=Image.BICUBIC)
        x = max(4, min(x, W - 4 - img.width))
        y = max(4, min(y, H - 4 - img.height))
        if test and _hits(boxes, x, y, img.width, img.height):
            continue
        canvas.alpha_composite(img, (int(x), int(y)))
        placed += 1
    return placed


def _card_layer(img: Image.Image, w: int, h: int, style: str, pal, opts, rng):
    bias = 0.42 if img.height > img.width else 0.5
    card = _cover(img, w, h, bias)
    if style == "polaroid":
        frame = int(22 * (w / 720))
        bottom = int(frame * 2.6)
        fw, fh = w + frame * 2, h + frame + bottom
        paper = Image.new("RGBA", (fw, fh), (247, 243, 234, 255))
        paper.paste(card, (frame, frame))
        card = _rounded(paper, int(6 * fw / 700))
    else:
        card = _rounded(card, int(28 * (w / 720)))

    angle = opts.rotate
    if angle is None:
        angle = rng.uniform(-1.6, 1.6) if style == "card" else -2.2
    card = card.rotate(angle, expand=True, resample=Image.BICUBIC)

    if pal.dark:
        pad = max(2, int(card.width * 0.02))
        canvas = Image.new("RGBA", (card.width + pad * 2, card.height + pad * 2), (0, 0, 0, 0))
        canvas.alpha_composite(card, (pad, pad))
    else:
        canvas = decor.soft_shadow(card, radius=max(8, int(card.width * 0.035)),
                                   offset=(0, int(card.width * 0.018)), opacity=95)
    return canvas


def _sticker_layer(photo, box_w, box_h, pal, opts, caption, S):
    """贴纸：主体 + 阴影 + 一圈虚线 + 沿底弧的名字。"""
    text_h = int(96 * S) if caption else int(8 * S)
    content_h = max(int(120 * S), int(box_h - text_h - 12 * S))
    ar = max(0.2, photo.width / photo.height)
    cw = int(content_h * ar)
    ch = content_h
    if cw > box_w * 0.96:
        cw = int(box_w * 0.96)
        ch = int(cw / ar)

    has_alpha = photo.mode == "RGBA" and photo.getchannel("A").getextrema()[0] < 250
    content = photo.resize((max(1, cw), max(1, ch)), Image.LANCZOS).convert("RGBA")
    if not has_alpha:
        content.putalpha(sticker.oval_mask(content.width, content.height, feather=2.2 * S))

    body = decor.soft_shadow(content, radius=max(8, int(15 * S)),
                             offset=(0, int(9 * S)), opacity=70 if pal.dark else 115)
    pad = (body.width - content.width) // 2

    if has_alpha:
        line = sticker.silhouette_outline(content, (*pal.ink, 235),
                                          width=max(2, int(3.6 * S)),
                                          offset=int(13 * S),
                                          dash=17 * S, gap=12 * S)
    else:
        line = sticker.dashed_ellipse((content.width, content.height),
                                      content.width / 2, content.height / 2,
                                      content.width / 2 - int(7 * S),
                                      content.height / 2 - int(7 * S),
                                      (255, 252, 245, 240), width=max(3, int(4.2 * S)),
                                      dash_deg=6.0, gap_deg=4.2)
    body.alpha_composite(line, (pad, pad))

    if not caption:
        return body

    out = Image.new("RGBA", (body.width, body.height + text_h), (0, 0, 0, 0))
    out.alpha_composite(body, (0, 0))

    f_cap = fonts.font("hand", int(40 * S), opts.font)
    text_w = sum(f_cap.getlength(c) + 3 * S for c in caption)
    R = max(content.width * 1.02, text_w / 0.92, 300 * S)
    cy = body.height - R + text_h * 0.36
    txt = sticker.arc_text(caption, out.size, out.width / 2, cy, R, f_cap,
                           (*pal.ink, 255), halo=(*pal.bg, 235),
                           halo_w=max(2, int(3 * S)), tracking=int(3 * S),
                           start_deg=166, end_deg=14)
    out.alpha_composite(txt)
    return out


def _cutout_layer(photo: Image.Image, w: int, h: int, pal):
    ar = photo.width / photo.height
    tw, th = _fit(ar, w, h)
    cut = photo.resize((max(1, int(tw)), max(1, int(th))), Image.LANCZOS)
    pad = Image.new("RGBA", (cut.width + 40, cut.height + 40), (0, 0, 0, 0))
    pad.alpha_composite(cut, (20, 20))
    shadow_op = 80 if pal.dark else 150
    return decor.soft_shadow(pad, radius=int(max(cut.width, cut.height) * 0.03),
                             offset=(0, int(cut.width * 0.012)), opacity=shadow_op)


def _tape(canvas: Image.Image, layer: Image.Image, px: int, py: int, pal, S, opts):
    tw = int(layer.width * 0.17)
    th = int(30 * S)
    color = palettes.mix(pal.accent, pal.bg, 0.18)
    for i, (rel_x, angle) in enumerate(((0.10, -24), (0.90, 24))):
        strip = decor.tape(tw, th, color, alpha=205, seed=opts.seed + i + 5)
        strip = strip.rotate(angle, expand=True, resample=Image.BICUBIC)
        cx = px + layer.width * rel_x
        cy = py + layer.height * 0.018
        canvas.alpha_composite(strip, (int(cx - strip.width / 2), int(cy - strip.height / 2)))

