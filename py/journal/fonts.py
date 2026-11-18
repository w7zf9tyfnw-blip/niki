"""字体解析与「带字距 / 手写抖动 / 笔画变细」的文本绘制。

参考图那种细手写感来自三件事：
1. 逐字绘制 + 加宽字距，字与字之间留气；
2. 2 倍超采样后再做 1px 腐蚀，笔画比字体原稿细一档；
3. 每个字 1px 内的随机抖动，避免印刷体的死板。

正文有 6 种字形可选（ystyle「字体」参数），默认姚体——最接近参考图那种细长手写。
"""
from __future__ import annotations

import os
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
ASSET_FONTS = os.path.join(PROJECT, "assets", "fonts")

_FONT_DIRS = [
    ASSET_FONTS,
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
]

# 正文字形。顺序 = UI 里的顺序。erode=是否需要「腐蚀变细」——本来就细的字体不用再削。
STYLES: dict[str, dict] = {
    "lemi": {"label": "蜡笔", "hand": ["乐米蜡笔体.ttf", "LemiLaBiTi.ttf"], "erode": False},
    "kai": {"label": "楷体", "hand": ["STKAITI.TTF", "simkai.ttf"], "erode": True},
    "yao": {"label": "姚体", "hand": ["FZYTK.TTF"], "erode": False},
    "fang": {"label": "仿宋", "hand": ["simfang.ttf"], "erode": False},
    "shu": {"label": "舒体", "hand": ["FZSTK.TTF"], "erode": True},
    "xing": {"label": "行楷", "hand": ["STXINGKA.TTF"], "erode": True},
    "thin": {"label": "极细", "hand": ["NotoSansSC-VF.ttf"], "axes": [220], "erode": False},
}
DEFAULT_STYLE = "lemi"


def erode_for(style: str) -> bool:
    return (STYLES.get(style) or STYLES[DEFAULT_STYLE]).get("erode", True)

_CANDIDATES = {
    "sans": ["yyb.ttf", "Dengl.ttf", "msyhl.ttc", "NotoSansSC-VF.ttf", "segoeuil.ttf"],
    "emoji": ["seguiemj.ttf"],
    "marker": ["Inkfree.ttf", "segoepr.ttf", "FZSTK.TTF", "yyb.ttf"],
}

_ENV_OVERRIDE = {"sans": "JOURNAL_FONT_SANS"}

EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0x1F1E6, 0x1F1FF),
    (0xFE0F, 0xFE0F),
    (0x200D, 0x200D),
)

_AXES_BY_PATH: dict[str, list] = {}


def _cmap_codepoints(path: str) -> set[int]:
    """极简 TTF cmap 解析：只为了知道这个字体有没有某个字。"""
    import struct

    try:
        data = open(path, "rb").read()
    except OSError:
        return set()
    try:
        num = struct.unpack(">H", data[4:6])[0]
        cmap_off = None
        for i in range(num):
            off = 12 + i * 16
            if data[off:off + 4] == b"cmap":
                cmap_off = struct.unpack(">I", data[off + 8:off + 12])[0]
                break
        if cmap_off is None:
            return set()
        n = struct.unpack(">H", data[cmap_off + 2:cmap_off + 4])[0]
        best = None
        for i in range(n):
            rec = cmap_off + 4 + i * 8
            pid, eid, off = struct.unpack(">HHI", data[rec:rec + 8])
            sub = cmap_off + off
            fmt = struct.unpack(">H", data[sub:sub + 2])[0]
            if fmt in (4, 12):
                best = (fmt, sub)
                if fmt == 12 and pid == 3:
                    break
        if not best:
            return set()
        fmt, sub = best
        cps: set[int] = set()
        if fmt == 4:
            segx2 = struct.unpack(">H", data[sub + 6:sub + 8])[0]
            seg = segx2 // 2
            end = sub + 14
            start = end + segx2 + 2
            for i in range(seg):
                e = struct.unpack(">H", data[end + i * 2:end + i * 2 + 2])[0]
                st = struct.unpack(">H", data[start + i * 2:start + i * 2 + 2])[0]
                if st == 0xFFFF or e < st:
                    continue
                cps.update(range(st, e + 1))
        else:
            ngroups = struct.unpack(">I", data[sub + 12:sub + 16])[0]
            p = sub + 16
            for _ in range(min(ngroups, 20000)):
                st, e, _g = struct.unpack(">III", data[p:p + 12])
                p += 12
                if e - st > 30000:
                    e = st + 30000
                cps.update(range(st, e + 1))
        return cps
    except Exception:
        return set()


@lru_cache(maxsize=32)
def coverage(path: str) -> frozenset:
    return frozenset(_cmap_codepoints(path))


def has_glyph(f: ImageFont.FreeTypeFont, ch: str) -> bool:
    path = getattr(f, "path", None)
    if not path:
        return True
    return ord(ch) in coverage(path)


def fallback_for(f: ImageFont.FreeTypeFont) -> ImageFont.FreeTypeFont:
    """主字体缺字时，用楷体顶上（Windows 自带的字库最全）。"""
    size = getattr(f, "size", 40)
    for style in ("kai", "yao", "thin", "shu"):
        try:
            cand = font("hand", size, style)
        except Exception:
            continue
        if getattr(cand, "path", None) != getattr(f, "path", None):
            return cand
    return f


def is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in EMOJI_RANGES)


def _find(names: list[str]) -> str | None:
    for name in names:
        for d in _FONT_DIRS:
            if not d:
                continue
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return None


def available_styles() -> list[str]:
    out = []
    for key in STYLES:
        if _find(STYLES[key]["hand"]):
            out.append(key)
    if _mine_path():
        out.insert(0, "mine")
    return out


def _mine_path() -> str | None:
    env = os.environ.get("JOURNAL_FONT_HAND")
    if env and os.path.exists(env):
        return env
    p = os.path.join(ASSET_FONTS, "手账体.ttf")
    return p if os.path.exists(p) else None


@lru_cache(maxsize=None)
def resolve(role: str, style: str = DEFAULT_STYLE) -> str:
    if role == "hand":
        if style == "mine":
            p = _mine_path()
            if p:
                _AXES_BY_PATH[p] = []
                return p
        spec = STYLES.get(style) or STYLES[DEFAULT_STYLE]
        path = _find(spec["hand"])
        if path:
            _AXES_BY_PATH[path] = list(spec.get("axes") or [])
            return path
        # 该字形缺字体时退回默认
        if style != DEFAULT_STYLE:
            return resolve(role, DEFAULT_STYLE)
    env = _ENV_OVERRIDE.get(role)
    if env and os.environ.get(env) and os.path.exists(os.environ[env]):
        return os.environ[env]
    path = _find(_CANDIDATES.get(role, []))
    if path:
        return path
    raise FileNotFoundError(
        f"找不到 {role} 字体，请把任意中文 ttf 放到 {ASSET_FONTS} 并改名 手账体.ttf"
    )


def _load(path: str, size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(path, size)
    axes = _AXES_BY_PATH.get(path)
    if axes:
        try:
            f.set_variation_by_axes(axes)
        except Exception:
            pass
    return f


@lru_cache(maxsize=512)
def font(role: str, size: int, style: str = DEFAULT_STYLE) -> ImageFont.FreeTypeFont:
    return _load(resolve(role, style), size)


def available(role: str) -> bool:
    try:
        resolve(role)
        return True
    except Exception:
        return False


def measure(text: str, f: ImageFont.FreeTypeFont, tracking: float = 0.0, emoji_f=None) -> float:
    if not text:
        return 0.0
    total = 0.0
    for ch in text:
        if emoji_f is not None and is_emoji(ch):
            total += emoji_f.getlength(ch)
        else:
            total += f.getlength(ch)
    return total + tracking * (len(text) - 1)


def _paste(dst: Image.Image, src: Image.Image, x: int, y: int) -> None:
    """alpha_composite，自动裁掉画布外的部分。"""
    if src.width <= 0 or src.height <= 0:
        return
    sx0 = max(0, -x)
    sy0 = max(0, -y)
    dx = max(0, x)
    dy = max(0, y)
    sx1 = min(src.width, dst.width - x)
    sy1 = min(src.height, dst.height - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    dst.alpha_composite(src.crop((sx0, sy0, sx1, sy1)), (dx, dy))


def draw_line(
    layer: Image.Image,
    x: float,
    baseline: float,
    text: str,
    f: ImageFont.FreeTypeFont,
    fill,
    tracking: float = 0.0,
    *,
    align: str = "center",
    jitter: float = 0.0,
    seed: int = 0,
    emoji_f: ImageFont.FreeTypeFont | None = None,
    thin: bool = True,
    ss: int = 2,
) -> float:
    """逐字绘制一行，返回该行宽度。x 为居中/左端参考点，baseline 为基线 y。"""
    if not text:
        return 0.0

    adv = [emoji_f.getlength(c) if (emoji_f is not None and is_emoji(c)) else f.getlength(c)
           for c in text]
    total = sum(adv) + tracking * (len(text) - 1)
    if align == "center":
        start_x = x - total / 2.0
    elif align == "right":
        start_x = x - total
    else:
        start_x = x
    cx = start_x

    ascent, descent = f.getmetrics()
    pad = 8
    box_w = int(total + pad * 2 + 4)
    box_h = int(ascent + descent + pad * 2)
    top = int(baseline - ascent - pad)

    mask = Image.new("L", (box_w * ss, box_h * ss), 0)
    md = ImageDraw.Draw(mask)
    rng = random.Random(seed or 1)
    emoji_positions: list[tuple[float, str]] = []

    for ch, a in zip(text, adv):
        if emoji_f is not None and is_emoji(ch):
            emoji_positions.append((cx, ch))
            cx += a + tracking
            continue
        dy = rng.uniform(-jitter, jitter) if jitter else 0.0
        dx = rng.uniform(-jitter, jitter) * 0.3 if jitter else 0.0
        use = f if has_glyph(f, ch) else fallback_for(f)
        md.text(((cx - start_x + pad + dx) * ss, (ascent + pad + dy) * ss), ch,
                font=_scaled(use, ss), fill=255, anchor="ls")
        cx += a + tracking

    if thin and ss >= 2:
        mask = mask.filter(ImageFilter.MinFilter(3))
    mask = mask.resize((box_w, box_h), Image.LANCZOS)

    alpha = mask
    if len(fill) == 4 and fill[3] < 255:
        alpha = mask.point(lambda v: v * fill[3] // 255)
    color = (fill[0], fill[1], fill[2], 255)
    layer_img = Image.new("RGBA", (box_w, box_h), color)
    layer_img.putalpha(alpha)
    _paste(layer, layer_img, int(round(start_x - pad)), top)

    # 表情单独按彩色绘制，不参与腐蚀
    if emoji_f is not None and emoji_positions:
        d = ImageDraw.Draw(layer)
        lift = getattr(emoji_f, "size", 40) * 0.13
        for ex, ch in emoji_positions:
            try:
                d.text((ex, baseline - lift), ch, font=emoji_f, fill=fill, anchor="ls",
                       embedded_color=True)
            except Exception:
                pass
    return total


@lru_cache(maxsize=512)
def _scaled(f: ImageFont.FreeTypeFont, ss: int) -> ImageFont.FreeTypeFont:
    path = getattr(f, "path", None)
    size = getattr(f, "size", None)
    if path and size:
        return _load(path, int(size * ss))
    return f


def wrap(text: str, f: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """按像素宽度折行（中文逐字）。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        trial = cur + ch
        if measure(trial, f) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines
