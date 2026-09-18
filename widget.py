import sys
import os
import logging

# Set Qt plugin path for macOS
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = '/usr/local/lib/python3.14/site-packages/PyQt5/Qt5/plugins'

from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPalette
from stats import get_random_stat, get_spending_summary
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MoneyGuiltWidget(QWidget):
    """Desktop widget for Money Guilt spending tracker"""

    stat_changed = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.current_stat = None
        self.init_ui()
        self.setup_timers()

    def init_ui(self):
        """Initialize the UI"""
        self.setWindowTitle("Money Guilt")
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.WindowType_Mask
        )

        # Set size
        self.setFixedSize(QSize(320, 200))

        # Dark theme
        self.set_dark_theme()

        # Layout
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        # Title label
        self.title_label = QLabel()
        self.title_label.setFont(QFont("Helvetica", 12, QFont.Bold))
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("color: #aaa; text-transform: uppercase; letter-spacing: 1px;")
        layout.addWidget(self.title_label)

        # Value label (large)
        self.value_label = QLabel()
        self.value_label.setFont(QFont("Helvetica", 32, QFont.Bold))
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setStyleSheet("color: #fff;")
        self.value_label.setWordWrap(True)
        layout.addWidget(self.value_label)

        # Subtitle label
        self.subtitle_label = QLabel()
        self.subtitle_label.setFont(QFont("Helvetica", 10))
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        self.subtitle_label.setStyleSheet("color: #666;")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

        # Footer (last updated)
        self.footer_label = QLabel()
        self.footer_label.setFont(QFont("Helvetica", 8))
        self.footer_label.setAlignment(Qt.AlignCenter)
        self.footer_label.setStyleSheet("color: #444;")
        layout.addStretch()
        layout.addWidget(self.footer_label)

        self.setLayout(layout)

        # Show initial stat
        self.show_next_stat()

    def set_dark_theme(self):
        """Apply dark theme"""
        dark_stylesheet = """
            QWidget {
                background-color: #1a1a1a;
                color: #fff;
            }
            QLabel {
                color: #fff;
            }
        """
        self.setStyleSheet(dark_stylesheet)

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
        """Display the next random stat"""
        stat = get_random_stat(30)
        self.current_stat = stat

        self.title_label.setText(stat.get('title', ''))
        self.value_label.setText(str(stat.get('value', '')))
        self.subtitle_label.setText(stat.get('subtitle', ''))

        self.update_footer()
        logger.info(f"Displaying stat: {stat['type']}")

    def update_footer(self):
        """Update the footer timestamp"""
        now = datetime.now().strftime("%I:%M %p")
        self.footer_label.setText(f"Last updated: {now}")

    def mousePressEvent(self, event):
        """Change stat on click"""
        if event.button() == Qt.LeftButton:
            self.show_next_stat()

    def mouseDoubleClickEvent(self, event):
        """Open dashboard on double-click (placeholder for future)"""
        if event.button() == Qt.LeftButton:
            logger.info("Double-click: would open dashboard")

    def show_summary(self):
        """Show spending summary in console"""
        summary = get_spending_summary(30)
        print("\n" + "=" * 50)
        print("Money Guilt - Spending Summary")
        print("=" * 50)
        for key, value in summary.items():
            print(f"{key.replace('_', ' ').title()}: {value}")
        print("=" * 50 + "\n")


def main():
    """Main entry point"""
    app = QApplication(sys.argv)

    # Create and show widget
    widget = MoneyGuiltWidget()
    widget.show()

    # Position in corner (top-right)
    screen = app.primaryScreen()
    size = widget.frameGeometry()
    x = screen.availableGeometry().right() - size.width() - 20
    y = screen.availableGeometry().top() + 20
    widget.move(x, y)

    logger.info(f"Widget started at ({x}, {y})")

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
