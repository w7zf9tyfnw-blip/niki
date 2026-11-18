"""手账装饰：胶带、手绘下划线、圈线、四角星、小圆点。

全部用代码画，不依赖贴纸素材，颜色跟着配色走。
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFilter


def _rgba(color, alpha: int):
    if len(color) == 4:
        return (color[0], color[1], color[2], int(color[3] * alpha / 255))
    return (color[0], color[1], color[2], alpha)


def tape(width: int, height: int, color, alpha: int = 210, seed: int = 1) -> Image.Image:
    """一条撕边胶带（横向），返回 RGBA，可再旋转。"""
    rng = random.Random(seed)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = _rgba(color, alpha)
    teeth = max(2, width // 26)
    top, bottom = [], []
    for i in range(teeth + 1):
        x = width * i / teeth
        top.append((x, rng.uniform(0, height * 0.10)))
        bottom.append((x, height - rng.uniform(0, height * 0.10)))
    d.polygon(top + bottom[::-1], fill=fill)
    # 一点半透明高光，别太塑料
    d.rectangle((0, height * 0.12, width, height * 0.30), fill=_rgba(color, alpha // 5))
    return img


def sparkle(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color, alpha: int = 255):
    k = r * 0.16
    pts = [
        (cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k),
        (cx, cy + r), (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k),
    ]
    d.polygon(pts, fill=_rgba(color, alpha))


def hand_circle(d: ImageDraw.ImageDraw, cx: float, cy: float, rx: float, ry: float,
                color, width: int = 3, seed: int = 1, turns: float = 1.06):
    rng = random.Random(seed)
    steps = 90
    phase = rng.uniform(0, 6.28)
    pts = []
    for i in range(steps + 1):
        t = turns * 2 * math.pi * i / steps
        wob = 1 + 0.03 * math.sin(t * 3 + phase) + rng.uniform(-0.004, 0.004)
        pts.append((cx + math.cos(t + phase) * rx * wob, cy + math.sin(t + phase) * ry * wob))
    d.line(pts, fill=_rgba(color, 200), width=width, joint="curve")


def scribble_underline(d: ImageDraw.ImageDraw, x0: float, x1: float, y: float,
                       color, width: int = 3, seed: int = 1, amp: float = 2.4):
    rng = random.Random(seed)
    steps = max(8, int((x1 - x0) / 12))
    pts = []
    for i in range(steps + 1):
        t = i / steps
        pts.append((x0 + (x1 - x0) * t, y + math.sin(t * 7 + rng.uniform(0, 0.4)) * amp))
    d.line(pts, fill=_rgba(color, 220), width=width, joint="curve")


def dots(d: ImageDraw.ImageDraw, x: float, y: float, cols: int, rows: int,
         gap: float, r: float, color, alpha: int = 120):
    for i in range(cols):
        for j in range(rows):
            d.ellipse((x + i * gap - r, y + j * gap - r, x + i * gap + r, y + j * gap + r),
                      fill=_rgba(color, alpha))


def soft_shadow(layer: Image.Image, radius: int = 30, offset: tuple[int, int] = (0, 16),
                opacity: int = 110) -> Image.Image:
    """由图层 alpha 生成投影，返回带留白的同尺寸图。"""
    w, h = layer.size
    pad = int(radius * 2.2)
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    alpha = layer.getchannel("A").point(lambda v: int(v * opacity / 255))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    shadow.paste(black, (pad + offset[0], pad + offset[1]), alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(layer, (pad, pad))
    return canvas
