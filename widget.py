import sys
import os
import logging

# Set Qt plugin path for macOS
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = '/usr/local/lib/python3.14/site-packages/PyQt5/Qt5/plugins'

from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSystemTrayIcon, QMenu
from PyQt5.QtCore import Qt, QTimer, QSize, pyqtSignal, QRect, QRectF
from PyQt5.QtGui import QFont, QCursor, QPainter, QPen, QColor, QBrush, QPixmap, QIcon, QPainterPath, QRegion
from stats import get_random_stat, get_all_stats
from categorization_dialog import CategorizationDialog
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MoneyGuiltWidget(QWidget):
    """Desktop widget for Money Guilt spending tracker"""

    stat_changed = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.current_stat = None
        self.stats = []
        self.current_stat_index = 0
        self.drag_position = None
        self.is_dragging = False
        self.resize_corner = None
        self.resize_start_rect = None
        self.resize_start_pos = None
        self.corner_threshold = 30
        self.init_ui()
        self.setup_timers()

    def init_ui(self):
        """Initialize the UI"""
        self.setWindowTitle("Money Guilt")
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.NoDropShadowWindowHint
        )

        # Note: Removed WA_TranslucentBackground as it was making widget invisible
        # The CSS provides the semi-transparent effect

        # Enable mouse tracking for hover effects
        self.setMouseTracking(True)

        # Set resizable size with minimum constraints
        self.setMinimumSize(QSize(280, 140))
        self.resize(QSize(350, 170))

        # Load stylesheet
        self.load_stylesheet()

        # Setup system tray icon
        self.setup_tray_icon()

        # Main layout. Top and bottom margins must match, or the content
        # area's midpoint won't line up with the widget's midpoint.
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(16, 10, 16, 10)
        main_layout.setSpacing(4)

        # Close button (not in layout, positioned absolutely)
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("close_button")
        self.close_button.setFixedSize(18, 18)
        self.close_button.clicked.connect(self.hide)
        self.close_button.setVisible(False)  # Hidden by default
        self.close_button.setParent(self)

        # Centering is a symmetry problem: the value only lands on the
        # widget's centerline if the band above it is the same height as the
        # band below it. The bottom band (subtitle + chart + footer) is the
        # taller one, so the title row is grown to match it by
        # _match_chrome_heights(); the title text stays pinned to the top of
        # that row. The interior in between then splits evenly around the
        # value.

        # Title row (top band) - height synced to the bottom band
        self.title_label = QLabel()
        self.title_label.setObjectName("title_label")
        self.title_label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        main_layout.addWidget(self.title_label, 0)

        # Interior: equal stretches on either side of the value
        interior = QVBoxLayout()
        interior.setContentsMargins(0, 0, 0, 0)
        interior.setSpacing(0)

        interior.addStretch(1)

        self.value_label = QLabel()
        self.value_label.setObjectName("value_label")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setWordWrap(True)
        interior.addWidget(self.value_label, 0)

        interior.addStretch(1)
        main_layout.addLayout(interior, 1)

        # Bottom band: subtitle, chart, then the footer pinned to the bottom
        self.bottom_band = QVBoxLayout()
        self.bottom_band.setContentsMargins(0, 0, 0, 0)
        self.bottom_band.setSpacing(4)

        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("subtitle_label")
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        self.bottom_band.addWidget(self.subtitle_label, 0)

        self.chart_label = QLabel()
        self.chart_label.setObjectName("chart_label")
        self.chart_label.setAlignment(Qt.AlignCenter)
        self.bottom_band.addWidget(self.chart_label, 0)

        # Footer with next button
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)

        # Footer (last updated)
        self.footer_label = QLabel()
        self.footer_label.setObjectName("footer_label")
        self.footer_label.setAlignment(Qt.AlignCenter)
        footer_layout.addStretch()
        footer_layout.addWidget(self.footer_label)

        # Next stat button
        self.next_button = QPushButton("→")
        self.next_button.setObjectName("next_button")
        self.next_button.setFixedSize(20, 20)
        self.next_button.clicked.connect(self.show_next_stat)
        footer_layout.addWidget(self.next_button)
        footer_layout.addStretch()

        self.bottom_band.addLayout(footer_layout)

        main_layout.addLayout(self.bottom_band, 0)
        self.setLayout(main_layout)

        # Apply rounded corners mask
        self.update_rounded_corners_mask()

        # Load all stats
        self.load_stats()

        # Show random initial stat
        if self.stats:
            stat = get_random_stat()
        else:
            stat = {
                'type': 'no_data',
                'title': 'No Data',
                'value': 'No wasteful spending tracked',
                'subtitle': 'Mark transactions as wasteful to see stats',
            }
        self.display_stat(stat)
        self.update_footer()

    def paintEvent(self, event):
        """Draw white border and rounded corners"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw dark grey border with rounded corners
        pen = QPen(QColor(100, 100, 100), 1)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        # Draw rounded rectangle border
        rect = self.rect()
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 24, 24)

        super().paintEvent(event)

    def setup_tray_icon(self):
        """Setup system tray icon for macOS menu bar"""
        # Create tray icon
        self.tray_icon = QSystemTrayIcon(self)

        # Create tray menu
        tray_menu = QMenu()

        # Show/Hide action
        self.toggle_action = tray_menu.addAction("Hide Widget")
        self.toggle_action.triggered.connect(self.toggle_widget)

        tray_menu.addSeparator()

        # Categorize transactions
        categorize_action = tray_menu.addAction("Categorize Transactions")
        categorize_action.triggered.connect(self.open_categorization_dialog)

        tray_menu.addSeparator()

        # Quit action
        quit_action = tray_menu.addAction("Quit Money Guilt")
        quit_action.triggered.connect(self.quit_app)

        self.tray_icon.setContextMenu(tray_menu)

        # Create icon with $ symbol
        icon_pixmap = QPixmap(44, 44)
        icon_pixmap.fill(Qt.transparent)
        icon_painter = QPainter(icon_pixmap)
        icon_painter.setRenderHint(QPainter.Antialiasing)
        font = QFont("Arial", 32, QFont.Bold)
        icon_painter.setFont(font)
        icon_painter.setPen(QColor(0, 0, 0))
        icon_painter.drawText(icon_pixmap.rect(), Qt.AlignCenter, "$")
        icon_painter.end()

        self.tray_icon.setIcon(QIcon(icon_pixmap))
        self.tray_icon.show()

        logger.info("System tray icon created")

    def open_categorization_dialog(self):
        """Open transaction categorization dialog"""
        dialog = CategorizationDialog(self)
        dialog.exec_()
        logger.info("Categorization dialog opened")

    def toggle_widget(self):
        """Toggle widget visibility"""
        if self.isVisible():
            self.hide()
            self.toggle_action.setText("Show Widget")
            self.tray_icon.setContextMenu(self.tray_icon.contextMenu())
            logger.info("Widget hidden")
        else:
            self.show()
            self.raise_()
            self.activateWindow()
            self.toggle_action.setText("Hide Widget")
            self.tray_icon.setContextMenu(self.tray_icon.contextMenu())
            logger.info("Widget shown")

    def quit_app(self):
        """Quit the application"""
        logger.info("Quitting application")
        QApplication.quit()

    def closeEvent(self, event):
        """Handle close event - hide instead of quit"""
        self.hide()
        self.toggle_action.setText("Show Widget")
        event.ignore()
        logger.info("Widget closed to tray")

    def load_stylesheet(self):
        """Load stylesheet from CSS file"""
        try:
            css_path = os.path.join(os.path.dirname(__file__), 'styles.css')
            with open(css_path, 'r') as f:
                stylesheet = f.read()
            self.setStyleSheet(stylesheet)
            logger.info("Stylesheet loaded from styles.css")
        except FileNotFoundError:
            logger.warning("styles.css not found, using default styling")
            self.setStyleSheet("QWidget { background-color: #1a1a1a; color: #fff; }")

    def load_stats(self):
        """Load all available stats"""
        self.stats = get_all_stats()
        logger.info(f"Loaded {len(self.stats)} stats")
        if self.stats:
            logger.info(f"Available stats: {[s['type'] for s in self.stats]}")

    def setup_timers(self):
        """Setup timers for updating stats"""
        # Update stat every hour
        self.stat_timer = QTimer()
        self.stat_timer.timeout.connect(self.show_next_stat)
        self.stat_timer.start(3600000)  # 1 hour in milliseconds

        # Update footer timestamp every minute
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_footer)
        self.update_timer.start(60000)  # 1 minute

        logger.info("Timers started: stat rotation every hour")

    def show_next_stat(self):
        """Display the next stat in rotation"""
        if not self.stats:
            self.load_stats()

        if not self.stats:
            stat = {
                'type': 'no_data',
                'title': 'No Data',
                'value': 'No wasteful spending tracked',
                'subtitle': 'Mark transactions as wasteful to see stats',
            }
        else:
            stat = self.stats[self.current_stat_index % len(self.stats)]
            self.current_stat_index += 1

        self.display_stat(stat)
        self.update_footer()
        logger.info(f"Displaying stat: {stat['type']}")

    def display_stat(self, stat):
        """Display a specific stat"""
        self.current_stat = stat

        self.title_label.setText(stat.get('title', ''))
        self.value_label.setText(str(stat.get('value', '')))
        self.subtitle_label.setText(stat.get('subtitle', ''))

        # Scale fonts to fit current widget size
        self.scale_fonts_to_fit()

        # Show chart for percentage stats
        if stat.get('type') == 'wasted_percentage':
            percentage = stat.get('data', {}).get('percentage', 0)
            self.draw_progress_bar(percentage)
            self.chart_label.show()
        else:
            self.chart_label.hide()

        self._match_chrome_heights()

    def draw_progress_bar(self, percentage):
        """Draw a progress bar for percentage stats"""
        scale_factor = self.width() / 340.0  # 340 is reference width
        width = max(100, int(200 * scale_factor))
        height = max(4, int(8 * scale_factor))

        # Create pixmap
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background (unfilled)
        painter.fillRect(0, 0, width, height, QColor(255, 255, 255, 30))

        # Filled portion (wasted percentage)
        filled_width = int(width * percentage / 100)
        painter.fillRect(0, 0, filled_width, height, QColor(255, 100, 100))

        # Border
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
        painter.drawRect(0, 0, width - 1, height - 1)

        painter.end()
        self.chart_label.setPixmap(pixmap)

    def update_footer(self):
        """Update the footer timestamp"""
        now = datetime.now().strftime("%I:%M %p")
        self.footer_label.setText(f"Last updated: {now}")

    def get_corner_at_pos(self, pos):
        """Determine which corner the position is near"""
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        t = self.corner_threshold

        # Top-left
        if x < t and y < t:
            return "top-left"
        # Top-right
        if x > w - t and y < t:
            return "top-right"
        # Bottom-left
        if x < t and y > h - t:
            return "bottom-left"
        # Bottom-right
        if x > w - t and y > h - t:
            return "bottom-right"

        return None

    def get_resize_cursor(self, corner):
        """Get the appropriate cursor for the corner"""
        # Hand cursor for all corners
        if corner:
            return Qt.OpenHandCursor
        return Qt.ArrowCursor

    def mousePressEvent(self, event):
        """Handle mouse press"""
        if event.button() == Qt.LeftButton:
            corner = self.get_corner_at_pos(event.pos())
            if corner:
                self.resize_corner = corner
                self.resize_start_rect = self.geometry()
                self.resize_start_pos = event.globalPos()
            else:
                self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
                self.is_dragging = False
            event.accept()

    def mouseMoveEvent(self, event):
        """Handle mouse move for resizing or dragging"""
        if self.resize_corner:
            # Resizing
            delta = event.globalPos() - self.resize_start_pos
            new_rect = QRect(self.resize_start_rect)

            if "top" in self.resize_corner:
                new_rect.setTop(self.resize_start_rect.top() + delta.y())
            if "bottom" in self.resize_corner:
                new_rect.setBottom(self.resize_start_rect.bottom() + delta.y())
            if "left" in self.resize_corner:
                new_rect.setLeft(self.resize_start_rect.left() + delta.x())
            if "right" in self.resize_corner:
                new_rect.setRight(self.resize_start_rect.right() + delta.x())

            # Apply minimum size
            if new_rect.width() >= self.minimumWidth() and new_rect.height() >= self.minimumHeight():
                self.setGeometry(new_rect)

            event.accept()
        elif event.buttons() == Qt.LeftButton and self.drag_position is not None:
            # Dragging
            self.move(event.globalPos() - self.drag_position)
            self.is_dragging = True
            event.accept()

        # Always update cursor based on corner proximity (for hover effect)
        if not self.resize_corner and event.buttons() == Qt.NoButton:
            corner = self.get_corner_at_pos(event.pos())
            if corner:
                self.setCursor(QCursor(self.get_resize_cursor(corner)))
            else:
                self.setCursor(QCursor(Qt.ArrowCursor))

    def mouseReleaseEvent(self, event):
        """End dragging or resizing"""
        if event.button() == Qt.LeftButton:
            if self.resize_corner:
                self.resize_corner = None
                self.resize_start_rect = None
                self.resize_start_pos = None
                # Update mask after resize complete
                self.update_rounded_corners_mask()
            elif not self.is_dragging:
                # Single click - advance stat
                self.show_next_stat()
            self.drag_position = None
            self.is_dragging = False
            event.accept()

    def mouseDoubleClickEvent(self, event):
        """Reload stats on double-click"""
        if event.button() == Qt.LeftButton:
            logger.info("Reloading stats...")
            self.load_stats()
            self.current_stat_index = 0
            self.show_next_stat()
            self.is_dragging = False
            event.accept()

    def enterEvent(self, event):
        """Show close button on mouse enter"""
        self.close_button.setVisible(True)
        self.close_button.move(16, 12)
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Hide close button on mouse leave"""
        self.close_button.setVisible(False)
        super().leaveEvent(event)

    def resizeEvent(self, event):
        """Handle resize event - update rounded corners mask and scale fonts"""
        super().resizeEvent(event)
        # Only update mask if not actively resizing from corners
        if not self.resize_corner:
            self.update_rounded_corners_mask()
            self.scale_fonts_to_fit()
            self._match_chrome_heights()

    def _match_chrome_heights(self):
        """Grow the title row to match the bottom band so the value centers.

        The value sits between two equal stretches, so it is centered within
        the interior. The interior is only centered in the widget when the
        bands above and below it are the same height.
        """
        margins = self.layout().contentsMargins()
        inner_width = self.width() - margins.left() - margins.right()

        def row_height(label):
            # A wrapping label's sizeHint assumes a width of its own choosing,
            # so ask what it actually needs at the width it will be given.
            if label.wordWrap() and inner_width > 0:
                wrapped = label.heightForWidth(inner_width)
                if wrapped > 0:
                    return wrapped
            return label.sizeHint().height()

        # Measured from the visible rows only - the layout's own sizeHint
        # still counts the chart label while it is hidden.
        rows = []
        if not self.subtitle_label.isHidden():
            rows.append(row_height(self.subtitle_label))
        if not self.chart_label.isHidden():
            rows.append(row_height(self.chart_label))
        rows.append(max(self.footer_label.sizeHint().height(),
                        self.next_button.height()))

        bottom_height = sum(rows) + self.bottom_band.spacing() * (len(rows) - 1)

        # Font metrics, not sizeHint: once a fixed height is set, sizeHint
        # reports that height back and the row could then only ever grow.
        natural_title = self.title_label.fontMetrics().height()
        self.title_label.setFixedHeight(max(natural_title, bottom_height))

    def update_rounded_corners_mask(self):
        """Update the rounded corners mask based on current size"""
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 24, 24)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)

    def scale_fonts_to_fit(self):
        """Scale fonts dynamically based on widget size"""
        widget_height = self.height()
        scale_factor = widget_height / 170.0  # 170 is the default height

        # Scale fonts proportionally
        title_size = max(9, int(13 * scale_factor))
        value_size = max(20, int(30 * scale_factor))
        subtitle_size = max(9, int(13 * scale_factor))
        footer_size = max(8, int(10 * scale_factor))

        # Update title
        title_font = self.title_label.font()
        title_font.setPointSize(title_size)
        self.title_label.setFont(title_font)

        # Update value
        value_font = self.value_label.font()
        value_font.setPointSize(value_size)
        self.value_label.setFont(value_font)

        # Update subtitle
        subtitle_font = self.subtitle_label.font()
        subtitle_font.setPointSize(subtitle_size)
        self.subtitle_label.setFont(subtitle_font)

        # Update footer
        footer_font = self.footer_label.font()
        footer_font.setPointSize(footer_size)
        self.footer_label.setFont(footer_font)

        # Force layout to recalculate with new font sizes
        self.layout().invalidate()
        self.update()


def main():
    """Main entry point"""
    app = QApplication(sys.argv)

    # Create and show widget
    widget = MoneyGuiltWidget()

    # Position in top-right of primary monitor
    screen = app.primaryScreen()
    screen_geom = screen.geometry()

    # Top-right corner with padding
    x = screen_geom.width() - 370  # 350 widget width + 20px padding
    y = 20

    logger.info(f"Screen: {screen.name()}, Geometry: {screen_geom.width()}x{screen_geom.height()}")
    logger.info(f"Positioning widget at ({x}, {y})")

    widget.move(x, y)
    widget.setVisible(True)
    widget.show()
    widget.raise_()
    widget.activateWindow()
    widget.setFocus()

    logger.info(f"Widget shown at ({x}, {y})")

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
