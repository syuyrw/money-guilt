"""Manual transaction categorization dialog"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QComboBox, QCheckBox, QListWidget,
                             QListWidgetItem, QMessageBox)
from PyQt5.QtCore import Qt, pyqtSignal
from categorizer import get_categorizer, CATEGORY_KEYWORDS


class CategorizationDialog(QDialog):
    """Dialog for manually categorizing transactions"""

    category_learned = pyqtSignal(str, str, bool)  # merchant, category, is_wasteful

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Categorize Transactions")
        self.setGeometry(100, 100, 600, 500)
        self.categorizer = get_categorizer()
        self.current_merchant = None
        self.init_ui()

    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()

        # Instructions
        instructions = QLabel("Manually categorize merchants to teach the system:")
        layout.addWidget(instructions)

        # Input area
        input_layout = QHBoxLayout()

        # Merchant name input
        merchant_label = QLabel("Merchant:")
        self.merchant_input = QComboBox()
        self.merchant_input.setEditable(True)
        self.merchant_input.setMinimumWidth(250)
        self.merchant_input.currentTextChanged.connect(self.on_merchant_changed)
        input_layout.addWidget(merchant_label)
        input_layout.addWidget(self.merchant_input)

        layout.addLayout(input_layout)

        # Category selector
        category_layout = QHBoxLayout()
        category_label = QLabel("Category:")
        self.category_combo = QComboBox()
        self.category_combo.addItems(sorted(CATEGORY_KEYWORDS.keys()))
        category_layout.addWidget(category_label)
        category_layout.addWidget(self.category_combo)
        category_layout.addStretch()

        layout.addLayout(category_layout)

        # Wasteful checkbox
        self.wasteful_checkbox = QCheckBox("Mark as wasteful spending")
        layout.addWidget(self.wasteful_checkbox)

        # Save button
        save_layout = QHBoxLayout()
        save_layout.addStretch()
        save_button = QPushButton("Learn This Merchant")
        save_button.clicked.connect(self.save_categorization)
        save_layout.addWidget(save_button)
        layout.addLayout(save_layout)

        # Recent merchants list
        recent_label = QLabel("Recently Learned:")
        layout.addWidget(recent_label)

        self.recent_list = QListWidget()
        self.recent_list.itemClicked.connect(self.on_recent_clicked)
        layout.addWidget(self.recent_list)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

        # Load recent categorizations
        self.refresh_recent()

    def on_merchant_changed(self, merchant_name: str):
        """Update UI when merchant changes"""
        if not merchant_name:
            return

        self.current_merchant = merchant_name

        # Suggest category
        suggestions = self.categorizer.get_category_suggestions(merchant_name, top_n=1)
        if suggestions:
            category, score = suggestions[0]
            self.category_combo.setCurrentText(category)

        # Check if wasteful
        is_wasteful = self.categorizer.is_wasteful(merchant_name)
        self.wasteful_checkbox.setChecked(is_wasteful)

    def save_categorization(self):
        """Save the manual categorization"""
        merchant = self.merchant_input.currentText().strip()
        if not merchant:
            QMessageBox.warning(self, "Error", "Please enter a merchant name")
            return

        category = self.category_combo.currentText()
        is_wasteful = self.wasteful_checkbox.isChecked()

        # Learn this categorization
        self.categorizer.learn_merchant_category(merchant, category, is_wasteful)

        # Emit signal
        self.category_learned.emit(merchant, category, is_wasteful)

        # Show feedback
        QMessageBox.information(
            self, "Learned",
            f"✓ Learned: {merchant} → {category}\n"
            f"Wasteful: {'Yes' if is_wasteful else 'No'}"
        )

        # Clear and refresh
        self.merchant_input.setCurrentText("")
        self.refresh_recent()

    def refresh_recent(self):
        """Refresh list of recently learned merchants"""
        self.recent_list.clear()

        overrides = self.categorizer.merchant_overrides
        # Show last 20 learned merchants
        for merchant, category in list(overrides.items())[-20:]:
            is_wasteful = self.categorizer.merchant_wasteful.get(merchant, False)
            text = f"{merchant.title()} → {category}"
            if is_wasteful:
                text += " (wasteful)"

            item = QListWidgetItem(text)
            self.recent_list.addItem(item)

    def on_recent_clicked(self, item: QListWidgetItem):
        """Load recent merchant when clicked"""
        text = item.text()
        merchant = text.split(" → ")[0]
        self.merchant_input.setCurrentText(merchant)
