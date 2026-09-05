# -*- coding: utf-8 -*-
"""
字体管理 —— 扫描 fonts 目录，支持"同字异形"的近似实现。

萝卜工坊的"AI 专属字体"依赖其服务端训练，本地无法复刻。
替代方案：准备多套手写 TTF，按户随机切换，即可模拟"不同人填写"。
"""

import random
from pathlib import Path

FONT_EXTS = {".ttf", ".otf", ".ttc"}

# 签名候选关键词：命中则优先作为签名字体
SIGN_HINTS = ("sign", "sig", "签名", "autograph", "署名")

# 用于检测字体是否支持中文
_CJK_PROBE = "电合"


class FontManager:
    """管理 fonts/ 目录下的全部字体文件。"""

    def __init__(self, font_dir):
        self.font_dir = Path(font_dir)
        self._cache = None

    # ---------- 扫描 ----------
    def scan(self, force: bool = False):
        """返回排序后的字体路径列表。"""
        if self._cache is not None and not force:
            return self._cache

        found = []
        if self.font_dir.exists():
            for p in sorted(self.font_dir.rglob("*")):
                if p.is_file() and p.suffix.lower() in FONT_EXTS:
                    found.append(p)
        self._cache = found
        return found

    @property
    def available(self) -> bool:
        return len(self.scan()) > 0

    @property
    def count(self) -> int:
        return len(self.scan())

    def names(self):
        return [p.stem for p in self.scan()]

    # ---------- 取用 ----------
    def get(self, key, default_index: int = 0) -> str:
        """按名称、索引或路径取字体，返回字符串路径。

        key 可以是：文件名 / 完整路径 / 整数索引 / None（取默认）。
        """
        fonts = self.scan()
        if not fonts:
            return ""

        if key is None:
            return str(fonts[min(default_index, len(fonts) - 1)])

        if isinstance(key, int):
            return str(fonts[key % len(fonts)])

        key = str(key)
        for p in fonts:
            if key in (p.name, p.stem, str(p)):
                return str(p)
        # 当作路径处理（可能字体在别处）
        if Path(key).exists():
            return key

        return str(fonts[min(default_index, len(fonts) - 1)])

    def pick(self, rng: random.Random = None, exclude: str = None) -> str:
        """随机取一套字体（用于按户切换笔迹）。"""
        fonts = self.scan()
        if not fonts:
            return ""
        rng = rng or random
        if exclude and len(fonts) > 1:
            pool = [str(p) for p in fonts if str(p) != exclude]
            if pool:
                return rng.choice(pool)
        return str(rng.choice(fonts))

    def guess_signature_font(self) -> str:
        """猜一个最适合做签名的字体（文件名含签名关键词者优先）。"""
        fonts = self.scan()
        if not fonts:
            return ""
        for p in fonts:
            low = p.stem.lower()
            if any(h in low for h in SIGN_HINTS):
                return str(p)
        return str(fonts[0])

    # ---------- 校验 ----------
    def supports_cjk(self, font_path: str) -> bool:
        """检测字体是否包含中文字符（避免写出豆腐块）。"""
        from PIL import ImageFont

        if not font_path:
            return False
        try:
            f = ImageFont.truetype(font_path, 32)
            # getbbox 对缺字字体往往返回极窄或全零
            box = f.getbbox(_CJK_PROBE[0])
            return box is not None and (box[2] - box[0]) > 8
        except Exception:
            return False

    def healthy_fonts(self):
        """返回能正常渲染中文的字体列表。"""
        return [p for p in self.scan() if self.supports_cjk(str(p))]
