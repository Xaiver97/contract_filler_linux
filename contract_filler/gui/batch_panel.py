# -*- coding: utf-8 -*-
"""
批量生成面板 —— 导入 Excel，预览数据，后台批量出图。

批量渲染放进 QThread 执行，避免大批量时界面卡死；
进度与日志通过信号回传主线程。
"""

import os
import subprocess
import sys
from pathlib import Path

from core.composer import ContractComposer
from batch.excel_loader import demo_row, export_template, load_excel
from batch.runner import BatchRunner
from .qt_compat import (AlignLeft, Horizontal, Qt, QtCore, QtGui, QtWidgets,
                        Signal, Slot)

MAX_PREVIEW_ROWS = 500

FMT_ITEMS = [
    ("JPG 图片（每户一张）", "jpg"),
    ("PNG 图片（每户一张）", "png"),
    ("PDF 文档（每户一份）", "pdf"),
    ("PDF 合订本（多户合并）", "pdf_merged"),
    ("DOCX 文档（每户一份）", "docx"),
    ("DOCX 合订本（多户合并）", "docx_merged"),
]


class _Worker(QtCore.QThread):
    """后台批量渲染。"""

    sig_progress = Signal(int, int, str)
    sig_done = Signal(object, object)

    def __init__(self, runner, records, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.records = records
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            files, errors = self.runner.run(
                self.records,
                progress_cb=lambda i, t, m: self.sig_progress.emit(i, t, m),
                cancel=lambda: self._cancel)
            self.sig_done.emit(files, errors)
        except Exception as e:
            self.sig_done.emit([], [(0, f"{type(e).__name__}: {e}")])


class BatchPanel(QtWidgets.QWidget):
    """批量导入与生成。"""

    statusMessage = Signal(str)
    rowActivateRequested = Signal(dict)

    def __init__(self, composer, parent=None):
        super().__init__(parent)
        self.composer = composer
        self.records = []
        self.worker = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        # ---------- 文件行 ----------
        row = QtWidgets.QHBoxLayout()
        self.edt_excel = QtWidgets.QLineEdit()
        self.edt_excel.setPlaceholderText("选择包含客户数据的 Excel 文件…")
        btn_browse = QtWidgets.QPushButton("浏览…")
        btn_reload = QtWidgets.QPushButton("重新读取")
        btn_tpl = QtWidgets.QPushButton("生成 Excel 模板")
        btn_browse.clicked.connect(self._browse_excel)
        btn_reload.clicked.connect(self._load)
        btn_tpl.clicked.connect(self._export_tpl)
        row.addWidget(QtWidgets.QLabel("数据文件："))
        row.addWidget(self.edt_excel, 1)
        row.addWidget(btn_browse)
        row.addWidget(btn_reload)
        row.addWidget(btn_tpl)
        root.addLayout(row)

        # ---------- 主体 ----------
        mid = QtWidgets.QSplitter(Horizontal)
        root.addWidget(mid, 1)

        # 左：数据表格
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(2, 2, 2, 2)
        lv.addWidget(QtWidgets.QLabel("数据预览（双击一行可载入左侧单户表单）"))
        self.table = QtWidgets.QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._row_activate)
        lv.addWidget(self.table, 1)
        mid.addWidget(left)

        # 右：输出设置
        right = QtWidgets.QWidget()
        right.setMinimumWidth(300)
        right.setMaximumWidth(420)
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(2, 2, 2, 2)

        box = QtWidgets.QGroupBox("输出设置")
        form = QtWidgets.QFormLayout(box)
        form.setLabelAlignment(AlignLeft)

        self.edt_out = QtWidgets.QLineEdit(
            str(Path(self.composer.base_dir) / "output"))
        btn_out = QtWidgets.QPushButton("选择…")
        btn_out.clicked.connect(self._browse_out)
        h = QtWidgets.QHBoxLayout()
        h.addWidget(self.edt_out, 1)
        h.addWidget(btn_out)
        form.addRow("输出目录：", h)

        self.cmb_fmt = QtWidgets.QComboBox()
        for text, key in FMT_ITEMS:
            self.cmb_fmt.addItem(text, key)
        self.cmb_fmt.currentIndexChanged.connect(self._fmt_changed)
        form.addRow("输出格式：", self.cmb_fmt)

        self.spin_quality = QtWidgets.QSpinBox()
        self.spin_quality.setRange(60, 100)
        self.spin_quality.setValue(95)
        self.spin_quality.setSuffix("  (JPG)")
        form.addRow("图片质量：", self.spin_quality)

        self.spin_chunk = QtWidgets.QSpinBox()
        self.spin_chunk.setRange(0, 9999)
        self.spin_chunk.setValue(0)
        self.spin_chunk.setSpecialValueText("不分卷（全部合并）")
        self.spin_chunk.setEnabled(False)
        form.addRow("PDF 分卷：", self.spin_chunk)

        self.cmb_font = QtWidgets.QComboBox()
        self.cmb_font.addItem("每户轮换字体（更像不同的人填写）", "random")
        self.cmb_font.addItem("全用同一套字体", "fixed")
        self.cmb_font.currentIndexChanged.connect(self._font_mode_changed)
        form.addRow("字体策略：", self.cmb_font)

        # 字体策略为 fixed 时，让用户指定用哪一套字体
        fonts = self.composer.fonts.scan()
        self.cmb_fixed_font = QtWidgets.QComboBox()
        self.cmb_fixed_font.addItem("（自动：目录第一套）", "")
        if not fonts:
            self.cmb_fixed_font.addItem("（未检测到字体）", "")
        for p in fonts:
            self.cmb_fixed_font.addItem(p.stem, str(p))
        self.cmb_fixed_font.setEnabled(False)
        form.addRow("指定字体：", self.cmb_fixed_font)

        self.edt_seed = QtWidgets.QLineEdit()
        self.edt_seed.setPlaceholderText("留空 = 每份都随机")
        form.addRow("随机种子：", self.edt_seed)

        rv.addWidget(box)

        # 操作按钮
        self.btn_start = QtWidgets.QPushButton("开始生成")
        self.btn_start.setFixedHeight(34)
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self._start)
        rv.addWidget(self.btn_start)

        self.btn_cancel = QtWidgets.QPushButton("取消")
        self.btn_cancel.setFixedHeight(28)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        rv.addWidget(self.btn_cancel)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setValue(0)
        rv.addWidget(self.progress)

        btn_open = QtWidgets.QPushButton("打开输出目录")
        btn_open.clicked.connect(self._open_out)
        rv.addWidget(btn_open)

        self.lbl_summary = QtWidgets.QLabel("")
        self.lbl_summary.setWordWrap(True)
        rv.addWidget(self.lbl_summary)

        rv.addStretch(1)
        mid.addWidget(right)
        mid.setStretchFactor(0, 1)
        mid.setStretchFactor(1, 0)

        # ---------- 日志 ----------
        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(110)
        self.log.setStyleSheet(
            "font-family:Consolas,Monospace;font-size:12px;")
        root.addWidget(self.log)

    # ------------------------------------------------------------------ #
    #  数据
    # ------------------------------------------------------------------ #
    def _browse_excel(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 Excel 文件", "",
            "Excel 文件 (*.xlsx *.xlsm *.xls);;所有文件 (*.*)")
        if path:
            self.edt_excel.setText(path)
            self._load()

    def _browse_out(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择输出目录", self.edt_out.text())
        if d:
            self.edt_out.setText(d)

    def _export_tpl(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "保存 Excel 模板", "合同批量导入模板.xlsx",
            "Excel 文件 (*.xlsx)")
        if not path:
            return
        try:
            export_template(path, self.composer.fields,
                            sample=demo_row(self.composer.fields))
            self._log(f"模板已生成：{path}")
            self.statusMessage.emit(f"Excel 模板已保存：{path}")
        except Exception as e:
            self._log(f"生成模板失败：{e}")

    def _load(self):
        path = self.edt_excel.text().strip()
        if not path:
            return
        try:
            records, warnings = load_excel(path, self.composer.fields)
        except Exception as e:
            self._log(f"读取失败：{e}")
            self.statusMessage.emit(f"Excel 读取失败：{e}")
            return

        self.records = records
        self._log(f"读取到 {len(records)} 条记录：{path}")
        for w in warnings[:20]:
            self._log(f"  [提示] {w}")
        if len(warnings) > 20:
            self._log(f"  ...另有 {len(warnings) - 20} 条提示")

        self._fill_table()
        self.btn_start.setEnabled(len(records) > 0)
        self.lbl_summary.setText(f"共 {len(records)} 条")
        self.statusMessage.emit(f"已载入 {len(records)} 条记录")

    def _fill_table(self):
        cols = list(self.composer.fields)
        self.table.clear()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(
            [c.get("label", c.get("key", "")) for c in cols])

        show = self.records[:MAX_PREVIEW_ROWS]
        self.table.setRowCount(len(show))
        for r, rec in enumerate(show):
            for c, f in enumerate(cols):
                v = rec.get(f.get("key"), "")
                if isinstance(v, bool):
                    v = "是" if v else "否"
                item = QtWidgets.QTableWidgetItem("" if v is None else str(v))
                item.setToolTip(str(v))
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()

    def _row_activate(self, r, _c):
        if 0 <= r < len(self.records):
            self.rowActivateRequested.emit(dict(self.records[r]))

    # ------------------------------------------------------------------ #
    #  生成
    # ------------------------------------------------------------------ #
    def _fmt_changed(self, *_):
        fmt = self.cmb_fmt.currentData()
        # 合订本类（pdf_merged / docx_merged）才需要分卷
        self.spin_chunk.setEnabled(fmt in ("pdf_merged", "docx_merged"))

    def _font_mode_changed(self, *_):
        # 只有"全用同一套字体"时才允许指定具体字体套系
        self.cmb_fixed_font.setEnabled(
            self.cmb_font.currentData() == "fixed")

    def _start(self):
        if not self.records:
            return
        if self.worker and self.worker.isRunning():
            self.statusMessage.emit("正在生成中，请稍候")
            return

        out_dir = self.edt_out.text().strip()
        if not out_dir:
            self.statusMessage.emit("请先选择输出目录")
            return

        seed_txt = self.edt_seed.text().strip()
        seed = int(seed_txt) if seed_txt.isdigit() else None

        # 批量用独立的 composer 实例，避免与预览争用状态
        composer = ContractComposer(
            config_path=self.composer.config_path,
            base_dir=self.composer.base_dir,
            font_dir=self.composer.font_dir)
        composer.tune = dict(self.composer.tune or {})

        runner = BatchRunner(
            composer, out_dir,
            fmt=self.cmb_fmt.currentData(),
            quality=self.spin_quality.value(),
            font_mode=self.cmb_font.currentData(),
            seed_base=seed,
            chunk_size=self.spin_chunk.value() if
            self.cmb_fmt.currentData() in ("pdf_merged", "docx_merged") else 0,
            fixed_font=self.cmb_fixed_font.currentData() or None)

        self.worker = _Worker(runner, self.records, self)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_done.connect(self._on_done)
        self.worker.start()

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setMaximum(len(self.records))
        self.progress.setValue(0)
        self._log("— 开始生成 —")

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self._log("正在取消…")
            self.btn_cancel.setEnabled(False)

    @Slot(int, int, str)
    def _on_progress(self, cur, total, msg):
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(cur)
        if msg:
            self._log(msg)

    @Slot(object, object)
    def _on_done(self, files, errors):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._log(f"— 完成 — 成功 {len(files)} 份，失败 {len(errors)} 份")
        for row, err in errors[:30]:
            self._log(f"  [失败] 第 {row} 行：{err}")
        self.lbl_summary.setText(
            f"成功 {len(files)} 份 / 失败 {len(errors)} 份\n"
            f"输出目录：{self.edt_out.text()}")
        self.statusMessage.emit(
            f"批量完成：成功 {len(files)} 份，失败 {len(errors)} 份")

    # ------------------------------------------------------------------ #
    def _log(self, msg):
        self.log.append(str(msg))
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _open_out(self):
        d = self.edt_out.text().strip()
        if not d or not Path(d).exists():
            self.statusMessage.emit("输出目录还不存在")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.run(["open", d], check=False)
            else:
                subprocess.run(["xdg-open", d], check=False)
        except Exception as e:
            self.statusMessage.emit(f"打开目录失败：{e}")

    def set_default_output(self, path):
        self.edt_out.setText(str(path))
