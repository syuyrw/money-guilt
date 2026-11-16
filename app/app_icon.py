"""The app icon: a green $ on a dark rounded square

Drawn entirely in code with PyQt5's QPainter (rather than loading an image
file), so there's no separate icon asset to keep in sync with the app.
"""

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap


def draw_icon(size):
    """Paints the icon at one specific pixel size (e.g. 32x32) and returns
    it as a QPixmap - an in-memory image PyQt5 can display or save."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)  # start from a blank, see-through square
    # QPainter is PyQt5's drawing API: every call below (setBrush, setPen,
    # drawRoundedRect, drawText, ...) issues one drawing instruction onto
    # the pixmap `pm`, in order, like layers stacked on top of each other.
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)  # smooths curved edges instead of leaving them jagged
    p.setBrush(QBrush(QColor(30, 30, 32)))  # fill color for the rounded square: a near-black gray
    p.setPen(Qt.NoPen)                      # no outline/border around the shape
    # The rounded square itself, sized to fill the whole icon. The corner
    # radius (size * 0.225) scales with the icon's own size, so a 512px
    # icon and a 32px icon look proportionally the same.
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.225, size * 0.225)
    p.setPen(QColor(48, 209, 88))  # switch to green for the dollar sign
    font = QFont("Helvetica Neue")
    font.setPixelSize(int(size * 0.6))  # the $ fills most of the icon's height
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "$")
    p.end()  # finishes painting - required before the pixmap can be used elsewhere
    return pm


def app_icon():
    """Builds the full QIcon macOS actually uses: several sizes of the
    same picture bundled together, so the OS can pick whichever size fits
    best (a small one for the menu bar, a large one for the Dock, etc.)
    without blurrily stretching a single image."""
    icon = QIcon()
    for size in (32, 64, 128, 256, 512):
        icon.addPixmap(draw_icon(size))
    return icon
