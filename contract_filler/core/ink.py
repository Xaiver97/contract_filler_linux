# -*- coding: utf-8 -*-
"""
墨迹渗透与背景融合 —— 本项目最关键的一层。

萝卜工坊是在"自己生成的干净纸张"上写字，纸墨天生一致，所以不需要这层。
我们是在"已有的泛黄扫描件"上写字，如果直接贴纯黑字，必然"跳出来"：

    扫描件原有文字          直接贴的新字          观感
    ----------------      ----------------      ------------------
    墨色偏暖深灰           纯 (0,0,0)          死黑，不融合
    边缘有扫描模糊         锐利抗锯齿边缘        太干净
    带 JPEG 压缩噪点       完全无噪点           浮在纸面上
    整体泛黄、光照不均     与背景无关            色调割裂

本模块通过四个手段消除上述破绽：
    1. 背景色采样 —— 墨色跟随背景，而非固定纯黑
    2. 墨迹渗透   —— 轻微模糊+提对比，模拟墨渗入纸纤维
    3. 噪点匹配   —— 给字图层加噪，与扫描件颗粒感一致
    4. 整体融合   —— 贴回画布后统一做褪色+柔化
"""

import random

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

# 扫描件常见纸张底色（采样失败时的兜底）
FALLBACK_PAPER = (245, 243, 238)


def _clamp(v, lo=0.0, hi=255.0):
    return max(lo, min(hi, v))


def sample_background(canvas: Image.Image, box, percentile: float = 75.0):
    """采样写字区域的纸张底色。

    取亮部像素的均值，以避开区域内可能已有的印刷字、下划线、印章。

    参数
    ----
    canvas : PIL.Image
        合同底图（任意模式，内部按 RGB 处理）。
    box : (x0, y0, x1, y1)
        待写字的外接框。
    percentile : float
        亮度分位阈值，越高越只取最亮的像素（更"纯"的纸色）。

    返回
    ----
    (r, g, b)
    """
    try:
        x0, y0, x1, y1 = [int(round(v)) for v in box]
    except Exception:
        return FALLBACK_PAPER

    W, H = canvas.size
    x0, y0 = max(0, min(W, x0)), max(0, min(H, y0))
    x1, y1 = max(0, min(W, x1)), max(0, min(H, y1))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return FALLBACK_PAPER

    region = canvas.convert("RGB").crop((x0, y0, x1, y1))
    arr = np.asarray(region, dtype=np.float32)
    if arr.size == 0:
        return FALLBACK_PAPER

    flat = arr.reshape(-1, 3)
    lum = flat.mean(axis=1)
    thr = np.percentile(lum, percentile)
    bright = flat[lum >= thr]
    if bright.shape[0] == 0:
        bright = flat

    mean = bright.mean(axis=0)
    return (int(_clamp(mean[0])), int(_clamp(mean[1])), int(_clamp(mean[2])))


def compute_ink_color(bg, depth: float = 0.16, warm_bias: float = 0.0,
                      jitter: int = 14, rng: random.Random = None):
    """由纸张底色推导墨色 —— 纯灰度，绝不偏移色相。

    早期版本会让 R/G/B 各自抖动，结果在某些泛黄纸张上出现肉眼可见的
    "红字"或"绿字"。这次直接锁死灰度：R = G = B = bg_luma * depth + jitter。
    浓淡随机也用同一个通道，整体只在黑（dark）→ 中灰（mid gray）→ 浅灰（light gray）
    之间变化，与扫描件原件完全同色系。

    参数
    ----
    bg : (r,g,b)
        采样到的纸张底色。
    depth : float
        压暗系数。越小越黑（0.08~0.18 常见），越大越淡（旧笔、快写完）。
    warm_bias : float
        保留参数兼容性，但已不生效（强制为 0）。
    jitter : int
        单字浓淡随机范围，模拟下笔力度差异。
    """
    rng = rng or random
    # 用亮度而非 R 通道作基色，避免偏色
    luma = sum(bg) / 3.0
    v = luma * depth + rng.randint(0, jitter)
    v = int(_clamp(v))
    return (v, v, v)


def apply_ink_bleed(layer: Image.Image, sigma: float = 0.45,
                    contrast: float = 1.25) -> Image.Image:
    """墨迹渗透：轻微高斯模糊 + 提升对比度。

    直接模糊会让字发灰，所以模糊后必须提对比把笔画"压回去"，
    结果是边缘略微晕开而笔画主体仍然扎实 —— 这正是墨渗进纸纤维的样子。

    alpha 通道不再做额外模糊：PIL 字体本身就有抗锯齿的软边，
    再模糊会让笔画看起来"虚"，且让浅色 alpha 的边缘像素扩散变多。
    只对 RGB 做墨渗效果。
    """
    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")
    if (not sigma or sigma <= 0) and abs(contrast - 1.0) < 1e-6:
        return layer

    r, g, b, a = layer.split()
    rgb = Image.merge("RGB", (r, g, b))

    if sigma and sigma > 0:
        # 只模糊 RGB，不动 alpha —— 让笔画像素边界保持锐利
        rgb = rgb.filter(ImageFilter.GaussianBlur(sigma))

    if contrast and abs(contrast - 1.0) > 1e-6:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)

    return Image.merge("RGBA", (*rgb.split(), a))


def add_grain(layer: Image.Image, sigma: float = 6.0,
              rng: np.random.Generator = None) -> Image.Image:
    """给字图层叠加噪点，使其颗粒感与扫描件一致。

    噪点只作用于已着墨区域（按 alpha 加权），不会在透明区留下脏点。
    用单通道噪点同步加到 R/G/B 三通道，保证噪点是纯灰度，不会引入色偏。
    """
    if not sigma or sigma <= 0:
        return layer
    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")

    g = rng if rng is not None else np.random.default_rng()
    arr = np.asarray(layer, dtype=np.float32)
    # 用单通道噪点（不是三通道独立）→ 三个通道同步变化 → 仍是灰度
    noise = g.normal(0.0, sigma, arr.shape[:2]).astype(np.float32)

    alpha = arr[:, :, 3:4] / 255.0
    arr[:, :, :3] += noise[:, :, None] * alpha
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def fade(layer: Image.Image, strength: float = 0.94) -> Image.Image:
    """整体褪色：模拟墨水被纸吸收、以及扫描时的轻微透光。

    strength=1.0 表示不褪色，0.9 表示墨色整体变淡一成。
    """
    if not strength or strength >= 1.0:
        return layer
    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")

    a = layer.split()[3]
    a = a.point(lambda v: int(v * strength))
    r, g, b, _ = layer.split()
    return Image.merge("RGBA", (r, g, b, a))


def match_scan_texture(layer: Image.Image, bg,
                       bleed_sigma: float = 0.45,
                       bleed_contrast: float = 1.25,
                       grain_sigma: float = 6.0,
                       fade_strength: float = 0.94,
                       rng: np.random.Generator = None) -> Image.Image:
    """一次性完成"墨迹层 + 融合层"的全部后处理。

    这是文字贴回画布前的最后一道工序，顺序不可调换：
        渗透 -> 噪点 -> 褪色
    """
    layer = apply_ink_bleed(layer, bleed_sigma, bleed_contrast)
    layer = add_grain(layer, grain_sigma, rng)
    layer = fade(layer, fade_strength)
    return layer


def soften_region(canvas: Image.Image, box, blur: float = 0.3):
    """贴回画布后，对写入区域做极轻微的柔化。

    让新字的锐度与扫描件原有文字的锐度接近，消除"太清楚"的违和感。
    原地修改 canvas。
    """
    if not blur or blur <= 0:
        return canvas
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    W, H = canvas.size
    x0, y0 = max(0, min(W, x0)), max(0, min(H, y0))
    x1, y1 = max(0, min(W, x1)), max(0, min(H, y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return canvas

    patch = canvas.crop((x0, y0, x1, y1))
    patch = patch.filter(ImageFilter.GaussianBlur(blur))
    canvas.paste(patch, (x0, y0))
    return canvas
