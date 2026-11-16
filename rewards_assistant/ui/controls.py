"""设置控件：禁用滚轮改值，保留键盘、鼠标点击与输入能力。"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox, QTimeEdit


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):  # noqa: N802
        event.ignore()  # 交由滚动容器处理，不改变选择项


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):  # noqa: N802
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):  # noqa: N802
        event.ignore()


class NoWheelTimeEdit(QTimeEdit):
    def wheelEvent(self, event):  # noqa: N802
        event.ignore()
