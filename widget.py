import sys
import os
import logging

# Set Qt plugin path for macOS
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = '/usr/local/lib/python3.14/site-packages/PyQt5/Qt5/plugins'

from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSystemTrayIcon, QMenu, QSizeGrip
from PyQt5.QtCore import Qt, QTimer, QSize, pyqtSignal, QRect
from PyQt5.QtGui import QFont, QCursor, QPainter, QPen, QColor, QBrush, QPixmap, QIcon
from stats import get_random_stat, get_all_stats
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

        # Set resizable size with minimum constraints
        self.setMinimumSize(QSize(280, 200))
        self.resize(QSize(400, 250))

        # Load stylesheet
        self.load_stylesheet()

        # Setup system tray icon
        self.setup_tray_icon()

        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(16, 12, 16, 16)
        main_layout.setSpacing(0)

        # Header with close button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 8)
        header_layout.setSpacing(0)

        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("close_button")
        self.close_button.setFixedSize(18, 18)
        self.close_button.clicked.connect(self.hide)
        header_layout.addWidget(self.close_button)
        header_layout.addStretch()

        main_layout.addLayout(header_layout)

        # Content layout - Section 2 (Shape and Size) - 16px content padding per Tahoe spec
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)  # Section 2.1 - 8px spacing for related items

        # Top spacer
        layout.addStretch()

        # Title label
        self.title_label = QLabel()
        self.title_label.setObjectName("title_label")
        self.title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_label)

        # Value label (large)
        self.value_label = QLabel()
        self.value_label.setObjectName("value_label")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setWordWrap(True)
        layout.addWidget(self.value_label)

        # Subtitle label
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("subtitle_label")
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

        # Chart container (for percentage stats)
        self.chart_label = QLabel()
        self.chart_label.setObjectName("chart_label")
        self.chart_label.setAlignment(Qt.AlignCenter)
        self.chart_label.setFixedHeight(40)
        layout.addWidget(self.chart_label)

        # Middle spacer
        layout.addStretch()

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

        # Size grip for resizing
        size_grip = QSizeGrip(self)
        size_grip.setStyleSheet("QSizeGrip { width: 16px; height: 16px; }")
        footer_layout.addWidget(size_grip)

        layout.addLayout(footer_layout)

        main_layout.addLayout(layout)
        self.setLayout(main_layout)

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

        # Show chart for percentage stats
        if stat.get('type') == 'wasted_percentage':
            percentage = stat.get('data', {}).get('percentage', 0)
            self.draw_progress_bar(percentage)
            self.chart_label.show()
        else:
            self.chart_label.hide()

    def draw_progress_bar(self, percentage):
        """Draw a progress bar for percentage stats"""
        width = 200
        height = 8

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

    def mousePressEvent(self, event):
        """Handle mouse press"""
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            self.is_dragging = False
            event.accept()

    def mouseMoveEvent(self, event):
        """Move window while dragging"""
        if event.buttons() == Qt.LeftButton and self.drag_position is not None:
            self.move(event.globalPos() - self.drag_position)
            self.is_dragging = True
            event.accept()

    def mouseReleaseEvent(self, event):
        """End dragging"""
        if event.button() == Qt.LeftButton:
            if not self.is_dragging:
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


def main():
    """Main entry point"""
    app = QApplication(sys.argv)

    # Create and show widget
    widget = MoneyGuiltWidget()

    # Position in top-right of primary monitor
    screen = app.primaryScreen()
    screen_geom = screen.geometry()

    # Top-right corner with padding
    x = screen_geom.width() - 420  # widget width + 20px padding
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
