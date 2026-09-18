import sys
import os
import logging

# Set Qt plugin path for macOS
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = '/usr/local/lib/python3.14/site-packages/PyQt5/Qt5/plugins'

from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QFont, QCursor
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

        # Set size
        self.setFixedSize(QSize(400, 250))

        # Load stylesheet
        self.load_stylesheet()

        # Layout
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(5)

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

        # Middle spacer
        layout.addStretch()

        # Footer (last updated)
        self.footer_label = QLabel()
        self.footer_label.setObjectName("footer_label")
        self.footer_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.footer_label)

        self.setLayout(layout)

        # Load all stats
        self.load_stats()

        # Show initial stat
        self.show_next_stat()

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
