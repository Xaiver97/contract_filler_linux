# -*- coding: utf-8 -*-
"""
单户填写面板 —— 左边填表，右边实时预览。

预览用固定 seed（默认 42），保证调参时效果稳定可比；
点"换一种笔迹"才重新随机，正式导出时也用随机 seed。
"""

import datetime as _dt

from PIL import Image, ImageDraw

from .preview import CanvasPreview
from .qt_compat import (AlignLeft, AlignTop, Horizontal, Qt, QtCore, QtGui,
                        QtWidgets, Signal)

PREVIEW_SEED = 42


def _placeholder(msg, sub=""):
    """模板缺失时的占位提示图。"""
    img = Image.new("RGB", (1100, 780), (248, 248, 248))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 1060, 740], outline=(200, 200, 200), width=2)
    d.text((80, 120), msg, fill=(120, 120, 120))
    if sub:
        d.text((80, 160), sub, fill=(160, 160, 160))
    return img


class SinglePanel(QtWidgets.QWidget):
    """单户填写 + 预览。"""

    statusMessage = Signal(str)
    calibrateRequested = Signal()

    def __init__(self, composer, parent=None):
        super().__init__(parent)
        self.composer = composer
        self.editors = {}          # key -> widget
        self._seed = PREVIEW_SEED
        self._last_image = None

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._do_render)

        self._build_ui()
        self._load_defaults()
        self._wire_signature_sync()

    # ------------------------------------------------------------------ #
    #  用电人签名：自动跟随「用电人」，但允许手动覆盖
    # ------------------------------------------------------------------ #
    def _wire_signature_sync(self):
        """把「用电人」与「用电人签名」联动。

        行为：
            - 用电人(customer_name)变化 → 用电人签名(signature)自动同步；
            - 若用户手动把签名改成与用电人不同 → 停止自动同步（保留用户输入）；
            - 把签名清空 → 恢复自动同步。
        """
        self._sig_syncing = False   # 程序自动写签名时的互斥锁
        name_w = self.editors.get("customer_name")
        sig_w = self.editors.get("signature")
        if name_w is None or sig_w is None:
            return
        self._sig_name_w = name_w
        self._sig_w = sig_w
        name_w.textChanged.connect(self._auto_sig)
        sig_w.textChanged.connect(self._on_sig_edited)

    def _set_signature_program(self, text):
        self._sig_syncing = True
        try:
            self._sig_w.setText(str(text or ""))
        finally:
            self._sig_syncing = False

    def _auto_sig(self, text):
        """用电人变化时同步签名（未手动锁定时）。"""
        if getattr(self, "_sig_w", None) is None:
            return
        if getattr(self, "_sig_locked", False):
            return   # 用户手动改过且非空 → 不再覆盖
        self._set_signature_program(text)

    def _on_sig_edited(self, text):
        """签名框内容变化：区分程序写与用户手改。"""
        if self._sig_syncing:
            return   # 我们自己同步触发的，不算用户手动
        name = self._sig_name_w.text().strip()
        txt = str(text or "").strip()
        # 与用电人相同 → 不锁定；为空 → 不锁定；不同 → 锁定（尊重用户输入）
        self._sig_locked = bool(txt) and (txt != name)

    # ------------------------------------------------------------------ #
    #  UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        splitter = QtWidgets.QSplitter(Horizontal)
        root.addWidget(splitter, 1)

        # ---------- 左：表单 ----------
        left = QtWidgets.QWidget()
        left.setMinimumWidth(360)
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 4)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        inner = QtWidgets.QWidget()
        self.form = QtWidgets.QFormLayout(inner)
        self.form.setLabelAlignment(AlignLeft | AlignTop)
        scroll.setWidget(inner)
        lv.addWidget(scroll, 1)

        self._build_fields()
        self._build_tuning(inner)
        self._build_actions(lv)

        splitter.addWidget(left)

        # ---------- 右：预览 ----------
        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(4, 4, 4, 4)
        rv.setSpacing(6)

        bar = QtWidgets.QHBoxLayout()
        self.btn_fit = QtWidgets.QPushButton("适应窗口")
        self.btn_100 = QtWidgets.QPushButton("100%")
        self.btn_in = QtWidgets.QPushButton("放大")
        self.btn_out = QtWidgets.QPushButton("缩小")
        for b, fn in ((self.btn_fit, self.preview_fit),
                      (self.btn_100, self.preview_zoom_actual),
                      (self.btn_in, self.preview_zoom_in),
                      (self.btn_out, self.preview_zoom_out)):
            b.setFixedHeight(28)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)

        self.btn_reroll = QtWidgets.QPushButton("换一种笔迹")
        self.btn_reroll.setFixedHeight(28)
        self.btn_reroll.setToolTip("重新随机笔迹与手指按压角度（正式导出时同样随机）")
        self.btn_reroll.clicked.connect(self.reroll)
        bar.addWidget(self.btn_reroll)

        rv.addLayout(bar)

        self.preview = CanvasPreview()
        rv.addWidget(self.preview, 1)

        self.lbl_hint = QtWidgets.QLabel("")
        self.lbl_hint.setStyleSheet("color:#666;")
        rv.addWidget(self.lbl_hint)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 900])

    # ---------- 字段表单 ----------
    def _build_fields(self):
        for f in self.composer.fields:
            key = f.get("key")
            if not key:
                continue
            # value_from 字段是镜像字段（值来自另一个字段），不在表单里显示，
            # 渲染时由 composer 自动同步。例如「供电人签订日期」是
            # 「用电人签订日期」的镜像，共享同一份数据。
            if f.get("value_from"):
                continue
            label = f.get("label", key)
            ftype = (f.get("type") or "text").lower()

            if ftype in ("text", "signature"):
                w = QtWidgets.QLineEdit()
                w.setPlaceholderText(label)
                w.textChanged.connect(self._schedule)
            elif ftype in ("select", "radio", "checkbox_group"):
                w = QtWidgets.QComboBox()
                w.setEditable(True)
                opts = self.composer.options_of(key)
                w.addItem("")
                for o in opts:
                    w.addItem(str(o))
                w.currentTextChanged.connect(self._schedule)
                w.editTextChanged.connect(self._schedule)
            else:
                w = QtWidgets.QLineEdit()
                w.textChanged.connect(self._schedule)

            self.editors[key] = w
            self.form.addRow(label + "：", w)

    # ---------- 风格微调 ----------
    def _build_tuning(self, parent):
        box = QtWidgets.QGroupBox("笔迹微调（可保存，下次自动加载）")
        box.setCheckable(True)
        box.setChecked(False)
        v = QtWidgets.QVBoxLayout(box)

        self.tune = {}
        specs = [
            ("size_scale", "字号整体", 0.5, 2.0, 1.0, 0.05, 2),
            ("chaos_scale", "混乱程度", 0.0, 2.0, 1.0, 0.05, 2),
        ]
        for name, text, lo, hi, val, step, dec in specs:
            val0 = self._tune_value(name, val)
            row = QtWidgets.QHBoxLayout()
            lab = QtWidgets.QLabel(text)
            lab.setFixedWidth(70)
            sld = QtWidgets.QSlider(Horizontal)
            sld.setRange(int(lo * 100), int(hi * 100))
            sld.setValue(int(val0 * 100))
            sld.setSingleStep(int(step * 100))
            val_lab = QtWidgets.QLabel(f"{val0:.2f}")
            val_lab.setFixedWidth(40)
            sld.valueChanged.connect(
                lambda v, l=val_lab, d=dec: l.setText(f"{v / 100.0:.{d}f}"))
            sld.valueChanged.connect(self._on_tune)
            row.addWidget(lab)
            row.addWidget(sld, 1)
            row.addWidget(val_lab)
            v.addLayout(row)
            self.tune[name] = sld

        # 字间距：汉字与数字分开
        # char_spacing: 汉字 ↔ 汉字之间的间距系数（正=留空，负=压缩）
        # digit_spacing: 数字 ↔ 数字之间的紧凑系数（绝对值越大越紧凑）
        spacing = [
            ("char_spacing",  "汉字间距",   -0.30, 0.50, 0.02),
            ("digit_spacing", "数字间距",   -0.50, 0.20, -0.18),
        ]
        for name, text, lo, hi, val in spacing:
            val0 = self._tune_value(name, val)
            row = QtWidgets.QHBoxLayout()
            lab = QtWidgets.QLabel(text)
            lab.setFixedWidth(70)
            sld = QtWidgets.QSlider(Horizontal)
            sld.setRange(int(lo * 100), int(hi * 100))
            sld.setValue(int(val0 * 100))
            sld.setSingleStep(1)
            val_lab = QtWidgets.QLabel(f"{val0:+.2f}")
            val_lab.setFixedWidth(40)
            sld.valueChanged.connect(
                lambda v, l=val_lab: l.setText(f"{v / 100.0:+.2f}"))
            sld.valueChanged.connect(self._on_tune)
            row.addWidget(lab)
            row.addWidget(sld, 1)
            row.addWidget(val_lab)
            v.addLayout(row)
            self.tune[name] = sld

        # 墨色 / 淡出 / 噪点
        more = [
            ("ink_depth", "墨色深浅", 0.02, 0.35, 0.07),
            ("fade_strength", "褪色程度", 0.75, 1.0, 0.985),
            ("grain_sigma", "纸张噪点", 0.0, 14.0, 4.0),
            ("bleed_sigma", "墨迹渗透", 0.0, 1.6, 0.45),
        ]
        for name, text, lo, hi, val in more:
            val0 = self._tune_value(name, val)
            row = QtWidgets.QHBoxLayout()
            lab = QtWidgets.QLabel(text)
            lab.setFixedWidth(70)
            sld = QtWidgets.QSlider(Horizontal)
            sld.setRange(int(lo * 1000), int(hi * 1000))
            sld.setValue(int(val0 * 1000))
            val_lab = QtWidgets.QLabel(
                f"{val0:.2f}" if name != "grain_sigma" else f"{val0:.1f}")
            val_lab.setFixedWidth(40)
            sld.valueChanged.connect(
                lambda v, l=val_lab, n=name: l.setText(
                    f"{v / 1000.0:.2f}" if n != "grain_sigma"
                    else f"{v / 1000.0:.1f}"))
            sld.valueChanged.connect(self._on_tune)
            row.addWidget(lab)
            row.addWidget(sld, 1)
            row.addWidget(val_lab)
            v.addLayout(row)
            self.tune[name] = sld

        # 字体选择
        fonts = self.composer.fonts.scan()
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("正文字体"))
        self.cmb_font = QtWidgets.QComboBox()
        self.cmb_font.addItem("（自动轮换）", "")
        for p in fonts:
            self.cmb_font.addItem(p.stem, str(p))
        self.cmb_font.currentIndexChanged.connect(self._schedule)
        row.addWidget(self.cmb_font, 1)
        v.addLayout(row)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("签名字体"))
        self.cmb_sigfont = QtWidgets.QComboBox()
        self.cmb_sigfont.addItem("（自动）", "")
        for p in fonts:
            self.cmb_sigfont.addItem(p.stem, str(p))
        self.cmb_sigfont.currentIndexChanged.connect(self._schedule)
        row2.addWidget(self.cmb_sigfont, 1)
        v.addLayout(row2)

        # 保存 / 恢复
        btns = QtWidgets.QHBoxLayout()
        self.btn_save_tune = QtWidgets.QPushButton("保存当前微调")
        self.btn_save_tune.setToolTip(
            "把当前滑块值写入 config/pen_tune.json，下次启动自动加载")
        self.btn_save_tune.clicked.connect(self._save_tune)
        btns.addWidget(self.btn_save_tune, 1)
        self.btn_reset_tune = QtWidgets.QPushButton("恢复默认")
        self.btn_reset_tune.setToolTip(
            "把笔迹微调重置为模板默认并删除存档")
        self.btn_reset_tune.clicked.connect(self._reset_tune)
        btns.addWidget(self.btn_reset_tune, 1)
        v.addLayout(btns)

        parent.layout().addWidget(box)

    # ---------- 动作按钮 ----------
    def _build_actions(self, layout):
        row = QtWidgets.QHBoxLayout()
        self.btn_export = QtWidgets.QPushButton("导出图片…")
        self.btn_export.setToolTip("导出为 JPG/PNG/PDF")
        self.btn_export.setFixedHeight(32)
        self.btn_export.clicked.connect(self.export_current)
        row.addWidget(self.btn_export, 1)

        self.btn_export_docx = QtWidgets.QPushButton("导出 DOCX")
        self.btn_export_docx.setToolTip("直接导出为 Word 文档（每户一页）")
        self.btn_export_docx.setFixedHeight(32)
        self.btn_export_docx.clicked.connect(self.export_docx)
        row.addWidget(self.btn_export_docx, 1)

        self.btn_calib = QtWidgets.QPushButton("校准坐标…")
        self.btn_calib.setFixedHeight(32)
        self.btn_calib.setToolTip("在预览图上点选，重新设定各字段的落笔位置")
        self.btn_calib.clicked.connect(self.calibrateRequested.emit)
        row.addWidget(self.btn_calib, 1)
        layout.addLayout(row)

    # ------------------------------------------------------------------ #
    #  数据与渲染
    # ------------------------------------------------------------------ #
    def _load_defaults(self):
        # YYYY.MM.DD 格式（个位数月份/日期不补 0）
        today = _dt.date.today().strftime("%Y.%m.%d")
        defaults = {
            "sign_date": today,
            # 用户要求的初始默认（GUI 启动即可见，无需手填）
            "usage_nature": "居民生活",
            "voltage": "220",
            "limit_plan": "1",
            "capacity": "12",
        }
        for f in self.composer.fields:
            d = f.get("default")
            if d and f.get("key") not in defaults:
                defaults[f.get("key")] = d

        for key, val in defaults.items():
            w = self.editors.get(key)
            if w is None:
                continue
            if isinstance(w, QtWidgets.QLineEdit):
                w.setText(str(val))
            elif isinstance(w, QtWidgets.QComboBox):
                w.setEditText(str(val))
            elif isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(bool(val))

        self._schedule()

    def collect_data(self):
        """收集表单为数据字典。"""
        data = {}
        for key, w in self.editors.items():
            if isinstance(w, QtWidgets.QLineEdit):
                data[key] = w.text().strip()
            elif isinstance(w, QtWidgets.QComboBox):
                data[key] = w.currentText().strip()
            elif isinstance(w, QtWidgets.QCheckBox):
                data[key] = w.isChecked()
        return data

    def set_data(self, data):
        """外部填充表单（如从 Excel 点选某一行）。"""
        for key, val in (data or {}).items():
            w = self.editors.get(key)
            if w is None:
                continue
            if isinstance(w, (QtWidgets.QLineEdit,)):
                w.setText(str(val))
            elif isinstance(w, QtWidgets.QComboBox):
                w.setEditText(str(val))
            elif isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(bool(val))
        self._schedule()

    def _schedule(self, *_):
        self._timer.start()

    def _on_tune(self, *_):
        self._apply_tune()
        self._schedule()

    def _apply_tune(self):
        t = self.composer.tune
        t["size_scale"] = self.tune["size_scale"].value() / 100.0
        t["chaos_scale"] = self.tune["chaos_scale"].value() / 100.0
        t["ink_depth"] = self.tune["ink_depth"].value() / 1000.0
        t["fade_strength"] = self.tune["fade_strength"].value() / 1000.0
        t["grain_sigma"] = self.tune["grain_sigma"].value() / 1000.0
        t["bleed_sigma"] = self.tune["bleed_sigma"].value() / 1000.0
        if "char_spacing" in self.tune:
            t["char_spacing"] = self.tune["char_spacing"].value() / 100.0
        if "digit_spacing" in self.tune:
            t["digit_spacing"] = self.tune["digit_spacing"].value() / 100.0

    # ---- 微调存档 ----
    # 笔迹微调的"出厂默认值"。若 config/pen_tune.json 缺失，用这套默认。
    TUNE_DEFAULTS = {
        "size_scale": 1.0,
        "chaos_scale": 1.0,
        "char_spacing": 0.02,
        "digit_spacing": -0.18,
        "ink_depth": 0.07,
        "fade_strength": 0.985,
        "grain_sigma": 4.0,
        "bleed_sigma": 0.45,
    }

    def _tune_value(self, name, fallback):
        """取某键当前生效值：优先已加载的存档，其次传入的默认。"""
        cur = self.composer.tune.get(name)
        if cur is not None:
            return float(cur)
        return fallback

    def _save_tune(self):
        try:
            self._apply_tune()
            self.composer.save_pen_tune()
            self.statusMessage.emit(
                "已保存笔迹微调，下次启动将自动加载")
        except Exception as e:
            self.statusMessage.emit(f"保存失败：{e}")

    def _reset_tune(self):
        """把滑块全部设回默认并删除存档。"""
        self.composer.clear_pen_tune()
        d = self.TUNE_DEFAULTS
        self.tune["size_scale"].setValue(int(d["size_scale"] * 100))
        self.tune["chaos_scale"].setValue(int(d["chaos_scale"] * 100))
        for name, lo_scale in (("char_spacing", 100), ("digit_spacing", 100)):
            if name in self.tune:
                self.tune[name].setValue(int(d[name] * lo_scale))
        for name in ("ink_depth", "fade_strength", "grain_sigma", "bleed_sigma"):
            if name in self.tune:
                self.tune[name].setValue(int(d[name] * 1000))
        self._apply_tune()
        self._schedule()
        self.statusMessage.emit("已恢复默认笔迹并删除存档")

    def _do_render(self):
        st = self.composer.status()
        self._apply_tune()

        if not st["template_exists"]:
            self.preview.set_image(_placeholder(
                "尚未放入合同模板",
                f"请把扫描件放到：{st['template']}"))
            self.lbl_hint.setText("模板缺失，无法预览")
            return

        if st["fonts_total"] == 0:
            self.lbl_hint.setText(
                "提示：fonts/ 目录还没有字体，当前使用系统默认字体。"
                "把手写字体放进 fonts/ 后重启即可。")
        elif self.composer.fonts.count and not self.composer.fonts.healthy_fonts():
            self.lbl_hint.setText(
                "警告：fonts/ 里的字体都不支持中文，会显示空白方块。")
        else:
            self.lbl_hint.setText("")

        try:
            data = self.collect_data()
            fb = self.cmb_font.currentData() or None
            fs = self.cmb_sigfont.currentData() or None
            img = self.composer.render(data, seed=self._seed,
                                       font_body=fb, font_sign=fs)
            self._last_image = img
            # 保持当前缩放，避免调参时视图跳动
            self.preview.set_image(img, keep_zoom=True)
            self.preview.set_ref_scale(
                1.0 / self.composer.scale if self.composer.scale else 1.0)
        except Exception as e:
            self.statusMessage.emit(f"渲染失败：{e}")

    # ------------------------------------------------------------------ #
    #  操作
    # ------------------------------------------------------------------ #
    def reroll(self):
        """换一种笔迹（重新随机种子）。"""
        import random
        self._seed = random.randint(1, 2 ** 31 - 1)
        self._do_render()

    def preview_fit(self):
        self.preview.fit_to_window()

    def preview_zoom_actual(self):
        self.preview.zoom_actual()

    def preview_zoom_in(self):
        self.preview.zoom_in()

    def preview_zoom_out(self):
        self.preview.zoom_out()

    def current_image(self):
        return self._last_image

    def export_current(self):
        if self._last_image is None:
            self.statusMessage.emit("还没有可导出的内容")
            return
        d = self.collect_data()
        name = d.get("customer_no") or d.get("customer_name") \
            or d.get("signature") or "合同"
        default = f"{name}.docx"

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出合同", default,
            "DOCX 文档 (*.docx);;JPEG 图片 (*.jpg *.jpeg);;"
            "PNG 图片 (*.png);;PDF 文档 (*.pdf)")
        if not path:
            return

        # 正式导出改用随机种子，保证每份笔迹不同
        import random
        data = self.collect_data()
        try:
            img = self.composer.render(
                data, seed=random.randint(1, 2 ** 31 - 1),
                font_body=self.cmb_font.currentData() or None,
                font_sign=self.cmb_sigfont.currentData() or None)
            self._save_image(img, path, data)
            self.statusMessage.emit(f"已导出：{path}")
        except Exception as e:
            self.statusMessage.emit(f"导出失败：{e}")

    def export_docx(self):
        """一键导出当前预览为 DOCX（更直观的入口）。"""
        if self._last_image is None:
            self.statusMessage.emit("还没有可导出的内容")
            return
        d = self.collect_data()
        name = d.get("customer_no") or d.get("customer_name") \
            or d.get("signature") or "合同"
        default = f"{name}.docx"

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出为 Word 文档", default, "DOCX 文档 (*.docx)")
        if not path:
            return
        if not path.lower().endswith(".docx"):
            path = path + ".docx"

        import random
        data = self.collect_data()
        try:
            img = self.composer.render(
                data, seed=random.randint(1, 2 ** 31 - 1),
                font_body=self.cmb_font.currentData() or None,
                font_sign=self.cmb_sigfont.currentData() or None)
            self._save_image(img, path, data)
            self.statusMessage.emit(f"已导出 DOCX：{path}")
        except Exception as e:
            self.statusMessage.emit(f"导出失败：{e}")

    @staticmethod
    def _save_image(img: Image.Image, path: str, data: dict = None):
        """按扩展名分发到不同保存方式。"""
        p = path.lower()
        if p.endswith(".pdf"):
            img.convert("RGB").save(path, "PDF", resolution=200)
        elif p.endswith(".docx"):
            from batch.docx_export import export_one
            export_one(img, path)
        elif p.endswith(".png"):
            img.convert("RGB").save(path, "PNG", optimize=True)
        else:
            img.convert("RGB").save(path, "JPEG", quality=95,
                                    subsampling=0, optimize=True)

    def refresh(self):
        """配置或模板变更后刷新。"""
        self._do_render()
