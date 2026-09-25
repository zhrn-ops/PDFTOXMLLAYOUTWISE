"""Panels for block properties and article metadata."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from pdf_to_jats.gui.structure_tree import ROLE_OPTIONS


# Severity order used when a link carries several issues: the worst one wins.
_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}
_REVIEW_COLORS = {"error": "#ff6b6b", "warning": "#d9a441", "info": "#9a9a9a"}


def _worst_severity(existing: str | None, candidate: str) -> str:
    """Keep the more serious of two severities."""

    if existing is None:
        return candidate
    if _SEVERITY_RANK.get(candidate, 0) > _SEVERITY_RANK.get(existing, 0):
        return candidate
    return existing


class PropertiesPanel(QWidget):
    """Display metadata for the selected block."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        box = QGroupBox("Properties")
        form = QFormLayout(box)
        self.fields = {
            name: QLabel("-")
            for name in [
                "Text",
                "Font",
                "Font Size",
                "Coordinates",
                "Page",
                "Confidence",
                "Detected Role",
                "Locked",
            ]
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
        layout.addStretch(1)
        self.setMinimumWidth(0)

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


class AuthorAffiliationReviewPanel(QWidget):
    """Dedicated review surface for extracted authors and affiliations."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        review_box = QGroupBox("Author / Affiliation Review")
        review_layout = QVBoxLayout(review_box)
        self.review_hint = QLabel("Load a PDF to review author/affiliation links.")
        self.review_hint.setWordWrap(True)
        self.review_tree = QTreeWidget()
        self.review_tree.setHeaderHidden(True)
        self.review_tree.setSelectionMode(QTreeWidget.NoSelection)
        self.review_tree.setAlternatingRowColors(True)
        self.review_tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.review_tree.setUniformRowHeights(True)
        self.review_tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        review_layout.addWidget(self.review_hint)
        review_layout.addWidget(self.review_tree, 1)
        layout.addWidget(review_box, 1)
        self.setMinimumWidth(0)

    def set_link_review(
        self,
        authors: list | None,
        affiliations: list | None,
        issues: list[dict] | None,
        enabled: bool = True,
    ) -> None:
        """Populate the author-to-affiliation links and matching warnings."""

        self.review_tree.clear()
        if not enabled:
            self.review_hint.setText("Load a PDF to review author/affiliation links.")
            return
        authors, affiliations, issues = list(authors or []), list(affiliations or []), list(issues or [])
        severity_by_affiliation: dict[str, str] = {}
        severity_by_author: dict[str, str] = {}
        for issue in issues:
            severity = str(issue.get("severity", "info") or "info")
            author_id = str(issue.get("author_id", "") or "")
            affiliation_id = str(issue.get("affiliation_id", "") or "")
            if affiliation_id:
                severity_by_affiliation[affiliation_id] = _worst_severity(severity_by_affiliation.get(affiliation_id), severity)
            if author_id:
                severity_by_author[author_id] = _worst_severity(severity_by_author.get(author_id), severity)

        authors_by_affiliation: dict[str, list] = {}
        for author in authors:
            for affiliation_id in getattr(author, "affiliation_ids", []) or []:
                authors_by_affiliation.setdefault(affiliation_id, []).append(author)
        for affiliation in affiliations:
            linked = authors_by_affiliation.get(affiliation.id, [])
            marker = str(getattr(affiliation, "marker", "") or "").strip()
            affiliation_text = " ".join(str(getattr(affiliation, "text", "") or "").split())
            prefix = f"[{marker}] " if marker else ""
            snippet = affiliation_text if len(affiliation_text) <= 60 else affiliation_text[:57] + "..."
            row = QTreeWidgetItem([f"{prefix}{snippet or '(affiliation)'} — {len(linked)} author(s)"])
            row.setToolTip(0, f"{prefix}{affiliation_text}\nLinked authors: {len(linked)}")
            self._tint(row, severity_by_affiliation.get(affiliation.id))
            for author in linked:
                child = QTreeWidgetItem([self._author_label(author)])
                child.setToolTip(0, self._author_label(author))
                self._tint(child, severity_by_author.get(author.id))
                row.addChild(child)
            self.review_tree.addTopLevelItem(row)

        unlinked = [author for author in authors if not (getattr(author, "affiliation_ids", []) or [])]
        if unlinked:
            group = QTreeWidgetItem([f"Unlinked authors ({len(unlinked)})"])
            self._tint(group, "warning")
            for author in unlinked:
                child = QTreeWidgetItem([self._author_label(author)])
                self._tint(child, severity_by_author.get(author.id, "warning"))
                group.addChild(child)
            self.review_tree.addTopLevelItem(group)
        if issues:
            errors = sum(issue.get("severity") == "error" for issue in issues)
            group = QTreeWidgetItem([f"Flagged for review ({errors} error(s), {len(issues) - errors} warning(s))"])
            for issue in issues:
                severity = str(issue.get("severity", "info") or "info")
                message = " ".join(str(issue.get("message", "") or "").split())
                child = QTreeWidgetItem([f"[{severity}] {message}"])
                child.setToolTip(0, message)
                self._tint(child, severity)
                group.addChild(child)
            self.review_tree.addTopLevelItem(group)
        self.review_tree.expandAll()
        if not authors and not affiliations:
            self.review_hint.setText("No author or affiliation blocks are classified yet; nothing to review.")
        elif issues:
            self.review_hint.setText(f"{len(authors)} author(s), {len(affiliations)} affiliation(s), {len(issues)} link(s) need a decision before export.")
        else:
            self.review_hint.setText(f"{len(authors)} author(s), {len(affiliations)} affiliation(s), all links resolved.")

    @staticmethod
    def _author_label(author) -> str:
        name = str(getattr(author, "display_name", "") or "").strip() or "(unnamed author)"
        markers = [str(marker) for marker in (getattr(author, "markers", []) or []) if str(marker).strip()]
        return f"{name} [{', '.join(markers)}]" if markers else name

    @staticmethod
    def _tint(item: QTreeWidgetItem, severity: str | None) -> None:
        color = _REVIEW_COLORS.get(severity or "")
        if color:
            item.setForeground(0, QBrush(QColor(color)))


class MetadataPanel(QWidget):
    """Article metadata and merged-block details."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.article_box = QGroupBox("Article Metadata")
        article_form = QFormLayout(self.article_box)
        self.abstract_number_editor = QLineEdit()
        self.abstract_number_editor.setPlaceholderText("e.g. 4349")
        self.abstract_number_editor.setClearButtonEnabled(True)
        self.abstract_number_editor.textChanged.connect(self._on_abstract_number_text_changed)
        self.apply_abstract_number_btn = QPushButton("Apply Abstract No")
        self.apply_abstract_number_btn.setEnabled(False)
        article_form.addRow("Abstract No", self.abstract_number_editor)
        article_form.addRow("", self.apply_abstract_number_btn)
        layout.addWidget(self.article_box)
        self._abstract_number_value = ""
        self._abstract_number_editable = False

        self.review_box = QGroupBox("Author / Affiliation Review")
        review_layout = QVBoxLayout(self.review_box)
        self.review_hint = QLabel("Load a PDF to review author/affiliation links.")
        self.review_hint.setWordWrap(True)
        self.review_tree = QTreeWidget()
        self.review_tree.setHeaderHidden(True)
        self.review_tree.setSelectionMode(QTreeWidget.NoSelection)
        self.review_tree.setAlternatingRowColors(True)
        self.review_tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.review_tree.setUniformRowHeights(True)
        self.review_tree.setMinimumHeight(140)
        self.review_tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        review_layout.addWidget(self.review_hint)
        review_layout.addWidget(self.review_tree, 1)
        # Link review now has its own work-area tab.  Keep this legacy widget
        # detached so callers of the older panel API remain safe without
        # duplicating the review in Article Metadata.
        self.review_box.setVisible(False)

        self.merge_box = QGroupBox("Merged Details")
        self.merge_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
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
        layout.addStretch(0)

        # Keep every section reachable on short windows by scrolling the pane
        # instead of clipping the lower group boxes.
        content = QWidget()
        content.setLayout(layout)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.setMinimumWidth(0)

    def set_abstract_number(self, abstract_number: str, enabled: bool = True) -> None:
        """Show the article's abstract number so it can be reviewed and edited."""

        value = str(abstract_number or "").strip()
        if value.upper() == "ABSN":
            value = ""
        self._abstract_number_value = value
        self._abstract_number_editable = enabled
        self.abstract_number_editor.blockSignals(True)
        self.abstract_number_editor.setText(value)
        self.abstract_number_editor.blockSignals(False)
        self.abstract_number_editor.setEnabled(enabled)
        self.apply_abstract_number_btn.setEnabled(False)

    def _on_abstract_number_text_changed(self, text: str) -> None:
        """Enable applying only when the reviewed value actually changed."""

        self.apply_abstract_number_btn.setEnabled(
            self._abstract_number_editable and text.strip() != self._abstract_number_value
        )

    def set_link_review(
        self,
        authors: list | None,
        affiliations: list | None,
        issues: list[dict] | None,
        enabled: bool = True,
    ) -> None:
        """Show the author/affiliation links and flag the ones that need review.

        The tree mirrors the export structure: one top-level row per affiliation
        with its authors nested underneath, then the authors that could not be
        linked, then every issue raised while matching them.
        """

        self.review_tree.clear()
        if not enabled:
            self.review_box.setVisible(False)
            self.review_hint.setText("Load a PDF to review author/affiliation links.")
            return
        self.review_box.setVisible(True)
        authors = list(authors or [])
        affiliations = list(affiliations or [])
        issues = list(issues or [])

        severity_by_affiliation: dict[str, str] = {}
        severity_by_author: dict[str, str] = {}
        for issue in issues:
            severity = str(issue.get("severity", "info") or "info")
            author_id = str(issue.get("author_id", "") or "")
            affiliation_id = str(issue.get("affiliation_id", "") or "")
            if affiliation_id:
                severity_by_affiliation[affiliation_id] = _worst_severity(
                    severity_by_affiliation.get(affiliation_id), severity
                )
            if author_id:
                severity_by_author[author_id] = _worst_severity(
                    severity_by_author.get(author_id), severity
                )

        authors_by_affiliation: dict[str, list] = {}
        for author in authors:
            for affiliation_id in getattr(author, "affiliation_ids", []) or []:
                authors_by_affiliation.setdefault(affiliation_id, []).append(author)

        for affiliation in affiliations:
            linked = authors_by_affiliation.get(affiliation.id, [])
            marker = str(getattr(affiliation, "marker", "") or "").strip()
            text = " ".join(str(getattr(affiliation, "text", "") or "").split())
            prefix = f"[{marker}] " if marker else ""
            snippet = text if len(text) <= 60 else text[:57] + "..."
            row = QTreeWidgetItem([f"{prefix}{snippet or '(affiliation)'} — {len(linked)} author(s)"])
            row.setToolTip(0, f"{prefix}{text}\nLinked authors: {len(linked)}")
            self._tint(row, severity_by_affiliation.get(affiliation.id))
            for author in linked:
                child = QTreeWidgetItem([self._author_label(author)])
                child.setToolTip(0, self._author_label(author))
                self._tint(child, severity_by_author.get(author.id))
                row.addChild(child)
            self.review_tree.addTopLevelItem(row)

        unlinked_authors = [
            author
            for author in authors
            if not (getattr(author, "affiliation_ids", []) or [])
        ]
        if unlinked_authors:
            group = QTreeWidgetItem([f"Unlinked authors ({len(unlinked_authors)})"])
            group.setForeground(0, QBrush(QColor(_REVIEW_COLORS["warning"])))
            for author in unlinked_authors:
                child = QTreeWidgetItem([self._author_label(author)])
                child.setToolTip(0, self._author_label(author))
                self._tint(child, severity_by_author.get(author.id, "warning"))
                group.addChild(child)
            self.review_tree.addTopLevelItem(group)

        if issues:
            errors = sum(1 for issue in issues if issue.get("severity") == "error")
            group = QTreeWidgetItem(
                [f"Flagged for review ({errors} error(s), {len(issues) - errors} warning(s))"]
            )
            for issue in issues:
                severity = str(issue.get("severity", "info") or "info")
                message = " ".join(str(issue.get("message", "") or "").split())
                child = QTreeWidgetItem([f"[{severity}] {message}"])
                child.setToolTip(0, message)
                self._tint(child, severity)
                group.addChild(child)
            self.review_tree.addTopLevelItem(group)

        self.review_tree.expandAll()
        if not authors and not affiliations:
            self.review_hint.setText(
                "No author or affiliation blocks are classified yet; nothing to review."
            )
        elif issues:
            self.review_hint.setText(
                f"{len(authors)} author(s), {len(affiliations)} affiliation(s), "
                f"{len(issues)} link(s) need a decision before export."
            )
        else:
            self.review_hint.setText(
                f"{len(authors)} author(s), {len(affiliations)} affiliation(s), all links resolved."
            )

    @staticmethod
    def _author_label(author) -> str:
        """Render one author row with the markers that drove the link."""

        name = str(getattr(author, "display_name", "") or "").strip() or "(unnamed author)"
        markers = [str(marker) for marker in (getattr(author, "markers", []) or []) if str(marker).strip()]
        return f"{name} [{', '.join(markers)}]" if markers else name

    @staticmethod
    def _tint(item: QTreeWidgetItem, severity: str | None) -> None:
        """Colour a row by the worst issue severity attached to it."""

        color = _REVIEW_COLORS.get(severity or "")
        if color:
            item.setForeground(0, QBrush(QColor(color)))

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
