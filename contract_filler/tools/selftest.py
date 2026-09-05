# -*- coding: utf-8 -*-
"""
项目自检 —— 不依赖 GUI，纯命令行验证渲染链路是否正常工作。

用途：
    1. 换机器 / 换字体后，快速确认渲染器、指纹、勾选都能跑通
    2. 生成 V1（朴素贴图）与 V2（六层模型）的对比图，直观看到差异
    3. 排查字体缺失、坐标越界等常见问题

用法::

    python tools/selftest.py                # 自动找系统字体
    python tools/selftest.py --font 路径.ttf # 指定字体
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import (FingerprintStamper, HandwriteRenderer,  # noqa: E402
                  StrokeStyle)

# Windows / Linux 常见中文字体候选（楷体优先，形态最接近手写）
CJK_CANDIDATES = [
    "C:/Windows/Fonts/simkai.ttf",     # 楷体
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
    "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
    "C:/Windows/Fonts/msyhl.ttc",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def find_cjk_font(explicit=None):
    """找一个能渲染中文的字体。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return str(p)
        print(f"[warn] 指定字体不存在：{explicit}")
    for c in CJK_CANDIDATES:
        if Path(c).exists():
            try:
                f = ImageFont.truetype(c, 32)
                if f.getbbox("电")[2] > 8:
                    return c
            except Exception:
                continue
    return None


def make_paper(w=1200, h=420, seed=7):
    """合成一张"泛黄扫描纸"背景：底色偏暖 + 光照不均 + 噪点 + 纤维。"""
    rng = np.random.default_rng(seed)
    base = np.array([244, 239, 228], dtype=np.float32)

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # 光照不均：左上偏亮，右下偏暗
    shade = 1.0 - 0.055 * (xx / w) - 0.045 * (yy / h)
    # 低频斑块（纸张不匀）
    blob = np.zeros((h, w), dtype=np.float32)
    for _ in range(14):
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        sx, sy = rng.uniform(90, 320), rng.uniform(70, 240)
        amp = rng.uniform(-3.2, 3.2)
        blob += amp * np.exp(-(((xx - cx) ** 2) / (2 * sx ** 2) +
                               ((yy - cy) ** 2) / (2 * sy ** 2)))

    img = base[None, None, :] * shade[:, :, None] + blob[:, :, None]
    img += rng.normal(0, 2.6, (h, w, 1)).astype(np.float32)   # 噪点
    img = np.clip(img, 0, 255).astype(np.uint8)
    return Image.fromarray(img, "RGB")


def draw_naive(canvas, text, x, y, font_path, size=40):
    """V1 对照组：最朴素的做法 —— 纯黑、规整、无抖动、无融合。"""
    d = ImageDraw.Draw(canvas)
    f = ImageFont.truetype(font_path, size)
    d.text((x, y), text, font=f, fill=(0, 0, 0))
    return canvas


def main():
    ap = argparse.ArgumentParser(description="手写渲染自检")
    ap.add_argument("--font", default=None, help="指定字体文件路径")
    ap.add_argument("--out", default=None, help="输出目录")
    ap.add_argument("--size", type=int, default=44, help="测试字号")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else (ROOT / "output" / "selftest")
    out_dir.mkdir(parents=True, exist_ok=True)

    font = find_cjk_font(args.font)
    if not font:
        print("[错误] 未找到可渲染中文的字体。")
        print("       请把任意 .ttf/.otf 字体放进 fonts/ 目录，或用 "
              "--font 指定路径。")
        return 2
    print(f"[ok] 使用字体: {font}")

    text = "已知晓自动停电规则，一欠费就停电"
    print(f"[ok] 测试文本: {text}")

    # ---------------- V1 vs V2 ----------------
    paper1 = make_paper().convert("RGBA")
    draw_naive(paper1, text, 60, 90, font, args.size)

    paper2 = make_paper().convert("RGBA")
    st = StrokeStyle(font_path=font, size=float(args.size))
    r = HandwriteRenderer(default_style=st)
    r.draw_text(paper2, text, 60, 110, st, seed=20260903, anchor="lm")

    # ---------------- 指纹 ----------------
    fp_dir = ROOT / "assets" / "fingerprints"
    stamp = FingerprintStamper(fp_dir)
    fp_ok = stamp.available
    print(f"[ok] 指纹素材: {stamp.count} 枚（真实指纹）" if fp_ok
          else "[warn] 无指纹素材，请放入真实指纹图（fp_real_*.png）到 assets/fingerprints/")

    paper3 = make_paper().convert("RGBA")
    r.draw_text(paper3, "张伟", 80, 150,
                StrokeStyle(font_path=font, size=52, size_jitter=5),
                seed=99, anchor="lm")
    if fp_ok:
        stamp.stamp(paper3, 190, 150, size=110, alpha=0.82, seed=5)

    # ---------------- 勾选项 ----------------
    paper4 = make_paper().convert("RGBA")
    cst = StrokeStyle(font_path=font, size=26)
    for i in range(3):
        r.draw_check(paper4, 80 + i * 260, 150, size=46,
                     style=cst, seed=10 + i, box=True)
        r.draw_text(paper4, ["一般工商业", "居民生活", "农业生产"][i],
                    80 + i * 260 + 60, 173, cst, seed=20 + i, anchor="lm")

    for i in range(2):
        r.draw_radio(paper4, 900 + i * 120, 150, size=42,
                     style=cst, seed=30 + i)

    # ---------------- 拼对比图 ----------------
    W = max(paper1.width, paper2.width, paper3.width, paper4.width)
    H = paper1.height * 2 + paper3.height + paper4.height + 40
    board = Image.new("RGB", (W, H), (60, 60, 60))
    d = ImageDraw.Draw(board)
    y = 0
    for img, title in ((paper1, "V1 朴素贴图：纯黑、规整、无融合"),
                       (paper2, "V2 六层模型：漂移+浓淡+渗透+噪点+背景融合"),
                       (paper3, "签名 + 红指纹"),
                       (paper4, "勾选框 与 单选圈")):
        board.paste(img.convert("RGB"), (0, y))
        d.text((14, y + 8), title, fill=(255, 225, 120))
        y += img.height + 8

    p = out_dir / "selftest_compare.png"
    board.save(p)

    # ---------------- 数值校验 ----------------
    arr = np.asarray(paper2.convert("RGB"))
    ink_ratio = float((arr.mean(axis=2) < 150).mean())
    print(f"[ok] V2 墨迹覆盖率: {ink_ratio:.3%} "
          f"（正常范围 1%~15%，过高说明糊成一团，过低说明没写上）")

    print(f"\n[完成] 对比图已保存: {p}")
    print("       请用图片查看器打开，重点看第二行是否比第一行自然。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
