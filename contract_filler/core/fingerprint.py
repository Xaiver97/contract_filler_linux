# -*- coding: utf-8 -*-
"""
红指纹叠加 —— 在签名处按上一枚红印泥。

真人按指纹的几个特征，这里都做了模拟：
    1. 角度随机    —— 不可能每次都正正好
    2. 力度不一    —— 整体浓淡随机
    3. 位置偏移    —— 按在签名上而非旁边，且每次略有偏差
    4. 多枚轮换    —— 同一批合同不会是同一枚指纹
    5. 子区域裁切  —— 真实指纹 PNG 通常含整张 565×580 像素，
                     而合同上只显示局部；这里从大图中随机切出一块，
                     既避免裁切到边角空白，也让每份合同都长得不一样
"""

import random
from pathlib import Path

import numpy as np
from PIL import Image

FP_EXTS = {".png", ".webp"}


class FingerprintStamper:
    """管理 assets/fingerprints/ 下的指纹素材并负责叠加。"""

    def __init__(self, fp_dir):
        self.fp_dir = Path(fp_dir)
        self._paths = None
        self._cache = {}

    # ---------- 素材 ----------
    def scan(self, force: bool = False):
        """扫描素材目录。

        只使用真实指纹素材 —— 文件名必须以 ``fp_real_`` 开头
        （用户提供或抠图的真实指纹 PNG）。程序不再支持/识别合成占位指纹，
        确保盖上去的一定是真实指纹图的旋转/缩放/裁切变体。
        """
        if self._paths is not None and not force:
            return self._paths
        if not self.fp_dir.exists():
            self._paths = []
            return self._paths
        # 仅真实指纹；合成占位（fp_red_* / fp_syn_*）一律忽略
        self._paths = sorted(
            p for p in self.fp_dir.iterdir()
            if p.is_file() and p.suffix.lower() in FP_EXTS
            and p.stem.startswith("fp_real"))
        return self._paths

    @property
    def available(self) -> bool:
        return len(self.scan()) > 0

    @property
    def count(self) -> int:
        return len(self.scan())

    @property
    def has_real(self) -> bool:
        return any(p.stem.startswith("fp_real") for p in self.scan())

    def _load(self, path: Path) -> Image.Image:
        key = str(path)
        if key not in self._cache:
            img = Image.open(key)
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            self._cache[key] = img
        return self._cache[key]

    def pick(self, rng: random.Random = None, index=None) -> Image.Image:
        """取一枚指纹（index 为 None 时随机）。"""
        paths = self.scan()
        if not paths:
            return None
        rng = rng or random
        if index is not None:
            p = paths[index % len(paths)]
        else:
            p = rng.choice(paths)
        return self._load(p).copy()

    # ---------- 子区域裁切（关键） ----------
    def _random_crop(self, fp: Image.Image, target: int,
                     rng: random.Random) -> Image.Image:
        """从大指纹图中随机切一块近似正方形的子区域。

        真实指纹图通常是整张 500~600 像素的椭圆，签章只用一小块；
        随机切+旋转+缩放可生成视觉上完全不同的指纹变体，避免雷同。

        若指纹本身就小于 target*1.4，则不裁切（避免越界）。
        """
        w, h = fp.size
        # 子区域边长：0.7~1.0 倍 target，模拟每次按的压力不同
        side = int(target * rng.uniform(0.85, 1.15))
        if w < side * 1.2 or h < side * 1.2:
            # 图太小，整体缩放即可，不再裁切
            return fp

        # 中心区域：椭圆分布（指纹几何中心比边缘更重要）
        # 在 (w,h) 中选一个中心点，sigma=0.18*w
        sigma_x = max(8.0, w * 0.18)
        sigma_y = max(8.0, h * 0.18)
        # 用 Box-Muller 在中心附近采一个高斯点
        cx = w / 2 + rng.gauss(0, sigma_x)
        cy = h / 2 + rng.gauss(0, sigma_y)

        # 限位
        half = side // 2
        cx = max(half, min(w - half, cx))
        cy = max(half, min(h - half, cy))

        x0 = int(cx - half)
        y0 = int(cy - half)
        x1 = x0 + side
        y1 = y0 + side

        # 椭圆蒙版：只保留指纹主体，边缘空白被裁掉
        crop = fp.crop((x0, y0, x1, y1)).convert("RGBA")
        if crop.size[0] > 4 and crop.size[1] > 4:
            arr = np.array(crop, dtype=np.uint8)  # 复制，避免只读
            cy_ = arr.shape[0] / 2.0
            cx_ = arr.shape[1] / 2.0
            yy, xx = np.mgrid[0:arr.shape[0], 0:arr.shape[1]]
            mask = ((xx - cx_) ** 2 / max(1.0, cx_ ** 2) +
                    (yy - cy_) ** 2 / max(1.0, cy_ ** 2)) <= 1.0
            arr[..., 3][~mask] = 0  # 椭圆外清空 alpha
            crop = Image.fromarray(arr, "RGBA")
        return crop

    # ---------- 盖章 ----------
    def stamp(self, canvas: Image.Image, cx, cy, size: float = 64.0,
              alpha: float = 0.82, angle=None, angle_range=(-32.0, 32.0),
              scale_range=(0.92, 1.10), offset_range=(-5.0, 5.0),
              seed=None, index=None, blend: str = "normal",
              crop_random: bool = True) -> bool:
        """在 (cx, cy) 处按一枚指纹。返回是否成功。

        参数
        ----
        canvas : PIL.Image
            目标画布（RGB），原地修改。
        cx, cy : float
            指纹**中心**坐标。
        size : float
            指纹目标边长（像素）。
        alpha : float
            印泥浓度 0~1，越大越浓。
        blend : 'normal' | 'multiply'
            multiply 更接近染料渗透感（下方笔迹仍可见），但会压暗背景。
        crop_random : bool
            是否从大指纹图中随机切子区域（避免每份都一模一样）。
        """
        rng = random.Random(seed)
        fp = self.pick(rng, index)
        if fp is None:
            return False

        # 随机缩放（按压力度不同，接触面积不同）
        sc = rng.uniform(*scale_range)
        target = max(16, int(size * sc))

        # 子区域裁切（关键：从真实大图中随机切块）
        if crop_random and fp.size[0] > target * 1.3:
            fp = self._random_crop(fp, target, rng)

        # 再缩放到目标尺寸
        if fp.size[0] != target:
            ratio = target / float(fp.size[0])
            fp = fp.resize((target, max(16, int(fp.size[1] * ratio))),
                           Image.LANCZOS)

        # 随机旋转
        if angle is None:
            angle = rng.uniform(*angle_range)
        if abs(angle) > 1e-3:
            fp = fp.rotate(angle, resample=Image.BICUBIC, expand=True)

        # 随机偏移
        ox = rng.uniform(*offset_range)
        oy = rng.uniform(*offset_range)

        # 浓度
        a = fp.split()[3]
        a = a.point(lambda v, k=alpha: int(v * k))
        r, g, b, _ = fp.split()
        fp = Image.merge("RGBA", (r, g, b, a))

        px = int(round(cx - fp.width / 2.0 + ox))
        py = int(round(cy - fp.height / 2.0 + oy))

        if blend == "multiply":
            self._blend_multiply(canvas, fp, px, py)
        else:
            self._blend_normal(canvas, fp, px, py)
        return True

    # ---------- 合成 ----------
    @staticmethod
    def _blend_normal(canvas, fp, px, py):
        W, H = canvas.size
        sx = max(0, -px)
        sy = max(0, -py)
        dx = max(0, px)
        dy = max(0, py)
        w = min(fp.width - sx, W - dx)
        h = min(fp.height - sy, H - dy)
        if w <= 0 or h <= 0:
            return
        canvas.alpha_composite(fp.crop((sx, sy, sx + w, sy + h)), (dx, dy))

    @staticmethod
    def _blend_multiply(canvas, fp, px, py):
        W, H = canvas.size
        sx = max(0, -px)
        sy = max(0, -py)
        dx = max(0, px)
        dy = max(0, py)
        w = min(fp.width - sx, W - dx)
        h = min(fp.height - sy, H - dy)
        if w <= 0 or h <= 0:
            return

        crop = fp.crop((sx, sy, sx + w, sy + h))
        bg = np.asarray(canvas.crop((dx, dy, dx + w, dy + h)).convert("RGB"),
                        dtype=np.float32)
        fa = np.asarray(crop, dtype=np.float32) / 255.0
        a = fa[:, :, 3:4]
        multiplied = bg * fa[:, :, :3]
        out = bg * (1.0 - a) + multiplied * a
        canvas.paste(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8),
                                     "RGB"), (dx, dy))
