# -*- coding: utf-8 -*-
"""
Qt 绑定兼容层 —— 自动适配 PyQt5 / PySide6 / PyQt6。

为什么要这层：
    用户的机器环境不可控。PyQt5 在 Python 3.13 上可能没有预编译 wheel，
    而 PySide6 由 Qt 官方维护、对新 Python 版本跟进更快。
    两者 API 差异很小，用这一层抹平后，装任意一个都能直接跑。

优先顺序：PyQt5 -> PySide6 -> PyQt6（可用环境变量 QT_BINDING 强制指定）。
"""

import importlib
import os

_BINDINGS = ("PyQt5", "PySide6", "PyQt6")


def _detect():
    forced = os.environ.get("QT_BINDING")
    order = [forced] if forced else list(_BINDINGS)
    errors = []
    for name in order:
        try:
            importlib.import_module(name)
            return name
        except ImportError as e:
            errors.append(f"{name}: {e}")
    raise ImportError(
        "未找到可用的 Qt 绑定。请安装以下任意一个：\n"
        "    pip install PyQt5       （或）\n"
        "    pip install PySide6     （或）\n"
        "    pip install PyQt6\n"
        "详细信息：\n    " + "\n    ".join(errors))


QT_BINDING = _detect()

QtCore = importlib.import_module(f"{QT_BINDING}.QtCore")
QtGui = importlib.import_module(f"{QT_BINDING}.QtGui")
QtWidgets = importlib.import_module(f"{QT_BINDING}.QtWidgets")

Qt = QtCore.Qt

# 信号：PyQt5/6 叫 pyqtSignal，PySide6 叫 Signal
Signal = getattr(QtCore, "Signal", None) or QtCore.pyqtSignal
Slot = getattr(QtCore, "Slot", None) or getattr(QtCore, "pyqtSlot", None)


def _resolve(*paths):
    """依次尝试属性路径，返回第一个命中的值。"""
    for path in paths:
        obj = Qt
        ok = True
        for part in path.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                ok = False
                break
        if ok:
            return obj
    return None


# ---- 常用枚举（PyQt5 用扁平名，PySide6 需走子枚举）----
AlignLeft = _resolve("AlignLeft", "AlignmentFlag.AlignLeft")
AlignRight = _resolve("AlignRight", "AlignmentFlag.AlignRight")
AlignHCenter = _resolve("AlignHCenter", "AlignmentFlag.AlignHCenter")
AlignVCenter = _resolve("AlignVCenter", "AlignmentFlag.AlignVCenter")
AlignCenter = _resolve("AlignCenter", "AlignmentFlag.AlignCenter")
AlignTop = _resolve("AlignTop", "AlignmentFlag.AlignTop")
AlignBottom = _resolve("AlignBottom", "AlignmentFlag.AlignBottom")

Horizontal = _resolve("Horizontal", "Orientation.Horizontal")
Vertical = _resolve("Vertical", "Orientation.Vertical")

KeepAspectRatio = _resolve("KeepAspectRatio",
                           "AspectRatioMode.KeepAspectRatio")
KeepAspectRatioByExpanding = _resolve(
    "KeepAspectRatioByExpanding",
    "AspectRatioMode.KeepAspectRatioByExpanding")
SmoothTransformation = _resolve(
    "SmoothTransformation",
    "TransformationMode.SmoothTransformation")
FastTransformation = _resolve(
    "FastTransformation", "TransformationMode.FastTransformation")

LeftButton = _resolve("LeftButton", "MouseButton.LeftButton")
RightButton = _resolve("RightButton", "MouseButton.RightButton")

UserRole = _resolve("UserRole", "ItemDataRole.UserRole")
AscendingOrder = _resolve("AscendingOrder", "SortOrder.AscendingOrder")

SolidLine = _resolve("SolidLine", "PenStyle.SolidLine")
DashLine = _resolve("DashLine", "PenStyle.DashLine")
RoundCap = _resolve("RoundCap", "PenCapStyle.RoundCap")
NoBrush = _resolve("NoBrush", "BrushStyle.NoBrush")

ScrollBarAsNeeded = _resolve("ScrollBarAsNeeded",
                             "ScrollBarPolicy.ScrollBarAsNeeded")
ScrollBarAlwaysOff = _resolve("ScrollBarAlwaysOff",
                              "ScrollBarPolicy.ScrollBarAlwaysOff")

WindowModal = _resolve("WindowModal", "WindowModality.WindowModal")
ApplicationModal = _resolve("ApplicationModal",
                            "WindowModality.ApplicationModal")


# ---- 便捷导出 ----
def export_widgets():
    """返回一个命名空间对象，便于 `from .qt_compat import QtWidgets` 风格使用。"""
    return QtWidgets


__all__ = [
    "QT_BINDING", "QtCore", "QtGui", "QtWidgets", "Qt",
    "Signal", "Slot",
    "AlignLeft", "AlignRight", "AlignHCenter", "AlignVCenter",
    "AlignCenter", "AlignTop", "AlignBottom",
    "Horizontal", "Vertical",
    "KeepAspectRatio", "KeepAspectRatioByExpanding",
    "SmoothTransformation", "FastTransformation",
    "LeftButton", "RightButton", "UserRole", "AscendingOrder",
    "SolidLine", "DashLine", "RoundCap", "NoBrush",
    "ScrollBarAsNeeded", "ScrollBarAlwaysOff",
    "WindowModal", "ApplicationModal",
]
