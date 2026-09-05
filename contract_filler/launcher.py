# -*- coding: utf-8 -*-
"""
PyInstaller 打包入口（隐藏启动器）。

PyInstaller 基于静态分析收集依赖，而 main.py 的 gui/cli 都在函数体内
import 各子模块，PyInstaller 会漏掉。这里在模块顶层把所有子模块 + Qt
绑定显式 import 一遍，保证分析器收集齐全。
"""
import sys


def _bootstrap_imports():
    # 确保打包后也能从 exe 所在目录 import 到自定义包
    if getattr(sys, "frozen", False):
        import os
        d = os.path.dirname(os.path.abspath(sys.executable))
        if d not in sys.path:
            sys.path.insert(0, d)
    # ---- 顶层显式导入（触发 PyInstaller 收集）----
    import core  # noqa: F401
    import core.composer  # noqa: F401
    import core.fonts  # noqa: F401
    import core.renderer  # noqa: F401
    import core.strokes  # noqa: F401
    import core.ink  # noqa: F401
    import core.noise  # noqa: F401
    import batch  # noqa: F401
    import batch.excel_loader  # noqa: F401
    import batch.runner  # noqa: F401
    import batch.docx_export  # noqa: F401
    import gui  # noqa: F401
    import gui.qt_compat  # noqa: F401
    import gui.main_window  # noqa: F401
    import gui.single_panel  # noqa: F401
    import gui.batch_panel  # noqa: F401
    import gui.picker  # noqa: F401
    import gui.preview  # noqa: F401


_bootstrap_imports()

import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main.main())
