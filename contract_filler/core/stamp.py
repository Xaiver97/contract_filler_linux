# -*- coding: utf-8 -*-
"""
合同编号（红色印章式编码）专用渲染器 — 位图贴图版。

原理
----
模板 assets/template_new.jpg 在合同右上方"合同编号：SQJXNCJQYXQOY"后面原本
预印红色"2520471"，已通过 PS 擦除。本模块用提供的红笔手写数字位图（assets/
code_digits/0.png..9.png）按相同位置、字高、间距贴回，并加入轻微抖动模拟
自然手写。每次随机生成 25 + 5 位数字编号（前缀 25 固定，后 5 位随机）。

设计要点
--------
1. **位图直接贴图**——不走六层手写渲染（手写流程针对印刷体反而破坏字形）；
2. **字符 advance 自适应**——可按原模板上"2520471"的实际像素间距配置；
3. **微变形**——每个数字贴图时随机 ±0.5° 旋转、±1 px 抖动；
4. **不需擦除**——template_new.jpg 已是擦除版，直接贴图即可；
5. **随机生成**——若 data 未提供 contract_code，按 prefix+length 位随机。

用法
----
    from core.stamp import regenerate_contract_code
    regenerate_contract_code(canvas, composer)  # 自动读 config + 改写图
"""

from __future__ import annotations

import random
import string
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------
def _default_digits_dir(base_dir: Path) -> Path:
    """位图素材默认目录：assets/code_digits/。"""
    return base_dir / "assets" / "code_digits"


def _load_digits(digits_dir: Path) -> dict:
    """从目录加载 0-9.png 至 9.png 的 RGBA 位图。"""
    digits = {}
    for i in range(10):
        p = digits_dir / f"{i}.png"
        if not p.exists():
            raise FileNotFoundError(
                f"合同编码数字位图缺失: {p}。"
                "请确保 assets/code_digits/0.png..9.png 存在。"
            )
        digits[i] = Image.open(p).convert("RGBA")
    return digits


# ---------------------------------------------------------------------------
# 随机生成
# ---------------------------------------------------------------------------
def generate_random_code(prefix: str = "25",
                         length: int = 5,
                         rng: Optional[random.Random] = None,
                         charset: str = string.digits) -> str:
    """生成形如 25xxxxx 的合同编号。prefix 不变，长度位随机数字。"""
    rng = rng or random.Random()
    tail = "".join(rng.choice(charset) for _ in range(length))
    return f"{prefix}{tail}"


# ---------------------------------------------------------------------------
# 单字符贴图（带微变形）
# ---------------------------------------------------------------------------
def _paste_digit(canvas: Image.Image,
                 digit_img: Image.Image,
                 x: int, y: int,
                 target_height: int,
                 rng: random.Random,
                 chaos: float = 1.0):
    """把单数字位图缩放到 target_height 高度后贴到 canvas (x, y) 位置。

    缩放保持宽高比（避免变形）；贴图前对位图做 ±0.5°×chaos 旋转和 ±1×chaos
    像素 Y 抖动，模拟真实手写不齐。
    """
    scale = target_height / digit_img.height
    new_w = max(1, int(round(digit_img.width * scale)))
    new_h = target_height
    resized = digit_img.resize((new_w, new_h), Image.LANCZOS)

    # 微抖动（旋转、Y 偏移）
    dy = int(round(rng.uniform(-1.0, 1.0) * chaos))
    angle = rng.uniform(-0.5, 0.5) * chaos
    if abs(angle) > 0.01:
        resized = resized.rotate(angle, resample=Image.BICUBIC, expand=True)

    canvas.paste(resized, (int(x), int(y + dy)), resized)
    return resized.width


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def regenerate_contract_code(canvas: Image.Image, composer) -> str:
    """读取 composer.config 中 stamp 块的配置，在 canvas 上贴图绘制合同编码。

    自动从 composer.config["stamp"] 读取 x/y/prefix/length/digits_dir/
    char_advance/target_height/chaos 等配置；
    value 由调用方预先写入 data["contract_code"]，若 data 未提供则随机生成 25xxxxx。

    返回
    ----
    实际写入的合同编号字符串。
    """
    cfg = composer.config.get("stamp")
    if not cfg or not cfg.get("enabled", True):
        return ""

    # 取值
    data = getattr(composer, "_last_data", {}) or {}
    val = data.get("contract_code")
    rng = random.Random()
    if not val:
        val = generate_random_code(
                prefix=cfg.get("prefix", "25"),
                length=int(cfg.get("length", 5)),
                rng=rng)

    # 加载位图素材（缓存于 composer 上避免重复 IO）
    digits = getattr(composer, "_stamp_digits", None)
    if digits is None:
        dd = Path(cfg.get("digits_dir") or
                  str(_default_digits_dir(composer.base_dir)))
        digits = _load_digits(dd)
        composer._stamp_digits = digits

    # 坐标按 reference_size 缩放
    sc = composer.scale
    x0 = float(cfg.get("x", 838)) * float(sc)
    y0 = float(cfg.get("y", 86)) * float(sc)
    char_advance = float(cfg.get("char_advance", 16)) * float(sc)
    target_height = float(cfg.get("target_height", 26)) * float(sc)
    chaos = float(cfg.get("chaos", 1.0))

    # 逐字符贴图
    x = x0
    for ch in val:
        d = int(ch)
        w = _paste_digit(canvas, digits[d], int(x), int(y0),
                         int(round(target_height)), rng, chaos=chaos)
        # 推进：按 char_advance 推进，但参考实际字符宽度自适应（避免太挤）
        # 经验：目标字符 advance=18 时，缩放后字符宽 ~13-14，间隙 ~4-5
        x += char_advance

    return val