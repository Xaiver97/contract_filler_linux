# -*- coding: utf-8 -*-
"""主窗口 —— 单户填写 / 批量生成 两个页签 + 工具栏。"""

import subprocess
import sys
from pathlib import Path

from .batch_panel import BatchPanel
from .picker import CalibrateDialog
from .qt_compat import (AlignLeft, Qt, QtCore, QtGui, QtWidgets, Signal)
from .single_panel import SinglePanel

# 打包后指向 exe 所在目录，源码运行指向项目根目录
from core.paths import app_base  # noqa: E402

ROOT = app_base()


class MainWindow(QtWidgets.QMainWindow):
    """应用主窗口。"""

    def __init__(self, composer, parent=None):
        super().__init__(parent)
        self.composer = composer
        self.setWindowTitle("供用电合同手写填充工具")
        self.resize(1420, 920)

        self.single = SinglePanel(composer)
        self.batch = BatchPanel(composer)

        self._build_ui()
        self._build_menu()
        # 首屏自检延后一拍，避免窗口还没显示就弹窗
        QtCore.QTimer.singleShot(350, self._check_resources)

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.single, "单户填写")
        self.tabs.addTab(self.batch, "批量生成")
        self.setCentralWidget(self.tabs)

        # 面板间联动
        self.single.calibrateRequested.connect(self.open_calibrate)
        self.batch.rowActivateRequested.connect(self._load_row_to_single)
        self.single.statusMessage.connect(self._status)
        self.batch.statusMessage.connect(self._status)

        sb = self.statusBar()
        self.lbl_status = QtWidgets.QLabel("就绪")
        sb.addWidget(self.lbl_status, 1)
        self.lbl_res = QtWidgets.QLabel("")
        self.lbl_res.setStyleSheet("color:#666;")
        sb.addPermanentWidget(self.lbl_res)

        self._refresh_res_label()

    def _build_menu(self):
        mb = self.menuBar()

        # ---- 文件 ----
        m = mb.addMenu("文件")
        a = QtWidgets.QAction("重新加载配置与模板", self)
        a.triggered.connect(self._reload)
        m.addAction(a)

        a = QtWidgets.QAction("打开项目目录", self)
        a.triggered.connect(lambda: self._open_path(ROOT))
        m.addAction(a)

        a = QtWidgets.QAction("打开输出目录", self)
        a.triggered.connect(lambda: self._open_path(ROOT / "output"))
        m.addAction(a)

        m.addSeparator()
        a = QtWidgets.QAction("退出", self)
        a.setShortcut("Ctrl+Q")
        a.triggered.connect(self.close)
        m.addAction(a)

        # ---- 工具 ----
        m = mb.addMenu("工具")
        a = QtWidgets.QAction("校准字段坐标…", self)
        a.setShortcut("Ctrl+K")
        a.triggered.connect(self.open_calibrate)
        m.addAction(a)

        a = QtWidgets.QAction("生成 Excel 导入模板…", self)
        a.triggered.connect(self.batch._export_tpl)
        m.addAction(a)

        a = QtWidgets.QAction("运行渲染自检…", self)
        a.triggered.connect(self._run_selftest)
        m.addAction(a)

        # ---- 帮助 ----
        m = mb.addMenu("帮助")
        a = QtWidgets.QAction("使用说明", self)
        a.triggered.connect(self._show_help)
        m.addAction(a)
        a = QtWidgets.QAction("关于", self)
        a.triggered.connect(self._about)
        m.addAction(a)

    # ------------------------------------------------------------------ #
    #  自检与提示
    # ------------------------------------------------------------------ #
    def _check_resources(self):
        st = self.composer.status()
        missing = []

        if not st["template_exists"]:
            missing.append(
                f"• 还没放合同模板\n    请把扫描件放到：{st['template']}")
        if st["fonts_total"] == 0:
            missing.append(
                f"• fonts/ 目录是空的\n    请把手写字体 .ttf/.otf 放进去："
                f"{st['fonts_dir']}")
        elif st["fonts_cjk"] == 0:
            missing.append(
                "• fonts/ 里的字体都不含中文，写出来会是空白方块\n"
                "    请换用手写中文字体（如演示佛系体、站酷快乐体等）")
        if not st["calibrated"] and st["template_exists"]:
            missing.append(
                "• 字段坐标尚未校准\n"
                "    点「工具 → 校准坐标」，在图上点选每个字段的落笔位置")

        if missing:
            QtWidgets.QMessageBox.information(
                self, "还需要做几件事",
                "检测到以下待办：\n\n" + "\n\n".join(missing) +
                "\n\n（这些都可以稍后再处理，程序现在也能打开。）")

        self._refresh_res_label()

    def _refresh_res_label(self):
        st = self.composer.status()
        self.lbl_res.setText(
            f"字体 {st['fonts_total']} | "
            f"模板 {'已载入' if st['template_exists'] else '缺失'} | "
            f"坐标 {'已校准' if st['calibrated'] else '未校准'}")

    # ------------------------------------------------------------------ #
    #  动作
    # ------------------------------------------------------------------ #
    def open_calibrate(self):
        st = self.composer.status()
        if not st["template_exists"]:
            QtWidgets.QMessageBox.warning(
                self, "无法校准",
                f"请先放入合同模板：\n{st['template']}")
            return
        dlg = CalibrateDialog(self.composer, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._status("坐标已保存")
            self.single.refresh()
            self._refresh_res_label()

    def _load_row_to_single(self, rec):
        self.single.set_data({k: v for k, v in rec.items()
                              if not str(k).startswith("_")})
        self.tabs.setCurrentWidget(self.single)
        self._status("已载入该行数据到单户表单")

    def _reload(self):
        try:
            self.composer.reload()
            self.single.refresh()
            self._refresh_res_label()
            self._status("配置已重新加载")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "加载失败", str(e))

    def _run_selftest(self):
        self._run_script(ROOT / "tools" / "selftest.py", "渲染自检")

    def _run_script(self, script: Path, title: str):
        if not script.exists():
            QtWidgets.QMessageBox.warning(self, "找不到脚本", str(script))
            return
        try:
            out = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True, text=True,
                cwd=str(ROOT), encoding="utf-8", errors="replace",
                timeout=180)
            text = (out.stdout or "") + (out.stderr or "")
            QtWidgets.QMessageBox.information(
                self, title + "结果",
                text[-3000:] if text else "（无输出）")
            self.composer.fonts.scan(force=True)
            self.single.refresh()
            self._refresh_res_label()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, title + "失败", str(e))

    @staticmethod
    def _open_path(p):
        p = Path(p)
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                import os
                os.startfile(str(p))
            elif sys.platform == "darwin":
                subprocess.run(["open", str(p)], check=False)
            else:
                subprocess.run(["xdg-open", str(p)], check=False)
        except Exception:
            pass

    def _show_help(self):
        QtWidgets.QMessageBox.information(
            self, "使用说明",
            "1. 把合同扫描件放到 assets/template.jpg\n"
            "2. 把手写字体放进 fonts/ 目录\n"
            "3. 「工具 → 校准字段坐标」逐个对准落笔位置（只需做一次）\n"
            "4. 单户页签：填表 → 预览 → 导出\n"
            "5. 批量页签：先「生成 Excel 模板」，填好后导入，一键批量出图\n\n"
            "完整说明见项目根目录 README.md")

    def _about(self):
        from .qt_compat import QT_BINDING
        QtWidgets.QMessageBox.about(
            self, "关于",
            "供用电合同手写填充工具\n\n"
            "手写质感采用六层模型：字形 / 笔触 / 布局 / 墨迹 / 融合，\n"
            "重点解决「新字贴到扫描件上会跳出来」的问题。\n\n"
            f"Qt 绑定：{QT_BINDING}")

    # ------------------------------------------------------------------ #
    def _status(self, msg):
        self.lbl_status.setText(str(msg))
