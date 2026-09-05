# -*- coding: utf-8 -*-
"""
手写渲染器 —— 六层模型的核心执行者。

渲染一段文字的完整流程：
    L1 字形层  选择手写 TTF，逐字随机字号（同字异形近似）
    L2 笔触层  逐字随机浓淡 alpha（压力映射的简化）
    L3 布局层  平滑噪声驱动基线漂移 + 蛇形 + 疲劳 + 微颤
    L4 墨迹层  高斯模糊 + 提对比（墨渗纸纤维）+ 噪点
    L5 融合层  采样背景色推导墨色，褪色后合成回画布

所有随机都可通过 seed 控制：预览时固定 seed（效果稳定可调参），
正式生成时 seed=None（每份都独一无二）。
"""

import math
import random
from dataclasses import dataclass, field, asdict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .ink import (apply_ink_bleed, compute_ink_color, fade,
                  match_scan_texture, sample_background, soften_region)
from .noise import BaselineDrifter
from .strokes import perturb_strokes


def _is_digit(ch: str) -> bool:
    """判断单字符是否数字（含阿拉伯数字 0-9 和全角数字）。"""
    if not ch:
        return False
    return ch.isdigit() or ch in "０１２３４５６７８９"


def _clamp(v, lo=0.0, hi=255.0):
    return max(lo, min(hi, v))


@dataclass
class StrokeStyle:
    """笔迹风格参数 —— 一一对应 GUI 上的滑杆。"""

    font_path: str = ""

    # ---- 基础 ----
    size: float = 30.0            # 基准字号
    line_spacing: float = 1.35    # 行距倍数（多行文本时）

    # ---- L1 字形层 ----
    size_jitter: float = 2.5      # 字号抖动：同一段文字前后不一样大

    # ---- L2 笔触层 ----
    alpha_min: float = 0.92       # 最淡笔画（提高到 0.92，避免边缘像素过虚）
    alpha_max: float = 1.00       # 最浓笔画
    ink_jitter: int = 10          # 墨色浓淡抖动
    ink_depth: float = 0.10       # 墨色压暗系数（越小越黑；0.10≈真实墨迹）
    ink_warm: float = 4.0         # 暖偏移，匹配泛黄纸张

    # ---- L2b 笔画分解层（A：逐笔画扰动 / B：超采样）----
    stroke_supersample: int = 4   # 单字超采样倍数（B 阶段：画大再缩回）
    stroke_dx: float = 1.1        # 单笔画横向扰动（目标像素，随字号缩放）
    stroke_dy: float = 1.1        # 单笔画纵向扰动
    stroke_rot: float = 1.0       # 单笔画绕自身中心旋转（度）
    stroke_min_pixels: int = 4    # 小于该像素数的"笔画"视为噪声，并入背景块

    # ---- L2c 笔画质感（C：粗细不均/压感；D：断墨飞白）----
    stroke_growth: float = 0.10   # C 每笔沿次要轴粗细高斯方差(比例)；0=关闭
    stroke_pressure: float = 0.22 # C 起笔重/收笔轻幅度(0~0.6)，沿主轴两端 alpha 渐变
    stroke_dry: float = 0.15      # D 断墨/飞白强度(0~0.6)；随 chaos 缩放

    # ---- L3 布局层 ----
    rot_jitter: float = 2.0       # 单字旋转抖动（度）
    pos_jitter: float = 1.6       # 单字位置抖动（像素）
    spacing_jitter: float = 1.2   # 字间距抖动（保留兼容，默认对中文）
    char_spacing: float = 0.02    # 文字（汉字）字间距倍率；正=字与字之间留点空
    digit_spacing: float = -0.18  # 数字字间距倍率；负=紧凑写，避免数字太散
    drift_amp: float = 2.4        # 手臂漂移幅度
    drift_scale: float = 45.0     # 手臂漂移波长（越大越缓）
    snake_amp: float = 1.1        # 蛇形弯曲幅度
    snake_period: float = 7.0     # 蛇形周期（字）
    fatigue: float = 1.8          # 疲劳下移总量
    tremor: float = 0.7           # 肌肉微颤

    # ---- L4 墨迹层 ----
    bleed_sigma: float = 0.45     # 墨迹渗透模糊半径
    bleed_contrast: float = 1.18  # 渗透后对比度补偿
    grain_sigma: float = 5.5      # 噪点强度

    # ---- L5 融合层 ----
    fade_strength: float = 0.97   # 褪色（0.97≈轻度褪色，几乎不影响笔画主体）
    region_blur: float = 0.30     # 贴回后区域柔化

    # ---- 总开关 ----
    chaos: float = 1.0            # 混乱程度 0~1.5，统一缩放上述所有抖动

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


class HandwriteRenderer:
    """把文字渲染到 PIL 画布上，带完整的手写扫描质感。"""

    def __init__(self, default_style: StrokeStyle = None,
                 font_fallback: str = None):
        self.style = default_style or StrokeStyle()
        self.font_fallback = font_fallback

    # ------------------------------------------------------------------ #
    #  主入口
    # ------------------------------------------------------------------ #
    def draw_text(self, canvas: Image.Image, text, x, y,
                  style: StrokeStyle = None, seed=None,
                  anchor: str = "lm", max_width: int = None,
                  bg=None) -> Image.Image:
        """在画布上写一段手写文字。

        参数
        ----
        canvas : PIL.Image
            合同底图，会被原地修改（要求 RGB 模式）。
        text : str
            待写内容，支持 \\n 换行。
        x, y : float
            锚点坐标，含义由 anchor 决定。
        style : StrokeStyle
            笔迹风格；None 则使用渲染器默认风格。
        seed : int | None
            随机种子。预览时给固定值保证效果稳定；正式生成时给 None。
        anchor : str
            'lm' 左中（默认，适合填空）、'lt' 左上、'lb' 左基线。
        max_width : int | None
            若指定，文本超宽时自动整体缩小字号以适应。
        bg : tuple | None
            纸张底色；为 None 时自动采样。

        返回
        ----
        canvas（原地修改后的同一对象）
        """
        st = style or self.style
        if text is None:
            return canvas
        text = str(text)
        if not text.strip():
            return canvas

        rng = random.Random(seed)
        np_rng = np.random.default_rng(seed)

        lines = text.split("\n")
        font_path = st.font_path or self.font_fallback

        # 超宽自动缩放
        eff_size = st.size
        if max_width:
            eff_size = self._fit_size(lines, font_path, st, max_width, eff_size)

        line_h = eff_size * st.line_spacing
        total_h = line_h * len(lines)

        # 采样纸张底色 → 推导墨色（整段统一，保证色调一致）
        if bg is None:
            probe_w = self._measure_line(max(lines, key=self._len_of),
                                         font_path, eff_size) + 40
            y0 = y - total_h / 2 - 10
            bg = sample_background(
                canvas, (x - 10, y0, x + probe_w, y0 + total_h + 20))
        base_ink = compute_ink_color(bg, depth=st.ink_depth,
                                     warm_bias=st.ink_warm,
                                     jitter=st.ink_jitter, rng=rng)

        # 逐行渲染
        for li, line in enumerate(lines):
            if not line:
                continue
            line_y = y + li * line_h
            line_seed = None if seed is None else seed + li * 977
            self._draw_line(canvas, line, x, line_y, st, eff_size,
                            font_path, base_ink, line_seed, anchor,
                            len(lines), np_rng, bg)

        return canvas

    # ------------------------------------------------------------------ #
    #  单行渲染（含全部六层处理）
    # ------------------------------------------------------------------ #
    def _draw_line(self, canvas, text, x, y, st, size, font_path,
                   base_ink, seed, anchor, n_lines, np_rng, bg):
        rng = random.Random(seed)
        drifter = BaselineDrifter(seed=seed)
        chars = list(text)
        n = len(chars)

        # --- 预测每个字符的推进宽度，用于确定画布尺寸 ---
        advances = []
        for ch in chars:
            if ch == " ":
                advances.append(size * 0.42)
            else:
                f = self._font(font_path, size)
                advances.append(self._char_width(f, ch) or size)
        total_w = sum(advances) + st.spacing_jitter * max(0, n - 1)

        # --- 画布余量：容纳旋转、漂移与抖动 ---
        v_margin = int(st.drift_amp + st.snake_amp + st.fatigue +
                       st.tremor + st.pos_jitter + size * 0.45) + 24
        h_margin = int(size * 0.6) + 16
        W = int(total_w) + h_margin * 2 + 8
        H = int(size * 1.5) + v_margin * 2

        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

        # --- 逐字渲染 ---
        cx = float(h_margin)
        center_y = H / 2.0
        total_advance = max(1e-6, total_w)

        for idx, ch in enumerate(chars):
            if ch == " ":
                cx += size * 0.42
                continue

            adv = advances[idx]
            # L3：基线漂移（低频手臂 + 中频蛇形 + 线性疲劳 + 高频微颤）
            dy = drifter.offset_y(
                idx, n, cx,
                drift_amp=st.drift_amp, drift_scale=st.drift_scale,
                snake_amp=st.snake_amp, snake_period=st.snake_period,
                fatigue=st.fatigue, tremor=st.tremor, chaos=st.chaos)
            dx = drifter.offset_x(st.pos_jitter, st.chaos)

            cx_char = (cx / total_advance) * max(1.0, total_advance)
            ch_layer = self._render_char(
                ch, st, size, font_path, base_ink, rng, seed=cx_char)

            px = int(round(cx + dx + adv / 2.0 - ch_layer.width / 2.0))
            py = int(round(center_y + dy - ch_layer.height / 2.0))
            px = max(0, min(W - 1, px))
            py = max(0, min(H - 1, py))
            layer.alpha_composite(ch_layer, (px, py))

            # 字间距：根据"本字符→下一字符"的类型决定（汉字 vs 数字）
            nxt = chars[idx + 1] if idx + 1 < n else ""
            if nxt == " ":
                base_gap = size * 0.30
            elif _is_digit(ch) and _is_digit(nxt):
                # 数字 ↔ 数字：用紧凑系数（负值=字符宽度外再压缩）
                base_gap = -adv * abs(st.digit_spacing)
            elif _is_digit(ch) or _is_digit(nxt):
                # 数字 ↔ 汉字：用较紧凑系数（数字旁不要离汉字太远）
                base_gap = -adv * abs(st.digit_spacing) * 0.5
            else:
                # 汉字 ↔ 汉字：用文字系数
                base_gap = adv * st.char_spacing

            cx += drifter.advance(adv + base_gap, st.spacing_jitter, st.chaos)

        # --- L4 + L5：墨迹渗透、噪点、褪色 ---
        layer = match_scan_texture(
            layer, bg,
            bleed_sigma=st.bleed_sigma,
            bleed_contrast=st.bleed_contrast,
            grain_sigma=st.grain_sigma,
            fade_strength=st.fade_strength,
            rng=np_rng)

        # --- 贴回画布（按 anchor 对齐）---
        if anchor == "lt":
            top = y
        elif anchor == "lb":
            top = y - center_y - size * 0.15
        else:  # 'lm' 左中：文本视觉中心对齐 y
            top = y - center_y
        left = x - h_margin

        px, py = int(round(left)), int(round(top))
        # 处理负坐标：alpha_composite 不支持，改用 paste 蒙版合成
        self._blend(canvas, layer, px, py)

        # 区域柔化，匹配扫描件锐度
        soften_region(canvas, (px, py, px + layer.width, py + layer.height),
                      st.region_blur)
        return canvas

    # ------------------------------------------------------------------ #
    #  单字符渲染（L1 字形 + L2 笔触 + 旋转）
    # ------------------------------------------------------------------ #
    def _render_char(self, ch, st, size, font_path, base_ink, rng,
                     seed=0.0):
        # L1：随机字号 —— 同一段文字前后不一样大
        cs = size + rng.uniform(-st.size_jitter, st.size_jitter) * st.chaos
        cs = max(8.0, cs)
        font = self._font(font_path, cs)

        try:
            bbox = font.getbbox(ch)
        except Exception:
            bbox = (0, 0, int(cs), int(cs))
        x0, y0, x1, y1 = bbox
        w = max(1, x1 - x0)
        h = max(1, y1 - y0)

        pad = int(cs * 0.5) + 8
        Wc, Hc = w + pad * 2, h + pad * 2

        # L2：逐字浓淡（压力映射的简化表达）
        jitter = st.ink_jitter
        # 三通道必须用同一个 delta，绝不能 per-channel 独立抖动
        # —— 否则即使 base_ink 是灰度 (v,v,v)，每个字符也会被抖成不同色相，
        # 在泛黄模板上叠加呈现"五颜六色"的彩字。
        delta = rng.randint(-jitter, jitter)
        ink = tuple(int(_clamp(c + delta)) for c in base_ink)
        alpha = int(255 * _clamp(rng.uniform(st.alpha_min, st.alpha_max), 0, 1))

        # ---- L2b（A+B 阶段）：超采样 + 逐笔画扰动 ----
        # 原理：
        #   * 先放大 S 倍画出单字灰度覆盖图 —— 扰动只在"高分辨率"下做，
        #     缩回后既平滑又保留笔画级错动（小字号也看得出手写感）；
        #   * 把字形按连通域拆成若干"笔画块"，每块独立随机平移+旋转；
        #   * 最后 LANCZOS 缩回目标字号。
        # 这样同一字里不同笔画会互相错动、开叉，不再像整字刚体那样整齐。
        S = max(1, int(getattr(st, "stroke_supersample", 1) or 1))
        sdx = getattr(st, "stroke_dx", 0.0) * st.chaos
        sdy = getattr(st, "stroke_dy", 0.0) * st.chaos
        srot = getattr(st, "stroke_rot", 0.0) * st.chaos
        # C/D 质感（growth/pressure/dry 本身就是无量纲比例，随 chaos 缩放）
        sgrowth = getattr(st, "stroke_growth", 0.0) * st.chaos
        spress = getattr(st, "stroke_pressure", 0.0) * st.chaos
        sdry = getattr(st, "stroke_dry", 0.0) * st.chaos

        if S <= 1 or (abs(sdx) <= 0 and abs(sdy) <= 0 and abs(srot) <= 0
                      and abs(sgrowth) <= 0 and spress <= 0 and sdry <= 0):
            # 关闭超采样/扰动 → 走原"整字 + 加粗"路径，行为一致
            layer = self._render_char_plain(ch, font, cs, Wc, Hc, bbox,
                                            ink, alpha, rng, st)
        else:
            # 超采样渲染：S 倍尺寸的透明画布，把字形画上去取灰度覆盖
            SW, SH = Wc * S, Hc * S
            font_big = self._font(font_path, cs * S)
            big = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
            ImageDraw.Draw(big).text(
                (SW / 2.0, SH / 2.0), ch, font=font_big,
                fill=(255, 255, 255, 255), anchor="mm")
            cov = big.split()[3]  # alpha 通道即灰度覆盖 0~255

            # 逐笔画处理：sigma 按超采样倍数放大（保持"目标像素"观感一致）
            # C/D 的 growth/pressure/dry 是无量纲系数，不随 S 放大。
            arr = np.asarray(cov, dtype=np.uint8)
            np_rng = np.random.default_rng(
                None if seed is None else int(abs(seed) * 1e3) + 7)
            arr = perturb_strokes(arr, sdx * S, sdy * S,
                                  math.radians(srot), np_rng,
                                  growth_sigma=abs(sgrowth),
                                  pressure=spress, dry=sdry)
            cov = Image.fromarray(arr, "L")

            # 缩回目标字号 —— LANCZOS 平滑边缘并保留笔画错动
            cov = cov.resize((Wc, Hc), Image.LANCZOS)

            # 墨色 RGB 恒定（已灰度），只有 alpha 带笔画浓淡/覆盖
            r = g = b = ink[0]
            layer = Image.merge("RGBA", (
                Image.new("L", (Wc, Hc), r),
                Image.new("L", (Wc, Hc), g),
                Image.new("L", (Wc, Hc), b),
                cov.point(lambda v: int(v * alpha / 255.0))))

        # 单字旋转（绕自身中心）—— 保留整字的小角度旋转，叠加在笔画扰动之上
        ang = rng.uniform(-st.rot_jitter, st.rot_jitter) * st.chaos
        if abs(ang) > 0.05:
            layer = layer.rotate(ang, resample=Image.BICUBIC, expand=True)

        return layer

    def _render_char_plain(self, ch, font, cs, Wc, Hc, bbox, ink, alpha,
                           rng, st):
        """原整字渲染路径（超采样关闭时兜底）：居中 + 加粗 + 强制灰度。"""
        x0, y0, x1, y1 = bbox
        layer = Image.new("RGBA", (Wc, Hc), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        dx = Wc / 2.0 - (x0 + x1) / 2.0
        dy = Hc / 2.0 - (y0 + y1) / 2.0
        for ox, oy in [(0, 0), (0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6)]:
            d.text((dx + ox, dy + oy), ch, font=font, fill=(*ink, alpha))
        r_ch, g_ch, b_ch, a_ch = layer.split()
        rgb = Image.merge("RGB", (r_ch, g_ch, b_ch)).convert("L")
        return Image.merge("RGBA", (rgb, rgb, rgb, a_ch))

    # ------------------------------------------------------------------ #
    #  勾选项：手绘勾号（比字体勾自然得多）
    # ------------------------------------------------------------------ #
    def draw_check(self, canvas, x, y, size: float = 24.0,
                   style: StrokeStyle = None, seed=None,
                   box: bool = False, bg=None) -> Image.Image:
        """画一个手绘勾号。

        参数
        ----
        x, y : float
            勾号外接框左上角。
        size : float
            勾号尺寸（边长）。
        box : bool
            True 则连同方框一起画（用于模板上原本没有框的情况）。
        """
        st = style or self.style
        rng = random.Random(seed)
        np_rng = np.random.default_rng(seed)

        if bg is None:
            bg = sample_background(canvas, (x - 6, y - 6,
                                            x + size + 6, y + size + 6))
        ink = compute_ink_color(bg, depth=st.ink_depth,
                                warm_bias=st.ink_warm,
                                jitter=st.ink_jitter, rng=rng)

        pad = int(size * 0.7) + 10
        W = int(size) + pad * 2
        H = int(size) + pad * 2
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)

        if box:
            bw = max(1.0, size * 0.07)
            d.rectangle([pad - 1, pad - 1, pad + size + 1, pad + size + 1],
                        outline=(*ink, 235), width=int(bw))

        # 勾的三个关键点（先短下探，再长上扬）
        p0 = (pad + size * 0.06, pad + size * 0.56)
        p1 = (pad + size * 0.30, pad + size * 0.92)
        p2 = (pad + size * 1.00, pad + size * 0.04)

        line_w = max(1.2, size * 0.115)
        self._hand_stroke(d, [p0, p1], line_w, ink, rng)
        self._hand_stroke(d, [p1, p2], line_w * 1.02, ink, rng)

        layer = match_scan_texture(
            layer, bg,
            bleed_sigma=st.bleed_sigma,
            bleed_contrast=st.bleed_contrast,
            grain_sigma=st.grain_sigma,
            fade_strength=min(1.0, st.fade_strength + 0.02),
            rng=np_rng)

        self._blend(canvas, layer, int(x - pad), int(y - pad))
        soften_region(canvas, (x - pad, y - pad,
                               x - pad + layer.width,
                               y - pad + layer.height), st.region_blur)
        return canvas

    def draw_radio(self, canvas, x, y, size: float = 20.0,
                   style: StrokeStyle = None, seed=None,
                   filled: bool = True, bg=None):
        """画一个手绘圆圈（限额方案等多选一场景）。"""
        st = style or self.style
        rng = random.Random(seed)
        np_rng = np.random.default_rng(seed)

        if bg is None:
            bg = sample_background(canvas, (x - 6, y - 6,
                                            x + size + 6, y + size + 6))
        ink = compute_ink_color(bg, depth=st.ink_depth,
                                warm_bias=st.ink_warm,
                                jitter=st.ink_jitter, rng=rng)

        pad = int(size * 0.8) + 12
        W = H = int(size) + pad * 2
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)

        cx = cy = W / 2.0
        r = size / 2.0
        line_w = max(1.2, size * 0.10)

        # 用 36 段折线近似圆，每段端点加抖动 → 手绘感
        pts = []
        steps = 36
        for i in range(steps + 1):
            a = (i / steps) * math.tau - math.pi * 0.5
            rr = r * rng.uniform(0.93, 1.07)
            pts.append((cx + math.cos(a) * rr, cy + math.sin(a) * rr))
        self._hand_stroke(d, pts, line_w, ink, rng, closed=True)

        if filled:
            # 圈内再补一个小实心点，模拟"重重地圈一下"
            rr = r * 0.28
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                      fill=(*ink, int(255 * rng.uniform(0.75, 0.92))))

        layer = match_scan_texture(
            layer, bg,
            bleed_sigma=st.bleed_sigma,
            bleed_contrast=st.bleed_contrast,
            grain_sigma=st.grain_sigma,
            fade_strength=min(1.0, st.fade_strength + 0.02),
            rng=np_rng)

        self._blend(canvas, layer, int(x - pad), int(y - pad))
        soften_region(canvas, (x - pad, y - pad,
                               x - pad + layer.width,
                               y - pad + layer.height), st.region_blur)
        return canvas

    # ------------------------------------------------------------------ #
    #  底层工具
    # ------------------------------------------------------------------ #
    def _hand_stroke(self, draw, pts, width, ink, rng, closed=False):
        """用一串小圆点画折线，模拟笔触：有粗细变化、有轻微抖动。"""
        if closed:
            pts = list(pts) + [pts[0]]
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            dist = math.hypot(x1 - x0, y1 - y0)
            steps = max(2, int(dist))
            for s in range(steps + 1):
                t = s / steps
                px = x0 + (x1 - x0) * t + rng.uniform(-0.35, 0.35)
                py = y0 + (y1 - y0) * t + rng.uniform(-0.35, 0.35)
                ww = width * rng.uniform(0.82, 1.18)
                draw.ellipse([px - ww / 2, py - ww / 2,
                              px + ww / 2, py + ww / 2],
                             fill=(*ink, int(255 * rng.uniform(0.88, 1.0))))

    def _blend(self, canvas, layer, px, py):
        """把 RGBA 图层合成到画布，支持负坐标（自动裁剪）。"""
        if layer.width <= 0 or layer.height <= 0:
            return
        # 完全在画布外则跳过
        if px >= canvas.width or py >= canvas.height:
            return
        if px + layer.width <= 0 or py + layer.height <= 0:
            return

        if px >= 0 and py >= 0 and \
           px + layer.width <= canvas.width and \
           py + layer.height <= canvas.height:
            canvas.alpha_composite(layer, (px, py))
            return

        # 裁剪到画布内
        sx = max(0, -px)
        sy = max(0, -py)
        dx = max(0, px)
        dy = max(0, py)
        w = min(layer.width - sx, canvas.width - dx)
        h = min(layer.height - sy, canvas.height - dy)
        if w <= 0 or h <= 0:
            return
        canvas.alpha_composite(layer.crop((sx, sy, sx + w, sy + h)), (dx, dy))

    def _font(self, path, size):
        size = max(6, int(round(size)))
        if path:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
        if self.font_fallback:
            try:
                return ImageFont.truetype(self.font_fallback, size)
            except Exception:
                pass
        return ImageFont.load_default(size)

    @staticmethod
    def _char_width(font, ch) -> float:
        try:
            bbox = font.getbbox(ch)
            return float(bbox[2] - bbox[0])
        except Exception:
            return 0.0

    @staticmethod
    def _len_of(s):
        return len(s)

    def _measure_line(self, text, font_path, size) -> float:
        f = self._font(font_path, size)
        return sum(self._char_width(f, c) if c != " " else size * 0.42
                   for c in text)

    def _fit_size(self, lines, font_path, st, max_width, size):
        """文本超出可用宽度时，按比例缩小字号。"""
        if not max_width or max_width <= 0:
            return size
        longest = max(lines, key=self._len_of)
        for _ in range(12):
            w = self._measure_line(longest, font_path, size)
            if w <= max_width or size <= 9:
                break
            size *= max(0.85, (max_width / max(w, 1e-6)) ** 0.92)
        return size
