# -*- coding: utf-8 -*-
"""
图像预览控件 —— 支持缩放、字段标记、坐标拾取。

坐标拾取是校准流程的核心：在预览图上点一下，直接换算出该点在
模板**参考坐标系**（config 里存的坐标系）下的位置，省去手工换算。
"""

from PIL import Image, ImageDraw

from .qt_compat import (AlignLeft, AlignTop, Qt, QtCore, QtGui, QtWidgets,
                        Signal, Slot, SmoothTransformation)


def pil_to_qpixmap(img: Image.Image) -> QtGui.QPixmap:
    """PIL.Image -> QPixmap（自动处理 RGBA/RGB）。"""
    if img is None:
        return QtGui.QPixmap()
    if img.mode == "RGBA":
        buf = img.convert("RGBA").tobytes("raw", "RGBA")
        qimg = QtGui.QImage(buf, img.width, img.height,
                            QtGui.QImage.Format.Format_RGBA8888)
    else:
        rgb = img.convert("RGB")
        buf = rgb.tobytes("raw", "RGB")
        qimg = QtGui.QImage(buf, rgb.width, rgb.height,
                            QtGui.QImage.Format.Format_RGB888)
    return QtGui.QPixmap.fromImage(qimg.copy())


class _Canvas(QtWidgets.QLabel):
    """内部画布：负责接收鼠标事件并发出像素坐标。"""

    clicked = Signal(tuple)    # (x, y) 相对 pixmap
    moved = Signal(tuple)      # (x, y) 相对 pixmap
    escaped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        # 必须左上对齐且尺寸贴合 pixmap，否则坐标会整体偏移
        self.setAlignment(AlignLeft | AlignTop)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _pos(self, e):
        if hasattr(e, "position"):
            p = e.position().toPoint()
        else:
            p = e.pos()
        return p.x(), p.y()

    def mousePressEvent(self, e):
        self.clicked.emit(self._pos(e))
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self.moved.emit(self._pos(e))
        super().mouseMoveEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
        super().keyPressEvent(e)


class CanvasPreview(QtWidgets.QScrollArea):
    """带滚动条的图像预览区。"""

    # 拾取到坐标：(模板参考坐标系 x, y)
    pointPicked = Signal(float, float)
    # 鼠标移动：(模板参考坐标系 x, y) 或 None
    hoverChanged = Signal(object)
    pickCancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = _Canvas()
        self.setWidget(self.canvas)
        self.setWidgetResizable(False)
        self.setAlignment(AlignLeft | AlignTop)

        self._pil = None
        self._zoom = 1.0
        self._markers = []
        self._show_markers = False
        self._picking = False
        self._to_ref = 1.0     # 模板像素 -> 参考坐标 的换算系数

        self.canvas.clicked.connect(self._on_click)
        self.canvas.moved.connect(self._on_move)
        self.canvas.escaped.connect(self._cancel_pick)

        self.setStyleSheet("background:#8a8a8a;")

    # ------------------------------------------------------------------ #
    def set_image(self, img: Image.Image, keep_zoom=False):
        """设置预览图像。img 需为 PIL.Image。"""
        self._pil = img.copy() if img is not None else None
        if not keep_zoom or self._zoom <= 0:
            self._zoom = 1.0
        self._render()

    def set_ref_scale(self, s: float):
        """设置"模板像素 -> 参考坐标"的换算系数（1 / composer.scale）。"""
        self._to_ref = float(s or 1.0)

    def set_markers(self, markers, show=True):
        """markers: [(x, y, label, color)]，坐标为**参考坐标系**。"""
        self._markers = markers or []
        self._show_markers = show
        self._render()

    # ------------------------------------------------------------------ #
    def _render(self):
        if self._pil is None:
            self.canvas.setPixmap(QtGui.QPixmap())
            self.canvas.resize(1, 1)
            return

        img = self._pil.copy()

        # 画字段锚点标记（坐标是参考系，需先换算到模板像素系）
        if self._show_markers and self._markers and self._to_ref:
            draw = ImageDraw.Draw(img, "RGBA")
            k = 1.0 / self._to_ref if self._to_ref else 1.0
            for m in self._markers:
                if len(m) >= 2:
                    x, y = m[0] * k, m[1] * k
                    label = m[2] if len(m) > 2 else ""
                    color = m[3] if len(m) > 3 else (220, 40, 40, 255)
                    r = max(6, int(9 / max(self._zoom, 0.05)))
                    draw.ellipse([x - r, y - r, x + r, y + r],
                                 outline=color, width=2)
                    draw.line([x - r * 2, y, x + r * 2, y],
                              fill=color, width=1)
                    draw.line([x, y - r * 2, x, y + r * 2],
                              fill=color, width=1)
                    if label:
                        draw.text((x + r + 3, y - r - 2), str(label),
                                  fill=color)

        w = max(1, int(img.width * self._zoom))
        h = max(1, int(img.height * self._zoom))
        pm = pil_to_qpixmap(img).scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                        SmoothTransformation)
        self.canvas.setPixmap(pm)
        self.canvas.resize(pm.size())

    # ------------------------------------------------------------------ #
    def set_zoom(self, z):
        self._zoom = max(0.05, min(8.0, float(z)))
        self._render()

    def zoom_in(self):
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self):
        self.set_zoom(self._zoom / 1.25)

    def zoom_actual(self):
        self.set_zoom(1.0)

    def fit_to_window(self):
        if self._pil is None or self._pil.width == 0:
            return
        vw = max(50, self.viewport().width() - 4)
        self.set_zoom(vw / float(self._pil.width))

    @property
    def zoom(self):
        return self._zoom

    # ------------------------------------------------------------------ #
    def set_picking(self, on: bool):
        self._picking = bool(on)
        self.canvas.setCursor(Qt.CursorShape.CrossCursor if on
                              else Qt.CursorShape.ArrowCursor)

    @property
    def picking(self):
        return self._picking

    def _to_template(self, px, py):
        """pixmap 坐标 -> 模板像素坐标 -> 参考坐标系坐标。"""
        if not self._zoom:
            return 0.0, 0.0
        tx = px / self._zoom
        ty = py / self._zoom
        return tx * self._to_ref, ty * self._to_ref

    @Slot(tuple)
    def _on_click(self, pos):
        if not self._picking or self._pil is None:
            return
        x, y = self._to_template(pos[0], pos[1])
        self.pointPicked.emit(round(x, 1), round(y, 1))

    @Slot(tuple)
    def _on_move(self, pos):
        if self._pil is None:
            return
        x, y = self._to_template(pos[0], pos[1])
        self.hoverChanged.emit((round(x, 1), round(y, 1)))

    def _cancel_pick(self):
        if self._picking:
            self.set_picking(False)
            self.pickCancelled.emit()

    # ------------------------------------------------------------------ #
    def resizeEvent(self, e):
        super().resizeEvent(e)

    def wheelEvent(self, e):
        """Ctrl + 滚轮 缩放。"""
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if hasattr(e, "angleDelta"):
                dy = e.angleDelta().y()
            else:
                dy = e.delta()
            if dy > 0:
                self.zoom_in()
            elif dy < 0:
                self.zoom_out()
            e.accept()
        else:
            super().wheelEvent(e)
