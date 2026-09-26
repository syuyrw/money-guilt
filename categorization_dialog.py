"""Manual transaction categorization dialog"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QComboBox, QCheckBox, QMessageBox,
                             QFrame, QProgressBar)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
import logging
from database import get_db
import telemetry
from categorizer import get_categorizer, CATEGORY_KEYWORDS

logger = logging.getLogger(__name__)

STYLE = """
QDialog { background-color: #1e1e20; }
QLabel { background: transparent; color: #f2f2f7; font-size: 13px; }
QLabel#title { font-size: 18px; font-weight: 600; }
QLabel#muted { color: rgba(255, 255, 255, 0.5); font-size: 12px; }
QLabel#caption { color: rgba(255, 255, 255, 0.45); font-size: 11px;
                 font-weight: 600; letter-spacing: 1px; }
QLabel#merchant { font-size: 20px; font-weight: 600; }
QLabel#amount { font-size: 15px; color: #ff9f7a; font-weight: 500; }
QFrame#card { background-color: #2a2a2d; border: 1px solid #3a3a3e;
              border-radius: 12px; }
QProgressBar { background-color: #333336; border: none; border-radius: 2px; }
QProgressBar::chunk { background-color: #0a84ff; border-radius: 2px; }
QComboBox { background-color: #2a2a2d; border: 1px solid #3a3a3e;
            border-radius: 8px; padding: 8px 12px; color: #f2f2f7; }
QComboBox QAbstractItemView { background-color: #2a2a2d; color: #f2f2f7;
            selection-background-color: #0a84ff; border: 1px solid #3a3a3e; }
QCheckBox { color: #f2f2f7; font-size: 13px; spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;
                       border: 1px solid #5a5a5e; background: #2a2a2d; }
QCheckBox::indicator:checked { background: #ff6b5a; border-color: #ff6b5a; }
QPushButton { background-color: #333336; color: #f2f2f7; border: none;
              border-radius: 8px; padding: 8px 16px; font-size: 13px; }
QPushButton:hover { background-color: #3f3f43; }
QPushButton:disabled { color: rgba(255, 255, 255, 0.3); background-color: #2a2a2d; }
QPushButton#primary { background-color: #0a84ff; color: white; font-weight: 600; }
QPushButton#primary:hover { background-color: #3395ff; }
QPushButton#primary:disabled { background-color: #24405f; color: rgba(255, 255, 255, 0.4); }
"""


class CategorizationDialog(QDialog):
    """Dialog for categorizing real transactions from the database"""

    def __init__(self, parent=None, limit=100):
        super().__init__(parent)
        self.limit = limit
        self.setWindowTitle("Categorize Transactions")
        self.setGeometry(100, 100, 700, 400)
        self.categorizer = get_categorizer()

        self.transactions = []
        self.current_index = 0
        self.categorized_count = 0

        self.init_ui()
        self.load_transactions()
        self.show_transaction()

    def init_ui(self):
        """Initialize UI"""
        self.setStyleSheet(STYLE)
        self.setFixedSize(460, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(0)

        title = QLabel("Help Train the Categorizer")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        self.progress_label = QLabel()
        self.progress_label.setObjectName("muted")
        self.progress_label.setAlignment(Qt.AlignCenter)
        root.addSpacing(2)
        root.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        root.addSpacing(10)
        root.addWidget(self.progress_bar)

        # Transaction card
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(4)

        self.merchant_label = QLabel()
        self.merchant_label.setObjectName("merchant")
        self.merchant_label.setWordWrap(True)
        card_layout.addWidget(self.merchant_label)

        self.amount_label = QLabel()
        self.amount_label.setObjectName("amount")
        card_layout.addWidget(self.amount_label)

        self.date_label = QLabel()
        self.date_label.setObjectName("muted")
        card_layout.addWidget(self.date_label)

        root.addSpacing(16)
        root.addWidget(card)

        # Controls
        self.category_caption = category_caption = QLabel("CATEGORY")
        category_caption.setObjectName("caption")
        root.addSpacing(18)
        root.addWidget(category_caption)
        root.addSpacing(6)

        self.category_combo = QComboBox()
        self.category_combo.addItems(sorted(CATEGORY_KEYWORDS.keys()))
        root.addWidget(self.category_combo)

        self.wasteful_checkbox = QCheckBox("Mark as wasteful spending")
        root.addSpacing(12)
        root.addWidget(self.wasteful_checkbox)

        root.addStretch()

        # Buttons
        buttons = QHBoxLayout()
        buttons.setSpacing(10)

        self.skip_button = QPushButton("Skip")
        self.skip_button.clicked.connect(self.skip_transaction)
        buttons.addWidget(self.skip_button)

        buttons.addStretch()

        done_button = QPushButton("Done")
        done_button.clicked.connect(self.close)
        buttons.addWidget(done_button)

        self.categorize_button = QPushButton("Categorize && Next")
        self.categorize_button.setObjectName("primary")
        self.categorize_button.setDefault(True)
        self.categorize_button.clicked.connect(self.categorize_transaction)
        buttons.addWidget(self.categorize_button)

        root.addLayout(buttons)

    def apply_learned_categories(self):
        """Apply what's been learned about a merchant to all its unreviewed
        transactions, so a merchant you've categorized never comes back."""
        overrides = self.categorizer.merchant_overrides
        wasteful = self.categorizer.merchant_wasteful
        try:
            with get_db() as conn:
                for merchant, category in overrides.items():
                    conn.execute("""
                        UPDATE transactions
                        SET category = ?, is_wasteful = ?, reviewed = 1, prompted = 1
                        WHERE LOWER(name) = ? AND reviewed = 0
                    """, (category, bool(wasteful.get(merchant, False)), merchant))
                conn.commit()
        except Exception:
            logger.exception("Could not apply learned merchant categories")

    def load_transactions(self):
        """Load transactions the user hasn't reviewed yet"""
        self.apply_learned_categories()
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, amount, date
                    FROM transactions
                    WHERE prompted = 0
                    ORDER BY date DESC
                    LIMIT ?
                """, (self.limit,))
                self.transactions = cursor.fetchall()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load transactions: {e}")
            self.close()

    def show_transaction(self):
        """Display current transaction"""
        if not self.transactions:
            self.show_all_done()
            return
        if self.current_index >= len(self.transactions):
            QMessageBox.information(
                self, "Complete",
                f"✓ You categorized {self.categorized_count} transactions!\n\n"
                "The system is now smarter and will categorize similar "
                "transactions automatically."
            )
            self.close()
            return

        trans = self.transactions[self.current_index]
        self.mark_prompted(trans[0])
        trans_id = trans[0]
        merchant = trans[1]
        amount = trans[2]
        date = trans[3]

        # Update progress
        self.progress_label.setText(
            f"Transaction {self.current_index + 1} of {len(self.transactions)} "
            f"· {self.categorized_count} categorized"
        )
        self.progress_bar.setRange(0, len(self.transactions))
        self.progress_bar.setValue(self.current_index)

        # Display transaction details
        self.merchant_label.setText(merchant)
        self.amount_label.setText(f"${amount:.2f}")
        self.date_label.setText(str(date))

        # Get suggestions
        suggestions = self.categorizer.get_category_suggestions(merchant, top_n=1)
        if suggestions:
            category, score = suggestions[0]
            self.category_combo.setCurrentText(category)

        # Check if wasteful
        is_wasteful = self.categorizer.is_wasteful(merchant)
        self.wasteful_checkbox.setChecked(is_wasteful)

        self.current_transaction = (trans_id, merchant, amount, date)

    def show_all_done(self):
        """Nothing new to ask about"""
        self.progress_label.setText("")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.merchant_label.setText("✓ All transactions have been categorized")
        for widget in (self.skip_button, self.categorize_button):
            widget.setEnabled(False)
        for widget in (self.amount_label, self.date_label, self.category_caption,
                       self.category_combo, self.wasteful_checkbox):
            widget.hide()

    def mark_prompted(self, trans_id):
        """Record that this transaction has been shown, so it isn't asked again"""
        try:
            with get_db() as conn:
                conn.execute("UPDATE transactions SET prompted = 1 WHERE id = ?",
                             (trans_id,))
                conn.commit()
        except Exception:
            logger.exception("Could not record that a transaction was shown")

    def categorize_transaction(self):
        """Categorize current transaction and move to next"""
        if not hasattr(self, 'current_transaction'):
            return

        trans_id, merchant, amount, date = self.current_transaction
        category = self.category_combo.currentText()
        is_wasteful = self.wasteful_checkbox.isChecked()

        # Save to database
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE transactions
                    SET category = ?, is_wasteful = ?, reviewed = 1
                    WHERE id = ?
                """, (category, is_wasteful, trans_id))
                conn.commit()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save: {e}")
            return

        # Learn this categorization
        self.categorizer.learn_merchant_category(merchant, category, is_wasteful)

        self.categorized_count += 1
        self.apply_learned_categories()
        telemetry.report_in_background()
        self.drop_learned_from_queue()

        # Move to next
        self.current_index += 1
        self.show_transaction()

    def drop_learned_from_queue(self):
        """Remove queued transactions from merchants that were just categorized"""
        learned = self.categorizer.merchant_overrides
        upcoming = [t for t in self.transactions[self.current_index + 1:]
                    if t[1].lower() not in learned]
        self.transactions = self.transactions[:self.current_index + 1] + upcoming

    def skip_transaction(self):
        """Skip current transaction"""
        self.current_index += 1
        self.show_transaction()
