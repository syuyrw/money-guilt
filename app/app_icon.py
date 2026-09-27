"""The app icon: a green $ on a dark rounded square"""

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap


def draw_icon(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(30, 30, 32)))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.225, size * 0.225)
    p.setPen(QColor(48, 209, 88))
    font = QFont("Helvetica Neue")
    font.setPixelSize(int(size * 0.6))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "$")
    p.end()
    return pm


def app_icon():
    icon = QIcon()
    for size in (32, 64, 128, 256, 512):
        icon.addPixmap(draw_icon(size))
    return icon
