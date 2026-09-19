"""Manual transaction categorization dialog"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QComboBox, QCheckBox, QMessageBox)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from database import get_db
from categorizer import get_categorizer, CATEGORY_KEYWORDS


class CategorizationDialog(QDialog):
    """Dialog for categorizing real transactions from the database"""

    def __init__(self, parent=None):
        super().__init__(parent)
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
        layout = QVBoxLayout()

        # Title
        title = QLabel("Help Train the Categorizer")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Progress
        self.progress_label = QLabel()
        layout.addWidget(self.progress_label)

        # Transaction details
        details_layout = QVBoxLayout()
        details_layout.addWidget(QLabel("Transaction Details:"))

        # Merchant
        merchant_layout = QHBoxLayout()
        merchant_layout.addWidget(QLabel("Merchant:"))
        self.merchant_label = QLabel()
        merchant_font = QFont()
        merchant_font.setBold(True)
        merchant_font.setPointSize(12)
        self.merchant_label.setFont(merchant_font)
        merchant_layout.addWidget(self.merchant_label)
        merchant_layout.addStretch()
        details_layout.addLayout(merchant_layout)

        # Amount and date
        amount_date_layout = QHBoxLayout()
        amount_date_layout.addWidget(QLabel("Amount:"))
        self.amount_label = QLabel()
        amount_date_layout.addWidget(self.amount_label)
        amount_date_layout.addWidget(QLabel("  Date:"))
        self.date_label = QLabel()
        amount_date_layout.addWidget(self.date_label)
        amount_date_layout.addStretch()
        details_layout.addLayout(amount_date_layout)

        layout.addLayout(details_layout)

        # Categorization controls
        controls_layout = QVBoxLayout()
        controls_layout.addWidget(QLabel("Select Category:"))

        category_layout = QHBoxLayout()
        self.category_combo = QComboBox()
        self.category_combo.addItems(sorted(CATEGORY_KEYWORDS.keys()))
        category_layout.addWidget(self.category_combo)
        category_layout.addStretch()
        controls_layout.addLayout(category_layout)

        # Wasteful checkbox
        self.wasteful_checkbox = QCheckBox("Mark as wasteful spending")
        controls_layout.addWidget(self.wasteful_checkbox)

        layout.addLayout(controls_layout)

        # Action buttons
        button_layout = QHBoxLayout()

        skip_button = QPushButton("Skip")
        skip_button.clicked.connect(self.skip_transaction)
        button_layout.addWidget(skip_button)

        button_layout.addStretch()

        categorize_button = QPushButton("Categorize & Next")
        categorize_button.setMinimumWidth(150)
        categorize_button.clicked.connect(self.categorize_transaction)
        button_layout.addWidget(categorize_button)

        done_button = QPushButton("Done")
        done_button.clicked.connect(self.close)
        button_layout.addWidget(done_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def load_transactions(self):
        """Load all transactions from database"""
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, amount, date
                    FROM transactions
                    ORDER BY date DESC
                    LIMIT 100
                """)
                self.transactions = cursor.fetchall()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load transactions: {e}")
            self.close()

    def show_transaction(self):
        """Display current transaction"""
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
        trans_id = trans[0]
        merchant = trans[1]
        amount = trans[2]
        date = trans[3]

        # Update progress
        self.progress_label.setText(
            f"Transaction {self.current_index + 1} of {len(self.transactions)} "
            f"({self.categorized_count} categorized)"
        )

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
                    SET category = ?, is_wasteful = ?
                    WHERE id = ?
                """, (category, is_wasteful, trans_id))
                conn.commit()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save: {e}")
            return

        # Learn this categorization
        self.categorizer.learn_merchant_category(merchant, category, is_wasteful)

        self.categorized_count += 1

        # Move to next
        self.current_index += 1
        self.show_transaction()

    def skip_transaction(self):
        """Skip current transaction"""
        self.current_index += 1
        self.show_transaction()
