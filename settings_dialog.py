"""The Settings window, opened from the menu bar icon.

Every control applies as soon as it changes, the way macOS settings do, so
there is no OK or Cancel. The window holds no state of its own: it reads from
and writes to the widget, so the menu bar checkboxes and this window can't
disagree.
"""
from PyQt5.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QFontMetrics, QPalette
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QSizePolicy, QSpinBox, QVBoxLayout)

import paths
import telemetry

# (label, minutes between automatic stat changes; 0 = only when asked)
ROTATION_CHOICES = [
    ("Every 15 minutes", 15),
    ("Every 30 minutes", 30),
    ("Every hour", 60),
    ("Every 4 hours", 240),
    ("Never (I'll change it myself)", 0),
]

DELETE_RESULTS = {
    "deleted": "Deleted. Your total was removed from the server, and sharing "
               "is now off.",
    "nothing": "Nothing had been shared from this Mac. Sharing is now off.",
    "pending": "Couldn't reach the server, so nothing was removed yet. Sharing "
               "is off, and the deletion will be retried automatically.",
}


class DeleteWorker(QThread):
    """Runs the deletion off the interface thread; it can wait on the network."""
    outcome = pyqtSignal(str)

    def run(self):
        try:
            result = telemetry.delete_reported_data()
        except Exception:
            # The request is queued by then; anything unexpected reads as pending.
            result = "pending"
        self.outcome.emit(result)


# Text width inside a group box: the fixed window width less the window's and
# the group's margins. Deliberately a little under the real width, so a note is
# measured as if it wrapped slightly more than it will: that leaves a touch of
# slack at worst, and never clips a line.
NOTE_WIDTH = 372


def _note(text):
    """Small gray explanatory text with its wrapped height set exactly.

    Qt sizes a word-wrapped label from a width it hasn't settled on yet, which
    left either clipped last lines or large empty gaps. The window's width is
    fixed, so the height can simply be measured.
    """
    label = QLabel(text)
    label.setWordWrap(True)
    font = label.font()
    font.setPointSize(max(9, font.pointSize() - 2))
    label.setFont(font)
    palette = label.palette()
    palette.setColor(QPalette.WindowText, QColor(128, 128, 128))
    label.setPalette(palette)
    wrapped = QFontMetrics(font).boundingRect(0, 0, NOTE_WIDTH, 10000, Qt.TextWordWrap, text)
    label.setFixedHeight(wrapped.height() + 2)
    return label


class SettingsDialog(QDialog):
    deletion_finished = pyqtSignal(str)

    def __init__(self, app_widget):
        # No Qt parent, on purpose: a child of the widget would inherit its dark
        # translucent stylesheet and mangle the native controls. It stays on top
        # so the always-on-top widget can't cover it.
        super().__init__(None)
        self.app_widget = app_widget
        self._worker = None
        self._loading = True
        self.setWindowTitle("Money Guilt Settings")
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint |
                            Qt.WindowCloseButtonHint)
        # A fixed width lets Qt work out the wrapped notes' heights exactly;
        # with a flexible width it reserves room for a narrower layout.
        self.setFixedWidth(440)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(self._build_reporting())
        layout.addWidget(self._build_privacy())
        layout.addWidget(self._build_display())
        layout.addWidget(self._build_startup())
        layout.addWidget(self._build_data())

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self.load_values()
        self._loading = False
        self.layout().activate()
        self.adjustSize()

    # ------------------------------------------------------------ sections
    def _build_reporting(self):
        box = QGroupBox("Money reporting")
        lay = QVBoxLayout(box)

        self.share_checkbox = QCheckBox("Share my anonymous wasted total")
        self.share_checkbox.toggled.connect(self._on_share_toggled)
        lay.addWidget(self.share_checkbox)
        lay.addWidget(_note(
            "Sends only a random install ID, your total wasted dollars and a "
            "count of transactions. Never merchants, dates or individual "
            "purchases."))

        self.share_status = QLabel()
        self.share_status.setWordWrap(True)
        self.share_status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        lay.addWidget(self.share_status)

        self.delete_button = QPushButton("Delete My Reported Data")
        self.delete_button.clicked.connect(self.delete_reported)
        row = QHBoxLayout()
        row.addWidget(self.delete_button)
        row.addStretch()
        lay.addLayout(row)
        lay.addWidget(_note(
            "Removes your total from the server right away and turns sharing "
            "off, so it isn't sent again. You can turn sharing back on later; "
            "it will start fresh, not linked to what you deleted."))

        self.delete_result = QLabel()
        self.delete_result.setWordWrap(True)
        self.delete_result.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.delete_result.hide()
        lay.addWidget(self.delete_result)
        return box

    def _build_privacy(self):
        box = QGroupBox("Privacy")
        lay = QVBoxLayout(box)

        self.hide_checkbox = QCheckBox("Hide amounts")
        self.hide_checkbox.toggled.connect(self._on_hide_toggled)
        lay.addWidget(self.hide_checkbox)

        self.auto_checkbox = QCheckBox("Hide amounts when I'm away")
        self.auto_checkbox.toggled.connect(self._on_auto_toggled)
        self.idle_spin = QSpinBox()
        self.idle_spin.setRange(1, 120)
        self.idle_spin.setSuffix(" min")
        self.idle_spin.valueChanged.connect(self._on_idle_changed)
        row = QHBoxLayout()
        row.addWidget(self.auto_checkbox)
        row.addWidget(QLabel("after"))
        row.addWidget(self.idle_spin)
        row.addStretch()
        lay.addLayout(row)
        lay.addWidget(_note("Amounts stay hidden until you click the widget."))

        self.capture_checkbox = QCheckBox("Hide from screenshots and screen sharing")
        self.capture_checkbox.toggled.connect(self._on_capture_toggled)
        lay.addWidget(self.capture_checkbox)
        return box

    def _build_display(self):
        box = QGroupBox("Display")
        row = QHBoxLayout(box)
        row.addWidget(QLabel("Change the stat"))
        self.rotation_combo = QComboBox()
        for label, minutes in ROTATION_CHOICES:
            self.rotation_combo.addItem(label, minutes)
        self.rotation_combo.currentIndexChanged.connect(self._on_rotation_changed)
        row.addWidget(self.rotation_combo)
        row.addStretch()
        return box

    def _build_startup(self):
        box = QGroupBox("Startup")
        lay = QVBoxLayout(box)
        self.ask_checkbox = QCheckBox(
            "Ask me to categorize new transactions on launch")
        self.ask_checkbox.toggled.connect(self._on_ask_toggled)
        lay.addWidget(self.ask_checkbox)
        return box

    def _build_data(self):
        box = QGroupBox("Your data")
        lay = QVBoxLayout(box)
        lay.addWidget(_note("Everything Money Guilt stores stays in this folder "
                            "on your Mac:"))
        self.folder_label = QLabel(paths.data_dir())
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.folder_label)
        self.folder_button = QPushButton("Show in Finder")
        self.folder_button.clicked.connect(self.show_data_folder)
        row = QHBoxLayout()
        row.addWidget(self.folder_button)
        row.addStretch()
        lay.addLayout(row)
        return box

    # ------------------------------------------------------ reading state
    def load_values(self):
        """Fill every control from the real settings without triggering them."""
        w = self.app_widget
        was_loading, self._loading = self._loading, True
        try:
            self.share_checkbox.setChecked(telemetry.is_enabled())
            self.hide_checkbox.setChecked(w.privacy.manual)
            self.auto_checkbox.setChecked(w.privacy.auto_hide)
            self.idle_spin.setValue(max(1, round(w.privacy.idle_limit / 60)))
            self.idle_spin.setEnabled(w.privacy.auto_hide)
            self.capture_checkbox.setChecked(w.hide_from_capture)
            self.ask_checkbox.setChecked(w.ask_categorize_at_start)
            index = self.rotation_combo.findData(w.rotation_minutes)
            if index < 0:
                # A value set outside this window (an older or hand-edited
                # setting): show it rather than silently pretending it's another.
                self.rotation_combo.addItem(f"Every {w.rotation_minutes} minutes",
                                            w.rotation_minutes)
                index = self.rotation_combo.count() - 1
            self.rotation_combo.setCurrentIndex(index)
            self.refresh_status()
        finally:
            self._loading = was_loading

    def status_text(self, status=None):
        status = status or telemetry.status()
        if status["delete_pending"]:
            return ("Deletion pending: the server couldn't be reached. It will "
                    "be retried automatically, and nothing is shared until it "
                    "goes through.")
        if not status["collector_configured"]:
            return "No reporting server is set up on this Mac, so nothing is being sent."
        return "Sharing is on." if status["enabled"] else "Sharing is off."

    def refresh_status(self):
        self.share_status.setText(self.status_text())

    # ------------------------------------------------------------ actions
    def _apply(self, action):
        """Run a change to the widget, then keep the menu bar checkboxes in step."""
        if self._loading:
            return
        action()
        self.app_widget.sync_tray_actions()

    def _on_share_toggled(self, checked):
        def go():
            telemetry.set_enabled(checked)
            self.refresh_status()
        self._apply(go)

    def _on_hide_toggled(self, checked):
        self._apply(lambda: self.app_widget.set_manual_privacy(checked))

    def _on_auto_toggled(self, checked):
        def go():
            self.app_widget.set_auto_hide(checked)
            self.idle_spin.setEnabled(checked)
        self._apply(go)

    def _on_idle_changed(self, minutes):
        self._apply(lambda: self.app_widget.set_idle_minutes(minutes))

    def _on_capture_toggled(self, checked):
        self._apply(lambda: self.app_widget.set_hide_from_capture(checked))

    def _on_rotation_changed(self, index):
        self._apply(lambda: self.app_widget.set_rotation_minutes(
            self.rotation_combo.itemData(index)))

    def _on_ask_toggled(self, checked):
        self._apply(lambda: self.app_widget.set_ask_categorize(checked))

    def delete_reported(self):
        """Delete straight away: no second step after this click."""
        if self._worker is not None and self._worker.isRunning():
            return
        self.delete_button.setEnabled(False)
        self.delete_result.setStyleSheet("")
        self.delete_result.setText("Deleting…")
        self.delete_result.show()
        self._worker = DeleteWorker()
        self._worker.outcome.connect(self._deletion_done)
        self._worker.start()

    def _deletion_done(self, outcome):
        self.delete_result.setText(DELETE_RESULTS.get(outcome, DELETE_RESULTS["pending"]))
        # Deleting turns sharing off; show that in the checkbox and menu.
        was_loading, self._loading = self._loading, True
        self.share_checkbox.setChecked(telemetry.is_enabled())
        self._loading = was_loading
        self.app_widget.sync_tray_actions()
        self.refresh_status()
        self.delete_button.setEnabled(True)
        self.deletion_finished.emit(outcome)

    def show_data_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(paths.data_dir()))

    def _wait_for_worker(self):
        # A running QThread must not be destroyed; the request gives up in 5 s.
        if self._worker is not None:
            self._worker.wait(8000)

    def done(self, result):
        self._wait_for_worker()
        super().done(result)
