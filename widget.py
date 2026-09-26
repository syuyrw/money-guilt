import sys
import os
import html
import logging

# Set Qt plugin path for macOS
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = '/usr/local/lib/python3.14/site-packages/PyQt5/Qt5/plugins'

from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSystemTrayIcon, QMenu, QMessageBox
from PyQt5.QtCore import QUrl, Qt, QTimer, QSize, QPoint, QSettings, pyqtSignal, QRect, QRectF, QLockFile, QDir
from PyQt5.QtGui import QDesktopServices, QFont, QFontMetrics, QCursor, QPainter, QPen, QColor, QBrush, QPixmap, QIcon, QPainterPath, QRegion, QLinearGradient
from stats import get_random_stat, get_all_stats
from categorization_dialog import CategorizationDialog
from datetime import datetime
from database import init_db
import telemetry
import feedback
import privacy
from app_icon import app_icon

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Dollar figures that represent waste, and the ring's filled arc.
WASTED_COLOR = "rgb(255, 100, 100)"


class _DimOverlay(QWidget):
    """Translucent dark veil over the widget; ignores the mouse."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(QRectF(self.rect()), 24, 24)


class MoneyGuiltWidget(QWidget):
    """Desktop widget for Money Guilt spending tracker"""

    stat_changed = pyqtSignal(dict)

    def __init__(self, settings=None):
        super().__init__()
        # Injectable so tests never touch the user's real saved position.
        self.settings = settings or QSettings("MoneyGuilt", "MoneyGuiltWidget")
        self.privacy = privacy.PrivacyState(
            manual=self.settings.value("privacy/manual", False, type=bool),
            auto_hide=self.settings.value("privacy/auto_hide", True, type=bool),
            idle_limit=self.settings.value("privacy/idle_minutes", 5, type=int) * 60)
        # On by default: the widget stays out of screenshots and screen shares
        # unless you turn that off from the tray menu.
        self.hide_from_capture = self.settings.value(
            "privacy/hide_from_capture", True, type=bool)
        self.current_stat = None
        self.stats = []
        self.current_stat_index = 0
        self.drag_position = None
        self.is_dragging = False
        self.resize_corner = None
        self.resize_start_rect = None
        self.resize_start_pos = None
        self.corner_threshold = 30
        self.ring_percentage = None
        self._value_lines = []
        self.init_ui()
        self.setup_timers()

    _dim_overlay = None

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
        self.close_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.close_button.clicked.connect(self.hide)
        self.close_button.setVisible(False)  # Hidden by default
        self.close_button.setParent(self)

        # Centering is a symmetry problem: the value only lands on the
        # widget's centerline if the band above it is the same height as the
        # band below it. The bottom band (subtitle + chart + footer) is the
        # taller one, so the title row is grown to match it by
        # _match_chrome_heights(). The interior in between then splits evenly
        # around the value.

        # Title row (top band) - height synced to the bottom band. The title
        # sits at the bottom of that band so the padding collects above it,
        # at the widget's top edge. Centering the title inside the band
        # instead splits that padding and pushes half of it between the title
        # and the value, which is what made the value look low.
        self.title_label = QLabel()
        self.title_label.setObjectName("title_label")
        self.title_label.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
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
        self.bottom_band.setSpacing(3)

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
        self.next_button.setFixedSize(16, 16)
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
        """Draw the glass edge, a soft top sheen, and rounded corners"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect()
        radius = 24
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5),
                               radius, radius)

        # Section 1.3 - LAYER 3 SPECULAR EDGE. A dim, fully-opaque grey
        # (100,100,100) here used to be the only border ever drawn - the
        # QSS "border: 5px solid white" never painted at all on a bare
        # QWidget, so this flat grey line was doing all the work and read
        # as a dull outline rather than a glass rim. A thin, translucent
        # white edge matches how light catches a real glass surface.
        painter.save()
        painter.setClipPath(outline)

        # Soft sheen along the top, fading out by the widget's midline -
        # the light-catches-glass cue that makes the surface read as glass
        # rather than flat plastic.
        sheen = QLinearGradient(0, 0, 0, rect.height() * 0.55)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 20))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, sheen)

        painter.restore()

        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(outline)

        self._draw_ring(painter)

        super().paintEvent(event)

    def _ring_geometry(self):
        """Circle for the percentage ring, or None when no ring is shown.

        The ring occupies the interior between the two bands instead of
        taking a row of its own. A row would push the bottom band down, and
        the top band mirrors it to keep the value centered, so every pixel
        of chart would cost two - at this widget's height that caps a
        charted row at about 15px, too small to read as a chart.
        """
        if self.ring_percentage is None:
            return None

        margins = self.layout().contentsMargins()
        band = self.title_label.height()
        interior = (self.height() - margins.top() - margins.bottom()
                    - 2 * self.layout().spacing() - 2 * band)

        diameter = min(interior, int(self.width() * 0.45))
        if diameter < 24:
            return None

        stroke = max(4, round(diameter / 12))
        return self.width() / 2, self.height() / 2, diameter, stroke

    def _draw_ring(self, painter):
        """Draw the donut chart behind the value."""
        ring = self._ring_geometry()
        if not ring:
            return

        cx, cy, diameter, stroke = ring
        # drawArc strokes centred on the path, so inset by half the width.
        box = QRectF(cx - diameter / 2 + stroke / 2,
                     cy - diameter / 2 + stroke / 2,
                     diameter - stroke, diameter - stroke)

        pen = QPen(QColor(255, 255, 255, 38), stroke, Qt.SolidLine, Qt.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(box, 0, 360 * 16)

        pct = max(0.0, min(100.0, self.ring_percentage))
        if pct > 0:
            pen.setColor(QColor(255, 100, 100))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            # Clockwise from twelve o'clock; Qt angles run counter-clockwise.
            painter.drawArc(box, 90 * 16, -int(360 * 16 * pct / 100))

    def setup_tray_icon(self):
        """Setup system tray icon for macOS menu bar"""
        # Create tray icon
        self.tray_icon = QSystemTrayIcon(self)

        # Create tray menu
        tray_menu = QMenu()

        # Show/Hide action
        self.toggle_action = tray_menu.addAction("Hide Widget")
        self.toggle_action.triggered.connect(self.toggle_widget)

        next_action = tray_menu.addAction("Next Stat")
        next_action.triggered.connect(lambda: self.advance_stat())

        tray_menu.addSeparator()

        # Privacy: the widget sits on screen showing spending
        self.hide_amounts_action = tray_menu.addAction("Hide Amounts")
        self.hide_amounts_action.setCheckable(True)
        self.hide_amounts_action.setChecked(self.privacy.manual)
        self.hide_amounts_action.toggled.connect(self.set_manual_privacy)

        self.auto_hide_action = tray_menu.addAction("Auto-Hide When Idle")
        self.auto_hide_action.setCheckable(True)
        self.auto_hide_action.setChecked(self.privacy.auto_hide)
        self.auto_hide_action.toggled.connect(self.set_auto_hide)

        self.capture_action = tray_menu.addAction("Hide From Screenshots && Sharing")
        self.capture_action.setCheckable(True)
        self.capture_action.setChecked(self.hide_from_capture)
        self.capture_action.toggled.connect(self.set_hide_from_capture)

        self.share_total_action = tray_menu.addAction("Share Anonymous Wasted Total")
        self.share_total_action.setCheckable(True)
        self.share_total_action.setChecked(telemetry.is_enabled())
        self.share_total_action.toggled.connect(telemetry.set_enabled)

        tray_menu.addSeparator()

        # Categorize transactions
        categorize_action = tray_menu.addAction("Categorize Transactions")
        categorize_action.triggered.connect(lambda: self.open_categorization_dialog())

        tray_menu.addSeparator()

        self.feedback_action = tray_menu.addAction("Send Feedback…")
        self.feedback_action.triggered.connect(self.send_feedback)

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

        icon = QIcon(icon_pixmap)
        # Marks the icon as a macOS template image: only its shape (alpha)
        # is used and the system tints it itself - white on a dark menu bar,
        # black on a light one, updating live as the bar changes. A fixed
        # black fill was invisible on dark backgrounds, and the app has no
        # way to read the pixels behind the menu bar to pick a colour itself.
        icon.setIsMask(True)
        self.tray_icon.setIcon(icon)
        self.tray_icon.show()

        logger.info("System tray icon created")

    def open_categorization_dialog(self, limit=100, only_if_new=False):
        """Open transaction categorization dialog.

        With only_if_new, does nothing when there is nothing to ask about. The
        tray menu leaves it off so choosing "Categorize Transactions" still
        answers with an "all done" message rather than silence.
        """
        dialog = CategorizationDialog(self, limit=limit)
        if only_if_new and not dialog.transactions:
            return
        self.set_dimmed(True)
        try:
            dialog.exec_()
        finally:
            self.set_dimmed(False)
        logger.info("Categorization dialog opened")

    def set_dimmed(self, dimmed):
        """Darken the widget while a modal window blocks it."""
        if self._dim_overlay is None:
            self._dim_overlay = _DimOverlay(self)
        self._dim_overlay.setGeometry(self.rect())
        self._dim_overlay.setVisible(dimmed)
        if dimmed:
            self._dim_overlay.raise_()

    def send_feedback(self):
        """Open a feedback email draft in the user's mail app"""
        if not QDesktopServices.openUrl(QUrl(feedback.feedback_url())):
            QMessageBox.information(
                self, "Send Feedback",
                f"Couldn't open your mail app. Email {feedback.FEEDBACK_EMAIL} instead.")

    def show_sharing_notice(self):
        """Once, say what the anonymous total is and how to turn it off"""
        if not telemetry.needs_notice():
            return
        box = QMessageBox(self)
        box.setWindowTitle("Money Guilt")
        box.setText("Money Guilt shares one anonymous number.")
        box.setInformativeText(
            "It reports your total wasted dollars, a transaction count, and a "
            "random ID that isn't linked to you. No merchants, dates, or "
            "individual purchases are ever sent.\n\n"
            "You can change this any time from the menu bar icon: "
            "Share Anonymous Wasted Total.")
        box.addButton("OK", QMessageBox.AcceptRole)
        turn_off = box.addButton("Turn Off Sharing", QMessageBox.DestructiveRole)
        box.exec_()
        if box.clickedButton() is turn_off:
            self.share_total_action.setChecked(False)
        telemetry.mark_notice_shown()

    def startup(self):
        self.show_sharing_notice()
        self.prompt_for_new_transactions()
        telemetry.report_in_background()

    def prompt_for_new_transactions(self):
        """Startup prompt: ask the user to categorize a few new transactions.

        Silent when there are none, so launching the widget doesn't open a
        window just to say there is nothing to do.
        """
        self.open_categorization_dialog(limit=10, only_if_new=True)

    def default_position(self):
        """Top-right corner of the primary screen."""
        geom = QApplication.primaryScreen().geometry()
        return QPoint(geom.x() + geom.width() - self.width() - 20,
                      geom.y() + 20)

    def save_position(self):
        self.settings.setValue("window/x", self.x())
        self.settings.setValue("window/y", self.y())
        self.settings.sync()

    def initial_position(self):
        """Where the widget was last left, or the default if that spot is
        no longer on any screen.

        Display layouts change (undocking, a monitor moved or unplugged), and
        a saved coordinate from the old layout can land outside every screen,
        leaving a widget that is running but can't be seen or dragged back.
        """
        if not (self.settings.contains("window/x")
                and self.settings.contains("window/y")):
            return self.default_position()

        pos = QPoint(int(self.settings.value("window/x", type=int)),
                     int(self.settings.value("window/y", type=int)))
        center = QPoint(pos.x() + self.width() // 2,
                        pos.y() + self.height() // 2)
        if any(screen.availableGeometry().contains(center)
               for screen in QApplication.screens()):
            return pos
        return self.default_position()

    def refresh_display(self):
        """Redraw the current stat, e.g. after privacy changes."""
        if self.current_stat is not None:
            self.display_stat(self.current_stat)

    def _check_idle(self):
        if self.privacy.check_idle():
            logger.info("Amounts hidden after inactivity")
            self.refresh_display()

    def set_manual_privacy(self, enabled):
        self.privacy.manual = bool(enabled)
        self.settings.setValue("privacy/manual", bool(enabled))
        self.settings.sync()
        self.refresh_display()

    def set_auto_hide(self, enabled):
        self.privacy.set_auto_hide(bool(enabled))
        self.settings.setValue("privacy/auto_hide", bool(enabled))
        self.settings.sync()
        self.refresh_display()

    def set_hide_from_capture(self, enabled):
        self.hide_from_capture = bool(enabled)
        self.settings.setValue("privacy/hide_from_capture", bool(enabled))
        self.settings.sync()
        self.apply_capture_exclusion()

    def apply_capture_exclusion(self):
        """Ask macOS to leave this window out of screenshots and screen sharing.

        Only on the real macOS platform plugin: the native call needs a genuine
        NSView, and any other plugin hands back something that is not one.
        """
        if QApplication.platformName() != "cocoa":
            return False
        applied = privacy.set_capture_excluded(int(self.winId()), self.hide_from_capture)
        if not applied:
            logger.warning("Could not change screen-capture exclusion")
        return applied

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_capture_exclusion()

    def hideEvent(self, event):
        """Remember where the widget was whenever it is hidden or closed."""
        self.save_position()
        super().hideEvent(event)

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
        self.save_position()
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

        # Check for inactivity twice a minute
        self.privacy_timer = QTimer()
        self.privacy_timer.timeout.connect(self._check_idle)
        self.privacy_timer.start(15000)

        logger.info("Timers started: stat rotation every hour")

    def advance_stat(self):
        """Refresh the stats from the database, then move on to the next one"""
        self.load_stats()
        self.show_next_stat()
        # Restart the hourly timer so a stat you just picked isn't replaced
        # a moment later
        self.stat_timer.start(3600000)

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

        subtitle = stat.get('subtitle', '')

        value_text = str(stat.get('value', ''))
        if stat.get('type') == 'wasted_percentage':
            # Inside a ring titled "Percent of Spending Wasted" the sign is
            # redundant, and dropping it buys the figure a larger size in a
            # tight opening.
            value_text = value_text.rstrip('%')

        masked = self.privacy.masked
        value_extra = stat.get('value_extra')
        if masked:
            # The whole value goes, not just its digits: a merchant or trip
            # name says as much about spending as the amount does.
            value_text = privacy.MASK
            value_extra = None
            subtitle = privacy.mask_numbers(subtitle)

        # A dollar figure is only reddened when the stat says it represents
        # waste. The percentage stat's subtitle also carries a figure, but
        # that one is total spending, so it stays in the normal colour.
        wasted_text = None if masked else stat.get('wasted_text')

        self.title_label.setText(stat.get('title', ''))

        # The value can run to a second line so an amount can share the
        # vendor's size. Lines are kept in plain form for measurement, since
        # the label's own text is markup once a line is coloured.
        self._value_lines = [value_text]
        if value_extra:
            self._value_lines.append(str(value_extra))

        rendered = []
        for line in self._value_lines:
            safe = html.escape(line)
            if wasted_text and line == wasted_text:
                safe = f'<span style="color: {WASTED_COLOR}">{safe}</span>'
            rendered.append(safe)
        self.value_label.setText('<br>'.join(rendered))

        if wasted_text and wasted_text in subtitle:
            subtitle = subtitle.replace(
                wasted_text,
                f'<span style="color: {WASTED_COLOR}">{wasted_text}</span>')
        self.subtitle_label.setText(subtitle)
        # An empty subtitle would otherwise hold an blank row open under
        # the value.
        self.subtitle_label.setVisible(bool(subtitle))

        # The percentage stat draws a ring around the value instead of
        # filling a chart row.
        if stat.get('type') == 'wasted_percentage':
            # An empty ring while masked: the arc would give the percentage away.
            self.ring_percentage = 0 if masked else stat.get('data', {}).get('percentage', 0)
        else:
            self.ring_percentage = None
        self.chart_label.hide()

        self._refresh_metrics()


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
            moved = bool(self.resize_corner) or self.is_dragging
            if self.resize_corner:
                self.resize_corner = None
                self.resize_start_rect = None
                self.resize_start_pos = None
                # Update mask after resize complete
                self.update_rounded_corners_mask()
            elif not self.is_dragging:
                if self.privacy.idle_locked:
                    # Hidden because nobody was here: this click is the
                    # deliberate act that brings the amounts back, so it
                    # does not also skip to the next stat.
                    self.privacy.reveal()
                    self.refresh_display()
                else:
                    # Single click - advance stat
                    self.show_next_stat()
            self.drag_position = None
            self.is_dragging = False
            if moved:
                # Saved here as well as on hide, so a crash or kill still
                # leaves the last position behind.
                self.save_position()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        """Show the next stat on double-click"""
        if event.button() == Qt.LeftButton:
            self.advance_stat()
            self.is_dragging = False
            event.accept()

    def enterEvent(self, event):
        """Show close button on mouse enter"""
        self.close_button.setVisible(True)
        self.close_button.move(16, 12)
        # The button was created before the layout's widgets, so those
        # widgets stack above it and intercept clicks at its position even
        # though the title_label there renders nothing but background.
        self.close_button.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Hide close button on mouse leave"""
        self.close_button.setVisible(False)
        super().leaveEvent(event)

    def resizeEvent(self, event):
        """Handle resize event - update rounded corners mask and scale fonts"""
        super().resizeEvent(event)
        if self._dim_overlay is not None:
            self._dim_overlay.setGeometry(self.rect())
        # Only update mask if not actively resizing from corners
        if not self.resize_corner:
            self.update_rounded_corners_mask()
            self._refresh_metrics()

    def _refresh_metrics(self):
        """Re-fit type and band heights.

        Order matters: the bands are measured from the scaled fonts, and the
        value is fitted to the space those bands leave over.
        """
        self.scale_fonts_to_fit()
        self._match_chrome_heights()
        self._fit_value()

    def _fit_value(self):
        """Size the value to the space it has.

        Constrained on both axes, because the value can run to two lines and
        a ring narrows the opening it has to sit in.
        """
        lines = [l for l in self._value_lines if l]
        if not lines:
            return

        margins = self.layout().contentsMargins()
        ring = self._ring_geometry()
        if ring:
            _, _, diameter, stroke = ring
            width_budget = height_budget = diameter - 2 * stroke - 6
        else:
            # Leave a gutter so the text stops short of the rounded edges.
            width_budget = (self.width() - margins.left() - margins.right()
                            - 16)
            band = self.title_label.height()
            height_budget = (self.height() - margins.top() - margins.bottom()
                             - 2 * self.layout().spacing() - 2 * band)

        scale = self.height() / 170.0
        # The hero size suits a short figure like "$346.98". Names read as
        # oversized at it, and real ones get long ("Uber 063015 SF**POOL**").
        if any(c.isalpha() for c in ''.join(lines)):
            preferred = max(16, int(24 * scale))
        else:
            preferred = max(20, int(30 * scale))

        font = self.value_label.font()
        probe = QFont(font)
        floor = max(12, int(preferred * 0.5))
        chosen = floor
        for size in range(preferred, floor - 1, -1):
            probe.setPixelSize(size)
            metrics = QFontMetrics(probe)
            widest = max(metrics.boundingRect(l).width() for l in lines)
            stack = metrics.lineSpacing() * len(lines)
            if widest <= width_budget and stack <= height_budget:
                chosen = size
                break

        font.setPixelSize(chosen)
        self.value_label.setFont(font)

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

        # The bands have to match from both sides. A large title can be the
        # taller one (a stat with no subtitle has only the footer below), so
        # pad the bottom band up to it. Done as a top margin rather than a
        # spacer item: an item adds a layout gap even at height 0, which
        # would shift every stat that needs no padding.
        self.bottom_band.setContentsMargins(
            0, max(0, natural_title - bottom_height), 0, 0)

    def update_rounded_corners_mask(self):
        """Update the rounded corners mask based on current size"""
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 24, 24)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)


    def _fitted_title_size(self, preferred):
        """Largest title size, at most preferred, that fits on one line.

        The title is 50% larger than the original 13px (about 20px), which
        is wide enough that the longest ("Percent of Spending Wasted") would
        clip on a narrow widget, so it steps down until it fits.
        """
        text = self.title_label.text()
        if not text:
            return preferred
        margins = self.layout().contentsMargins()
        available = self.width() - margins.left() - margins.right() - 8
        probe = QFont(self.title_label.font())
        probe.setBold(True)
        for size in range(preferred, 11, -1):
            probe.setPixelSize(size)
            if QFontMetrics(probe).boundingRect(text).width() <= available:
                return size
        return 12

    def scale_fonts_to_fit(self):
        """Scale fonts dynamically based on widget size.

        Sizes are in pixels to match the type scale styles.css used to
        declare. They have to be set here rather than in the stylesheet: a
        stylesheet font-size wins over setFont, which would pin every label
        to one size and make this method do nothing.
        """
        widget_height = self.height()
        scale_factor = widget_height / 170.0  # 170 is the default height

        # Scale fonts proportionally. The value is sized separately in
        # _fit_value, once the bands are known.
        title_size = self._fitted_title_size(max(12, round(19.5 * scale_factor)))
        subtitle_size = max(9, int(13 * scale_factor))
        footer_size = max(8, int(10 * scale_factor))

        # Update title
        title_font = self.title_label.font()
        title_font.setPixelSize(title_size)
        self.title_label.setFont(title_font)

        # Update subtitle
        subtitle_font = self.subtitle_label.font()
        subtitle_font.setPixelSize(subtitle_size)
        self.subtitle_label.setFont(subtitle_font)

        # Update footer
        footer_font = self.footer_label.font()
        footer_font.setPixelSize(footer_size)
        self.footer_label.setFont(footer_font)

        # Force layout to recalculate with new font sizes
        self.layout().invalidate()
        self.update()


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())

    # Only one copy at a time. The lock is released if the process dies, so a
    # crash can't leave the widget unable to start.
    lock = QLockFile(os.path.join(QDir.tempPath(), "money_guilt.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        logger.info("Money Guilt is already running; exiting")
        sys.exit(0)

    init_db()

    # Create and show widget
    widget = MoneyGuiltWidget()

    pos = widget.initial_position()
    logger.info(f"Positioning widget at ({pos.x()}, {pos.y()})")

    widget.move(pos)
    widget.setVisible(True)
    widget.show()
    widget.raise_()
    widget.activateWindow()
    widget.setFocus()

    logger.info(f"Widget shown at ({pos.x()}, {pos.y()})")

    # Ask about new transactions once the event loop is running
    QTimer.singleShot(0, widget.startup)

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
