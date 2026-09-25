"""Document structure tree."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
)


ROLE_OPTIONS = [
    "title",
    "author",
    "corresponding_author",
    "affiliation",
    "abstract",
]


class RoleDelegate(QStyledItemDelegate):
    """Editor delegate that constrains block roles to known values."""

    def createEditor(self, parent, option, index):  # type: ignore[override]
        editor = QComboBox(parent)
        editor.addItems(ROLE_OPTIONS)
        editor.setEditable(False)
        return editor

    def setEditorData(self, editor, index):  # type: ignore[override]
        value = str(index.data(Qt.EditRole) or index.data(Qt.DisplayRole) or "")
        pos = editor.findText(value)
        editor.setCurrentIndex(pos if pos >= 0 else 0)

    def setModelData(self, editor, model, index):  # type: ignore[override]
        model.setData(index, editor.currentText(), Qt.EditRole)


class StructureTree(QTreeWidget):
    """Tree of detected document elements."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHeaderLabels(["Document Structure", "Role"])
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Stretch both columns within the pane instead of fixed widths so the
        # editable Role column stays reachable in narrow splitters.
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        self.setItemDelegateForColumn(1, RoleDelegate(self))
        self.categories: dict[str, QTreeWidgetItem] = {}
        for label in ["Title", "Authors", "Corresponding Author", "Affiliations", "Abstract"]:
            item = QTreeWidgetItem([label, ""])
            item.setFlags(item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            self.addTopLevelItem(item)
            self.categories[label] = item

    def clear_dynamic_items(self) -> None:
        """Remove previously populated items under dynamic branches."""

        for child in self.categories.values():
            while child.childCount():
                child.removeChild(child.child(0))

    def category_item(self, label: str) -> QTreeWidgetItem | None:
        """Return the category node for a label."""

        return self.categories.get(label)
