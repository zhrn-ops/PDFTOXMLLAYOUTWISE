"""Properties inspector panel."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pdf_to_jats.gui.structure_tree import ROLE_OPTIONS


class PropertiesPanel(QWidget):
    """Display metadata for the selected block."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        box = QGroupBox("Properties")
        form = QFormLayout(box)
        self.fields = {
            name: QLabel("-")
            for name in ["Text", "Font", "Font Size", "Coordinates", "Page", "Confidence", "Detected Role"]
        }
        for name, widget in self.fields.items():
            form.addRow(name, widget)
        self.role_editor = QComboBox()
        self.role_editor.addItems(ROLE_OPTIONS)
        self.apply_role_btn = QPushButton("Apply Role")
        self.apply_role_btn.setEnabled(False)
        form.addRow("Assign Role", self.role_editor)
        form.addRow("", self.apply_role_btn)
        layout.addWidget(box)

        merge_box = QGroupBox("Merged Details")
        merge_layout = QVBoxLayout(merge_box)
        self.merge_table = QTableWidget(0, 2)
        self.merge_table.setHorizontalHeaderLabels(["Field", "Value"])
        self.merge_table.verticalHeader().setVisible(False)
        self.merge_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.merge_table.setSelectionMode(QTableWidget.NoSelection)
        self.merge_table.setAlternatingRowColors(True)
        self.merge_table.setColumnWidth(0, 120)
        merge_layout.addWidget(self.merge_table)
        layout.addWidget(merge_box)

    def set_role(self, role: str | None) -> None:
        """Update the role editor to match the selected block."""

        if not role:
            self.role_editor.setCurrentIndex(0)
            self.role_editor.setEnabled(True)
            self.apply_role_btn.setEnabled(False)
            return
        index = self.role_editor.findText(role)
        self.role_editor.setCurrentIndex(index if index >= 0 else 0)
        self.role_editor.setEnabled(True)
        self.apply_role_btn.setEnabled(True)

    def set_merge_details(self, details: dict[str, str] | None) -> None:
        """Populate the merged-details table."""

        if not details:
            self.merge_table.setRowCount(1)
            self.merge_table.setItem(0, 0, QTableWidgetItem("Status"))
            self.merge_table.setItem(0, 1, QTableWidgetItem("No merged block selected"))
            return
        self.merge_table.setRowCount(len(details))
        for row, (key, value) in enumerate(details.items()):
            self.merge_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.merge_table.setItem(row, 1, QTableWidgetItem(str(value)))
