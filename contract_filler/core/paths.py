# -*- coding: utf-8 -*-
"""
应用根目录解析 —— 兼容「源码运行」与「PyInstaller 打包 exe」两种场景。

打包后（sys.frozen == True）：
    程序实际运行目录 = exe 所在目录（config/ assets/ fonts/ 等资源应放在
    exe 旁边，用户可直接编辑模板/配置，程序也能正常写入 pen_tune.json、
    output/ 等）。返回 sys.executable 的父目录。
源码运行：
    返回本项目根目录（contract_filler/）。
"""

import sys
from pathlib import Path


def app_base() -> Path:
    """返回应用根目录（打包后 = exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
