"""手绘简笔画：全部用代码画线条，不用任何贴图素材。

风格目标 = 你发的那种：黑色细马克笔、圆头、线条微微抖、造型圆滚滚、旁边配一句手写小英文。

用法：
    from journal.doodles import render, pick
    img = render("cake", 220, ink=(40,38,34,240), label=True)
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw

from . import fonts


# ---------------------------------------------------------------- 几何工具
def qbez(p0, p1, p2, n=22):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def cbez(p0, p1, p2, p3, n=28):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def ellipse(cx, cy, rx, ry, rot=0.0, n=56):
    out = []
    ca, sa = math.cos(rot), math.sin(rot)
    for i in range(n):
        a = 2 * math.pi * i / n
        x, y = math.cos(a) * rx, math.sin(a) * ry
        out.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    return out


def arc(cx, cy, r, a0, a1, n=20, ry=None):
    ry = r if ry is None else ry
    out = []
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        out.append((cx + math.cos(a) * r, cy + math.sin(a) * ry))
    return out


def rect(x0, y0, x1, y1, r=0.0, n=8):
    if r <= 0:
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    pts = []
    corners = [
        (x1 - r, y0 + r, -90, 0), (x1 - r, y1 - r, 0, 90),
        (x0 + r, y1 - r, 90, 180), (x0 + r, y0 + r, 180, 270),
    ]
    for cx, cy, a0, a1 in corners:
        for i in range(n + 1):
            a = math.radians(a0 + (a1 - a0) * i / n)
            pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    return pts


class Pen:
    """一支会抖的马克笔。坐标全部用 0~1 的归一化空间。"""

    def __init__(self, size: int, ink, width: float = 0.018, amp: float = 0.004, seed: int = 0):
        self.S = size
        self.img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.ink = ink if len(ink) == 4 else (*ink, 240)
        self.w = max(1, round(width * size))
        self.amp = amp
        self.rng = random.Random(seed)
        self.phase = self.rng.uniform(0, 12)

    # 每条线都加一点点连续的低频抖动，像手抖又不像噪声
    def _wob(self, x: float, y: float):
        a = self.amp * self.S
        return (x + a * math.sin(y * 0.013 + self.phase),
                y + a * math.cos(x * 0.011 + self.phase * 1.7))

    def stroke(self, pts, close: bool = False, width: float | None = None):
        if len(pts) < 2:
            return
        pts = [(px * self.S, py * self.S) for px, py in pts]
        if close:
            pts = pts + [pts[0]]
        w = int(width or self.w)
        dense = []
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            n = max(1, int(seg / 9))
            for i in range(n):
                t = i / n
                dense.append(self._wob(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
        dense.append(self._wob(*pts[-1]))
        self.d.line(dense, fill=self.ink, width=w, joint="curve")
        r = w / 2
        for x, y in (dense[0], dense[-1]):
            self.d.ellipse((x - r, y - r, x + r, y + r), fill=self.ink)

    def fill(self, pts):
        self.d.polygon([(x * self.S, y * self.S) for x, y in pts], fill=self.ink)

    def dot(self, x: float, y: float, r: float):
        R = r * self.S
        cx, cy = x * self.S, y * self.S
        self.d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=self.ink)

    def dashed(self, pts, dash: float = 0.012, gap: float = 0.012):
        """虚线——用来表现手绘里那种“没画满”的随意感。"""
        for a, b in zip(pts, pts[1:]):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if seg == 0:
                continue
            t = 0.0
            while t < 1:
                t1 = min(1.0, t + dash / seg)
                self.stroke([(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t),
                             (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)])
                t = t1 + gap / seg


# ---------------------------------------------------------------- 图形库
def _cake(p: Pen):
    p.stroke(ellipse(0.50, 0.82, 0.30, 0.065))                  # 盘子
    p.stroke([(0.30, 0.76), (0.30, 0.48)], close=False)          # 左侧
    p.stroke([(0.70, 0.76), (0.70, 0.48)])                       # 右侧
    p.stroke(qbez((0.30, 0.48), (0.50, 0.34), (0.70, 0.48)))     # 顶
    p.stroke([(0.30, 0.76), (0.70, 0.76)])                       # 底
    p.stroke([(0.315, 0.62), (0.685, 0.60)])                     # 夹层
    p.stroke([(0.32, 0.70), (0.68, 0.68)])
    p.stroke(ellipse(0.50, 0.30, 0.055, 0.058))                  # 草莓
    p.stroke([(0.47, 0.245), (0.50, 0.215), (0.53, 0.245)])
    p.dot(0.49, 0.295, 0.006)


def _sushi(p: Pen):
    p.stroke(ellipse(0.50, 0.55, 0.21, 0.21), close=True, width=p.w * 2.0)   # 海苔
    p.stroke(ellipse(0.50, 0.55, 0.135, 0.135))                               # 米饭
    p.dot(0.455, 0.505, 0.028)                                                # 馅
    p.dot(0.545, 0.535, 0.026)
    p.dot(0.50, 0.60, 0.024)


def _dumpling(p: Pen):
    p.stroke(ellipse(0.50, 0.80, 0.32, 0.065))                   # 盘
    for cx in (0.32, 0.50, 0.68):
        body = cbez((cx - 0.09, 0.68), (cx - 0.11, 0.50), (cx + 0.11, 0.50), (cx + 0.09, 0.68))
        p.stroke(body + [(cx - 0.09, 0.68)])
        for k in (-0.045, 0.0, 0.045):                            # 褶
            p.stroke(qbez((cx + k, 0.68), (cx + k * 1.1, 0.60), (cx + k * 0.6, 0.545)))


def _milk(p: Pen):
    p.stroke([(0.32, 0.42), (0.32, 0.80), (0.68, 0.80), (0.68, 0.42)], close=True)
    p.stroke([(0.32, 0.42), (0.40, 0.30), (0.60, 0.30), (0.68, 0.42)])
    p.stroke([(0.40, 0.30), (0.40, 0.42)])
    p.stroke([(0.60, 0.30), (0.60, 0.42)])
    p.dot(0.435, 0.55, 0.011)                                     # 猫眼
    p.dot(0.565, 0.55, 0.011)
    p.stroke([(0.475, 0.60), (0.525, 0.60), (0.50, 0.635)], close=True)
    p.stroke([(0.50, 0.64), (0.50, 0.665)])
    for y in (0.585, 0.615, 0.645):
        p.stroke([(0.41, y), (0.36, y - 0.01)])
        p.stroke([(0.59, y), (0.64, y - 0.01)])


def _chicken(p: Pen):
    meat = cbez((0.60, 0.42), (0.58, 0.19), (0.23, 0.19), (0.21, 0.39))
    meat += cbez((0.21, 0.39), (0.19, 0.58), (0.40, 0.67), (0.56, 0.54))[1:]
    meat += cbez((0.56, 0.54), (0.62, 0.50), (0.63, 0.46), (0.60, 0.42))[1:]
    p.stroke(meat, close=True)
    p.stroke([(0.57, 0.53), (0.71, 0.67)], width=p.w * 1.5)      # 骨头
    p.stroke(ellipse(0.735, 0.685, 0.052, 0.046))
    p.stroke(ellipse(0.685, 0.735, 0.045, 0.04))
    p.stroke(qbez((0.28, 0.30), (0.34, 0.34), (0.40, 0.31)))     # 高光


def _coffee(p: Pen):
    p.stroke(ellipse(0.47, 0.80, 0.27, 0.05))                     # 碟
    p.stroke([(0.30, 0.42), (0.34, 0.74), (0.62, 0.74), (0.66, 0.42)], close=True)
    p.stroke(cbez((0.655, 0.47), (0.77, 0.50), (0.77, 0.63), (0.645, 0.66)))  # 把手
    p.stroke(qbez((0.36, 0.33), (0.40, 0.24), (0.44, 0.33)))     # 热气
    p.stroke(qbez((0.48, 0.31), (0.52, 0.22), (0.56, 0.31)))
    p.stroke([(0.32, 0.47), (0.64, 0.47)])


def _strawberry(p: Pen):
    body = cbez((0.50, 0.30), (0.20, 0.34), (0.26, 0.72), (0.50, 0.82))
    body += cbez((0.50, 0.82), (0.74, 0.72), (0.80, 0.34), (0.50, 0.30))[1:]
    p.stroke(body, close=True)
    for x, y in ((0.43, 0.45), (0.57, 0.45), (0.50, 0.55), (0.40, 0.57), (0.60, 0.57), (0.47, 0.68), (0.55, 0.68)):
        p.dot(x, y, 0.008)
    p.stroke([(0.42, 0.28), (0.50, 0.19), (0.58, 0.28)])
    p.stroke([(0.50, 0.19), (0.50, 0.13)])


def _dog(p: Pen):
    """戴派对帽的小狗（腊肠）。"""
    p.stroke(ellipse(0.29, 0.40, 0.095, 0.092))                       # 头
    p.stroke(cbez((0.335, 0.47), (0.46, 0.43), (0.62, 0.43), (0.72, 0.49)))   # 背
    p.stroke([(0.72, 0.49), (0.72, 0.66)])
    p.stroke(cbez((0.72, 0.66), (0.60, 0.72), (0.44, 0.72), (0.335, 0.60)))   # 肚
    p.stroke([(0.335, 0.60), (0.335, 0.47)])                          # 脖子
    for x, y0 in ((0.40, 0.705), (0.50, 0.715), (0.60, 0.705), (0.69, 0.66)):
        p.stroke([(x, y0), (x, y0 + 0.095)])
        p.stroke([(x, y0 + 0.095), (x + 0.035, y0 + 0.095)])
    p.stroke(cbez((0.72, 0.52), (0.79, 0.50), (0.82, 0.45), (0.80, 0.40)))    # 尾
    p.stroke(cbez((0.335, 0.35), (0.31, 0.44), (0.34, 0.52), (0.385, 0.49)))  # 耳
    p.stroke(qbez((0.215, 0.42), (0.16, 0.45), (0.17, 0.50)))                 # 嘴
    p.dot(0.175, 0.505, 0.012)                                        # 鼻
    p.dot(0.275, 0.385, 0.010)                                        # 眼
    p.stroke([(0.225, 0.335), (0.315, 0.335), (0.273, 0.235)], close=True)    # 帽
    p.dot(0.273, 0.215, 0.015)
    p.dot(0.258, 0.315, 0.006)
    p.dot(0.285, 0.305, 0.006)


def _puppy(p: Pen):
    """戴墨镜的狗。"""
    p.stroke(ellipse(0.50, 0.47, 0.185, 0.17))
    left = cbez((0.335, 0.37), (0.23, 0.42), (0.24, 0.60), (0.35, 0.575))
    left += cbez((0.35, 0.575), (0.34, 0.50), (0.33, 0.42), (0.335, 0.37))[1:]
    p.stroke(left, close=True)
    right = cbez((0.665, 0.37), (0.77, 0.42), (0.76, 0.60), (0.65, 0.575))
    right += cbez((0.65, 0.575), (0.66, 0.50), (0.67, 0.42), (0.665, 0.37))[1:]
    p.stroke(right, close=True)
    p.fill(rect(0.355, 0.415, 0.475, 0.485, r=0.028))                 # 墨镜
    p.fill(rect(0.525, 0.415, 0.645, 0.485, r=0.028))
    p.stroke([(0.475, 0.44), (0.525, 0.44)])
    p.stroke([(0.355, 0.44), (0.325, 0.455)])
    p.stroke([(0.645, 0.44), (0.675, 0.455)])
    p.fill([(0.465, 0.545), (0.535, 0.545), (0.50, 0.605)])           # 鼻子
    p.stroke(arc(0.50, 0.588, 0.05, 25, 155))                         # 嘴
    p.stroke([(0.50, 0.605), (0.50, 0.645)])
    p.fill([(0.462, 0.645), (0.538, 0.645), (0.50, 0.715)])           # 领结
    p.stroke([(0.455, 0.645), (0.545, 0.645)])


def _catpaw(p: Pen):
    pad = cbez((0.50, 0.56), (0.24, 0.56), (0.26, 0.86), (0.50, 0.86))
    pad += cbez((0.50, 0.86), (0.74, 0.86), (0.76, 0.56), (0.50, 0.56))[1:]
    p.stroke(pad, close=True)
    for cx, cy, r in ((0.28, 0.40, 0.075), (0.42, 0.30, 0.072), (0.58, 0.30, 0.072), (0.72, 0.40, 0.075)):
        p.stroke(ellipse(cx, cy, r, r * 0.95))
    p.stroke(qbez((0.44, 0.66), (0.50, 0.74), (0.56, 0.66)))


def _balloon(p: Pen):
    p.stroke(ellipse(0.38, 0.34, 0.135, 0.165))
    p.stroke([(0.375, 0.505), (0.36, 0.86)])
    p.stroke([(0.345, 0.505), (0.395, 0.505), (0.37, 0.55)], close=True)     # 结
    p.dot(0.335, 0.30, 0.009)
    p.dot(0.425, 0.30, 0.009)
    p.stroke(qbez((0.33, 0.365), (0.38, 0.40), (0.43, 0.365)))
    # 小心形气球
    h = cbez((0.62, 0.50), (0.56, 0.40), (0.56, 0.31), (0.63, 0.31))
    h += cbez((0.63, 0.31), (0.70, 0.31), (0.70, 0.40), (0.62, 0.50))[1:]
    p.stroke(h, close=True)
    p.stroke([(0.62, 0.50), (0.63, 0.80)])
    p.dot(0.60, 0.36, 0.007)
    p.dot(0.655, 0.36, 0.007)


def _plant(p: Pen):
    p.stroke([(0.34, 0.62), (0.66, 0.62), (0.62, 0.84), (0.38, 0.84)], close=True)
    p.stroke([(0.33, 0.56), (0.67, 0.56)])
    for sx, tip, bend in ((0.42, (0.34, 0.30), 0.05), (0.50, (0.50, 0.18), 0.0), (0.58, (0.68, 0.30), -0.05)):
        p.stroke(cbez((0.50, 0.58), (0.50 + bend, 0.46), (sx + bend, 0.36), tip))
        p.stroke(cbez(tip, (tip[0] - 0.05, tip[1] + 0.06), (tip[0] + 0.04, tip[1] + 0.09), (0.50, 0.56)), close=True)


def _star(p: Pen):
    pts = [(0.50, 0.10), (0.575, 0.42), (0.90, 0.50), (0.575, 0.58),
           (0.50, 0.90), (0.425, 0.58), (0.10, 0.50), (0.425, 0.42)]
    p.stroke(pts, close=True)


def _heart(p: Pen):
    h = cbez((0.50, 0.82), (0.12, 0.60), (0.14, 0.28), (0.34, 0.28))
    h += cbez((0.34, 0.28), (0.45, 0.28), (0.49, 0.36), (0.50, 0.42))[1:]
    h += cbez((0.50, 0.42), (0.51, 0.36), (0.55, 0.28), (0.66, 0.28))[1:]
    h += cbez((0.66, 0.28), (0.86, 0.28), (0.88, 0.60), (0.50, 0.82))[1:]
    p.stroke(h, close=True)


def _cloud(p: Pen):
    p.stroke(arc(0.36, 0.52, 0.15, 130, 340))
    p.stroke(arc(0.52, 0.42, 0.19, 180, 330))
    p.stroke(arc(0.68, 0.55, 0.13, 200, 20))
    p.stroke([(0.22, 0.70), (0.80, 0.70)])


def _moon(p: Pen):
    outer = arc(0.50, 0.50, 0.34, 60, 300, n=44)
    inner = arc(0.941, 0.50, 0.40, 227.3, 132.7, n=34)
    p.stroke(outer + inner[1:], close=True)
    p.dot(0.30, 0.28, 0.011)
    p.dot(0.22, 0.44, 0.008)
    p.dot(0.35, 0.16, 0.007)


def _camera(p: Pen):
    p.stroke(rect(0.22, 0.36, 0.78, 0.76, r=0.06), close=True)
    p.stroke(rect(0.38, 0.28, 0.54, 0.38, r=0.02), close=True)
    p.stroke(ellipse(0.50, 0.56, 0.135, 0.135))
    p.stroke(ellipse(0.50, 0.56, 0.06, 0.06))
    p.dot(0.69, 0.44, 0.014)
    p.stroke([(0.25, 0.46), (0.30, 0.46)])


def _flower(p: Pen):
    for a in range(5):
        ang = -math.pi / 2 + a * 2 * math.pi / 5
        cx, cy = 0.50 + math.cos(ang) * 0.14, 0.36 + math.sin(ang) * 0.14
        p.stroke(ellipse(cx, cy, 0.085, 0.075, rot=ang))
    p.stroke(ellipse(0.50, 0.36, 0.055, 0.055))
    p.stroke(cbez((0.50, 0.44), (0.48, 0.62), (0.52, 0.72), (0.50, 0.86)))
    p.stroke(cbez((0.505, 0.66), (0.60, 0.62), (0.66, 0.66), (0.62, 0.72)), close=True)


def _book(p: Pen):
    p.stroke(cbez((0.50, 0.36), (0.36, 0.32), (0.22, 0.34), (0.18, 0.38)))
    p.stroke([(0.18, 0.38), (0.18, 0.74)])
    p.stroke(cbez((0.18, 0.74), (0.32, 0.70), (0.44, 0.72), (0.50, 0.78)))
    p.stroke(cbez((0.50, 0.36), (0.64, 0.32), (0.78, 0.34), (0.82, 0.38)))
    p.stroke([(0.82, 0.38), (0.82, 0.74)])
    p.stroke(cbez((0.82, 0.74), (0.68, 0.70), (0.56, 0.72), (0.50, 0.78)))
    p.stroke([(0.50, 0.36), (0.50, 0.78)])
    for y in (0.46, 0.54, 0.62):
        p.stroke([(0.25, y), (0.43, y + 0.01)])
        p.stroke([(0.57, y + 0.01), (0.75, y)])


def _music_note(p: Pen, x, y, s=1.0):
    p.stroke(ellipse(x, y, 0.045 * s, 0.035 * s, rot=-0.35))
    p.stroke([(x + 0.042 * s, y - 0.01 * s), (x + 0.042 * s, y - 0.16 * s)])
    p.stroke([(x + 0.042 * s, y - 0.16 * s), (x + 0.12 * s, y - 0.20 * s)])


def _musicdog(p: Pen):
    p.stroke(ellipse(0.50, 0.52, 0.20, 0.185))
    p.stroke(arc(0.50, 0.52, 0.255, 190, 350))                       # 头梁
    p.stroke(rect(0.215, 0.44, 0.30, 0.62, r=0.05), close=True)      # 耳罩
    p.stroke(rect(0.70, 0.44, 0.785, 0.62, r=0.05), close=True)
    p.stroke(cbez((0.30, 0.62), (0.26, 0.72), (0.34, 0.74), (0.40, 0.70)))   # 耳朵
    p.stroke(cbez((0.70, 0.62), (0.74, 0.72), (0.66, 0.74), (0.60, 0.70)))
    p.dot(0.425, 0.50, 0.011)
    p.dot(0.575, 0.50, 0.011)
    p.fill([(0.47, 0.56), (0.53, 0.56), (0.50, 0.605)])
    p.stroke(arc(0.50, 0.575, 0.045, 30, 150))
    _music_note(p, 0.76, 0.28, 0.9)
    _music_note(p, 0.88, 0.36, 0.7)


def _sakura(p: Pen):
    for a in range(5):
        ang = -math.pi / 2 + a * 2 * math.pi / 5
        cx, cy = 0.50 + math.cos(ang) * 0.20, 0.50 + math.sin(ang) * 0.20
        petal = ellipse(cx, cy, 0.075, 0.115, rot=ang)
        p.stroke(petal, close=True)
    p.stroke(ellipse(0.50, 0.50, 0.045, 0.045))


DOODLES: dict[str, tuple] = {
    "cake": (_cake, "cake."),
    "sushi": (_sushi, "sushi."),
    "dumpling": (_dumpling, "dumpling."),
    "milk": (_milk, "milk."),
    "chicken": (_chicken, "Chicken"),
    "coffee": (_coffee, "coffee."),
    "strawberry": (_strawberry, "berry."),
    "dog": (_dog, "puppy~"),
    "puppy": (_puppy, "Cute puppy~"),
    "catpaw": (_catpaw, "cat paw."),
    "balloon": (_balloon, "Enjoy Life!"),
    "plant": (_plant, "grow."),
    "star": (_star, "shine."),
    "heart": (_heart, "happy"),
    "cloud": (_cloud, "cloudy"),
    "moon": (_moon, "good night."),
    "camera": (_camera, "snap."),
    "flower": (_flower, "bloom."),
    "book": (_book, "read."),
    "musicdog": (_musicdog, "Music"),
}

# 按内容分类挑画
BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "food": ("cake", "dumpling", "sushi", "milk", "chicken", "coffee", "strawberry"),
    "beauty": ("flower", "heart", "star", "balloon"),
    "body": ("heart", "plant", "star"),
    "work": ("coffee", "book", "camera"),
    "low": ("moon", "cloud", "coffee"),
    "high": ("balloon", "heart", "star"),
    "weather": ("cloud", "moon", "star"),
    "home": ("plant", "catpaw", "dog", "milk"),
    "travel": ("camera", "balloon", "cloud"),
    "night": ("moon", "star", "cloud"),
    "life": ("star", "heart", "plant", "flower"),
}


def pick(categories: list[str], n: int, rng: random.Random) -> list[str]:
    pool: list[str] = []
    for c in categories:
        pool.extend(BY_CATEGORY.get(c, ()))
    pool.extend(BY_CATEGORY["life"])
    uniq = list(dict.fromkeys(pool))
    rng.shuffle(uniq)
    return uniq[:n]


def render(name: str, size: int, ink, *, label: bool = False, label_color=None,
           seed: int = 0, width: float = 0.019) -> Image.Image:
    """画一个简笔画，返回 RGBA 图（已裁掉空白）。"""
    fn, word = DOODLES.get(name, DOODLES["star"])
    ss = 2  # 超采样，缩回来线条更顺
    pen = Pen(size * ss, ink, width=width, seed=seed)
    fn(pen)
    img = pen.img.resize((size, size), Image.LANCZOS)

    if not label:
        return img.crop(img.getbbox() or (0, 0, size, size))

    fs = max(12, int(size * 0.17))
    f = fonts.font("marker", fs)
    tile = Image.new("RGBA", (size, size + int(fs * 1.5)), (0, 0, 0, 0))
    tile.alpha_composite(img, (0, 0))
    d = ImageDraw.Draw(tile)
    color = label_color or ink
    tw = d.textlength(word, font=f)
    d.text(((size - tw) / 2, size + fs * 0.15), word, font=f, fill=color)
    return tile.crop(tile.getbbox() or (0, 0, size, size))
