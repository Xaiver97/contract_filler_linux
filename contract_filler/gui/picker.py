# -*- coding: utf-8 -*-
"""
坐标校准器 —— 在预览图上点一下，把字段落笔位置写回配置。

为什么必须有它：
    config/template.json 里预置的坐标只是"估计值"，不同扫描件的
    分辨率、留白、排版都不一样，不校准必然写歪。校准一次即可长期复用。

坐标说明：
    配置里存的是**参考坐标系**坐标（对应 reference_size），与模板实际
    分辨率解耦。拾取时会自动换算，用户看到的始终是配置里的数值。
"""

import datetime as _dt

from .preview import CanvasPreview
from .qt_compat import (AlignLeft, Horizontal, Qt, QtCore, QtGui, QtWidgets,
                        Signal, Slot)


class CalibrateDialog(QtWidgets.QDialog):
    """字段坐标校准对话框。"""

    def __init__(self, composer, parent=None):
        super().__init__(parent)
        self.composer = composer
        self.setWindowTitle("校准字段坐标")
        self.resize(1280, 860)

        self._picking_row = -1
        self._updating = False
        self.targets = self._build_targets()

        self._build_ui()
        self._load_preview()
        self._fill_table()

    # ------------------------------------------------------------------ #
    def _build_targets(self):
        out = []
        for f in self.composer.fields:
            t = (f.get("type") or "text").lower()
            if t in ("checkbox_group", "radio"):
                for i, opt in enumerate(f.get("options", [])):
                    out.append({
                        "field": f, "opt": i,
                        "name": f"{f.get('label','')} / {opt.get('value','')}",
                        "x": float(opt.get("x", 0)),
                        "y": float(opt.get("y", 0)),
                        "extra": float(opt.get("size", 22)),
                        "extra_name": "勾选尺寸",
                    })
            else:
                out.append({
                    "field": f, "opt": None,
                    "name": f.get("label", f.get("key", "")),
                    "x": float(f.get("x", 0)),
                    "y": float(f.get("y", 0)),
                    "extra": float(f.get("w", 0)),
                    "extra_name": "可用宽度",
                })

            # select 的联动勾选框（如 电压等级 → 单相/三相 的勾位）
            if t == "select":
                lc = f.get("linked_checks") or {}
                for key, cc in lc.items():
                    out.append({
                        "field": f, "opt": None, "linked_key": key,
                        "name": f"{f.get('label','')} / 勾选·{key}",
                        "x": float(cc.get("x", 0)),
                        "y": float(cc.get("y", 0)),
                        "extra": float(cc.get("size", 22)),
                        "extra_name": "勾选尺寸",
                    })
        return out

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # 说明
        tip = QtWidgets.QLabel(
            "用法：在左侧选中一个字段 → 点「拾取位置」→ 在右侧图上点击落笔点。"
            "坐标也可直接在表格里修改。勾选类字段请逐个校准。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#555;background:#f6f6f6;padding:6px;"
                          "border-radius:4px;")
        root.addWidget(tip)

        mid = QtWidgets.QSplitter(Horizontal)
        root.addWidget(mid, 1)

        # ---------- 左：目标表 ----------
        left = QtWidgets.QWidget()
        left.setMinimumWidth(430)
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(2, 2, 2, 2)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["字段 / 选项", "X", "Y", "附加"])
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 200)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.currentCellChanged.connect(self._on_current_changed)
        lv.addWidget(self.table, 1)

        brow = QtWidgets.QHBoxLayout()
        self.btn_pick = QtWidgets.QPushButton("拾取位置")
        self.btn_pick.setFixedHeight(30)
        self.btn_pick.clicked.connect(self._start_pick)
        brow.addWidget(self.btn_pick, 2)

        self.btn_pick_next = QtWidgets.QPushButton("拾取并跳到下一个")
        self.btn_pick_next.setFixedHeight(30)
        self.btn_pick_next.clicked.connect(self._pick_and_next)
        brow.addWidget(self.btn_pick_next, 3)
        lv.addLayout(brow)

        mid.addWidget(left)

        # ---------- 右：预览 ----------
        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(2, 2, 2, 2)

        bar = QtWidgets.QHBoxLayout()
        b1 = QtWidgets.QPushButton("适应窗口")
        b2 = QtWidgets.QPushButton("100%")
        b3 = QtWidgets.QPushButton("放大")
        b4 = QtWidgets.QPushButton("缩小")
        b1.clicked.connect(self.preview_fit)
        b2.clicked.connect(self.preview_zoom_actual)
        b3.clicked.connect(self.preview_in)
        b4.clicked.connect(self.preview_out)
        for b in (b1, b2, b3, b4):
            b.setFixedHeight(26)
            bar.addWidget(b)
        bar.addStretch(1)

        self.chk_marks = QtWidgets.QCheckBox("显示锚点")
        self.chk_marks.setChecked(True)
        self.chk_marks.stateChanged.connect(self._render_marks)
        bar.addWidget(self.chk_marks)
        rv.addLayout(bar)

        self.preview = CanvasPreview()
        self.preview.pointPicked.connect(self._on_picked)
        self.preview.pickCancelled.connect(
            lambda: self.btn_pick.setChecked(False))
        rv.addWidget(self.preview, 1)

        self.lbl_info = QtWidgets.QLabel("")
        self.lbl_info.setStyleSheet("color:#666;")
        rv.addWidget(self.lbl_info)

        mid.addWidget(right)
        mid.setStretchFactor(0, 0)
        mid.setStretchFactor(1, 1)

        # ---------- 底部 ----------
        brow2 = QtWidgets.QHBoxLayout()
        self.lbl_scale = QtWidgets.QLabel("")
        brow2.addWidget(self.lbl_scale)
        brow2.addStretch(1)

        btn_save = QtWidgets.QPushButton("保存配置")
        btn_save.setFixedHeight(32)
        btn_save.clicked.connect(self._save)
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.setFixedHeight(32)
        btn_cancel.clicked.connect(self.reject)
        brow2.addWidget(btn_cancel)
        brow2.addWidget(btn_save)
        root.addLayout(brow2)

    # ------------------------------------------------------------------ #
    def _load_preview(self):
        try:
            img = self.composer.load_template()
        except Exception as e:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (1100, 780), (250, 250, 250))
            d = ImageDraw.Draw(img)
            d.text((60, 60), f"模板加载失败：{e}", fill=(150, 60, 60))
            img = img.convert("RGBA")

        self.preview.set_image(img)
        self.preview.set_ref_scale(
            1.0 / self.composer.scale if self.composer.scale else 1.0)
        self.preview.fit_to_window()
        self._render_marks()

        ref = self.composer.config.get("reference_size") or [0, 0]
        self.lbl_scale.setText(
            f"模板 {img.width}×{img.height} | 参考 {ref[0]}×{ref[1]} | "
            f"缩放 ×{self.composer.scale:.4f}")

    # ------------------------------------------------------------------ #
    def _fill_table(self):
        self._updating = True
        self.table.clearContents()
        self.table.setRowCount(len(self.targets))
        for r, t in enumerate(self.targets):
            self._set_row(r, t)
        self._updating = False

    def _set_row(self, r, t):
        vals = [t["name"], f"{t['x']:.1f}", f"{t['y']:.1f}",
                f"{t['extra']:.1f}"]
        for c, v in enumerate(vals):
            it = QtWidgets.QTableWidgetItem(v)
            if c == 0:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, c, it)
        hdr = self.table.horizontalHeaderItem(3)
        if hdr and t.get("extra_name"):
            hdr.setText(t["extra_name"])

    def _on_item_changed(self, item):
        if self._updating:
            return
        r = item.row()
        c = item.column()
        if not (0 <= r < len(self.targets)) or c == 0:
            return
        t = self.targets[r]
        try:
            v = float(item.text())
        except ValueError:
            return
        if c == 1:
            t["x"] = v
            self._apply(t, x=v)
        elif c == 2:
            t["y"] = v
            self._apply(t, y=v)
        elif c == 3:
            t["extra"] = v
            self._apply(t, extra=v)
        self._render_marks()

    def _apply(self, t, x=None, y=None, extra=None):
        f, oi = t["field"], t["opt"]
        lk = t.get("linked_key")
        if lk:
            # 联动勾选框（linked_checks 的某一枚勾）
            lc = f.setdefault("linked_checks", {}).setdefault(lk, {})
            if x is not None:
                lc["x"] = x
            if y is not None:
                lc["y"] = y
            if extra is not None:
                lc["size"] = extra
        elif oi is None:
            if x is not None:
                f["x"] = x
            if y is not None:
                f["y"] = y
            if extra is not None:
                f["w"] = extra
        else:
            opt = f["options"][oi]
            if x is not None:
                opt["x"] = x
            if y is not None:
                opt["y"] = y
            if extra is not None:
                opt["size"] = extra

    def _on_current_changed(self, cur_r, *_):
        self._render_marks(cur_r)

    # ------------------------------------------------------------------ #
    def _render_marks(self, highlight_row=None):
        if highlight_row is None:
            highlight_row = self.table.currentRow()
        marks = []
        for i, t in enumerate(self.targets):
            color = (30, 150, 90, 255) if i == highlight_row else (220, 40, 40, 255)
            marks.append((t["x"], t["y"], t["name"], color))
        self.preview.set_markers(marks, show=self.chk_marks.isChecked())

    # ------------------------------------------------------------------ #
    def _start_pick(self):
        r = self.table.currentRow()
        if r < 0:
            return
        self._picking_row = r
        self.preview.set_picking(True)
        self.btn_pick.setText("请在图上点击…")
        self.lbl_info.setText(f"正在拾取：{self.targets[r]['name']}"
                              f"（按 Esc 取消）")

    def _pick_and_next(self):
        r = self.table.currentRow()
        if r < 0:
            r = -1
        nxt = min(r + 1, len(self.targets) - 1)
        self.table.selectRow(nxt)
        self._start_pick()

    @Slot(float, float)
    def _on_picked(self, x, y):
        r = self._picking_row
        self.preview.set_picking(False)
        self.btn_pick.setText("拾取位置")
        self.lbl_info.setText(f"已拾取：X={x:.1f}  Y={y:.1f}")
        if not (0 <= r < len(self.targets)):
            return

        t = self.targets[r]
        t["x"], t["y"] = float(x), float(y)
        self._apply(t, x=float(x), y=float(y))

        self._updating = True
        self._set_row(r, t)
        self._updating = False
        self._render_marks()

    # ------------------------------------------------------------------ #
    def preview_fit(self):
        self.preview.fit_to_window()

    def preview_zoom_actual(self):
        self.preview.zoom_actual()

    def preview_in(self):
        self.preview.zoom_in()

    def preview_out(self):
        self.preview.zoom_out()

    # ------------------------------------------------------------------ #
    def _save(self):
        self.composer.config["calibrated"] = True
        self.composer.config["calibrated_at"] = \
            _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.composer.save_config()
            self.accept()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "保存失败", str(e))
