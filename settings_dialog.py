"""The Settings window, opened from the menu bar icon.

Every control applies as soon as it changes, the way macOS settings do, so
there is no OK or Cancel. The window holds no state of its own: it reads from
and writes to the widget and the telemetry settings. This is the only place
these settings live; the menu bar icon just opens it.
"""
from PyQt5.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QFontMetrics, QPalette
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QMessageBox, QSizePolicy, QSpinBox, QVBoxLayout)

import disconnect
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


DISCONNECT_RESULTS = {
    "disconnected": "Disconnected. Plaid no longer has access to your bank, and the "
                    "saved token was removed from your Keychain. Your saved "
                    "transactions are still on this Mac.",
    "not_linked": "No bank account was linked.",
    "unconfigured": "Plaid isn't set up on this Mac (its credentials are missing or "
                    "invalid), so nothing was changed.",
    "keychain": "Couldn't read your Keychain, so nothing was changed.",
    "failed": "Couldn't reach Plaid, so nothing was changed and your token was "
              "kept. Try again when you're online.",
}

CONFIRM_DISCONNECT = (
    "Disconnect your bank account?\n\n"
    "Money Guilt will ask Plaid to revoke its access, then remove the saved "
    "token. Transactions already saved stay on this Mac. To use a bank again "
    "you would link it again.")

CONFIRM_ERASE = (
    "Delete all saved transactions and accounts, and what you've taught the "
    "app about merchants?\n\n"
    "This can't be undone. It doesn't disconnect your bank, and it doesn't "
    "remove anything you shared (use the buttons above for those).")


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


class DisconnectWorker(QThread):
    """Asks Plaid to revoke access off the interface thread."""
    outcome = pyqtSignal(str)

    def run(self):
        try:
            result = disconnect.disconnect_bank()
        except Exception:
            result = "failed"       # the token is still saved, so a retry is possible
        self.outcome.emit(result)


# Text width inside a group box: the fixed window width less the window's and
# the group's margins. Deliberately a little under the real width, so a note is
# measured as if it wrapped slightly more than it will: that leaves a touch of
# slack at worst, and never clips a line.
NOTE_WIDTH = 372


def _count(n, noun):
    return f"{n} {noun}" + ("" if n == 1 else "s")


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
    disconnect_finished = pyqtSignal(str)

    def __init__(self, app_widget):
        # No Qt parent, on purpose: a child of the widget would inherit its dark
        # translucent stylesheet and mangle the native controls. It stays on top
        # so the always-on-top widget can't cover it.
        super().__init__(None)
        self.app_widget = app_widget
        self._worker = None
        self._disconnect_worker = None
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
        layout.addWidget(self._build_bank())
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
            "Sends only a random install ID and your total wasted dollars. "
            "Never merchants, vendors, dates, counts or individual "
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

    def _build_bank(self):
        box = QGroupBox("Bank account")
        lay = QVBoxLayout(box)
        self.bank_status_label = QLabel()
        lay.addWidget(self.bank_status_label)
        self.disconnect_button = QPushButton("Disconnect Bank Account\u2026")
        self.disconnect_button.clicked.connect(self.disconnect_bank_clicked)
        row = QHBoxLayout()
        row.addWidget(self.disconnect_button)
        row.addStretch()
        lay.addLayout(row)
        lay.addWidget(_note(
            "Asks Plaid to revoke Money Guilt's access, then removes the saved "
            "token. Transactions already saved stay on this Mac."))
        self.bank_result = QLabel()
        self.bank_result.setWordWrap(True)
        self.bank_result.hide()
        lay.addWidget(self.bank_result)
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

        self.erase_button = QPushButton("Delete Local Data\u2026")
        self.erase_button.clicked.connect(self.erase_clicked)
        row = QHBoxLayout()
        row.addWidget(self.erase_button)
        row.addStretch()
        lay.addLayout(row)
        lay.addWidget(_note(
            "Erases every saved transaction and account and everything you've "
            "taught the app about merchants. It can't be undone."))
        self.erase_result = QLabel()
        self.erase_result.setWordWrap(True)
        self.erase_result.hide()
        lay.addWidget(self.erase_result)
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
            self.refresh_bank_status()
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
        """Run a change to the widget, unless the window is only filling itself in."""
        if self._loading:
            return
        action()

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
        # Deleting turns sharing off; show that in the checkbox.
        was_loading, self._loading = self._loading, True
        self.share_checkbox.setChecked(telemetry.is_enabled())
        self._loading = was_loading
        self.refresh_status()
        self.delete_button.setEnabled(True)
        self.deletion_finished.emit(outcome)

    BANK_STATUS_TEXT = {
        "linked": "A bank account is linked.",
        "not_linked": "No bank account is linked.",
        "unknown": "Couldn't read your Keychain, so the link status is unknown.",
    }

    def refresh_bank_status(self):
        status = disconnect.bank_status()
        self.bank_status_label.setText(self.BANK_STATUS_TEXT[status])
        self.disconnect_button.setEnabled(status != "not_linked")

    def _confirm(self, text):
        """Ask before anything irreversible. Cancel is the default answer."""
        return QMessageBox.question(
            self, "Money Guilt", text, QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel) == QMessageBox.Yes

    def disconnect_bank_clicked(self):
        if self._disconnect_worker is not None and self._disconnect_worker.isRunning():
            return
        if not self._confirm(CONFIRM_DISCONNECT):
            return
        self.disconnect_button.setEnabled(False)
        self.bank_result.setText("Disconnecting\u2026")
        self.bank_result.show()
        self._disconnect_worker = DisconnectWorker()
        self._disconnect_worker.outcome.connect(self._disconnect_done)
        self._disconnect_worker.start()

    def _disconnect_done(self, outcome):
        self.bank_result.setText(DISCONNECT_RESULTS.get(outcome, DISCONNECT_RESULTS["failed"]))
        self.refresh_bank_status()
        self.disconnect_finished.emit(outcome)

    def erase_clicked(self):
        if not self._confirm(CONFIRM_ERASE):
            return
        counts = disconnect.erase_local_data()
        self.erase_result.setText(
            f"Deleted {_count(counts['transactions'], 'transaction')} and "
            f"{_count(counts['accounts'], 'account')}, and forgot what you'd "
            "taught the app.")
        self.erase_result.show()
        self.app_widget.advance_stat()      # the widget must stop showing what's gone

    def show_data_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(paths.data_dir()))

    def _wait_for_worker(self):
        # A running QThread must not be destroyed; the request gives up in 5 s.
        for worker in (self._worker, self._disconnect_worker):
            if worker is not None:
                worker.wait(8000)

    def done(self, result):
        self._wait_for_worker()
        super().done(result)
