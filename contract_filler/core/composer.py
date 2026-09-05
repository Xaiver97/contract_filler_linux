# -*- coding: utf-8 -*-
"""
合同合成器 —— 把配置、渲染器、指纹串成一条流水线。

职责：
    1. 加载模板与 JSON 配置，按实际模板尺寸缩放全部坐标与字号
    2. 按字段类型分发渲染（文本 / 选择 / 勾选项 / 单选 / 签名）
    3. 管理随机种子：预览固定、正式生成随机
    4. 管理字体：支持按户切换笔迹
"""

import copy
import json
import random
from pathlib import Path

from PIL import Image

from .fonts import FontManager
from .paths import app_base
from .renderer import HandwriteRenderer, StrokeStyle
from .stamp import regenerate_contract_code

BASE_DIR = app_base()
DEFAULT_CONFIG = BASE_DIR / "config" / "template.json"


class ContractComposer:
    """一次加载模板与配置，可反复渲染多份合同（批量场景效率关键）。"""

    def __init__(self, config_path=None, base_dir=None,
                 font_dir=None):
        self.base_dir = Path(base_dir) if base_dir else BASE_DIR
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG
        self.config = self._load_config()

        # 资源目录（配置可覆盖）
        self.font_dir = Path(font_dir) if font_dir else \
            self.base_dir / "fonts"

        self.fonts = FontManager(self.font_dir)
        self.renderer = HandwriteRenderer()

        # 全局微调（GUI 滑杆写入）。优先从笔迹存档加载，这样上次
        # 保存的笔迹（颜色浓淡、间距等）一启动即生效，单户/批量一致。
        self.tune = {}
        self._load_pen_tune()

        self._template = None
        self._scale = 1.0

    # ------------------------------------------------------------------ #
    #  配置
    # ------------------------------------------------------------------ #
    def _load_config(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_config(self, path=None):
        path = Path(path) if path else self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
        return path

    def reload(self):
        self.config = self._load_config()
        self._template = None
        return self

    # ---- 笔迹微调存档（config/pen_tune.json）----
    def pen_tune_path(self) -> Path:
        return self.base_dir / "config" / "pen_tune.json"

    def _load_pen_tune(self):
        """把笔迹存档读入 self.tune。文件缺失/损坏则保持空（用默认）。"""
        p = self.pen_tune_path()
        if not p.exists():
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                for k, v in d.items():
                    if k.startswith("_") or v is None:
                        continue
                    self.tune[k] = float(v)
        except Exception:
            # 存档损坏时忽略，退回默认
            self.tune = {}

    def save_pen_tune(self, tune: dict = None) -> Path:
        """把当前微调写回 config/pen_tune.json，供下次启动加载。"""
        if tune is not None:
            self.tune = {k: float(v) for k, v in tune.items()
                         if v is not None and not k.startswith("_")}
        path = self.pen_tune_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(self.tune)
        payload["_readme"] = (
            "笔迹微调存档（程序自动维护）。"
            "对应「笔迹微调」面板滑块值，启动时自动加载。"
            "如需恢复出厂，可在界面点「恢复默认」或删除本文件。")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    def clear_pen_tune(self):
        """删除笔迹存档并清空运行时微调（回到配置文件默认值）。"""
        self.tune = {}
        p = self.pen_tune_path()
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


    @property
    def fields(self):
        return self.config.get("fields", [])

    @property
    def calibrated(self) -> bool:
        return bool(self.config.get("calibrated", False))

    def template_path(self) -> Path:
        p = self.config.get("template", "assets/template.jpg")
        p = Path(p)
        return p if p.is_absolute() else (self.base_dir / p)

    # ------------------------------------------------------------------ #
    #  模板
    # ------------------------------------------------------------------ #
    def load_template(self, path=None, force=False) -> Image.Image:
        """加载合同底图（带缓存，批量时只解码一次）。"""
        p = Path(path) if path else self.template_path()
        key = str(p)
        if self._template is not None and not force and \
                getattr(self, "_tpl_key", None) == key:
            return self._template.copy()

        if not p.exists():
            raise FileNotFoundError(f"找不到合同模板: {p}")

        img = Image.open(p).convert("RGBA")
        self._template = img
        self._tpl_key = key
        self._update_scale()
        return img.copy()

    def _update_scale(self):
        """按 reference_size 计算缩放系数。"""
        ref = self.config.get("reference_size") or [0, 0]
        if self._template is None or not ref or ref[0] <= 0:
            self._scale = 1.0
            return
        self._scale = self._template.width / float(ref[0])

    @property
    def scale(self) -> float:
        return self._scale

    # ------------------------------------------------------------------ #
    #  风格
    # ------------------------------------------------------------------ #
    def style(self, name: str, scale: float = None) -> StrokeStyle:
        """取一份风格并按需缩放像素级参数。"""
        sc = self._scale if scale is None else scale
        d = copy.deepcopy(self.config.get("styles", {}).get(name, {}))
        st = StrokeStyle.from_dict(d)

        # 像素级参数随模板分辨率缩放
        st.size *= sc
        st.size_jitter *= sc
        st.pos_jitter *= sc
        st.spacing_jitter *= sc
        # 字间距是相对系数（不缩放）
        # char_spacing / digit_spacing 是相对字符宽度的倍率，与分辨率无关
        st.drift_amp *= sc
        st.snake_amp *= sc
        st.fatigue *= sc
        st.tremor *= sc
        st.bleed_sigma *= sc
        st.region_blur *= sc
        # 噪点强度是像素值偏移，不随分辨率缩放

        # 全局微调（GUI 实时调节，不写入配置文件）
        t = self.tune or {}
        if t.get("size_scale"):
            st.size *= float(t["size_scale"])
            st.size_jitter *= float(t["size_scale"])
        if t.get("chaos_scale") is not None:
            st.chaos *= float(t["chaos_scale"])
        for k in ("ink_depth", "fade_strength", "grain_sigma", "bleed_sigma"):
            if t.get(k) is not None:
                setattr(st, k, float(t[k]))
        # 字间距覆盖（GUI 实时调节，写入配置文件则走 config 的值）
        if t.get("char_spacing") is not None:
            st.char_spacing = float(t["char_spacing"])
        if t.get("digit_spacing") is not None:
            st.digit_spacing = float(t["digit_spacing"])
        return st

    def available_styles(self):
        return list((self.config.get("styles") or {}).keys())

    # ------------------------------------------------------------------ #
    #  渲染
    # ------------------------------------------------------------------ #
    def render(self, data: dict, seed=None, font_body=None,
               font_sign=None, template=None) -> Image.Image:
        """渲染一份合同。

        参数
        ----
        data : dict
            字段名 -> 值。见 config/template.json 的 key。
        seed : int | None
            None 表示真随机（每份不同）；给整数则完全可复现。
        font_body / font_sign : str | None
            指定正文/签名字体路径；None 则自动（按 seed 随机选一套）。

        返回
        ----
        PIL.Image (RGBA)
        """
        canvas = self.load_template(template)
        sc = self._scale
        rng = random.Random(seed)

        # 保留最近一次渲染的数据，供 stamp 模块读取 contract_code 等
        self._last_data = data

        # 字体分配：未指定则按 seed 随机挑一套（批量时自动"换人写"）
        if not font_body:
            font_body = self.fonts.pick(rng) or ""
        if not font_sign:
            font_sign = self.fonts.guess_signature_font() or font_body

        for i, field in enumerate(self.fields):
            fseed = None if seed is None else seed + i * 7919
            self._draw_field(canvas, field, data, fseed,
                             font_body, font_sign, sc, rng)

        # 合同编号（红色印章式）：擦除原模板预印的编码并绘制新码
        # 若 fields 中无 contract_code 配置则跳过，对原模板零侵入
        regenerate_contract_code(canvas, self)

        return canvas

    # ------------------------------------------------------------------ #
    #  字段分发
    # ------------------------------------------------------------------ #
    def _draw_field(self, canvas, field, data, seed,
                    font_body, font_sign, sc, rng):
        ftype = (field.get("type") or "text").lower()
        key = field.get("key")
        value = data.get(key, None)

        # value_from: 从其他字段拷贝值（如供电人日期 = 用电人日期）
        if value in (None, "") and field.get("value_from"):
            value = data.get(field["value_from"], None)

        if ftype == "text":
            self._draw_text_field(canvas, field, value, seed, font_body, sc)

        elif ftype == "select":
            self._draw_select_field(canvas, field, value, seed, font_body, sc)

        elif ftype == "checkbox_group":
            self._draw_checkbox_group(canvas, field, value, seed, sc)

        elif ftype == "radio":
            self._draw_radio(canvas, field, value, seed, sc)

        elif ftype == "signature":
            self._draw_signature(canvas, field, value, seed, font_sign, sc)

    # ---------- 文本 ----------
    def _draw_text_field(self, canvas, field, value, seed, font_body, sc):
        if value in (None, ""):
            value = field.get("default")
        if value in (None, ""):
            return
        st = self.style(field.get("style", "body"))
        st.font_path = font_body
        x = field.get("x", 0) * sc
        y = field.get("y", 0) * sc
        w = field.get("w")
        max_w = int(w * sc) if w else None
        self.renderer.draw_text(canvas, str(value), x, y, st,
                                seed=seed,
                                anchor=field.get("align", "lm"),
                                max_width=max_w)

    # ---------- 选择（带联动勾选） ----------
    def _draw_select_field(self, canvas, field, value, seed, font_body, sc):
        if value in (None, ""):
            return
        st = self.style(field.get("style", "body"))
        st.font_path = font_body

        text = f"{value}{field.get('suffix', '')}"
        x = field.get("x", 0) * sc
        y = field.get("y", 0) * sc
        w = field.get("w")
        max_w = int(w * sc) if w else None
        self.renderer.draw_text(canvas, text, x, y, st, seed=seed,
                                anchor=field.get("align", "lm"),
                                max_width=max_w)

        # 联动勾选：如 220 → 单相，380 → 三相
        linked = (field.get("linked_checks") or {}).get(str(value))
        if linked:
            cst = self.style(field.get("style", "check"))
            cst.font_path = font_body
            self.renderer.draw_check(
                canvas,
                linked.get("x", 0) * sc,
                linked.get("y", 0) * sc,
                size=linked.get("size", 22) * sc,
                style=cst, seed=None if seed is None else seed + 31,
                box=bool(linked.get("box", False)))

    # ---------- 勾选项 ----------
    def _draw_checkbox_group(self, canvas, field, value, seed, sc):
        if value in (None, ""):
            return
        selected = value if isinstance(value, (list, tuple, set)) else [value]
        selected = {str(s).strip() for s in selected if str(s).strip()}
        if not selected:
            return

        cst = self.style(field.get("style", "check"))
        for i, opt in enumerate(field.get("options", [])):
            if str(opt.get("value", "")).strip() not in selected:
                continue
            self.renderer.draw_check(
                canvas,
                opt.get("x", 0) * sc,
                opt.get("y", 0) * sc,
                size=opt.get("size", 22) * sc,
                style=cst,
                seed=None if seed is None else seed + i * 17,
                box=bool(opt.get("box", False)))

    # ---------- 单选圈 ----------
    def _draw_radio(self, canvas, field, value, seed, sc):
        if value in (None, ""):
            return
        target = str(value).strip()
        cst = self.style(field.get("style", "check"))
        for i, opt in enumerate(field.get("options", [])):
            if str(opt.get("value", "")).strip() != target:
                continue
            self.renderer.draw_radio(
                canvas,
                opt.get("x", 0) * sc,
                opt.get("y", 0) * sc,
                size=opt.get("size", 20) * sc,
                style=cst,
                seed=None if seed is None else seed + i * 23,
                filled=bool(opt.get("filled", True)))

    # ---------- 签名 ----------
    # 注意：模板图上已预印指纹印，程序只负责书写签名文字，
    # 不再叠加任何指纹（真实或合成）——见 config 说明。
    def _draw_signature(self, canvas, field, value, seed, font_sign, sc):
        st = self.style(field.get("style", "signature"))
        st.font_path = font_sign
        x = field.get("x", 0) * sc
        y = field.get("y", 0) * sc
        w = field.get("w")
        max_w = int(w * sc) if w else None

        if value not in (None, ""):
            self.renderer.draw_text(canvas, str(value), x, y, st,
                                    seed=seed,
                                    anchor=field.get("align", "lm"),
                                    max_width=max_w)

    # ------------------------------------------------------------------ #
    #  辅助
    # ------------------------------------------------------------------ #
    def field_labels(self):
        """返回 [(key, label, type)]，供 Excel 模板与 GUI 使用。"""
        out = []
        for f in self.fields:
            out.append((f.get("key"), f.get("label", f.get("key")),
                        f.get("type", "text")))
        return out

    def options_of(self, key):
        """返回某字段的可选值列表。"""
        for f in self.fields:
            if f.get("key") == key:
                if f.get("type") in ("select",):
                    return list(f.get("options", []))
                if f.get("type") in ("checkbox_group", "radio"):
                    return [o.get("value") for o in f.get("options", [])]
        return []

    def status(self):
        """资源自检，GUI 启动时展示。"""
        tpl = self.template_path()
        fonts = self.fonts.scan()
        healthy = self.fonts.healthy_fonts()
        return {
            "template": str(tpl),
            "template_exists": tpl.exists(),
            "template_size": (self._template.size
                              if self._template is not None else None),
            "scale": self._scale,
            "fonts_dir": str(self.font_dir),
            "fonts_total": len(fonts),
            "fonts_cjk": len(healthy),
            "calibrated": self.calibrated,
        }
