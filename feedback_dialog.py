"""The Send Feedback window"""
import threading

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QTextEdit, QVBoxLayout)

import feedback
from categorization_dialog import STYLE

EXTRA_STYLE = """
QTextEdit, QLineEdit { background-color: #2a2a2d; border: 1px solid #3a3a3e;
    border-radius: 8px; padding: 8px 10px; color: #f2f2f7; font-size: 13px; }
QLabel#error { color: #ff6b5a; font-size: 12px; }
"""


class FeedbackDialog(QDialog):
    sent = pyqtSignal(bool, str)

    def __init__(self, parent=None, send=feedback.send):
        super().__init__(parent)
        self._send = send
        self.setWindowTitle("Send Feedback")
        self.setStyleSheet(STYLE + EXTRA_STYLE)
        self.setFixedSize(460, 420)
        self.sent.connect(self._on_sent)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)

        title = QLabel("Send Feedback")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)
        root.addSpacing(14)

        self.message = QTextEdit()
        self.message.setPlaceholderText("What's on your mind? A bug, an idea, anything.")
        self.message.setAcceptRichText(False)
        root.addWidget(self.message)

        caption = QLabel("YOUR EMAIL (OPTIONAL, IF YOU WANT A REPLY)")
        caption.setObjectName("caption")
        root.addSpacing(12)
        root.addWidget(caption)
        root.addSpacing(6)
        self.reply_to = QLineEdit()
        self.reply_to.setPlaceholderText("you@example.com")
        root.addWidget(self.reply_to)

        note = QLabel("Only what you type here is sent. No spending data is included.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addSpacing(8)
        root.addWidget(note)

        self.status = QLabel("")
        self.status.setObjectName("error")
        self.status.setWordWrap(True)
        root.addSpacing(4)
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch()
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self.submit)
        buttons.addWidget(self.send_button)
        root.addLayout(buttons)

    def submit(self):
        message = self.message.toPlainText()
        reply_to = self.reply_to.text().strip()
        error = feedback.validate(message, reply_to)
        if error:
            self.status.setText(error)
            return
        self.status.setText("")
        self.send_button.setEnabled(False)
        self.send_button.setText("Sending…")

        def work():
            ok, err = self._send(message, reply_to)
            self.sent.emit(ok, err or "")

        threading.Thread(target=work, daemon=True).start()

    def _on_sent(self, ok, error):
        if ok:
            self.accept()
            return
        self.send_button.setEnabled(True)
        self.send_button.setText("Send")
        self.status.setText(error)
