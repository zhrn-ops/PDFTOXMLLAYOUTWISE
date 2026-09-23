"""Properties inspector panel."""

from __future__ import annotations

from PySide6.QtCore import Qt
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
    QSizePolicy,
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
            for name in ["Text", "Font", "Font Size", "Coordinates", "Page", "Confidence", "Detected Role", "Locked"]
        }
        for name, widget in self.fields.items():
            widget.setWordWrap(True)
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            if name == "Text":
                widget.setMaximumHeight(52)
            form.addRow(name, widget)
        self.role_editor = QComboBox()
        self.role_editor.addItems(ROLE_OPTIONS)
        self.apply_role_btn = QPushButton("Apply Role")
        self.apply_role_btn.setEnabled(False)
        form.addRow("Assign Role", self.role_editor)
        form.addRow("", self.apply_role_btn)
        layout.addWidget(box)
        self.setMinimumWidth(0)
        self.setMaximumWidth(480)

        self.merge_box = QGroupBox("Merged Details")
        self.merge_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.merge_box.setMaximumHeight(190)
        merge_layout = QVBoxLayout(self.merge_box)
        self.merge_table = QTableWidget(0, 2)
        self.merge_table.setHorizontalHeaderLabels(["Field", "Value"])
        self.merge_table.verticalHeader().setVisible(False)
        self.merge_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.merge_table.setSelectionMode(QTableWidget.NoSelection)
        self.merge_table.setAlternatingRowColors(True)
        self.merge_table.setColumnWidth(0, 120)
        self.merge_table.setWordWrap(False)
        self.merge_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.merge_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.merge_table.setMaximumHeight(145)
        merge_layout.addWidget(self.merge_table, 0)
        layout.addWidget(self.merge_box)
        self.merge_box.setVisible(False)
        layout.addStretch(1)

    def set_role(self, role: str | None, locked: bool = False) -> None:
        """Update the role editor to match the selected block."""

        self._set_locked_state(locked)
        if not role:
            self.role_editor.setCurrentIndex(0)
            self.role_editor.setEnabled(True and not locked)
            self.apply_role_btn.setEnabled(False)
            return
        index = self.role_editor.findText(role)
        self.role_editor.setCurrentIndex(index if index >= 0 else 0)
        self.role_editor.setEnabled(not locked)
        self.apply_role_btn.setEnabled(not locked)

    def set_locked_state(self, locked: bool) -> None:
        """Reflect the lock state of the current selection."""

        self._set_locked_state(locked)
        self.apply_role_btn.setEnabled(not locked and self.apply_role_btn.isEnabled())

    def _set_locked_state(self, locked: bool) -> None:
        self.fields["Locked"].setText("🔒 yes" if locked else "no")
        self.role_editor.setEnabled(not locked)
        if locked:
            self.apply_role_btn.setEnabled(False)

    def set_merge_details(self, details: dict[str, str] | None) -> None:
        """Populate the merged-details table."""

        if not details:
            self.merge_table.clearContents()
            self.merge_table.setRowCount(0)
            self.merge_box.setVisible(False)
            return
        self.merge_box.setVisible(True)
        self.merge_table.setRowCount(len(details))
        for row, (key, value) in enumerate(details.items()):
            self.merge_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.merge_table.setItem(row, 1, QTableWidgetItem(str(value)))
