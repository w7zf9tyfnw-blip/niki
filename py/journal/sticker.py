"""贴纸排版：抠好的主体 + 一圈手绘虚线 + 沿弧线的名字。

两种形态：
- 轮廓贴纸：有干净抠图时，虚线顺着主体轮廓走（像手账贴纸）；
- 椭圆贴纸：抠不干净时，把照片裁进一个椭圆，虚线绕着椭圆走（像菜单卡片）。

弧线文字每个字单独旋转，所以是真的「沿着边」写，不是把一行字摆成弯的。
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import fonts

NEIGH = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def _dilate(m: np.ndarray, r: int = 1) -> np.ndarray:
    out = m
    for _ in range(max(0, r)):
        o = out.copy()
        o[1:, :] |= out[:-1, :]
        o[:-1, :] |= out[1:, :]
        o[:, 1:] |= out[:, :-1]
        o[:, :-1] |= out[:, 1:]
        out = o
    return out


def _trace(mask: np.ndarray, max_steps: int = 200000) -> list[tuple[int, int]]:
    """Moore 邻域轮廓跟踪，返回 (x, y) 闭合点列。"""
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return []
    y0 = int(ys.min())
    x0 = int(xs[ys == y0].min())
    p = (y0, x0)
    b = (y0, x0 - 1)
    path = [p]
    first = None
    for _ in range(max_steps):
        by, bx = b[0] - p[0], b[1] - p[1]
        try:
            idx = NEIGH.index((by, bx))
        except ValueError:
            idx = 7
        nxt = None
        for k in range(1, 9):
            dy, dx = NEIGH[(idx + k) % 8]
            ny, nx = p[0] + dy, p[1] + dx
            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx]:
                nxt = (ny, nx)
                py, px = NEIGH[(idx + k - 1) % 8]
                b = (p[0] + py, p[1] + px)
                break
        if nxt is None:
            break
        p = nxt
        path.append(p)
        if first is None:
            first = p
        elif p == first and len(path) > 8:
            break
    return [(x, y) for y, x in path]


def _resample(pts: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    if len(pts) < 2:
        return pts
    out = [pts[0]]
    acc = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if seg == 0:
            continue
        t = 0.0
        while acc + (seg - t) >= step:
            t += step - acc
            acc = 0.0
            out.append((a[0] + (b[0] - a[0]) * t / seg, a[1] + (b[1] - a[1]) * t / seg))
        acc += seg - t
    return out


def _smooth(pts: list[tuple[float, float]], k: int = 4) -> list[tuple[float, float]]:
    if len(pts) < k * 2:
        return pts
    out = []
    n = len(pts)
    for i in range(n):
        xs = sum(pts[(i + j) % n][0] for j in range(-k, k + 1))
        ys = sum(pts[(i + j) % n][1] for j in range(-k, k + 1))
        out.append((xs / (2 * k + 1), ys / (2 * k + 1)))
    return out


def _dash_segments(pts, dash: float, gap: float, step: float = 2.4):
    rs = _resample(pts, step)
    period = max(dash + gap, 1e-3)
    segs, cur = [], []
    for i, p in enumerate(rs):
        if (i * step) % period < dash:
            cur.append(p)
        else:
            if len(cur) > 1:
                segs.append(cur)
            cur = []
    if len(cur) > 1:
        segs.append(cur)
    return segs


def _stroke(d: ImageDraw.ImageDraw, segs, fill, width: int):
    for seg in segs:
        d.line(seg, fill=fill, width=width, joint="curve")
        r = width / 2
        for x, y in (seg[0], seg[-1]):
            d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def silhouette_outline(cut: Image.Image, color, *, width: int = 4,
                       offset: int = 10, dash: float = 15, gap: float = 11) -> Image.Image:
    """顺着 alpha 轮廓画一圈虚线，返回同尺寸 RGBA。"""
    w, h = cut.size
    small = max(180, int(max(w, h) * 0.4))
    s = small / max(w, h)
    sw, sh = max(8, int(w * s)), max(8, int(h * s))
    a = np.asarray(cut.getchannel("A").resize((sw, sh), Image.BILINEAR)) > 110
    if not a.any():
        return Image.new("RGBA", cut.size, (0, 0, 0, 0))
    a = _dilate(a, max(1, int(offset * s)))
    pts = _trace(a)
    if len(pts) < 12:
        return Image.new("RGBA", cut.size, (0, 0, 0, 0))
    pts = _smooth([(x / s, y / s) for x, y in pts], 3)
    segs = _dash_segments(pts, dash * s, gap * s, step=max(2.0, max(w, h) / 400))

    layer = Image.new("RGBA", cut.size, (0, 0, 0, 0))
    _stroke(ImageDraw.Draw(layer), segs, color, max(2, int(width)))
    return layer


def dashed_ellipse(size, cx, cy, rx, ry, color, width: int = 4,
                   dash_deg: float = 7.0, gap_deg: float = 5.5) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ang = 0.0
    while ang < 360:
        a0, a1 = ang, min(360.0, ang + dash_deg)
        d.arc((cx - rx, cy - ry, cx + rx, cy + ry), a0, a1, fill=color, width=width)
        ang = a1 + gap_deg
    return layer


def arc_text(text: str, size, cx: float, cy: float, radius: float, font,
             fill, *, halo=(0, 0, 0, 0), halo_w: int = 3, tracking: float = 0.0,
             start_deg: float = 158.0, end_deg: float = 22.0) -> Image.Image:
    """把字沿一段圆弧排：从左下绕到右下，每个字按切线旋转。"""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    if not text:
        return layer

    adv = [font.getlength(c) + tracking for c in text]
    total = sum(adv)
    max_span = math.radians(min(108.0, abs(end_deg - start_deg)))
    if total / radius > max_span:            # 太长就收紧字距，别绕成一圈
        scale = max(max_span * radius / total, 0.5)
        adv = [a * scale for a in adv]
        total = sum(adv)
    span = total / radius

    mid = math.radians((start_deg + end_deg) / 2)
    ang = mid + span / 2

    for ch, a in zip(text, adv):
        ang -= a / radius
        if ch.strip():
            gt = _glyph_tile(ch, font, fill)
            rot = 90 - math.degrees(ang)
            for dx, dy in ((halo_w, 0), (-halo_w, 0), (0, halo_w), (0, -halo_w),
                           (halo_w, halo_w), (-halo_w, -halo_w), (halo_w, -halo_w), (-halo_w, halo_w)):
                if any(halo):
                    ht = _glyph_tile(ch, font, halo)
                    _paste_rot(layer, ht, cx + math.cos(ang) * radius + dx,
                               cy + math.sin(ang) * radius + dy, rot)
            _paste_rot(layer, gt, cx + math.cos(ang) * radius,
                       cy + math.sin(ang) * radius, rot)
    return layer


def _glyph_tile(ch: str, font, fill) -> Image.Image:
    pad = int(getattr(font, "size", 40) * 0.4) + 4
    asc, desc = font.getmetrics()
    w = int(font.getlength(ch)) + pad * 2
    h = asc + desc + pad * 2
    tile = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((pad, pad + asc), ch, font=font, fill=fill, anchor="ls")
    return tile


def _paste_rot(layer: Image.Image, tile: Image.Image, cx: float, cy: float, deg: float):
    t = tile.rotate(deg, expand=True, resample=Image.BICUBIC)
    x = int(cx - t.width / 2)
    y = int(cy - t.height / 2)
    box = (max(0, x), max(0, y), min(layer.width, x + t.width), min(layer.height, y + t.height))
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    layer.alpha_composite(t.crop((box[0] - x, box[1] - y, box[2] - x, box[3] - y)), (box[0], box[1]))


def oval_mask(w: int, h: int, feather: float = 3.0) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    d.ellipse((0, 0, w - 1, h - 1), fill=255)
    return m.filter(ImageFilter.GaussianBlur(feather))
