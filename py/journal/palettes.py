"""纯色背景配色。

一个 palette = 背景 + 主字色 + 两个点缀色。风格参考图：黑底、米白细字、留白多。
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass

from PIL import Image


def _h(hexstr: str) -> tuple[int, int, int]:
    hexstr = hexstr.strip().lstrip("#")
    return tuple(int(hexstr[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


@dataclass(frozen=True)
class Palette:
    key: str
    name: str
    bg: tuple[int, int, int]
    ink: tuple[int, int, int]
    accent: tuple[int, int, int]
    accent2: tuple[int, int, int]

    @property
    def dark(self) -> bool:
        return luminance(self.bg) < 0.5

    @property
    def sub(self) -> tuple[int, int, int, int]:
        return (*self.ink, 150)

    @property
    def faint(self) -> tuple[int, int, int, int]:
        return (*self.ink, 70)

    def tint(self, t: float) -> tuple[int, int, int, int]:
        """ink 与 bg 的中间色，用于极淡的线条。"""
        return (*mix(self.ink, self.bg, 1 - t), int(255 * t))

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "bg": "#%02X%02X%02X" % self.bg,
            "ink": "#%02X%02X%02X" % self.ink,
            "accent": "#%02X%02X%02X" % self.accent,
            "dark": self.dark,
        }


PRESETS: dict[str, Palette] = {
    "ink": Palette("ink", "墨黑", _h("0F0F12"), _h("F4F1EA"), _h("E3C08A"), _h("B9C7BE")),
    "dusk": Palette("dusk", "暮蓝", _h("1E2733"), _h("EDEFE8"), _h("D9B98A"), _h("9CB0C4")),
    "forest": Palette("forest", "深林", _h("16231C"), _h("EEF0E4"), _h("D8C48C"), _h("A8BE9E")),
    "wine": Palette("wine", "夜莓", _h("241a22"), _h("F3EAE6"), _h("E0A98F"), _h("C4A3B8")),
    "paper": Palette("paper", "米白", _h("F7F3E9"), _h("2B2823"), _h("B9714A"), _h("8E9A82")),
    "butter": Palette("butter", "黄油", _h("FBF1CE"), _h("3A3226"), _h("C4843C"), _h("9AA96F")),
    "blush": Palette("blush", "藕粉", _h("F7E8E4"), _h("4A3230"), _h("C4867A"), _h("B49AA0")),
    "sage": Palette("sage", "鼠尾草", _h("E8EEE2"), _h("2F3A2C"), _h("8FA382"), _h("C2A57A")),
    "sky": Palette("sky", "雾蓝", _h("E3EAF0"), _h("28313A"), _h("7E97AB"), _h("C0A98E")),
    "cream": Palette("cream", "奶油", _h("F6EDE0"), _h("352C24"), _h("C08055"), _h("9BA88C")),
}

ORDER = ["ink", "dusk", "forest", "wine", "paper", "butter", "blush", "sage", "sky", "cream"]


def luminance(rgb) -> float:
    r, g, b = [c / 255 for c in rgb[:3]]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def contrast_ink(bg) -> tuple[int, int, int]:
    return _h("F7F4EC") if luminance(bg) < 0.5 else _h("26221C")


def from_hex(hexstr: str) -> Palette:
    bg = _h(hexstr)
    lum = luminance(bg)
    if lum < 0.5:
        accent = mix(bg, _h("E3C08A"), 0.55)
        return Palette("custom", "自定义", bg, contrast_ink(bg), accent, mix(bg, _h("B9C7BE"), 0.45))
    accent = mix(bg, _h("A9643C"), 0.75)
    return Palette("custom", "自定义", bg, contrast_ink(bg), accent, mix(bg, _h("6E7F5E"), 0.6))


def dominant_color(img: Image.Image) -> tuple[int, int, int]:
    small = img.convert("RGB").resize((80, 80), Image.LANCZOS)
    q = small.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    palette = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    best = None
    for count, idx in counts:
        rgb = tuple(palette[idx * 3 : idx * 3 + 3])
        if not rgb or len(rgb) < 3:
            continue
        lum = luminance(rgb)
        if lum < 0.06 or lum > 0.96:  # 纯黑/纯白不计入
            continue
        best = rgb
        break
    return best or (128, 128, 128)


def auto_from_image(img: Image.Image) -> Palette:
    """取照片主色调（去掉极端明暗后的均值），压成一块「同色系纯色背景」。"""
    import numpy as np

    small = img.convert("RGB").resize((64, 64), Image.LANCZOS)
    px = np.asarray(small).reshape(-1, 3).astype(np.float32) / 255.0
    lum = px @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    lo, hi = np.percentile(lum, 12), np.percentile(lum, 88)
    keep = px[(lum >= lo) & (lum <= hi)]
    if len(keep) < 16:
        keep = px
    r, g, b = keep.mean(0)
    h, s, v = colorsys.rgb_to_hsv(float(r), float(g), float(b))

    s = min(max(s, 0.05), 0.5)
    if v > 0.55:  # 亮照片 -> 浅底深字
        bg = colorsys.hsv_to_rgb(h, s * 0.35, 0.94)
    else:  # 暗照片 -> 深底浅字
        bg = colorsys.hsv_to_rgb(h, min(s * 0.9, 0.38), 0.13)
    bg_rgb = tuple(int(c * 255) for c in bg)
    ink = contrast_ink(bg_rgb)
    accent = mix(bg_rgb, (226, 190, 138) if luminance(bg_rgb) < 0.5 else (169, 100, 60), 0.62)
    return Palette("auto", "取自照片", bg_rgb, ink, accent, mix(bg_rgb, ink, 0.45))


def get(key: str, img: Image.Image | None = None) -> Palette:
    key = (key or "ink").strip()
    if key == "auto" and img is not None:
        return auto_from_image(img)
    if key.startswith("#"):
        return from_hex(key)
    return PRESETS.get(key, PRESETS["ink"])


def catalog() -> list[dict]:
    return [PRESETS[k].to_dict() for k in ORDER]
