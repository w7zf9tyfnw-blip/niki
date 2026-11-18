"""不用任何模型的后台抠图。

思路（比「全局三个背景色」稳得多）：
1. 在画面四周取一圈样本，拟合一个「背景颜色随位置缓慢变化」的二次曲面
   —— 这样墙角受光不均、桌面有渐变也不会误判；
2. 按残差大小取阈值，从画面边缘向内做连通填充，只有跟边缘连通的才算背景；
3. 在前景里只保留最大的一块（把没去干净的墙角、杂物剔掉）；
4. 羽化边缘，裁到主体外框。

主体和背景颜色几乎一样时（白衣服靠白墙）它仍然会失败，但会给出质量分，
上层可以据此回退成照片卡片，或者由用户手动修。
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def _fit_background(arr: np.ndarray, band: int) -> np.ndarray:
    """用四周一圈像素拟合二次曲面，预测每个位置的背景色。"""
    h, w, _ = arr.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = xs / max(w - 1, 1), ys / max(h - 1, 1)
    feats = np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], -1).reshape(-1, 6)

    mask = np.zeros((h, w), bool)
    mask[:band, :] = True
    mask[-band:, :] = True
    mask[:, :band] = True
    mask[:, -band:] = True
    a = feats.reshape(h, w, 6)[mask]
    b = arr[mask].astype(np.float32)

    try:
        coef, *_ = np.linalg.lstsq(a, b, rcond=None)
    except np.linalg.LinAlgError:
        coef = np.zeros((6, 3), np.float32)
        coef[0] = b.mean(0)
    pred = feats @ coef
    return pred.reshape(h, w, 3).astype(np.float32)


def _flood_from_border(similar: np.ndarray) -> np.ndarray:
    """与画面边缘连通的相似像素 = 背景。"""
    h, w = similar.shape
    img = Image.fromarray((similar * 255).astype(np.uint8), "L")
    padded = Image.new("L", (w + 2, h + 2), 255)
    padded.paste(img, (1, 1))
    ImageDraw.floodfill(padded, (0, 0), 128, thresh=0)
    return np.asarray(padded)[1:-1, 1:-1] == 128


def _dilate(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    out[1:, :] |= m[:-1, :]
    out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    return out


def _prune(alpha: Image.Image) -> Image.Image:
    """只保留与「主体中心」连通的那一块，去掉零散的前景残渣。"""
    w, h = alpha.size
    s = 150.0 / max(w, h)
    sw, sh = max(12, int(w * s)), max(12, int(h * s))
    a = np.asarray(alpha.resize((sw, sh), Image.BILINEAR)) > 100
    if not a.any():
        return alpha

    ys, xs = np.nonzero(a)
    cy, cx = int(ys.mean()), int(xs.mean())
    if not a[cy, cx]:
        i = int(((ys - cy) ** 2 + (xs - cx) ** 2).argmin())
        cy, cx = int(ys[i]), int(xs[i])

    seed = np.zeros_like(a)
    seed[cy, cx] = True
    for _ in range(400):
        grown = _dilate(seed) & a
        if grown.sum() == seed.sum():
            break
        seed = grown

    keep = Image.fromarray((seed * 255).astype(np.uint8), "L").resize((w, h), Image.BILINEAR)
    keep = keep.filter(ImageFilter.MaxFilter(5))          # 稍微外扩，别啃掉边缘
    out = np.asarray(alpha, dtype=np.float32) * (np.asarray(keep, dtype=np.float32) / 255.0)
    return Image.fromarray(out.astype(np.uint8), "L")


def remove_background(
    img: Image.Image,
    tolerance: float = 30.0,
    *,
    work_size: int = 760,
    feather: float = 1.2,
    trim: bool = True,
) -> tuple[Image.Image | None, float, dict]:
    """返回 (裁好的 RGBA 素材, 质量分 0~1, 诊断信息)。找不到前景时返回 None。"""
    src = img.convert("RGBA")
    if src.getchannel("A").getextrema()[0] < 250:
        alpha = src.getchannel("A")
        box = alpha.point(lambda v: 255 if v > 12 else 0).getbbox()
        if box:
            src = src.crop(box)
        return src, 0.99, {"mode": "alpha", "foreground": 1.0, "reason": "图片自带透明通道"}

    w, h = src.size
    scale = min(1.0, work_size / max(w, h))
    sw, sh = max(8, int(w * scale)), max(8, int(h * scale))
    small = src.convert("RGB").resize((sw, sh), Image.LANCZOS)
    arr = np.asarray(small, dtype=np.float32)

    band = max(3, int(min(sw, sh) * 0.05))
    pred = _fit_background(arr, band)
    resid = np.linalg.norm(arr - pred, axis=2)

    bmask = np.zeros((sh, sw), bool)
    bmask[:band, :] = True
    bmask[-band:, :] = True
    bmask[:, :band] = True
    bmask[:, -band:] = True

    base = float(np.percentile(resid[bmask], 88)) if bmask.any() else 12.0
    # 背景本身越花，阈值就得越松；但松过头会把主体一起吃掉，所以封顶 46
    tol = float(np.clip(base * 1.35 + tolerance * 0.35, 12.0, 46.0))

    similar = resid < tol
    bg = _flood_from_border(similar)
    fg = ~bg

    # 贴着上/左/右边缘的前景，多半是没去干净背景（人像一般不会顶到边）。
    # 只有在这种「边缘前景」占比很小的时候才动，避免啃掉真正的主体。
    edge_seed = np.zeros_like(fg)
    edge_seed[:2, :] = fg[:2, :]
    edge_seed[:, :2] |= fg[:, :2]
    edge_seed[:, -2:] |= fg[:, -2:]
    if edge_seed.any():
        grown = edge_seed.copy()
        for _ in range(600):
            nxt = _dilate(grown) & fg
            if nxt.sum() == grown.sum():
                break
            grown = nxt
        if grown.sum() < 0.28 * max(fg.sum(), 1):
            bg = bg | grown
    alpha = np.where(bg, 0, 255).astype(np.uint8)

    alpha_img = Image.fromarray(alpha, "L")
    alpha_img = alpha_img.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    alpha_img = _prune(alpha_img)

    a_small = np.asarray(alpha_img) > 128          # 统计都在小图上做，跟 resid 对得上
    if not a_small.any():
        return None, 0.0, {"mode": "fail", "reason": "没找到前景，背景可能太花"}

    if scale < 1.0:
        alpha_img = alpha_img.resize((w, h), Image.LANCZOS)
    alpha_img = alpha_img.filter(ImageFilter.GaussianBlur(feather))

    out = src.copy()
    out.putalpha(alpha_img)

    box = alpha_img.point(lambda v: 255 if v > 16 else 0).getbbox()
    if not box:
        return None, 0.0, {"mode": "fail", "reason": "没找到前景"}

    fg_ratio = float(a_small.mean())
    uniq = float(similar.mean())
    edge = max(1, band // 2)
    touch = float((a_small[:edge, :].mean() + a_small[:, :edge].mean()
                   + a_small[:, -edge:].mean()) / 3.0)
    separation = float(np.clip(resid[a_small].mean() / max(tol, 1.0), 0, 2) / 2)
    fill = 1.0 - min(abs(fg_ratio - 0.45) / 0.45, 1.0)
    score = float(np.clip(0.28 * uniq + 0.22 * (1 - min(touch * 4, 1))
                          + 0.20 * fill + 0.30 * separation, 0, 1))

    pad = int(max(w, h) * 0.01)
    x0, y0, x1, y1 = box
    if trim:
        out = out.crop((max(0, x0 - pad), max(0, y0 - pad),
                        min(w, x1 + pad), min(h, y1 + pad)))

    info = {
        "mode": "cutout",
        "foreground": round(fg_ratio, 3),
        "background": round(uniq, 3),
        "edge_touch": round(touch, 3),
        "tolerance": round(tol, 1),
    }
    if fg_ratio > 0.94:
        info["reason"] = "几乎没有背景可去，背景和主体颜色太接近"
    elif touch > 0.25:
        info["reason"] = "主体和背景分不开（颜色太像）"

    return out, score, info


def from_mask_png(img: Image.Image, mask_png: bytes, *, feather: float = 1.0):
    """用手动画好的蒙版（PNG 的 alpha）来抠图。"""
    import io

    alpha_src = Image.open(io.BytesIO(mask_png))
    # 画布导出的 PNG 是带 alpha 的，蒙版就在 alpha 通道里；
    # 如果是灰度图，才退回去读亮度。
    if alpha_src.mode in ("RGBA", "LA"):
        alpha = alpha_src.getchannel("A")
    else:
        alpha = alpha_src.convert("L")
    alpha = alpha.resize(img.size, Image.LANCZOS)
    alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    out = img.convert("RGBA")
    out.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 16 else 0).getbbox()
    if not box:
        return None, 0.0, {"mode": "manual", "reason": "蒙版是空的"}
    pad = 2
    x0, y0, x1, y1 = box
    out = out.crop((max(0, x0 - pad), max(0, y0 - pad),
                    min(img.width, x1 + pad), min(img.height, y1 + pad)))
    return out, 1.0, {"mode": "manual", "foreground": round(float(box[2] - box[0]) / img.width, 3)}
