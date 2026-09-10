"""Main application window."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import re
from html import escape
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pdf_to_jats.core.author_linker import AuthorLinker
from pdf_to_jats.core.jats_generator import JATSGenerator
from pdf_to_jats.core.pdf_extractor import PDFExtractor
from pdf_to_jats.core.paragraph_reconstructor import ParagraphReconstructor
from pdf_to_jats.core.semantic_classifier import SemanticClassifier
from pdf_to_jats.core.validator import Validator
from pdf_to_jats.llm.openrouter_client import OpenRouterClient, OpenRouterRateLimitError
from pdf_to_jats.llm.prompts import QUESTION_ANSWER_PROMPT, ROLE_ASSIGNMENT_PROMPT
from pdf_to_jats.gui.pdf_viewer import PDFViewer
from pdf_to_jats.gui.properties_panel import PropertiesPanel
from pdf_to_jats.gui.structure_tree import StructureTree
from pdf_to_jats.gui.styles import APP_STYLE
from pdf_to_jats.models.document import Document, DocumentZone
from pdf_to_jats.models.paragraph import Paragraph
from pdf_to_jats.utils.config import load_config, save_openrouter_settings


class MainWindow(QMainWindow):
    """Main window with extraction and export controls."""

    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
        self.extractor = PDFExtractor(
            visible_text_only=self.config.visible_text_only,
            dark_threshold=self.config.visible_text_dark_threshold,
            # Docling is optional enrichment and can load heavyweight OCR
            # models during PDF open. PyMuPDF remains the layout source.
            use_docling=False,
        )
        self.classifier = SemanticClassifier()
        self.paragraph_reconstructor = ParagraphReconstructor()
        self.openrouter_client = self._create_llm_client()
        self.linker = AuthorLinker()
        self.generator = JATSGenerator()
        self.validator = Validator()
        self.document = None
        self.current_xml_path: Path | None = None
        self._last_manual_merge: dict[str, object] | None = None
        self._redo_manual_merge: dict[str, object] | None = None
        self._html_preview_merges: list[tuple[str, ...]] = []
        self._selected_block_ids: set[str] = set()
        self._selected_zone_id: str | None = None
        self._selected_zone_ids: set[str] = set()
        self._undo_stack: list[dict[str, object]] = []
        self._active_zone_move_undo_id: str | None = None
        self.setWindowTitle(self.config.app_name)
        self.setMinimumSize(720, 520)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(1400, max(900, available.width() - 80))
            height = min(900, max(700, available.height() - 80))
            self.resize(width, height)
        else:
            self.resize(1400, 900)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        actions = QGridLayout()
        load_btn = QPushButton("Browse PDF")
        load_btn.clicked.connect(self.load_pdf)
        merge_btn = QPushButton("Merge Selected")
        merge_btn.clicked.connect(self.merge_selected_blocks)
        continue_btn = QPushButton("Continue Paragraph")
        continue_btn.clicked.connect(self.continue_selected_paragraphs)
        classify_btn = QPushButton("Classify Selected")
        classify_btn.clicked.connect(self.classify_selected_blocks)
        unmerge_btn = QPushButton("Undo Merge")
        unmerge_btn.clicked.connect(self.undo_last_merge)
        redo_btn = QPushButton("Redo Merge")
        redo_btn.clicked.connect(self.redo_last_merge)
        xml_btn = QPushButton("Generate XML...")
        xml_btn.clicked.connect(self.generate_xml)
        json_btn = QPushButton("Export JSON...")
        json_btn.clicked.connect(self.export_json)
        action_buttons = [load_btn, merge_btn, continue_btn, classify_btn, unmerge_btn, redo_btn, xml_btn, json_btn]
        for index, button in enumerate(action_buttons):
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            actions.addWidget(button, index // 4, index % 4)
        for column in range(4):
            actions.setColumnStretch(column, 1)
        root.addLayout(actions)

        self.openrouter_box = QGroupBox("OpenRouter")
        self.openrouter_box.setCheckable(True)
        self.openrouter_box.setChecked(False)
        openrouter_box_layout = QVBoxLayout(self.openrouter_box)
        self.openrouter_settings_panel = QWidget()
        openrouter_layout = QFormLayout(self.openrouter_settings_panel)
        self.openrouter_api_key_edit = QLineEdit(self.config.openrouter_api_key)
        self.openrouter_api_key_edit.setEchoMode(QLineEdit.Password)
        self.openrouter_api_key_edit.setPlaceholderText("sk-or-v1-...")
        api_key_container = QWidget()
        api_key_layout = QHBoxLayout(api_key_container)
        api_key_layout.setContentsMargins(0, 0, 0, 0)
        api_key_layout.addWidget(self.openrouter_api_key_edit, 1)
        self.show_api_key_btn = QToolButton()
        self.show_api_key_btn.setText("Show")
        self.show_api_key_btn.setCheckable(True)
        self.show_api_key_btn.setToolTip("Show or hide the API key")
        self.show_api_key_btn.toggled.connect(self._toggle_api_key_visibility)
        api_key_layout.addWidget(self.show_api_key_btn)
        self.openrouter_model_edit = QLineEdit(self.config.openrouter_model)
        self.openrouter_model_edit.setPlaceholderText("openrouter/free")
        self.openrouter_fallback_models_edit = QLineEdit(self.config.openrouter_fallback_models)
        self.openrouter_fallback_models_edit.setPlaceholderText(
            "model-a:free, model-b:free"
        )
        for field in (
            self.openrouter_api_key_edit,
            self.openrouter_model_edit,
            self.openrouter_fallback_models_edit,
        ):
            palette = field.palette()
            palette.setColor(QPalette.ColorRole.Text, QColor("#e6e6e6"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#303030"))
            field.setPalette(palette)
        openrouter_buttons = QHBoxLayout()
        apply_openrouter_btn = QPushButton("Apply")
        apply_openrouter_btn.clicked.connect(self.apply_openrouter_settings)
        test_openrouter_btn = QPushButton("Test")
        test_openrouter_btn.clicked.connect(self.test_openrouter_settings)
        openrouter_buttons.addWidget(apply_openrouter_btn)
        openrouter_buttons.addWidget(test_openrouter_btn)
        openrouter_buttons.addStretch(1)
        openrouter_layout.addRow("API key", api_key_container)
        openrouter_layout.addRow("Model", self.openrouter_model_edit)
        openrouter_layout.addRow("Fallbacks", self.openrouter_fallback_models_edit)
        openrouter_layout.addRow(openrouter_buttons)
        openrouter_box_layout.addWidget(self.openrouter_settings_panel)
        self.openrouter_box.toggled.connect(self._set_openrouter_settings_visible)
        self._set_openrouter_settings_visible(False)
        root.addWidget(self.openrouter_box)

        self.viewer = PDFViewer()
        self.html_preview = QTextBrowser()
        self.html_preview.setOpenLinks(False)
        self.html_preview.anchorClicked.connect(self._select_preview_block)
        self.html_preview.setPlaceholderText("HTML reading-order preview will appear after loading a PDF.")
        self.tree = StructureTree()
        self.props = PropertiesPanel()
        self.viewer.setMinimumSize(0, 0)
        self.tree.setMinimumSize(0, 0)
        self.props.setMinimumSize(0, 0)
        self.tree_hint = QLabel("Selected blocks: 0")
        self.tree_hint.setWordWrap(True)
        self.selection_hint = QLabel("Merge selection is empty.")
        self.selection_hint.setWordWrap(True)
        self.selection_list = QListWidget()
        self.selection_list.setSelectionMode(QListWidget.NoSelection)
        self.selection_list.setMinimumHeight(80)
        self.clear_selection_btn = QPushButton("Clear Selection")
        self.clear_selection_btn.clicked.connect(self.clear_selection)
        self.props.apply_role_btn.clicked.connect(self.apply_selected_block_role)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.viewer.label.blockSelected.connect(self._on_viewer_block_selected)
        self.viewer.label.zoneSelected.connect(self._on_viewer_zone_selected)
        self.viewer.label.zoneMoved.connect(self._on_viewer_zone_moved)
        self.viewer.label.zoneCreated.connect(self._on_viewer_zone_created)
        self.viewer.label.zoneEditFinished.connect(self._on_viewer_zone_edit_finished)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self.undo_last_action)
        self.continuation_preview_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.continuation_preview_shortcut.activated.connect(self.show_selected_continuation_arrow)
        viewer_column = QWidget()
        viewer_layout = QVBoxLayout(viewer_column)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.addWidget(self.viewer)
        tree_column = QWidget()
        tree_layout = QVBoxLayout(tree_column)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.addWidget(self.tree_hint)
        tree_layout.addWidget(self.tree, 1)
        tree_layout.addWidget(self.selection_hint)
        tree_layout.addWidget(self.selection_list)
        tree_layout.addWidget(self.clear_selection_btn)
        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.addWidget(viewer_column)
        self.body_splitter.addWidget(tree_column)
        self.body_splitter.addWidget(self.props)
        self.body_splitter.addWidget(self.html_preview)
        self.body_splitter.setStretchFactor(0, 3)
        self.body_splitter.setStretchFactor(1, 2)
        self.body_splitter.setStretchFactor(2, 1)
        self.body_splitter.setStretchFactor(3, 2)
        self.body_splitter.setChildrenCollapsible(False)
        root.addWidget(self.body_splitter, 1)

        self.setAcceptDrops(True)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Processing log will appear here.")
        self.log.setMaximumHeight(140)
        root.addWidget(QLabel("Processing Log"))
        root.addWidget(self.log)

    def _configured_openrouter_fallback_models(self) -> tuple[str, ...]:
        return tuple(
            model.strip()
            for model in self.config.openrouter_fallback_models.split(",")
            if model.strip()
        )

    def _create_llm_client(self) -> OpenRouterClient:
        return OpenRouterClient(
            model=self.config.openrouter_model,
            fallback_models=self._configured_openrouter_fallback_models(),
            api_key=self.config.openrouter_api_key or None,
        )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Stack the work areas when the window is too narrow for three panes."""

        if hasattr(self, "body_splitter"):
            narrow = self.width() < 1050
            orientation = Qt.Vertical if narrow else Qt.Horizontal
            if self.body_splitter.orientation() != orientation:
                self.body_splitter.setOrientation(orientation)
                if narrow:
                    self.body_splitter.setSizes([420, 260, 220])
                else:
                    self.body_splitter.setSizes([600, 400, 280])
        super().resizeEvent(event)

    def _set_openrouter_settings_visible(self, visible: bool) -> None:
        self.openrouter_settings_panel.setVisible(visible)

    def _toggle_api_key_visibility(self, visible: bool) -> None:
        self.openrouter_api_key_edit.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.show_api_key_btn.setText("Hide" if visible else "Show")

    def apply_openrouter_settings(self) -> None:
        self.config.openrouter_api_key = self.openrouter_api_key_edit.text().strip()
        self.config.openrouter_model = self.openrouter_model_edit.text().strip() or "openrouter/free"
        self.config.openrouter_fallback_models = self.openrouter_fallback_models_edit.text().strip()
        save_openrouter_settings(self.config)
        self.openrouter_client = self._create_llm_client()
        key_label = self._masked_openrouter_key(self.config.openrouter_api_key)
        self._log(
            f"OpenRouter settings applied: model={self.config.openrouter_model}, key={key_label}"
        )

    def test_openrouter_settings(self) -> None:
        self.apply_openrouter_settings()
        try:
            response = self.openrouter_client.classify(
                prompt=(
                    "Return exactly this JSON object and no extra text: "
                    '{"assignments":[]}'
                ),
                payload={"blocks": []},
            )
        except OpenRouterRateLimitError as exc:
            self._log(f"OpenRouter test rate-limited: {exc}")
            QMessageBox.warning(self, "OpenRouter rate-limited", str(exc))
            return
        except Exception as exc:
            self._log(f"OpenRouter test failed: {exc}")
            QMessageBox.warning(self, "OpenRouter test failed", str(exc))
            return
        self._log(f"OpenRouter test succeeded: {response}")
        QMessageBox.information(self, "OpenRouter test", "Connection test succeeded.")

    def _masked_openrouter_key(self, api_key: str) -> str:
        if not api_key:
            return "not set"
        if len(api_key) <= 12:
            return "***"
        return f"{api_key[:5]}...{api_key[-4:]}"

    def load_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        try:
            self._load_pdf_path(path)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf"):
                self._load_pdf_path(path)
                event.acceptProposedAction()
                break

    def generate_xml(self) -> None:
        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return
        selected_blocks = [block for block in self.document.blocks if block.id in self._selected_block_ids]
        if len(selected_blocks) >= 2:
            valid_continuation, _reason = self._selection_looks_like_paragraph_continuation(selected_blocks)
            if valid_continuation and not self._has_html_preview_continuation(
                {block.id for block in selected_blocks}
            ):
                QMessageBox.information(
                    self,
                    "Continue paragraph first",
                    "Click Continue Paragraph to add the selected blocks to the HTML preview before generating XML.",
                )
                return
        self._update_document_model()
        export_document = self._document_with_html_preview_continuations()
        validation_errors = self.validator.validate_document(export_document)
        if validation_errors:
            for error in validation_errors:
                self._log(f"Document validation error: {error.message}")
        segments = export_document.metadata.get("article_segments", [])
        documents = [self._document_for_segment(segment, export_document) for segment in segments]
        if len(documents) <= 1:
            documents = [export_document]
        output_dir = self._choose_output_directory(
            "Choose XML output folder", self.config.generated_xml_dir
        )
        if output_dir is None:
            return
        output_paths: list[Path] = []
        for index, segment_document in enumerate(documents, start=1):
            filename = "article.xml" if len(documents) == 1 else f"article_{index:03d}.xml"
            output_path = output_dir / filename
            generated_path = self.generator.generate(segment_document, output_path)
            output_paths.append(generated_path)
            errors = self.validator.validate(generated_path)
            if errors:
                for error in errors:
                    self._log(f"Validation error in {generated_path.name}: {error.message}")
            else:
                self._log(f"XML written to {generated_path}")
        self.current_xml_path = output_paths[0] if output_paths else None

    def export_json(self) -> None:
        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return
        output_dir = self._choose_output_directory(
            "Choose JSON output folder", self.config.output_dir
        )
        if output_dir is None:
            return
        self._update_document_model()
        export_document = self._document_with_html_preview_continuations()
        output_path = output_dir / "document.json"
        layout_path = output_dir / "layout.json"
        report_path = output_dir / "pipeline_report.json"
        export_document.to_json(output_path)
        layout_path.parent.mkdir(parents=True, exist_ok=True)
        layout_path.write_text(json.dumps(export_document.to_layout_json(), indent=2, ensure_ascii=False), encoding="utf-8")
        report_path.write_text(json.dumps(export_document.to_pipeline_report(), indent=2, ensure_ascii=False), encoding="utf-8")
        self._log(f"JSON written to {output_path}")
        self._log(f"Layout JSON written to {layout_path}")
        self._log(f"Pipeline report written to {report_path}")

    def _choose_output_directory(self, title: str, initial_dir: Path) -> Path | None:
        """Ask the user where an export should be written."""

        path = QFileDialog.getExistingDirectory(self, title, str(initial_dir.resolve()))
        return Path(path) if path else None

    def _has_html_preview_continuation(self, block_ids: set[str]) -> bool:
        return any(set(group) == block_ids for group in self._html_preview_merges)

    def _blocks_with_html_preview_continuations(self, blocks):
        """Create export-only blocks that collapse the active HTML continuation groups."""

        blocks_by_id = {block.id: block for block in blocks}
        groups: list[list] = []
        grouped_ids: set[str] = set()
        for group in self._html_preview_merges:
            group_blocks = self._sort_reading_order(
                [blocks_by_id[block_id] for block_id in group if block_id in blocks_by_id]
            )
            if len(group_blocks) != len(group) or len(group_blocks) < 2:
                continue
            groups.append(group_blocks)
            grouped_ids.update(block.id for block in group_blocks)

        virtual_blocks = {}
        for group_blocks in groups:
            first = group_blocks[0]
            source_ids = [block.id for block in group_blocks]
            source_line_ids = [
                str(line_id)
                for block in group_blocks
                for line_id in block.metadata.get("source_line_ids", [block.id])
            ]
            roles = {block.role for block in group_blocks}
            semantic_roles = roles - {"unclassified"}
            if len(semantic_roles) == 1:
                merged_role = semantic_roles.pop()
            elif len(roles) == 1:
                merged_role = roles.pop()
            else:
                merged_role = "unclassified"
            metadata = dict(first.metadata)
            metadata.update(
                {
                    "preview_source_block_ids": source_ids,
                    "source_line_ids": list(dict.fromkeys(source_line_ids)),
                    "is_paragraph_unit": True,
                    "merge_source": "html_preview_continuation",
                    "continuation_from": source_ids[:-1],
                    "continuation_to": source_ids[-1],
                }
            )
            virtual_blocks[first.id] = replace(
                first,
                text=" ".join(" ".join(block.text.split()) for block in group_blocks),
                bbox=[
                    min(block.bbox[0] for block in group_blocks),
                    min(block.bbox[1] for block in group_blocks),
                    max(block.bbox[2] for block in group_blocks),
                    max(block.bbox[3] for block in group_blocks),
                ],
                x=min(block.x for block in group_blocks),
                y=min(block.y for block in group_blocks),
                width=max(block.bbox[2] for block in group_blocks) - min(block.bbox[0] for block in group_blocks),
                height=max(block.bbox[3] for block in group_blocks) - min(block.bbox[1] for block in group_blocks),
                confidence=min(block.confidence for block in group_blocks),
                role=merged_role,
                metadata=metadata,
            )

        result = []
        for block in self._sort_reading_order(blocks):
            if block.id in grouped_ids:
                virtual_block = virtual_blocks.get(block.id)
                if virtual_block is not None:
                    result.append(virtual_block)
                continue
            result.append(replace(block, metadata=dict(block.metadata)))
        return self._sort_reading_order(result)

    def _document_with_html_preview_continuations(self) -> Document:
        """Build an export document without changing the editable block model."""

        if self.document is None:
            raise RuntimeError("Load a PDF first.")
        source = self.document
        blocks = self._blocks_with_html_preview_continuations(source.blocks)
        visible_blocks = [block for block in blocks if not self._looks_like_footer_noise(block)]
        author_blocks = [
            block for block in visible_blocks if block.role in {"author", "corresponding_author"}
        ]
        affiliation_blocks = [block for block in visible_blocks if block.role == "affiliation"]
        linked = self.linker.link_from_blocks(author_blocks, affiliation_blocks)
        return Document(
            title=self._compose_title(visible_blocks),
            authors=linked.authors,
            corresponding_author=linked.corresponding_author,
            affiliations=linked.affiliations,
            abstract="\n".join(
                self._sanitize_xml_text(block.text)
                for block in visible_blocks
                if block.role == "abstract" and block.text.strip()
            ),
            raw_blocks=[replace(block, metadata=dict(block.metadata)) for block in source.raw_blocks],
            blocks=blocks,
            paragraphs=(
                self.paragraph_reconstructor.propose(source.raw_blocks)
                + self.paragraph_reconstructor.accepted_from_blocks(blocks)
            ),
            zones=list(source.zones),
            figures=list(source.figures),
            tables=list(source.tables),
            references=list(source.references),
            metadata=dict(source.metadata),
        )

    def _log(self, message: str) -> None:
        self.log.append(message)

    def _update_html_preview(self) -> None:
        """Render blocks in backend reading order as selectable HTML."""

        if self.document is None:
            self.html_preview.clear()
            return
        parts = [
            "<style>body{font-family:Arial;color:#222;background:#fff;}"
            "p{margin:0 0 10px;padding:7px;border:1px solid #ddd;}"
            ".selected{border:2px solid #2d75d6;background:#eef5ff;}"
            ".continued{border:2px solid #218838;background:#eef9f0;}"
            ".meta{color:#666;font-size:11px;}</style>"
            "<h3>Reading Order Preview</h3>"
        ]
        ordered_blocks = self._sort_reading_order(self.document.blocks)
        blocks_by_id = {block.id: block for block in ordered_blocks}
        reading_order = {block.id: index for index, block in enumerate(ordered_blocks)}
        merged_ids = {block_id for group in self._html_preview_merges for block_id in group}
        preview_items: list[tuple[tuple[str, ...], str, int, str]] = []
        for group in self._html_preview_merges:
            group_blocks = self._sort_reading_order([blocks_by_id[block_id] for block_id in group if block_id in blocks_by_id])
            if not group_blocks:
                continue
            preview_items.append(
                (
                    group,
                    " ".join(" ".join(block.text.split()) for block in group_blocks),
                    group_blocks[0].page,
                    f"merged {len(group_blocks)} blocks",
                )
            )
        for block in ordered_blocks:
            if block.id not in merged_ids:
                preview_items.append(((block.id,), " ".join(block.text.split()), block.page, block.role))

        preview_items.sort(key=lambda item: min(reading_order[block_id] for block_id in item[0] if block_id in reading_order))
        for index, (block_ids, text, page, role) in enumerate(preview_items, start=1):
            selected = " selected" if any(block_id in self._selected_block_ids for block_id in block_ids) else ""
            if len(block_ids) == 1:
                anchor = f"block-{block_ids[0]}"
                label = (
                    f'<a name="{escape(anchor)}"></a><a href="select:{escape(block_ids[0])}">'
                    f'<span class="meta">{index}. page {page} | {escape(role)}</span></a>'
                )
                css_class = selected
            else:
                anchor = f"continued-{'-'.join(block_ids)}"
                label = f'<a name="{escape(anchor)}"></a><span class="meta">CONTINUED | {index}. page {page} | {escape(role)}</span>'
                css_class = f"continued{selected}"
            parts.append(
                f'<p class="{css_class}">{label}'
                f'<br>{escape(text)}</p>'
            )
        self.html_preview.setHtml("".join(parts))

    def _select_preview_block(self, url: QUrl) -> None:
        """Select a block clicked in the HTML preview."""

        if url.scheme() != "select" or self.document is None:
            return
        block_id = url.path().lstrip("/") or url.host()
        block = next((item for item in self.document.blocks if item.id == block_id), None)
        if block is None:
            return
        additive = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        if not additive:
            self._selected_zone_id = None
            self._selected_zone_ids.clear()
            self.viewer.set_selected_zones(set())
            self._selected_block_ids = {block.id}
        elif block.id in self._selected_block_ids:
            self._selected_block_ids.remove(block.id)
        else:
            self._selected_block_ids.add(block.id)

        active_block_id = block.id if block.id in self._selected_block_ids else None
        self.viewer.set_selected_blocks(self._selected_block_ids, active_block_id)
        self._sync_tree_selection()
        self._update_selection_status()
        self._update_selection_panel()
        if active_block_id:
            self._select_block_by_id(block.id, preserve_selection=True)
        self._update_html_preview()

    def _update_document_model(self) -> None:
        """Build document metadata from user-assigned roles only."""

        if self.document is None:
            return
        excluded_ids = set(self.document.metadata.get("abstract_number_block_ids", []))
        blocks = [
            block
            for block in self.document.blocks
            if block.id not in excluded_ids and not self._looks_like_footer_noise(block)
        ]
        self.document.title = self._compose_title(blocks)
        abstract_blocks = [b for b in blocks if b.role == "abstract"]
        abstract_blocks.sort(key=lambda b: (b.page, b.y, b.x))
        abstract_text = "\n".join(self._sanitize_xml_text(b.text) for b in abstract_blocks)
        self.document.abstract = abstract_text
        author_blocks = [b for b in blocks if b.role in {"author", "corresponding_author"}]
        author_blocks.sort(key=lambda b: (b.page, b.y, b.x))
        affiliation_blocks = [b for b in blocks if b.role == "affiliation"]
        affiliation_blocks.sort(key=lambda b: (b.page, b.y, b.x))
        linked = self.linker.link_from_blocks(author_blocks, affiliation_blocks)
        self.document.authors = linked.authors
        self.document.corresponding_author = linked.corresponding_author
        self.document.affiliations = linked.affiliations
        self._rebuild_paragraph_model()
        self._sync_auto_zones()

    def _document_for_segment(
        self, segment: dict[str, object], source_document: Document | None = None
    ) -> Document:
        """Build an exportable document view for one detected article range."""

        source = source_document or self.document
        if source is None:
            raise RuntimeError("Load a PDF first.")
        start_page = int(segment.get("start_page", 1))
        end_page = int(segment.get("end_page", start_page))
        blocks = [
            replace(block, metadata=dict(block.metadata))
            for block in source.blocks
            if start_page <= block.page <= end_page
        ]
        block_ids = {block.id for block in blocks}
        raw_blocks = [
            replace(block, metadata=dict(block.metadata))
            for block in (source.raw_blocks or source.blocks)
            if block.id in block_ids
        ]
        paragraphs = [
            replace(
                paragraph,
                source_line_ids=[line_id for line_id in paragraph.source_line_ids if line_id in block_ids],
            )
            for paragraph in source.paragraphs
            if any(line_id in block_ids for line_id in paragraph.source_line_ids)
        ]
        metadata = dict(source.metadata)
        metadata["abstract_number"] = str(segment.get("abstract_number", "ABSN"))
        metadata["abstract_number_block_ids"] = [str(segment.get("abstract_number_block_id", ""))]
        metadata["article_segment"] = dict(segment)
        metadata.pop("article_segments", None)
        abstract_number = metadata["abstract_number"]
        title = self._compose_title(blocks)
        title = re.sub(
            rf"^\s*{re.escape(abstract_number)}\s*(?:\||\u2502)\s*",
            "",
            title,
            count=1,
        ).strip()
        return Document(
            title=title,
            authors=list(source.authors),
            affiliations=list(source.affiliations),
            abstract="\n".join(
                self._sanitize_xml_text(block.text)
                for block in blocks
                if block.role == "abstract" and block.text.strip()
            ),
            raw_blocks=raw_blocks,
            blocks=blocks,
            paragraphs=paragraphs,
            zones=[zone for zone in source.zones if start_page <= zone.page <= end_page],
            figures=[figure for figure in source.figures if start_page <= figure.get("page", 0) <= end_page],
            tables=[table for table in source.tables if start_page <= table.get("page", 0) <= end_page],
            references=list(source.references),
            metadata=metadata,
        )

    def _looks_like_footer_noise(self, block) -> bool:
        """Return true for page numbers, copyright, and publisher footer lines."""

        text = str(block.text or "").strip()
        lower = text.lower()
        if re.fullmatch(r"[\s|.\-]*\d+[\s|.\-]*", text):
            return True
        footer_terms = (
            "©",
            "copyright",
            "published by",
            "john wiley",
            "wiley and sons",
            "allergy_",
            "onlinelibrary",
        )
        return any(term in lower for term in footer_terms)

    def _rebuild_paragraph_model(self) -> None:
        """Refresh proposed paragraphs and preserve accepted manual paragraph units."""

        if self.document is None:
            return
        source_lines = self.document.raw_blocks or self.document.blocks
        proposals = self.paragraph_reconstructor.propose(source_lines)
        accepted = self.paragraph_reconstructor.accepted_from_blocks(self.document.blocks)
        self.document.paragraphs = proposals + accepted

    def _compose_title(self, blocks) -> str:
        """Build the title from blocks the user marked as title."""

        title_blocks = [block for block in blocks if block.role == "title"]
        if not title_blocks:
            return ""
        title_blocks.sort(key=lambda b: (b.page, b.y, b.x))
        page1_blocks = [block for block in title_blocks if block.page == 1]
        candidates = page1_blocks if page1_blocks else title_blocks
        top = candidates[0]
        grouped = [top]
        baseline = top.y + top.height
        for block in candidates[1:]:
            if block.y - baseline > max(top.height, block.height) * 1.6:
                break
            grouped.append(block)
            baseline = max(baseline, block.y + block.height)
        parts = [self._sanitize_xml_text(block.text).strip() for block in grouped if self._sanitize_xml_text(block.text).strip()]
        return " ".join(parts) if parts else self._sanitize_xml_text(top.text)

    def _populate_tree(self) -> None:
        """Populate the structure tree with detected blocks."""

        if self.document is None:
            return
        self.tree.blockSignals(True)
        self.tree.clear_dynamic_items()
        buckets: dict[str, list] = {
            "Title": [],
            "Authors": [],
            "Corresponding Author": [],
            "Affiliations": [],
            "Abstract": [],
        }
        for block in self.document.blocks:
            if block.role == "title":
                buckets["Title"].append(block)
            elif block.role == "author":
                buckets["Authors"].append(block)
            elif block.role == "corresponding_author":
                buckets["Corresponding Author"].append(block)
            elif block.role == "affiliation":
                buckets["Affiliations"].append(block)
            elif block.role == "abstract":
                buckets["Abstract"].append(block)
        for label, category_blocks in buckets.items():
            parent = self.tree.category_item(label)
            if parent is None:
                continue
            for block in category_blocks:
                item = self._make_block_item(block)
                parent.addChild(item)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._refresh_zone_list()

    def _sync_auto_zones(self) -> None:
        """Keep auto-generated zones aligned with currently classified blocks."""

        if self.document is None:
            return
        manual_zones = [zone for zone in self.document.zones if zone.source != "auto"]
        manual_zone_ids = {zone.id for zone in manual_zones}
        auto_zones: list[DocumentZone] = []
        for index, block in enumerate(self.document.blocks, start=1):
            if block.role == "unclassified":
                continue
            zone_id = f"zone_auto_{index:03d}"
            if zone_id in manual_zone_ids:
                continue
            auto_zones.append(
                DocumentZone(
                    id=zone_id,
                    page=block.page,
                    label=block.role,
                    bbox=list(block.bbox),
                    source="auto",
                )
            )
        self.document.zones = auto_zones + manual_zones

    def _refresh_zone_list(self) -> None:
        """Compatibility shim after removing the zone list UI."""

        return

    def _make_block_item(self, block) -> object:
        item = self._tree_item(block.id, f"{block.text[:60]}", block)
        self._attach_merge_details(item, block)
        return item

    def _tree_item(self, title: str, text: str, block) -> object:
        from PySide6.QtWidgets import QTreeWidgetItem

        item = QTreeWidgetItem([text, block.role])
        item.setData(0, Qt.UserRole, block.id)
        item.setToolTip(0, block.text)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        return item

    def _attach_merge_details(self, item, block) -> None:
        """Add merge provenance as nested rows in the structure tree."""

        metadata = block.metadata or {}
        merged_ids = metadata.get("merged_block_ids")
        if not merged_ids:
            return

        details = {
            "Merge Count": str(metadata.get("merge_count", len(merged_ids))),
            "Merge Source": str(metadata.get("merge_source", "unknown")),
            "Merged From": ", ".join(str(item_id) for item_id in merged_ids),
        }
        from PySide6.QtWidgets import QTreeWidgetItem

        for label, value in details.items():
            child = QTreeWidgetItem([label, value])
            child.setFlags(child.flags() & ~Qt.ItemIsEditable)
            item.addChild(child)

    def _on_tree_selection_changed(self) -> None:
        if self.document is None:
            return
        items = self.tree.selectedItems()
        if not items:
            self._selected_block_ids.clear()
            self.viewer.set_selected_blocks(set())
            self._selected_zone_id = None
            self._selected_zone_ids.clear()
            self._update_selection_status()
            self._update_selection_panel()
            self.props.set_role(None)
            return
        selected_ids = {
            str(item.data(0, Qt.UserRole))
            for item in items
            if item.parent() is not None and item.data(0, Qt.UserRole)
        }
        if not selected_ids:
            return
        self._selected_zone_id = None
        self._selected_zone_ids.clear()
        self._selected_block_ids = selected_ids
        primary_id = str(items[-1].data(0, Qt.UserRole)) if items[-1].data(0, Qt.UserRole) else next(iter(selected_ids))
        self.viewer.set_selected_blocks(self._selected_block_ids, primary_id)
        self._update_selection_status()
        self._update_selection_panel()
        self._select_block_by_id(primary_id, preserve_selection=True)

    def _on_tree_item_changed(self, item, column: int) -> None:
        if self.document is None or column != 1:
            return
        block_id = item.data(0, Qt.UserRole)
        if not block_id:
            return
        block = next((b for b in self.document.blocks if b.id == block_id), None)
        if block is None:
            return
        valid_roles = {"title", "author", "corresponding_author", "affiliation", "abstract"}
        new_role = item.text(1).strip().lower()
        if new_role in valid_roles:
            if new_role == "title":
                if self.document is not None:
                    self.document.metadata["manual_title_block_id"] = str(block_id)
                    for other in self.document.blocks:
                        if other.id != block_id and other.role == "title":
                            other.role = "unclassified"
            block.role = new_role
            self._update_document_model()
            self.viewer.set_blocks([asdict(b) for b in self.document.blocks])
            self.viewer.set_zones([asdict(zone) for zone in self.document.zones])
            self._populate_tree()
            self._log(f"Reassigned {block.id} to role '{new_role}'")
        else:
            self.tree.blockSignals(True)
            item.setText(1, block.role)
            self.tree.blockSignals(False)

    def merge_selected_blocks(self) -> None:
        """Merge selected blocks or the blocks covered by selected zones."""

        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return

        selected_ids = set(self._selected_block_ids)
        self._log(
            f"Merge requested with {len(self._selected_block_ids)} block(s) and {len(self._selected_zone_ids)} zone(s) selected."
        )
        if len(selected_ids) >= 2:
            self._merge_blocks_by_ids(selected_ids)
            return
        selected_blocks = self._blocks_from_selected_zones()
        selected_blocks.extend(block for block in self.document.blocks if block.id in selected_ids and block.id not in {item.id for item in selected_blocks})
        if len(selected_blocks) >= 2:
            self._merge_selected_block_list(selected_blocks)
            return
        if self._selected_zone_ids:
            QMessageBox.information(self, "Select blocks", "Select a zone that covers at least two blocks, then merge.")
            return
        selected_blocks = [block for block in self.document.blocks if block.id in selected_ids]
        if len(selected_blocks) < 2:
            QMessageBox.information(self, "Select blocks", "Select at least two blocks to merge.")
            return
        self._apply_manual_merge(selected_blocks, merge_source="manual", join_with_space=False, action_label="Merged")

    def _continue_blocks_in_html_preview(self, selected_ids: set[str]) -> None:
        """Show selected paragraph units as one item without changing the document."""

        if self.document is None:
            return
        selected_blocks = [block for block in self.document.blocks if block.id in selected_ids]
        if len(selected_blocks) < 2:
            QMessageBox.information(self, "Select blocks", "Select at least two blocks to merge.")
            return
        group = tuple(block.id for block in self._sort_reading_order(selected_blocks))
        self._html_preview_merges = [
            existing_group
            for existing_group in self._html_preview_merges
            if not set(existing_group).intersection(group)
        ]
        self._html_preview_merges.append(group)
        self._selected_zone_id = None
        self._selected_zone_ids.clear()
        self.viewer.set_selected_zones(set())
        continuation_links = [(group[index], group[index + 1]) for index in range(len(group) - 1)]
        self.viewer.set_selected_blocks(self._selected_block_ids, group[0])
        self.viewer.set_continuation_links(continuation_links)
        self._sync_tree_selection()
        self._update_selection_status()
        self._update_selection_panel()
        self._update_html_preview()
        self.html_preview.scrollToAnchor(f"continued-{'-'.join(group)}")
        self._log(f"Continued {len(group)} paragraph blocks in the HTML preview.")
        QMessageBox.information(
            self,
            "Paragraph continued",
            f"The {len(group)} selected paragraph blocks are now combined in the HTML preview. The PDF blocks and exported document are unchanged.",
        )

    def continue_selected_paragraphs(self) -> None:
        """Collapse selected merged paragraphs into a single continuation block."""

        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return

        selected_blocks = [block for block in self.document.blocks if block.id in self._selected_block_ids]
        if len(selected_blocks) < 2:
            QMessageBox.information(self, "Select blocks", "Select at least two paragraph blocks to continue.")
            return
        valid, reason = self._selection_looks_like_paragraph_continuation(selected_blocks)
        if not valid:
            QMessageBox.information(self, "Invalid selection", reason)
            return
        self._continue_blocks_in_html_preview({block.id for block in selected_blocks})

    def show_selected_continuation_arrow(self) -> None:
        """Preview continuation direction between selected paragraph units."""

        if self.document is None:
            return
        selected_blocks = [block for block in self.document.blocks if block.id in self._selected_block_ids]
        if len(selected_blocks) < 2:
            QMessageBox.information(self, "Select paragraphs", "Select two merged paragraph blocks first.")
            return
        valid, reason = self._selection_looks_like_paragraph_continuation(selected_blocks)
        if not valid:
            QMessageBox.information(self, "Cannot preview continuation", reason)
            return
        blocks = self._sort_paragraph_continuation_blocks(selected_blocks)
        links = [(blocks[index].id, blocks[index + 1].id) for index in range(len(blocks) - 1)]
        self.viewer.set_continuation_links(links)
        self._log("Continuation preview: " + " -> ".join(block.id for block in blocks))

    def classify_selected_blocks(self) -> None:
        """Run model-backed classification on selected blocks or blocks inside selected zones."""

        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return
        selected_blocks = [block for block in self.document.blocks if block.id in self._selected_block_ids]
        selected_zone_blocks = self._blocks_from_selected_zones()
        selected_zone_block_ids = {block.id for block in selected_zone_blocks}
        if selected_zone_blocks:
            selected_blocks_by_id = {block.id: block for block in selected_blocks}
            for block in selected_zone_blocks:
                selected_blocks_by_id.setdefault(block.id, block)
            selected_blocks = list(selected_blocks_by_id.values())
            selected_blocks.sort(key=lambda block: (block.page, block.y, block.x))
        if not selected_blocks:
            QMessageBox.information(self, "Select blocks", "Select one or more text blocks or a zone first.")
            return

        payload = {
            "blocks": [
                {
                    "block_id": block.id,
                    "page": block.page,
                    "text": block.text,
                    "bbox": block.bbox,
                    "font_size": block.font_size,
                    "font_name": block.font_name,
                    "bold": block.bold,
                    "italic": block.italic,
                    "alignment": block.alignment,
                    "structural_evidence": block.metadata.get("docling", {}),
                }
                for block in selected_blocks
            ]
        }

        def local_classifier_response() -> dict[str, list[dict[str, object]]]:
            results = self.classifier.classify_blocks(selected_blocks)
            return {
                "assignments": [
                    {
                        "block_id": block.id,
                        "role": result.role,
                        "confidence": result.confidence,
                    }
                    for block, result in zip(selected_blocks, results, strict=False)
                ]
            }

        try:
            answer_summary = self.openrouter_client.classify(
                prompt=QUESTION_ANSWER_PROMPT,
                payload=payload,
            )
            raw_response = self.openrouter_client.classify(
                prompt=ROLE_ASSIGNMENT_PROMPT,
                payload={"blocks": payload["blocks"], "answer_summary": answer_summary},
            )
            if isinstance(raw_response, dict):
                raw_response["answer_summary"] = answer_summary
        except OpenRouterRateLimitError as exc:
            self._log(f"OpenRouter classification rate-limited: {exc}")
            self._log("Falling back to local heuristic classifier for selected blocks.")
            raw_response = local_classifier_response()
        except Exception as exc:
            self._log(f"OpenRouter classification failed: {exc}")
            self._log("Falling back to local heuristic classifier for selected blocks.")
            raw_response = local_classifier_response()
        assignments = self._extract_role_assignments(raw_response)
        if not assignments:
            QMessageBox.information(self, "No changes", "The model did not return usable role assignments.")
            return

        by_id = {block.id: block for block in selected_blocks}
        changed: list[tuple[str, str]] = []
        zone_role_votes: dict[str, list[str]] = {}
        for block_id, role, confidence in assignments:
            block = by_id.get(block_id)
            if block is None or role not in {"title", "author", "affiliation", "abstract", "unclassified"}:
                continue
            block.role = role
            block.confidence = confidence
            changed.append((block.id, role))
            if block.id in selected_zone_block_ids:
                for zone in (zone for zone in self.document.zones if zone.id in self._selected_zone_ids):
                    zone_blocks = set()
                    for zone_block in self._blocks_for_zone(zone):
                        zone_blocks.add(zone_block.id)
                    if block.id in zone_blocks:
                        zone_role_votes.setdefault(zone.id, []).append(role)
            if role == "title":
                self.document.metadata["manual_title_block_id"] = block.id
                for other in self.document.blocks:
                    if other.id != block.id and other.role == "title":
                        other.role = "unclassified"
                        self._clear_manual_title_metadata_if_needed(other.id)

        if not changed:
            QMessageBox.information(self, "No changes", "The selected blocks were not reclassified.")
            return

        changed_ids = [block_id for block_id, _role in changed]
        for zone_id, roles in zone_role_votes.items():
            if not roles:
                continue
            zone = next((entry for entry in self.document.zones if entry.id == zone_id), None)
            if zone is None:
                continue
            role_counts: dict[str, int] = {}
            for role in roles:
                role_counts[role] = role_counts.get(role, 0) + 1
            most_common_role, most_common_count = max(role_counts.items(), key=lambda item: item[1])
            if most_common_count == len(roles) and len(role_counts) == 1:
                zone.label = most_common_role
                if zone.source == "auto":
                    zone.source = "user"
        self._update_document_model()
        self.viewer.set_blocks([asdict(block) for block in self.document.blocks])
        self.viewer.set_zones([asdict(zone) for zone in self.document.zones])
        self._populate_tree()
        self._sync_tree_selection()
        self._selected_block_ids = set(changed_ids)
        primary_id = changed_ids[0]
        self.viewer.set_selected_blocks(self._selected_block_ids, primary_id)
        self._update_selection_status()
        self._update_selection_panel()
        self._select_block_by_id(primary_id, preserve_selection=True)
        self._log(
            "Classified selected items: " + ", ".join(f"{block_id}={role}" for block_id, role in changed)
        )

    def _extract_role_assignments(self, response: object) -> list[tuple[str, str, float]]:
        """Normalize model output into block-role assignments."""

        candidates: list[object] = []
        if isinstance(response, dict):
            candidates.extend([response, response.get("generated_text"), response.get("choices")])
        elif isinstance(response, list):
            candidates.extend(response)
        else:
            candidates.append(response)

        def _maybe_parse_json(value: object) -> object:
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return None
                try:
                    return json.loads(text)
                except Exception:
                    return None
            return value

        for candidate in list(candidates):
            parsed = _maybe_parse_json(candidate)
            if parsed is not None:
                candidates.append(parsed)

        for candidate in candidates:
            assignments = None
            if isinstance(candidate, dict):
                assignments = candidate.get("assignments")
                if assignments is None and "role" in candidate and "block_id" in candidate:
                    assignments = [candidate]
            elif isinstance(candidate, list):
                assignments = candidate
            if not isinstance(assignments, list):
                continue
            normalized: list[tuple[str, str, float]] = []
            for item in assignments:
                if not isinstance(item, dict):
                    continue
                block_id = str(item.get("block_id", "")).strip()
                role = str(item.get("role", "")).strip().lower()
                try:
                    confidence = float(item.get("confidence", 0.0))
                except Exception:
                    confidence = 0.0
                if block_id and role:
                    normalized.append((block_id, role, confidence))
            if normalized:
                return normalized
        return []

    def _merge_blocks_by_ids(self, selected_ids: set[str]) -> None:
        """Merge a set of explicitly selected blocks."""

        selected_blocks = [block for block in self.document.blocks if block.id in selected_ids]
        self._merge_selected_block_list(selected_blocks)

    def _merge_selected_block_list(self, selected_blocks) -> None:
        """Merge the provided blocks into one block and refresh the UI."""

        if self._selection_is_paragraph_units(selected_blocks):
            self._apply_manual_merge(
                selected_blocks,
                merge_source="paragraph_continuation",
                join_with_space=True,
                action_label="Continued",
            )
            return
        self._apply_manual_merge(selected_blocks, merge_source="manual", join_with_space=False, action_label="Merged")

    def _selection_is_paragraph_units(self, selected_blocks) -> bool:
        """Return true when a selection contains only accepted paragraph units."""

        return (
            len(selected_blocks) >= 2
            and all(bool(block.metadata.get("is_paragraph_unit")) for block in selected_blocks)
        )

    def _apply_manual_merge(self, selected_blocks, merge_source: str, join_with_space: bool, action_label: str) -> None:
        """Apply a manual merge or continuation to the selected blocks."""

        if self.document is None:
            return
        if len(selected_blocks) < 2:
            QMessageBox.information(self, "Select blocks", "Select at least two blocks to merge.")
            return

        if merge_source == "paragraph_continuation":
            selected_blocks = self._sort_paragraph_continuation_blocks(selected_blocks)
        else:
            selected_blocks = sorted(selected_blocks, key=lambda block: (block.page, block.y, block.x))
        selected_ids = {block.id for block in selected_blocks}
        merged_block = self._merge_blocks(selected_blocks, merge_source=merge_source, join_with_space=join_with_space)
        remaining_blocks = [block for block in self.document.blocks if block.id not in selected_ids]
        remaining_blocks.append(merged_block)
        remaining_blocks = self._sort_reading_order(remaining_blocks)
        self.document.blocks = remaining_blocks
        self._selected_block_ids = {merged_block.id}
        self._selected_zone_ids.clear()
        self._selected_zone_id = None
        self._last_manual_merge = {
            "merged_id": merged_block.id,
            "original_blocks": [replace(block) for block in selected_blocks],
            "merged_block": replace(merged_block),
        }
        self._redo_manual_merge = None

        self._update_document_model()
        self.viewer.set_blocks([asdict(block) for block in self.document.blocks])
        self.viewer.set_zones([asdict(zone) for zone in self.document.zones])
        self._populate_tree()
        self.viewer.set_selected_blocks(self._selected_block_ids, merged_block.id)
        self._update_selection_status()
        self._update_selection_panel()
        self._update_html_preview()
        self._log(f"{action_label} {len(selected_blocks)} blocks into {merged_block.id}")
        self._select_block_by_id(merged_block.id, preserve_selection=True)

    def _selection_looks_like_paragraph_continuation(self, selected_blocks) -> tuple[bool, str]:
        """Validate that the current selection is safe to collapse as one paragraph."""

        if self.document is None:
            return False, "Load a PDF first."

        blocks = self._sort_paragraph_continuation_blocks(selected_blocks)

        for block in blocks:
            if not bool(block.metadata.get("is_paragraph_unit")):
                return False, "Select only blocks that were previously created as paragraph units."
            if len(block.metadata.get("merged_block_ids", [])) < 1:
                return False, "Selected blocks must already be merged paragraph units."
            if len(block.text.strip().split()) < 8:
                return False, "Selected blocks are too short to continue as a paragraph."
        first_text = blocks[0].text.strip().lower()
        if re.match(r"^(abstract|flash talks?|poster|oral presentation|session|symposium)\b", first_text):
            return False, "The first selected block looks like a header or section label."
        if re.match(r"^(doi:|[\d.]+\/)", first_text):
            return False, "The first selected block looks like metadata, not a paragraph."

        for first, second in zip(blocks, blocks[1:]):
            if second.page - first.page > 1:
                return False, "Selected paragraphs skip a page and are not a continuous reading sequence."
            if second.page != first.page:
                if not self._looks_like_page_continuation(first, second):
                    return False, "The selected paragraphs do not look like a page continuation."
                continue
            vertical_gap = max(0.0, second.y - (first.y + first.height))
            right_column_continuation = second.x > first.x + first.width * 0.75
            if not right_column_continuation and vertical_gap > max(first.height, second.height) * 0.5:
                return False, "Selected blocks are too far apart to be treated as one continuation."
            if abs(first.font_size - second.font_size) > 1.75:
                return False, "Selected blocks have mismatched font sizes."
            if first.alignment and second.alignment and first.alignment != second.alignment:
                return False, "Selected blocks should share the same alignment."
            if first.x > second.x + max(first.width, second.width) * 0.15:
                return False, "Selected blocks are not in a clean reading order."
            if first.bold != second.bold or first.italic != second.italic:
                return False, "Selected blocks should share the same style."
        return True, ""

    def _is_continuation_boundary(self, block) -> bool:
        """Reject text that must start or end a logical section instead."""

        text = " ".join(block.text.split()).strip()
        lower = text.lower()
        if not text or self._looks_like_footer_noise(block):
            return True
        if re.match(
            r"^(background|aims?|methods?|results?|discussion|conclusions?|summary(?:/| )|references?|acknowledg)",
            lower,
        ):
            return True
        if re.match(r"^(doi:|https?://|www\.)", lower):
            return True
        return bool(block.role in {"title", "author", "corresponding_author", "affiliation"})

    def _looks_like_page_continuation(self, first, second) -> bool:
        """Accept an explicitly selected continuation across consecutive pages."""

        if second.page <= first.page or second.page - first.page != 1:
            return False
        previous_text = " ".join(first.text.split()).rstrip()
        next_text = " ".join(second.text.split()).lstrip()
        return bool(previous_text and next_text)

    def _sort_paragraph_continuation_blocks(self, blocks) -> list:
        """Sort selected paragraph units in natural reading order."""

        return self._sort_reading_order(blocks)

    def _sort_reading_order(self, blocks) -> list:
        """Sort blocks by page, then column, then top-to-bottom position."""

        ordered: list = []
        pages: dict[int, list] = {}
        for block in blocks:
            pages.setdefault(int(block.page), []).append(block)

        for page in sorted(pages):
            page_blocks = sorted(pages[page], key=lambda block: (block.x, block.y))
            columns: list[list] = []
            for block in page_blocks:
                if not columns:
                    columns.append([block])
                    continue
                previous_column = columns[-1]
                anchor = sum(float(item.x) for item in previous_column) / len(previous_column)
                typical_width = max(
                    1.0,
                    sum(float(item.width) for item in previous_column) / len(previous_column),
                )
                if float(block.x) - anchor > typical_width * 0.55:
                    columns.append([block])
                else:
                    previous_column.append(block)

            for column in columns:
                ordered.extend(sorted(column, key=lambda block: (block.y, block.x)))
        return ordered

    def _blocks_from_selected_zones(self) -> list:
        """Return blocks that fall inside the currently selected zones."""

        if self.document is None or not self._selected_zone_ids:
            return []

        def _rect(bbox: list[float]) -> tuple[float, float, float, float]:
            return tuple(float(value) for value in bbox)  # type: ignore[return-value]

        def _intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
            return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]

        selected_zones = [zone for zone in self.document.zones if zone.id in self._selected_zone_ids]
        blocks: list = []
        seen: set[str] = set()
        for zone in selected_zones:
            zone_bboxes = [bbox for bbox in (getattr(zone, "bboxes", None) or [zone.bbox]) if isinstance(bbox, list) and len(bbox) == 4]
            if not zone_bboxes:
                continue
            zone_rects = [_rect(bbox) for bbox in zone_bboxes]
            zone_bounds = (
                min(rect[0] for rect in zone_rects),
                min(rect[1] for rect in zone_rects),
                max(rect[2] for rect in zone_rects),
                max(rect[3] for rect in zone_rects),
            )
            for block in self.document.blocks:
                if block.id in seen or int(block.page) != int(zone.page):
                    continue
                block_rect = _rect(block.bbox)
                if block_rect in zone_rects or _intersects(block_rect, zone_bounds):
                    blocks.append(block)
                    seen.add(block.id)
        return self._sort_reading_order(blocks)

    def _select_blocks_from_selected_zones(self) -> None:
        """Expand the current selection to include blocks covered by selected zones."""

        if self.document is None or not self._selected_zone_ids:
            return
        zone_blocks = self._blocks_from_selected_zones()
        if not zone_blocks:
            return
        self._selected_block_ids = {block.id for block in zone_blocks}
        primary_id = zone_blocks[0].id
        self.viewer.set_selected_blocks(self._selected_block_ids, primary_id)
        self._sync_tree_selection()
        self._update_selection_status()
        self._update_selection_panel()

    def undo_last_merge(self) -> None:
        """Restore the most recent manually merged block set."""

        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return
        if not self._last_manual_merge:
            QMessageBox.information(self, "Nothing to undo", "No manual merge is available to undo.")
            return

        merged_id = str(self._last_manual_merge["merged_id"])
        original_blocks = list(self._last_manual_merge["original_blocks"])
        merged_block = replace(self._last_manual_merge["merged_block"])
        self.document.blocks = [block for block in self.document.blocks if block.id != merged_id]
        self.document.blocks.extend(original_blocks)
        self.document.blocks.sort(key=lambda block: (block.page, block.y, block.x))
        self._selected_block_ids = {block.id for block in original_blocks}
        self._redo_manual_merge = {
            "merged_id": merged_id,
            "original_blocks": [replace(block) for block in original_blocks],
            "merged_block": merged_block,
        }
        self._last_manual_merge = None

        self._update_document_model()
        self.viewer.set_blocks([asdict(block) for block in self.document.blocks])
        self.viewer.set_zones([asdict(zone) for zone in self.document.zones])
        self._populate_tree()
        self.viewer.set_selected_blocks(self._selected_block_ids)
        self._update_selection_status()
        self._update_selection_panel()
        self._log(f"Restored {len(original_blocks)} blocks from {merged_id}")

    def undo_last_action(self) -> None:
        """Undo the latest user-editable action, with merge undo as fallback."""

        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return
        self._active_zone_move_undo_id = None
        if not self._undo_stack:
            if self._last_manual_merge:
                self.undo_last_merge()
                return
            QMessageBox.information(self, "Nothing to undo", "No user action is available to undo.")
            return

        action = self._undo_stack.pop()
        action_type = str(action.get("type", ""))
        if action_type == "zone_create":
            zone_id = str(action.get("zone_id", ""))
            self.document.zones = [zone for zone in self.document.zones if zone.id != zone_id]
            if self._selected_zone_id == zone_id:
                self._selected_zone_id = None
                self.props.set_role(None)
            self.viewer.set_zones([asdict(entry) for entry in self.document.zones])
            self.viewer.set_selected_zone(self._selected_zone_id)
            self._log(f"Undid created zone {zone_id}")
            return
        if action_type == "zone_merge":
            merged_zone_id = str(action.get("merged_zone_id", ""))
            original_zones = action.get("original_zones", [])
            if isinstance(original_zones, list):
                self.document.zones = [zone for zone in self.document.zones if zone.id != merged_zone_id]
                for snapshot in original_zones:
                    if isinstance(snapshot, dict):
                        self._restore_zone_snapshot(snapshot)
                self._selected_zone_ids = {
                    str(snapshot.get("id"))
                    for snapshot in original_zones
                    if isinstance(snapshot, dict) and snapshot.get("id")
                }
                self.viewer.set_selected_zones(self._selected_zone_ids)
                self._log(f"Undid merged zone {merged_zone_id}")
            return
        if action_type == "zone_update":
            snapshot = action.get("before")
            if isinstance(snapshot, dict):
                restored = self._restore_zone_snapshot(snapshot)
                if restored:
                    self._log(f"Undid zone edit {restored.id}")
            return

    def _push_zone_update_undo(self, zone: DocumentZone) -> None:
        self._undo_stack.append({"type": "zone_update", "before": self._snapshot_zone(zone)})

    def _snapshot_zone(self, zone: DocumentZone) -> dict[str, object]:
        return {
            "id": zone.id,
            "page": int(zone.page),
            "label": zone.label,
            "bbox": [float(value) for value in zone.bbox],
            "source": zone.source,
            "bboxes": [[float(value) for value in bbox] for bbox in getattr(zone, "bboxes", [])],
        }

    def _restore_zone_snapshot(self, snapshot: dict[str, object]) -> DocumentZone | None:
        if self.document is None:
            return None
        zone_id = str(snapshot.get("id", ""))
        if not zone_id:
            return None
        bbox = snapshot.get("bbox", [0.0, 0.0, 0.0, 0.0])
        if not isinstance(bbox, list) or len(bbox) != 4:
            return None
        zone = next((entry for entry in self.document.zones if entry.id == zone_id), None)
        if zone is None:
            zone = DocumentZone(
                id=zone_id,
                page=int(snapshot.get("page", 1)),
                label=str(snapshot.get("label", "custom")),
                bbox=[float(value) for value in bbox],
                source=str(snapshot.get("source", "user")),
                bboxes=[
                    [float(value) for value in entry]
                    for entry in snapshot.get("bboxes", [])
                    if isinstance(entry, list) and len(entry) == 4
                ],
            )
            self.document.zones.append(zone)
        else:
            zone.page = int(snapshot.get("page", zone.page))
            zone.label = str(snapshot.get("label", zone.label))
            zone.bbox = [float(value) for value in bbox]
            zone.source = str(snapshot.get("source", zone.source))
            zone.bboxes = [
                [float(value) for value in entry]
                for entry in snapshot.get("bboxes", [])
                if isinstance(entry, list) and len(entry) == 4
            ]
        self._selected_zone_id = zone.id
        self._selected_block_ids.clear()
        self.viewer.set_selected_blocks(set())
        self.viewer.set_zones([asdict(entry) for entry in self.document.zones])
        self.viewer.set_selected_zone(zone.id)
        self._sync_zone_role_to_panel(zone)
        return zone

    def _merge_selected_regions(self) -> None:
        """Merge selected editable zones and/or blocks into one user zone."""

        if self.document is None:
            return
        selected_zones = [zone for zone in self.document.zones if zone.id in self._selected_zone_ids]
        selected_blocks = [block for block in self.document.blocks if block.id in self._selected_block_ids]
        if len(selected_zones) + len(selected_blocks) < 2:
            QMessageBox.information(self, "Select items", "Select at least two blocks or zones to merge.")
            return
        pages = {int(zone.page) for zone in selected_zones}
        pages.update(int(block.page) for block in selected_blocks)
        if len(pages) != 1:
            QMessageBox.information(self, "Same page only", "Only zones on the same page can be merged.")
            return

        selected_zones.sort(key=lambda zone: (zone.page, zone.bbox[1], zone.bbox[0]))
        selected_blocks.sort(key=lambda block: (block.page, block.y, block.x))
        all_bboxes = []
        for zone in selected_zones:
            zone_bboxes = getattr(zone, "bboxes", []) or [zone.bbox]
            all_bboxes.extend(zone_bboxes)
        all_bboxes.extend(block.bbox for block in selected_blocks)
        label = selected_zones[0].label if selected_zones else selected_blocks[0].role
        merged_zone = DocumentZone(
            id=f"zone_user_merged_{len(self.document.zones) + 1:03d}",
            page=next(iter(pages)),
            label=label,
            bbox=[
                min(bbox[0] for bbox in all_bboxes),
                min(bbox[1] for bbox in all_bboxes),
                max(bbox[2] for bbox in all_bboxes),
                max(bbox[3] for bbox in all_bboxes),
            ],
            source="user",
            bboxes=[[float(value) for value in bbox] for bbox in all_bboxes],
        )
        self._undo_stack.append(
            {
                "type": "zone_merge",
                "merged_zone_id": merged_zone.id,
                "original_zones": [self._snapshot_zone(zone) for zone in selected_zones],
            }
        )
        removed_ids = {zone.id for zone in selected_zones}
        self.document.zones = [zone for zone in self.document.zones if zone.id not in removed_ids]
        self.document.zones.append(merged_zone)
        self._selected_block_ids.clear()
        self._selected_zone_id = merged_zone.id
        self._selected_zone_ids = {merged_zone.id}
        self.viewer.set_selected_blocks(set())
        self.viewer.set_zones([asdict(entry) for entry in self.document.zones])
        self.viewer.set_selected_zones(self._selected_zone_ids, merged_zone.id)
        self._sync_zone_role_to_panel(merged_zone)
        self._update_selection_status()
        self._update_selection_panel()
        self._log(f"Merged {len(selected_blocks)} block(s) and {len(selected_zones)} zone(s) into {merged_zone.id}")

    def redo_last_merge(self) -> None:
        """Reapply the most recently undone manual merge."""

        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return
        if not self._redo_manual_merge:
            QMessageBox.information(self, "Nothing to redo", "No undone merge is available to redo.")
            return

        original_blocks = list(self._redo_manual_merge["original_blocks"])
        merged_block = replace(self._redo_manual_merge["merged_block"])
        removed_ids = {block.id for block in original_blocks}
        self.document.blocks = [block for block in self.document.blocks if block.id not in removed_ids]
        self.document.blocks.append(merged_block)
        self.document.blocks.sort(key=lambda block: (block.page, block.y, block.x))
        self._selected_block_ids = {merged_block.id}
        self._last_manual_merge = {
            "merged_id": merged_block.id,
            "original_blocks": [replace(block) for block in original_blocks],
            "merged_block": replace(merged_block),
        }
        self._redo_manual_merge = None

        self._update_document_model()
        self.viewer.set_blocks([asdict(block) for block in self.document.blocks])
        self.viewer.set_zones([asdict(zone) for zone in self.document.zones])
        self._populate_tree()
        self.viewer.set_selected_blocks(self._selected_block_ids, merged_block.id)
        self._update_selection_status()
        self._update_selection_panel()
        self._log(f"Reapplied merge into {merged_block.id}")

    def _merge_blocks(self, blocks, merge_source: str = "manual", join_with_space: bool = False) -> object:
        first = blocks[0]
        text_parts = [block.text.strip() for block in blocks if block.text.strip()]
        text = " ".join(text_parts) if join_with_space else "\n".join(text_parts)
        bbox = [
            min(block.bbox[0] for block in blocks),
            min(block.bbox[1] for block in blocks),
            max(block.bbox[2] for block in blocks),
            max(block.bbox[3] for block in blocks),
        ]
        metadata = dict(first.metadata)
        metadata["merged_block_ids"] = [block.id for block in blocks]
        source_line_ids: list[str] = []
        for block in blocks:
            source_line_ids.extend(str(value) for value in block.metadata.get("source_line_ids", [block.id]))
        metadata["source_line_ids"] = list(dict.fromkeys(source_line_ids))
        metadata["merge_count"] = len(blocks)
        metadata["merge_source"] = merge_source
        metadata["is_paragraph_unit"] = merge_source in {"manual", "paragraph_continuation"}
        if merge_source == "paragraph_continuation":
            metadata["continuation_from"] = [block.id for block in blocks[:-1]]
            metadata["continuation_to"] = blocks[-1].id
        return replace(
            first,
            id=f"merged_{first.page:03d}_{len(self.document.blocks):03d}",
            text=text,
            bbox=bbox,
            x=min(block.x for block in blocks),
            y=min(block.y for block in blocks),
            width=bbox[2] - bbox[0],
            height=bbox[3] - bbox[1],
            confidence=0.0,
            role="unclassified",
            metadata=metadata,
        )

    def _select_block_by_id(self, block_id: str, preserve_selection: bool = False) -> None:
        if self.document is None:
            return
        block = next((b for b in self.document.blocks if b.id == block_id), None)
        if block is None:
            return
        if preserve_selection:
            self.viewer.set_selected_blocks(self._selected_block_ids, block_id)
        else:
            self._selected_block_ids = {block_id}
            self.viewer.set_selected_blocks(self._selected_block_ids, block_id)
            self._update_selection_status()
        self.props.fields["Text"].setText(block.text)
        self.props.fields["Font"].setText(block.font_name or "-")
        self.props.fields["Font Size"].setText(f"{block.font_size:.1f}")
        self.props.fields["Coordinates"].setText(str(block.bbox))
        self.props.fields["Page"].setText(str(block.page))
        self.props.fields["Confidence"].setText(f"{block.confidence:.2f}")
        self.props.fields["Detected Role"].setText(block.role)
        self.props.set_role(block.role)
        self._update_merged_details(block)

    def _on_viewer_block_selected(self, block_id: str, additive: bool) -> None:
        if self.document is None:
            return
        if not additive:
            self._selected_zone_id = None
            self._selected_zone_ids.clear()
            self.viewer.set_selected_zones(set())
        if additive:
            if block_id in self._selected_block_ids:
                self._selected_block_ids.remove(block_id)
            else:
                self._selected_block_ids.add(block_id)
        else:
            self._selected_block_ids = {block_id}
        self.viewer.set_selected_blocks(self._selected_block_ids, block_id if block_id in self._selected_block_ids else None)
        self.viewer.set_selected_zones(self._selected_zone_ids, self._selected_zone_id)
        self._sync_tree_selection()
        self._update_selection_status()
        self._update_selection_panel()
        if block_id in self._selected_block_ids:
            self._select_block_by_id(block_id, preserve_selection=True)
        else:
            self.props.set_role(None)
        self._update_html_preview()
        self.html_preview.scrollToAnchor(self._html_preview_anchor_for_block(block_id))

    def _html_preview_anchor_for_block(self, block_id: str) -> str:
        """Return the preview location for a source block or its continued group."""

        for group in self._html_preview_merges:
            if block_id in group:
                return f"continued-{'-'.join(group)}"
        return f"block-{block_id}"

    def _sync_tree_selection(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        for index in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(index)
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                block_id = child.data(0, Qt.UserRole)
                if block_id and str(block_id) in self._selected_block_ids:
                    child.setSelected(True)
        self.tree.blockSignals(False)

    def _update_selection_status(self) -> None:
        block_count = len(self._selected_block_ids)
        zone_count = len(self._selected_zone_ids)
        self.tree_hint.setText(
            f"Selected blocks: {block_count}; zones: {zone_count}. Use Ctrl-click in the tree or PDF view to select multiple."
        )

    def _update_selection_panel(self) -> None:
        """Show the exact blocks currently queued for a merge."""

        if self.document is None:
            self.selection_hint.setText("Load a PDF to start selecting blocks.")
            self.selection_list.clear()
            return
        selected_blocks = [block for block in self.document.blocks if block.id in self._selected_block_ids]
        selected_zones = [zone for zone in self.document.zones if zone.id in self._selected_zone_ids]
        selected_blocks.sort(key=lambda block: (block.page, block.y, block.x))
        selected_zones.sort(key=lambda zone: (zone.page, zone.bbox[1], zone.bbox[0]))
        self.selection_list.clear()
        if not selected_blocks and not selected_zones:
            self.selection_hint.setText("Merge selection is empty.")
            return
        self.selection_hint.setText(f"{len(selected_blocks)} block(s), {len(selected_zones)} zone(s) selected for merging.")
        for block in selected_blocks:
            snippet = block.text.strip().replace("\n", " ")
            label = f"{block.id} | p{block.page} | {block.role} | {snippet[:70]}"
            self.selection_list.addItem(QListWidgetItem(label))
        for zone in selected_zones:
            label = f"{zone.id} | p{zone.page} | {zone.label} | {zone.bbox}"
            self.selection_list.addItem(QListWidgetItem(label))

    def clear_selection(self) -> None:
        """Clear the current merge selection and synced highlights."""

        self._selected_block_ids.clear()
        self._selected_zone_id = None
        self._selected_zone_ids.clear()
        self.viewer.set_selected_blocks(set())
        self.viewer.set_selected_zone(None)
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        self.tree.blockSignals(False)
        self._update_selection_status()
        self._update_selection_panel()
        self.props.set_merge_details(None)
        self.props.set_role(None)

    def _load_pdf_path(self, path: str) -> None:
        self.document = self.extractor.extract(path)
        self._undo_stack.clear()
        self._html_preview_merges.clear()
        self._active_zone_move_undo_id = None
        results = self.classifier.classify_blocks(self.document.blocks)
        for block, result in zip(self.document.blocks, results, strict=False):
            block.role = result.role
            block.confidence = result.confidence
        self._update_document_model()
        self.viewer.load_pdf(path)
        self.viewer.set_blocks([asdict(block) for block in self.document.blocks])
        self._selected_block_ids.clear()
        self._selected_zone_ids.clear()
        self._selected_zone_id = None
        self._update_selection_status()
        self._update_selection_panel()
        self._populate_tree()
        self._sync_auto_zones()
        self.viewer.set_zones([asdict(zone) for zone in self.document.zones])
        self._update_html_preview()
        report_path = self.config.output_dir / "pipeline_report.json"
        report_path.write_text(json.dumps(self.document.to_pipeline_report(), indent=2, ensure_ascii=False), encoding="utf-8")
        self._log(f"Loaded {path}")
        self._log(f"Detected {len(self.document.blocks)} text blocks")
        self._log(f"Pipeline report written to {report_path}")
        self.props.set_merge_details(None)
        self.props.set_role(None)

    def _update_merged_details(self, block) -> None:
        """Show merge metadata for merged blocks and clear it otherwise."""

        metadata = block.metadata or {}
        merged_ids = metadata.get("merged_block_ids")
        if not merged_ids:
            self.props.set_merge_details(None)
            return
        details = {
            "Block ID": block.id,
            "Merge Count": str(metadata.get("merge_count", len(merged_ids))),
            "Merge Source": str(metadata.get("merge_source", "unknown")),
            "Merged From": ", ".join(str(item) for item in merged_ids),
        }
        self.props.set_merge_details(details)

    def _on_viewer_zone_selected(self, zone_id: str, additive: bool = False) -> None:
        """Select a zone from the PDF viewer."""

        if self.document is None:
            return
        zone = next((entry for entry in self.document.zones if entry.id == zone_id), None)
        if zone is None:
            return
        if int(getattr(zone, "page", 0)) != getattr(self.viewer, "_page_index", 0) + 1:
            return
        if additive:
            if zone_id in self._selected_zone_ids:
                self._selected_zone_ids.remove(zone_id)
            else:
                self._selected_zone_ids.add(zone_id)
        else:
            self._selected_zone_ids = {zone_id}
            self._selected_block_ids.clear()
            self.viewer.set_selected_blocks(set())
        self._selected_zone_id = zone_id if zone_id in self._selected_zone_ids else next(iter(self._selected_zone_ids), None)
        self.viewer.set_selected_zones(self._selected_zone_ids, self._selected_zone_id)
        if self._selected_zone_id:
            active_zone = next((entry for entry in self.document.zones if entry.id == self._selected_zone_id), zone)
            if active_zone.source == "auto":
                active_zone.source = "user"
            self._sync_zone_role_to_panel(active_zone)
            self._select_blocks_from_selected_zones()
        self._update_selection_status()
        self._update_selection_panel()
        self._log(f"Selected {len(self._selected_zone_ids)} zone(s)")

    def _on_viewer_zone_moved(self, zone_id: str, bbox: list[float]) -> None:
        """Persist a dragged zone location back to the document."""

        if self.document is None:
            return
        zone = next((entry for entry in self.document.zones if entry.id == zone_id), None)
        if zone is None:
            return
        if self._active_zone_move_undo_id != zone_id:
            self._push_zone_update_undo(zone)
            self._active_zone_move_undo_id = zone_id
        old_bbox = [float(value) for value in zone.bbox]
        new_bbox = [float(value) for value in bbox]
        dx = new_bbox[0] - old_bbox[0]
        dy = new_bbox[1] - old_bbox[1]
        zone.bbox = [float(value) for value in bbox]
        if getattr(zone, "bboxes", []):
            zone.bboxes = [
                [child_bbox[0] + dx, child_bbox[1] + dy, child_bbox[2] + dx, child_bbox[3] + dy]
                for child_bbox in zone.bboxes
            ]
        if zone.source == "auto":
            zone.source = "user"
        self.viewer.set_zones([asdict(entry) for entry in self.document.zones])
        self.viewer.set_selected_zone(zone_id)
        self._selected_zone_id = zone_id
        self._selected_zone_ids = {zone_id}
        self._sync_zone_role_to_panel(zone)
        self._log(f"Moved zone {zone_id}")

    def _on_viewer_zone_edit_finished(self, zone_id: str) -> None:
        """Allow the next drag/resize of the same zone to create a new undo step."""

        if self._active_zone_move_undo_id == zone_id:
            self._active_zone_move_undo_id = None

    def _on_viewer_zone_created(self, bbox: list[float]) -> None:
        """Create a new user zone from a drawn rectangle."""

        if self.document is None:
            return
        page = getattr(self.viewer, "_page_index", 0) + 1
        zone = DocumentZone(
            id=f"zone_user_{len([z for z in self.document.zones if z.source != 'auto']) + 1:03d}",
            page=page,
            label="custom",
            bbox=[float(value) for value in bbox],
            source="user",
        )
        self.document.zones.append(zone)
        self._undo_stack.append({"type": "zone_create", "zone_id": zone.id})
        self.viewer.set_zones([asdict(entry) for entry in self.document.zones])
        self.viewer.set_selected_zone(zone.id)
        self._selected_zone_id = zone.id
        self._selected_zone_ids = {zone.id}
        self._sync_zone_role_to_panel(zone)
        self._log(f"Created zone {zone.id}")

    def _sync_zone_role_to_panel(self, zone) -> None:
        """Reflect the selected zone role in the inspector."""

        self.props.fields["Detected Role"].setText(zone.label)
        self.props.role_editor.setCurrentText(zone.label)
        self.props.role_editor.setEnabled(True)
        self.props.apply_role_btn.setEnabled(True)

    def apply_selected_block_role(self) -> None:
        """Assign the chosen role to the current selected block."""

        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return
        new_role = self.props.role_editor.currentText().strip().lower()
        if not new_role:
            return

        if self._selected_zone_id:
            zone = next((entry for entry in self.document.zones if entry.id == self._selected_zone_id), None)
            if zone is None:
                return
            if zone.label == new_role and zone.source != "auto":
                return
            self._push_zone_update_undo(zone)
            zone.label = new_role
            if zone.source == "auto":
                zone.source = "user"
            for block in self._blocks_for_zone(zone):
                block.role = new_role
                if new_role == "title":
                    self.document.metadata["manual_title_block_id"] = block.id
                    for other in self.document.blocks:
                        if other.id != block.id and other.role == "title":
                            other.role = "unclassified"
                            self._clear_manual_title_metadata_if_needed(other.id)
            self._update_document_model()
            self.viewer.set_zones([asdict(entry) for entry in self.document.zones])
            self.viewer.set_blocks([asdict(b) for b in self.document.blocks])
            self._populate_tree()
            if len(self._selected_zone_ids) > 1:
                self.viewer.set_selected_zones(self._selected_zone_ids, zone.id)
            else:
                self.viewer.set_selected_zone(zone.id)
            self._sync_zone_role_to_panel(zone)
            self._log(f"Reassigned {zone.id} to role '{new_role}'")
            return

        if len(self._selected_block_ids) != 1:
            QMessageBox.information(self, "Select one block", "Select exactly one block to assign a role.")
            return

        block_id = next(iter(self._selected_block_ids))
        block = next((b for b in self.document.blocks if b.id == block_id), None)
        if block is None:
            return

        if block.role == new_role:
            return

        block.role = new_role
        if new_role == "title":
            self.document.metadata["manual_title_block_id"] = block_id
            for other in self.document.blocks:
                if other.id != block_id and other.role == "title":
                    other.role = "unclassified"
        self._selected_block_ids = {block_id}
        self._selected_zone_id = None
        self._selected_zone_ids.clear()
        self._update_document_model()
        self.viewer.set_blocks([asdict(b) for b in self.document.blocks])
        self.viewer.set_zones([asdict(zone) for zone in self.document.zones])
        self._populate_tree()
        self._sync_tree_selection()
        self.viewer.set_selected_blocks(self._selected_block_ids, block_id)
        self._update_selection_status()
        self._update_selection_panel()
        self._select_block_by_id(block_id, preserve_selection=True)
        self._update_selection_panel()
        self._log(f"Reassigned {block.id} to role '{new_role}'")

    def _blocks_for_zone(self, zone: DocumentZone) -> list:
        """Return blocks whose geometry matches the zone's source regions."""

        if self.document is None:
            return []

        def _rect(bbox: list[float]) -> tuple[float, float, float, float]:
            return tuple(float(value) for value in bbox)  # type: ignore[return-value]

        def _intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
            return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]

        zone_bboxes = [bbox for bbox in (getattr(zone, "bboxes", None) or [zone.bbox]) if isinstance(bbox, list) and len(bbox) == 4]
        if not zone_bboxes:
            return []
        zone_rects = [_rect(bbox) for bbox in zone_bboxes]
        zone_bounds = (
            min(rect[0] for rect in zone_rects),
            min(rect[1] for rect in zone_rects),
            max(rect[2] for rect in zone_rects),
            max(rect[3] for rect in zone_rects),
        )
        matches: list = []
        for block in self.document.blocks:
            if int(block.page) != int(zone.page):
                continue
            block_rect = _rect(block.bbox)
            if block_rect in zone_rects or _intersects(block_rect, zone_bounds):
                matches.append(block)
        return matches

    def _clear_manual_title_metadata_if_needed(self, block_id: str) -> None:
        """Drop the manual-title marker if it no longer points at an active title block."""

        if self.document is None:
            return
        current = str(self.document.metadata.get("manual_title_block_id", ""))
        if current != block_id:
            return
        if not any(block.id == block_id and block.role == "title" for block in self.document.blocks):
            self.document.metadata.pop("manual_title_block_id", None)

    def _sanitize_xml_text(self, value: str | None) -> str:
        """Remove XML-incompatible control characters from extracted text."""

        if value is None:
            return ""
        text = str(value)
        return "".join(ch for ch in text if ch in "\t\n\r" or ord(ch) >= 0x20)
