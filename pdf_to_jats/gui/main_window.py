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
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QTextBrowser,
    QToolButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pdf_to_jats.core.author_linker import AuthorLinker
from pdf_to_jats.core.auto_refiner import AutoRefiner, abstract_number_marker, is_abstract_heading
from pdf_to_jats.core.jats_generator import JATSGenerator
from pdf_to_jats.core.pdf_extractor import PDFExtractor
from pdf_to_jats.core.paragraph_reconstructor import ParagraphReconstructor
from pdf_to_jats.core.semantic_classifier import SemanticClassifier
from pdf_to_jats.core.validator import Validator
from pdf_to_jats.llm.openrouter_client import OpenRouterClient, OpenRouterRateLimitError
from pdf_to_jats.llm.prompts import QUESTION_ANSWER_PROMPT, ROLE_ASSIGNMENT_PROMPT
from pdf_to_jats.gui.pdf_viewer import PDFViewer
from pdf_to_jats.gui.properties_panel import (
    AuthorAffiliationReviewPanel,
    MetadataPanel,
    PropertiesPanel,
)
from pdf_to_jats.gui.structure_tree import StructureTree
from pdf_to_jats.gui.styles import APP_STYLE
from pdf_to_jats.models.block import reading_order_key
from pdf_to_jats.models.document import (
    Document,
    DocumentZone,
    REFINE_ROLES,
    block_matches_segment,
    segment_is_manual,
)
from pdf_to_jats.models.paragraph import Paragraph
from pdf_to_jats.models.segmentation import (
    SOURCE_MANUAL,
    ArticleBoundary,
    Segmentation,
    legacy_segment_boundaries,
)
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
        self.auto_refiner = AutoRefiner()
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
        self._last_refine_report = None
        self._review_block_ids: list[str] = []
        self._review_index = -1
        self._finished_segment_keys: list[str] = []
        self._deleted_segment_keys: list[str] = []
        self._current_segment_key: str | None = None
        # Cached article identities, held together with the block and segment
        # lists they were computed from so a replaced list invalidates them.
        self._segment_identity_cache: tuple | None = None
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
        self.finish_article_btn = QPushButton("Finish Article")
        self.finish_article_btn.setToolTip(
            "Export the current article to XML, mark it finished, and lock its fields."
        )
        self.finish_article_btn.clicked.connect(self.finish_current_article)
        self.reopen_article_btn = QPushButton("Reopen Article")
        self.reopen_article_btn.setToolTip("Unlock a finished article so it can be edited again.")
        self.reopen_article_btn.clicked.connect(self.reopen_current_article)
        auto_pipeline_btn = QPushButton("Auto Pipeline")
        auto_pipeline_btn.setToolTip(
            "Extract, classify, auto-refine, and export XML to the default folder in one click."
        )
        auto_pipeline_btn.clicked.connect(self.run_auto_pipeline)
        self.finish_selection_btn = QPushButton("Finish Selection")
        self.finish_selection_btn.setToolTip(
            "Export the selected lines as one article, lock them, and start the\n"
            "next article at the first line you did not select (Ctrl+Return)."
        )
        self.finish_selection_btn.clicked.connect(self.finish_selected_article)
        review_btn = QPushButton("Next Review Item")
        review_btn.setToolTip("Jump to the next block the auto-refiner flagged for review.")
        review_btn.clicked.connect(self.goto_next_review_item)
        action_buttons = [
            load_btn,
            merge_btn,
            continue_btn,
            classify_btn,
            unmerge_btn,
            redo_btn,
            xml_btn,
            json_btn,
            auto_pipeline_btn,
            self.finish_selection_btn,
            review_btn,
            self.finish_article_btn,
            self.reopen_article_btn,
        ]
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
        self.html_preview.setStyleSheet(
            "QTextBrowser { background-color: #202020; color: #f2f2f2; "
            "border: 1px solid #606060; }"
        )
        self.html_preview.anchorClicked.connect(self._select_preview_block)
        self.html_preview.setPlaceholderText("HTML reading-order preview will appear after loading a PDF.")
        self.tree = StructureTree()
        self.props = PropertiesPanel()
        self.metadata_panel = MetadataPanel()
        self.author_affiliation_review_panel = AuthorAffiliationReviewPanel()
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
        self.metadata_panel.apply_abstract_number_btn.clicked.connect(self.apply_abstract_number)
        self.actions_layout = actions
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_tree_article_menu)
        self.viewer.label.blockSelected.connect(self._on_viewer_block_selected)
        self.viewer.label.blockRangeSelected.connect(self._on_viewer_block_range_selected)
        self.viewer.label.blocksRectSelected.connect(self._on_viewer_blocks_rect_selected)
        self.viewer.label.blockContextRequested.connect(self._show_block_article_menu)
        self.viewer.label.zoneSelected.connect(self._on_viewer_zone_selected)
        self.viewer.label.zoneMoved.connect(self._on_viewer_zone_moved)
        self.viewer.label.zoneCreated.connect(self._on_viewer_zone_created)
        self.viewer.label.zoneEditFinished.connect(self._on_viewer_zone_edit_finished)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self.undo_last_action)
        self.continuation_preview_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.continuation_preview_shortcut.activated.connect(self.show_selected_continuation_arrow)
        # Splitting is the most repeated action in a multi-article PDF, so it is
        # reachable from the keyboard as well as by button and right-click.
        self.split_shortcut = QShortcut(QKeySequence("Ctrl+Shift+N"), self)
        self.split_shortcut.activated.connect(self.split_article_at_selection)
        # Finishing one article and starting the next is the whole multi-article
        # loop, so it is reachable without leaving the keyboard.
        self.finish_selection_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.finish_selection_shortcut.activated.connect(self.finish_selected_article)
        # Processing a PDF of several articles is mostly a sequence of articles,
        # so stepping to the next one should not require aiming at a tab.
        self.next_article_shortcut = QShortcut(QKeySequence("Alt+Right"), self)
        self.next_article_shortcut.activated.connect(lambda: self.select_adjacent_article(1))
        self.previous_article_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        self.previous_article_shortcut.activated.connect(lambda: self.select_adjacent_article(-1))
        viewer_column = QWidget()
        viewer_layout = QVBoxLayout(viewer_column)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.addWidget(self.viewer)
        self.articles_box = QGroupBox("Detected Articles")
        articles_layout = QVBoxLayout(self.articles_box)
        self.articles_hint = QLabel("Load a PDF to see detected articles.")
        self.articles_hint.setWordWrap(True)
        # One tab per detected article keeps the box clean; the merge flow for
        # false splits moves into a dialog from the current article's tab.
        self.articles_tabs = QTabWidget()
        self.articles_tabs.setDocumentMode(True)
        self.articles_tabs.tabBar().setExpanding(True)
        self.articles_tabs.setUsesScrollButtons(True)
        self.articles_tabs.tabBarClicked.connect(self._on_article_tab_clicked)
        self.articles_tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.articles_tabs.customContextMenuRequested.connect(self._show_article_tab_menu)
        self.articles_tabs.setToolTip(
            "Alt+Left / Alt+Right steps through the detected articles."
        )
        self.split_segment_btn = QPushButton("Split Article Here")
        self.split_segment_btn.setToolTip(
            "Start a new article at the selected block (Ctrl+Shift+N). Right-clicking\n"
            "a line in the page does the same thing without selecting it first."
        )
        self.split_segment_btn.setEnabled(False)
        self.split_segment_btn.clicked.connect(self.split_article_at_selection)
        # Folding an article back into its neighbour is the usual repair after a
        # wrong split, so it gets its own button instead of the picker dialog.
        self.merge_previous_btn = QPushButton("Merge Into Previous")
        self.merge_previous_btn.setToolTip(
            "Fold the current article into the one before it, undoing a split that\n"
            "cut an article in two. Ctrl+Z also undoes a split or merge."
        )
        self.merge_previous_btn.setEnabled(False)
        self.merge_previous_btn.clicked.connect(lambda: self.merge_article_with_previous())
        self.merge_segments_btn = QPushButton("Merge Current With...")
        self.merge_segments_btn.setToolTip(
            "Combine the current article with any others that were split by mistake."
        )
        self.merge_segments_btn.setEnabled(False)
        self.merge_segments_btn.clicked.connect(self.merge_selected_segments)
        self.delete_article_btn = QPushButton("Delete Article")
        self.delete_article_btn.setToolTip(
            "Keep the current article visible but leave it out of every export.\n"
            "Right-click its tab and choose Restore to export it again. Ctrl+Z undoes."
        )
        self.delete_article_btn.setEnabled(False)
        self.delete_article_btn.clicked.connect(self.delete_current_article)
        articles_layout.addWidget(self.articles_hint)
        articles_layout.addWidget(self.articles_tabs, 1)
        articles_layout.addWidget(self.split_segment_btn)
        articles_layout.addWidget(self.merge_previous_btn)
        articles_layout.addWidget(self.merge_segments_btn)
        articles_layout.addWidget(self.delete_article_btn)

        selected_column = QWidget()
        selected_layout = QVBoxLayout(selected_column)
        selected_layout.setContentsMargins(0, 0, 0, 0)
        selected_layout.addWidget(self.tree_hint)
        selected_layout.addWidget(self.tree, 1)
        selected_layout.addWidget(self.selection_hint)
        selected_layout.addWidget(self.selection_list)
        selected_layout.addWidget(self.clear_selection_btn)

        preview_page = QWidget()
        preview_layout = QVBoxLayout(preview_page)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self.html_preview)

        # The right side stacks all work areas as tabs so each gets the full
        # pane height instead of competing in one narrow column.
        self.work_tabs = QTabWidget()
        self.work_tabs.setDocumentMode(True)
        self.work_tabs.addTab(self.articles_box, "Detected Articles")
        self.work_tabs.addTab(selected_column, "Selected Blocks")
        self.work_tabs.addTab(self.props, "Properties")
        self.work_tabs.addTab(self.metadata_panel, "Article Metadata")
        self.work_tabs.addTab(
            self.author_affiliation_review_panel, "Author & Affiliation Review"
        )
        self.work_tabs.addTab(preview_page, "Preview")

        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.addWidget(viewer_column)
        self.body_splitter.addWidget(self.work_tabs)
        self.body_splitter.setStretchFactor(0, 3)
        self.body_splitter.setStretchFactor(1, 2)
        self.body_splitter.setChildrenCollapsible(False)
        root.addWidget(self.body_splitter, 1)

        self._refresh_article_buttons()
        self.setAcceptDrops(True)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Processing log will appear here.")
        self.log.setMaximumHeight(140)
        root.addWidget(QLabel("Processing Log"))
        root.addWidget(self.log)

    # Tab indices of the right-side work-area tabs.
    TAB_ARTICLES = 0
    TAB_SELECTED = 1
    TAB_PROPERTIES = 2
    TAB_METADATA = 3
    TAB_AUTHOR_AFFILIATIONS = 4
    TAB_PREVIEW = 5

    def _show_work_tab(self, index: int) -> None:
        """Bring one of the right-side work-area tabs to the front."""

        if hasattr(self, "work_tabs"):
            self.work_tabs.setCurrentIndex(index)

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
            narrow = self.width() < 900
            orientation = Qt.Vertical if narrow else Qt.Horizontal
            if self.body_splitter.orientation() != orientation:
                self.body_splitter.setOrientation(orientation)
                if narrow:
                    self.body_splitter.setSizes([360, 420])
                else:
                    self.body_splitter.setSizes([620, 640])
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
        all_segments = list(export_document.metadata.get("article_segments", []))
        segments = self._exportable_segments(all_segments)
        if not segments and all_segments:
            # Every detected article is deleted or still only a suggestion, and a
            # suggestion is never written out. Exporting the whole document here
            # would resurrect exactly the articles the user has not vouched for.
            self._log(
                "No article is ready to export: every detected article is deleted or "
                "still only a suggestion."
            )
            QMessageBox.information(
                self,
                "Nothing to export",
                "Detection is only a suggestion. Finish an article, or build one from "
                "a selection, before exporting. Deleted articles are always left out.",
            )
            return
        documents = [self._document_for_segment(segment, export_document) for segment in segments]
        if len(documents) <= 1 and len(all_segments) <= 1 and not self._deleted_segment_keys:
            # A PDF with at most one article (or no detected segments at all) has
            # no boundary to confirm, so it keeps the whole-document export. A
            # deleted article must never leak back through this fallback.
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

    def goto_next_review_item(self) -> None:
        """Cycle selection through blocks the auto-refiner flagged for review."""

        if self.document is None or not self._review_block_ids:
            QMessageBox.information(
                self, "Nothing to review", "No blocks are flagged for review."
            )
            return
        by_id = {block.id: block for block in self.document.blocks}
        # Skip blocks whose roles were fixed by hand since the review list built.
        while self._review_index + 1 < len(self._review_block_ids):
            self._review_index += 1
            candidate = by_id.get(self._review_block_ids[self._review_index])
            if candidate is not None:
                break
        else:
            self._review_index = -1
            QMessageBox.information(self, "Review complete", "All review items processed.")
            return
        block_id = candidate.id
        block = by_id.get(block_id)
        if block is None:
            return
        self._selected_zone_id = None
        self._selected_zone_ids.clear()
        self.viewer.set_selected_zones(set())
        self._selected_block_ids = {block_id}
        self.viewer.set_selected_blocks(self._selected_block_ids, block_id)
        self._select_block_by_id(block_id, preserve_selection=False)
        self._sync_tree_selection()
        self._update_selection_status()
        self._update_selection_panel()
        self._update_html_preview()
        self._log(
            f"Review {self._review_index + 1}/{len(self._review_block_ids)}: {block_id} ({block.role})"
        )

    def run_auto_pipeline(self) -> None:
        """One-click pipeline: current PDF through refine + export with no dialogs."""

        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return
        self._update_document_model()
        self._log_link_issues()
        export_document = self._document_with_html_preview_continuations()
        validation_errors = self.validator.validate_document(export_document)
        for error in validation_errors:
            self._log(f"Document validation error: {error.message}")
        all_segments = list(export_document.metadata.get("article_segments", []))
        segments = self._exportable_segments(all_segments)
        if segments:
            documents = [self._document_for_segment(segment, export_document) for segment in segments]
        elif all_segments:
            # Detected articles that are deleted or still only suggestions must
            # not leak back in through a whole-document export.
            documents = []
            self._log(
                "No article is ready to export: finish an article or build one from a "
                "selection, then run the pipeline again."
            )
        else:
            documents = [export_document]
        output_dir = self.config.generated_xml_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_paths: list[Path] = []
        for index, segment_document in enumerate(documents, start=1):
            filename = self._article_filename(segment_document, index, len(documents))
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
        review_count = len(self._review_block_ids)
        if review_count:
            self._log(f"Auto pipeline complete: {review_count} block(s) flagged for review.")
        else:
            self._log("Auto pipeline complete.")

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
                    # A preview continuation is virtual, so its own page and
                    # column come from only its first block.  Preserve every
                    # contributing ID as merge provenance as well: the
                    # per-article exporter uses it to keep this block with the
                    # article that owns any of its source text.
                    "merged_block_ids": source_ids,
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
                if block.role == "abstract" and block.text.strip() and not is_abstract_heading(block.text)
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
        log = getattr(self, "log", None)
        if log is None:
            return
        log.append(message)

    def _update_html_preview(self) -> None:
        """Render blocks in backend reading order as selectable HTML."""

        if self.document is None:
            self.html_preview.clear()
            return
        parts = [
            "<style>body{font-family:Arial;color:#f2f2f2;background:#202020;}"
            "h3{color:#ffffff;}"
            "p{margin:0 0 10px;padding:7px;border:1px solid #606060;color:#f2f2f2;background:#2b2b2b;}"
            ".selected{border:2px solid #4ea1ff;background:#183653;color:#ffffff;}"
            ".continued{border:2px solid #42c767;background:#173d24;color:#ffffff;}"
            ".finished{border:1px dashed #8a8a8a;background:#232323;color:#8f8f8f;}"
            ".finished.selected{border:2px dashed #a0a0a0;background:#2a2a2a;color:#c0c0c0;}"
            ".finished.continued{border:2px dashed #8a8a8a;background:#232323;color:#8f8f8f;}"
            ".deleted{border:1px dotted #7a4a4a;background:#241d1d;color:#7d6b6b;}"
            ".deleted.selected{border:2px dotted #a06a6a;background:#2d2020;color:#9a8a8a;}"
            ".deleted.continued{border:2px dotted #7a4a4a;background:#241d1d;color:#7d6b6b;}"
            ".meta{color:#bdbdbd;font-size:11px;}</style>"
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
        blocks_by_id_all = {block.id: block for block in self.document.blocks}
        for index, (block_ids, text, page, role) in enumerate(preview_items, start=1):
            selected = " selected" if any(block_id in self._selected_block_ids for block_id in block_ids) else ""
            item_locked = any(
                bool((blocks_by_id_all.get(block_id) is not None) and (blocks_by_id_all[block_id].metadata or {}).get("article_locked"))
                for block_id in block_ids
            )
            item_deleted = any(block_id in self._deleted_view_block_ids for block_id in block_ids)
            delete_prefix = "✕ " if item_deleted else ""
            lock_prefix = f"{delete_prefix}🔒 " if item_locked else delete_prefix
            if len(block_ids) == 1:
                anchor = f"block-{block_ids[0]}"
                label = (
                    f'<a name="{escape(anchor)}"></a><a href="select:{escape(block_ids[0])}">'
                    f'<span class="meta">{lock_prefix}{index}. page {page} | {escape(role)}</span></a>'
                )
                css_class = ("deleted " if item_deleted else "finished " if item_locked else "") + selected
            else:
                anchor = f"continued-{'-'.join(block_ids)}"
                label = f'<a name="{escape(anchor)}"></a><span class="meta">{lock_prefix}CONTINUED | {index}. page {page} | {escape(role)}</span>'
                css_class = ("deleted " if item_deleted else "finished " if item_locked else "") + f"continued{selected}"
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

    # ------------------------------------------------------------------
    # Per-article finish / lock workflow
    # ------------------------------------------------------------------

    def _document_segments(self) -> list[dict[str, object]]:
        """Return the article segments detected for the loaded PDF."""

        if self.document is None:
            return []
        segments = self.document.metadata.get("article_segments", [])
        return [segment for segment in segments if isinstance(segment, dict)]
    @staticmethod
    def _segment_locator(segment: dict[str, object]) -> tuple:
        """Value fingerprint used to recognize a segment dict across copies.

        The export document shallow-copies metadata, so its article segments are
        usually the same objects. A caller that hands over an equal dict instead
        is matched back to the stored segment through these fields.
        """

        start_order = segment.get("start_order")
        return (
            int(segment.get("index", 0)),
            str(segment.get("abstract_number", "")),
            int(segment.get("start_page", 0)),
            int(segment.get("end_page", 0)),
            tuple(int(column) for column in segment.get("columns") or []),
            int(start_order) if start_order is not None else -1,
        )

    def _invalidate_segment_identity(self) -> None:
        """Forget cached article identities after the blocks or segments change."""

        self._segment_identity_cache = None

    def _segment_boundary_ids(self) -> list[str | None]:
        """Identity per detected article: the id of the block that starts it.

        An article's index, page range and columns all change when another
        article is inserted before or merged into it, so bookkeeping keyed on
        those fields has to be renumbered after every edit. The block an article
        starts at does not move, which is why it is the identity here. The ids
        come from the boundary model, whose reading stream makes "which article
        owns this block" one lookup instead of a scan.
        """

        segments = self._document_segments()
        if self.document is None or not segments:
            return [None] * len(segments)
        stored = self.document.metadata.get("article_segments")
        cached = self._segment_identity_cache
        if cached is not None and cached[0] is self.document.blocks and cached[1] is stored:
            return cached[2]
        identities = legacy_segment_boundaries(self.document.blocks, segments)
        self._segment_identity_cache = (self.document.blocks, stored, identities)
        return identities

    def _segment_key(self, segment: dict[str, object] | None) -> str | None:
        """Identity of one segment: the block that starts its article."""

        if not segment:
            return None
        identities = self._segment_boundary_ids()
        locator = self._segment_locator(segment)
        for index, candidate in enumerate(self._document_segments()):
            if candidate is segment or self._segment_locator(candidate) == locator:
                return identities[index] if index < len(identities) else None
        return None

    def _segments_with_keys(self) -> list[tuple[dict[str, object], str | None]]:
        return [(segment, self._segment_key(segment)) for segment in self._document_segments()]

    def _current_segment(self) -> tuple[dict[str, object], str | None] | None:
        """Return the segment the user is currently working on, if any."""

        if self._current_segment_key is not None:
            for segment, key in self._segments_with_keys():
                if key == self._current_segment_key:
                    return segment, key
            return None
        # Fall back to the first segment that is neither finished nor deleted,
        # so working state lands on an article an export would still produce.
        for segment, key in self._segments_with_keys():
            if (
                key not in self._finished_segment_keys
                and key not in self._deleted_segment_keys
            ):
                return segment, key
        return None

    def _notify(self, message: str) -> None:
        """Report an outcome without interrupting: log it, echo it in the status bar.

        Working through several articles means many small decisions, and a modal
        box for each of them would have to be dismissed before the next one.
        """

        self._log(message)
        try:
            self.statusBar().showMessage(message, 8000)
        except (AttributeError, RuntimeError):
            # A window assembled without Qt (tests) has no status bar.
            pass

    def _refresh_after_article_change(self) -> None:
        """Re-render every view that mirrors article state and block locking."""

        if self.document is None:
            return
        self._update_document_model()
        self.viewer.set_blocks([asdict(block) for block in self.document.blocks])
        self.viewer.set_zones([asdict(zone) for zone in self.document.zones])
        self._sync_deleted_block_overlay()
        self._populate_tree()
        self._sync_tree_selection()
        self._update_selection_status()
        self._update_html_preview()
        self._update_selection_panel()
        self._refresh_article_buttons()

    def _article_segmentation(self, ordered=None) -> Segmentation | None:
        """Return the current articles as boundaries over the reading stream.

        Edits are expressed on this model rather than on page/column ranges, so
        a cut inside one page and column is exact and every block ends up owned
        by exactly one article.
        """

        if self.document is None:
            return None
        stream = list(ordered) if ordered is not None else self._ensure_reading_orders()
        return Segmentation.from_legacy_segments(
            stream,
            self._document_segments(),
            number=self._normalize_abstract_number(self.document.abstract_number()),
        )

    def _boundary_block_ids(self) -> list[str]:
        """Ids of the blocks that start an article, in reading order."""

        return [block_id for block_id in self._segment_boundary_ids() if block_id]

    def select_adjacent_article(self, offset: int) -> None:
        """Make the next or previous article the working one, and show its page."""

        if self.document is None:
            return
        segments = self._document_segments()
        if not segments:
            self._notify("No article was detected in this PDF.")
            return
        current = self._current_segment()
        target = self._neighbour_segment(current[0], offset) if current is not None else None
        if target is None:
            target = segments[0] if offset > 0 else segments[-1]
        self._current_segment_key = self._segment_key(target)
        self._refresh_article_buttons()
        self.viewer.render_page(max(0, int(target.get("start_page", 1)) - 1))
        # Keep the Selected Blocks panel on the article being stepped through.
        self._refresh_selected_blocks_view()
        number = str(target.get("abstract_number", "")).strip() or "ABSN"
        self._notify(
            f"Working on article {target.get('index', '?')} of {len(segments)} "
            f"(ABSN {number}, page {target.get('start_page')})."
        )

    def _segment_for_key(self, key: str | None) -> dict[str, object] | None:
        """Return the article that starts at a block, if any."""

        if not key:
            return None
        for segment, segment_key in self._segments_with_keys():
            if segment_key == key:
                return segment
        return None

    def _segment_index(self, segment: dict[str, object]) -> int:
        """Position of one article in the stored list."""

        segments = self._document_segments()
        for index, candidate in enumerate(segments):
            if candidate is segment:
                return index
        locator = self._segment_locator(segment)
        for index, candidate in enumerate(segments):
            if self._segment_locator(candidate) == locator:
                return index
        return -1

    def _neighbour_segment(self, segment: dict[str, object], offset: int) -> dict[str, object] | None:
        """Return the article ``offset`` tabs away from one, if it exists."""

        position = self._segment_index(segment) + offset
        segments = self._document_segments()
        if 0 <= position < len(segments):
            return segments[position]
        return None

    def _neighbour_boundary_key(self, key: str | None, offset: int) -> str | None:
        """Boundary block id of the article ``offset`` positions away from one."""

        boundaries = self._boundary_block_ids()
        if not key or key not in boundaries:
            return None
        position = boundaries.index(key) + offset
        if 0 <= position < len(boundaries):
            return boundaries[position]
        return None

    def _push_article_undo(self, label: str) -> None:
        """Snapshot the articles so Ctrl+Z can undo a split, merge, or rename.

        Splitting and merging are the two edits a person repeats while working
        through a PDF, so both have to be reversible without reloading the file.
        """

        stack = getattr(self, "_undo_stack", None)
        if stack is None or self.document is None:
            return
        stack.append(
            {
                "type": "article_segments",
                "label": label,
                "before": [dict(segment) for segment in self._document_segments()],
                "current_key": getattr(self, "_current_segment_key", None),
                "finished_keys": list(getattr(self, "_finished_segment_keys", [])),
                "deleted_keys": list(getattr(self, "_deleted_segment_keys", [])),
            }
        )

    def _name_articles_from_markers(self, segmentation: Segmentation, ordered) -> None:
        """Give every unnamed article the number its first line prints, if any.

        A person drawing a boundary has already decided where an article starts,
        so the bare marker these conference PDFs print ("P-605") is read here
        even though detection needs a label to trust it.
        """

        by_id = {block.id: block for block in ordered}
        for position, boundary in enumerate(list(segmentation.boundaries)):
            if str(boundary.number).strip():
                continue
            block = by_id.get(boundary.block_id)
            if block is None:
                continue
            number = self._manual_article_number(block)
            if not number:
                continue
            segmentation.boundaries[position] = ArticleBoundary(
                block_id=boundary.block_id,
                source=boundary.source,
                number=number,
                marker_block_id=block.id,
                deleted=boundary.deleted,
            )

    def _report_segmentation_issues(self, segmentation: Segmentation) -> None:
        """Log what the boundary model had to correct instead of hiding it."""

        for issue in segmentation.issues:
            self._log(f"Segmentation: {issue}")

    def _article_locked(self, block) -> bool:
        """A block is locked when it was finished as part of an article.

        Merged blocks inherit the flag through their first source block's
        metadata, and mixing locked with unlocked blocks in one merge is
        rejected by the merge guard, so the flag alone is authoritative.
        """

        return bool(block.metadata.get("article_locked"))

    def _locked_selection_blocks(self, selected_blocks) -> list:
        """Return the subset of selected blocks that are finished."""

        return [block for block in selected_blocks if self._article_locked(block)]

    def _warn_locked_blocks(self, locked_blocks) -> None:
        QMessageBox.information(
            self,
            "Article finished",
            f"{len(locked_blocks)} selected block(s) belong to a finished article.\n"
            "Use Reopen Article to unlock it before editing.",
        )

    def _export_segment_document(self, segment: dict[str, object], output_dir: Path) -> Path:
        """Generate the XML for one article segment without dialogs."""

        self._update_document_model()
        export_document = self._document_with_html_preview_continuations()
        segment_document = self._document_for_segment(segment, export_document)
        index_value = segment.get("index")
        try:
            index = int(index_value) if index_value is not None else 0
        except (TypeError, ValueError):
            index = 0
        filename = self._article_filename(segment_document, index, 1, always_number=index > 0)
        output_path = output_dir / filename
        generated_path = self.generator.generate(segment_document, output_path)
        errors = self.validator.validate(generated_path)
        if errors:
            for error in errors:
                self._log(f"Validation error in {generated_path.name}: {error.message}")
        else:
            self._log(f"XML written to {generated_path}")
        return generated_path

    @staticmethod
    def _article_filename(
        document: Document, index: int, total: int, *, always_number: bool = False
    ) -> str:
        """Name an exported article after its Abstract No when it prints one.

        A folder of article_P-605.xml files can be matched to the proceedings
        entries at a glance, which article_001.xml cannot.
        """

        number = str(document.metadata.get("abstract_number", "")).strip()
        if number and number.upper() != "ABSN":
            return f"article_{number}.xml"
        if total > 1 or always_number:
            return f"article_{index:03d}.xml"
        return "article.xml"

    def _lock_blocks(self, blocks, key: str | None) -> int:
        """Flag the given blocks as belonging to a finished article.

        Locking is per block, so a selection-based finish can lock exactly the
        lines the user exported instead of every role-carrying line of a range.
        """

        locked_count = 0
        for block in blocks:
            metadata = dict(block.metadata)
            if metadata.get("article_locked"):
                continue
            metadata["article_locked"] = True
            block.metadata.clear()
            block.metadata.update(metadata)
            locked_count += 1
        if key is not None and key not in self._finished_segment_keys:
            self._finished_segment_keys.append(key)
        return locked_count

    def _lock_segment_blocks(self, segment: dict[str, object], key: str | None) -> int:
        """Lock the worked-on fields (title, authors, affiliations, abstract).

        Only blocks inside the segment that carry a classified role are locked;
        unclassified and noise blocks stay editable. Merged blocks are locked
        through their source block's inherited metadata.
        """

        if self.document is None:
            return 0
        role_blocks = [
            block
            for block in self.document.blocks
            if block_matches_segment(block, segment) and block.role in REFINE_ROLES
        ]
        # A segment that owns no block of its own still has an identity to lock.
        return self._lock_blocks(role_blocks, key)

    def _unlock_segment_blocks(self, segment: dict[str, object], key: str | None) -> int:
        """Remove the finished/locked state from a segment's blocks."""

        if self.document is None:
            return 0
        unlocked_count = 0
        for block in self.document.blocks:
            if not block_matches_segment(block, segment):
                continue
            metadata = dict(block.metadata)
            if not metadata.pop("article_locked", False):
                continue
            block.metadata.clear()
            block.metadata.update(metadata)
            unlocked_count += 1
        if key is not None and key in self._finished_segment_keys:
            self._finished_segment_keys.remove(key)
        return unlocked_count

    def finish_current_article(self) -> None:
        """Export the current article's XML, then lock its fields."""

        if self.document is None:
            self._notify("Load a PDF first.")
            return
        current = self._current_segment()
        if current is None:
            self._notify(
                "No unfinished article was detected in this PDF. Articles are identified by "
                "their abstract numbers."
            )
            return
        segment, key = current
        if key in self._finished_segment_keys:
            self._notify("This article is already finished.")
            return
        if key in self._deleted_segment_keys:
            self._notify("This article is deleted; restore it before finishing it.")
            return
        confirm = QMessageBox.question(
            self,
            "Finish article",
            (
                f"Finish article {segment.get('abstract_number', '')!r} "
                f"(pages {segment.get('start_page')}-{segment.get('end_page')})?\n\n"
                "Its XML will be exported and its fields locked."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return
        # A full document can validate while this detected article is missing
        # content. Validate the segment before writing or locking it, so both
        # finish paths share one gate.
        try:
            generated_path, validation_errors = self._export_article_xml(segment)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", f"The article XML could not be written: {exc}")
            return
        if validation_errors:
            messages = "\n".join(f"• {error.message}" for error in validation_errors)
            self._log(
                f"Cannot finish article {segment.get('abstract_number', '')}: "
                f"{'; '.join(error.message for error in validation_errors)}"
            )
            QMessageBox.warning(
                self,
                "Article needs review",
                "The article was not exported or locked because it is incomplete:\n\n" + messages,
            )
            return
        locked_count = self._lock_segment_blocks(segment, key)
        self._current_segment_key = self._next_unfinished_segment_key()
        self._refresh_after_article_change()
        # Move the page on to the article that comes next, so finishing one is a
        # step towards the next instead of a trip back to the tab bar.
        following = self._segment_for_key(self._current_segment_key)
        if following is not None:
            self.viewer.render_page(max(0, int(following.get("start_page", 1)) - 1))
        self._notify(
            f"Finished article {segment.get('abstract_number', '')}: exported "
            f"{generated_path.name}; locked {locked_count} block(s)."
        )

    def _export_article_xml(self, segment: dict[str, object]) -> tuple[Path | None, list]:
        """Validate and write one article's XML, without any dialogs.

        Returns the written path and the validation errors. An article with
        errors is never written, which is the gate both finish actions share.
        """

        self._update_document_model()
        export_document = self._document_with_html_preview_continuations()
        segment_document = self._document_for_segment(segment, export_document)
        validation_errors = self.validator.validate_document(segment_document)
        if validation_errors:
            return None, validation_errors
        output_dir = self.config.generated_xml_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        return self._export_segment_document(segment, output_dir), []

    def _selected_contiguous_run(self) -> list | None:
        """Return the selected blocks when they form one run in reading order.

        Finishing a selection turns it into an article, and an article is a slice
        of the reading stream. A selection with a gap between two lines is not one
        slice, so it is refused here rather than exported with a hole in it.
        """

        if self.document is None:
            self._notify("Load a PDF first.")
            return None
        if not self._selected_block_ids:
            self._notify("Select the lines that make up the article first.")
            return None
        ordered = self._ensure_reading_orders()
        positions = {block.id: index for index, block in enumerate(ordered)}
        if not set(self._selected_block_ids).issubset(positions):
            self._notify("The selection refers to a block that is no longer in the document.")
            return None
        selected = [block for block in ordered if block.id in self._selected_block_ids]
        first = positions[selected[0].id]
        if [positions[block.id] for block in selected] != list(range(first, first + len(selected))):
            self._notify(
                "Select a contiguous run of lines: finishing an article exports one "
                "slice of the reading order, not scattered lines."
            )
            return None
        locked = self._locked_selection_blocks(selected)
        if locked:
            self._warn_locked_blocks(locked)
            return None
        return selected

    def _create_article_from_selection(self, selected: list) -> str | None:
        """Bound the selected run as an article and return the block that starts it.

        Two boundaries express the cut: one starts the article at the selection's
        first line, and one starts the article after it at the first line that was
        not selected. Detection is only a suggestion, so this overrides whichever
        article happened to own those lines before.
        """

        if self.document is None:
            return None
        ordered = self._ensure_reading_orders()
        segmentation = self._article_segmentation(ordered)
        if segmentation is None:
            return None
        ids = [block.id for block in ordered]
        positions = {block_id: index for index, block_id in enumerate(ids)}
        first_position = positions[selected[0].id]
        last_position = positions[selected[-1].id]
        self._push_article_undo("finish selection")
        if first_position > 0:
            segmentation.insert_boundary(selected[0].id, source=SOURCE_MANUAL)
        after_position = last_position + 1
        if after_position < len(ids):
            segmentation.insert_boundary(ids[after_position], source=SOURCE_MANUAL)
        # Name each article from the marker line it starts with, when it prints one.
        self._name_articles_from_markers(segmentation, ordered)
        self._replace_article_segments(segmentation.to_legacy_segments(self.document.blocks))
        self._report_segmentation_issues(segmentation)
        self._current_segment_key = selected[0].id
        return selected[0].id

    def _rollback_article_change(self, snapshot: dict[str, object] | None) -> None:
        """Put the articles back after a finish that was refused.

        A finish that fails validation or export must not leave the boundary edit
        it made behind: the selection is not an article yet, so the snapshot taken
        just before the boundaries were written is restored in place.
        """

        if not snapshot or self.document is None:
            return
        stack = getattr(self, "_undo_stack", None)
        if stack and stack[-1] is snapshot:
            stack.pop()
        before = snapshot.get("before")
        if not isinstance(before, list):
            return
        self.document.metadata["article_segments"] = [
            dict(segment) for segment in before if isinstance(segment, dict)
        ]
        self._invalidate_segment_identity()
        self._finished_segment_keys = [str(key) for key in (snapshot.get("finished_keys") or [])]
        self._deleted_segment_keys = [str(key) for key in (snapshot.get("deleted_keys") or [])]
        known = {key for key in self._segment_boundary_ids() if key}
        current_key = snapshot.get("current_key")
        self._current_segment_key = str(current_key) if current_key in known else None

    def finish_selected_article(self) -> None:
        """Export exactly the selected lines as an article, lock them, and move on.

        Selection is the primary way an article is defined: pick the run of lines
        that makes up one paper, assign their roles, and finish it. Detection is
        only a suggestion; the exported article is the selection itself, and the
        next article starts at the first line that was not selected.
        """

        if self.document is None:
            self._notify("Load a PDF first.")
            return
        selected = self._selected_contiguous_run()
        if selected is None:
            return
        stack = getattr(self, "_undo_stack", None)
        depth = len(stack) if stack is not None else 0
        key = self._create_article_from_selection(selected)
        snapshot = stack[-1] if stack and len(stack) > depth else None
        segment = self._segment_for_key(key) if key else None
        if segment is None:
            self._rollback_article_change(snapshot)
            self._notify("The selection could not be turned into an article.")
            return
        try:
            generated_path, validation_errors = self._export_article_xml(segment)
        except Exception as exc:
            self._rollback_article_change(snapshot)
            self._notify(f"The article XML could not be written: {exc}")
            return
        if validation_errors:
            # A refused finish must not leave extra article splits behind.
            self._rollback_article_change(snapshot)
            self._log(
                f"Cannot finish article {segment.get('abstract_number', '')}: "
                f"{'; '.join(error.message for error in validation_errors)}"
            )
            for error in validation_errors:
                self._log(f"Article validation error: {error.message}")
            self._notify(
                "The selection was not exported or locked because the article is "
                "incomplete; assign roles to the missing fields and finish again."
            )
            return
        locked_count = self._lock_blocks(selected, key)
        # The next article starts at the first line that was not selected, so the
        # working article follows the selection instead of jumping back to the top.
        ordered = self._ensure_reading_orders()
        ids = [block.id for block in ordered]
        position = ids.index(selected[-1].id)
        following_key = ids[position + 1] if position + 1 < len(ids) else None
        self._current_segment_key = following_key or self._next_unfinished_segment_key()
        self._refresh_after_article_change()
        following = self._segment_for_key(self._current_segment_key)
        viewer = getattr(self, "viewer", None)
        if following is not None and viewer is not None:
            viewer.render_page(max(0, int(following.get("start_page", 1)) - 1))
        number = str(segment.get("abstract_number", "")).strip() or "ABSN"
        exported_name = generated_path.name if generated_path is not None else ""
        where = (
            f"next article starts on page {following.get('start_page')}."
            if following is not None
            else "the selection was the last article in the document."
        )
        self._notify(
            f"Finished article {number}: exported {exported_name}; locked "
            f"{locked_count} block(s); {where}"
        )

    def reopen_current_article(self) -> None:
        """Unlock the most recently finished article for further editing."""

        if self.document is None:
            self._notify("Load a PDF first.")
            return
        candidates = [
            (segment, key)
            for segment, key in self._segments_with_keys()
            if key in self._finished_segment_keys
        ]
        if not candidates:
            self._notify("No finished article is available to reopen.")
            return
        segment, key = candidates[-1]
        self._reopen_segment(segment, key)

    def reopen_article(self, segment: dict[str, object] | None = None) -> bool:
        """Unlock one named article again, from its own tab or menu.

        The button reopens the last finished article; a multi-article PDF needs
        any of them to be reachable, not just the most recent one.
        """

        if self.document is None:
            self._notify("Load a PDF first.")
            return False
        if segment is None:
            self._notify("Select an article tab before reopening one.")
            return False
        key = self._segment_key(segment)
        if key is None or key not in self._finished_segment_keys:
            self._notify("That article is not finished.")
            return False
        self._reopen_segment(segment, key)
        return True

    def _reopen_segment(self, segment: dict[str, object], key: str | None) -> int:
        """Unlock a finished article's blocks and refresh the views."""

        unlocked_count = self._unlock_segment_blocks(segment, key)
        if self._current_segment_key == key:
            self._current_segment_key = None
        self._refresh_after_article_change()
        self._notify(
            f"Reopened article {segment.get('abstract_number', '')}; "
            f"unlocked {unlocked_count} block(s)."
        )
        return unlocked_count

    def _next_unfinished_segment_key(self) -> str | None:
        for _segment, key in self._segments_with_keys():
            if (
                key not in self._finished_segment_keys
                and key not in self._deleted_segment_keys
            ):
                return key
        return None

    def _is_segment_deleted(self, segment: dict[str, object]) -> bool:
        """Whether an article is deleted, by key or its stored boundary flag."""

        return (
            self._segment_key(segment) in self._deleted_segment_keys
            or bool(segment.get("deleted"))
        )

    def _sync_deleted_block_overlay(self) -> None:
        """Mirror the deleted articles on the page overlay."""

        viewer = getattr(self, "viewer", None)
        if viewer is not None:
            viewer.set_deleted_block_ids(self._deleted_view_block_ids)

    @property
    def _deleted_view_block_ids(self) -> frozenset[str]:
        """Blocks of deleted articles, for the page overlay and HTML preview.

        Membership is resolved through the boundary model rather than stored, so
        the set survives splits and merges exactly like the finish/restore
        bookkeeping does.
        """

        if self.document is None or not self._deleted_segment_keys:
            return frozenset()
        deleted = set(self._deleted_segment_keys)
        return frozenset(
            block.id
            for segment, key in self._segments_with_keys()
            if key in deleted
            for block in self.document.blocks
            if block_matches_segment(block, segment)
        )

    def _segment_has_manual_boundary(self, segment: dict[str, object]) -> bool:
        """Whether an article's start boundary was drawn by hand."""

        declared = str(segment.get("boundary_source") or "").strip()
        if declared:
            return declared == SOURCE_MANUAL
        # Legacy dicts predate boundary_source; reading-order bounds there are
        # the sign of a hand-made cut.
        return segment_is_manual(segment)

    def _segment_is_confirmed(
        self,
        segment: dict[str, object],
        segments: list[dict[str, object]],
        index: int,
    ) -> bool:
        """Whether the user has vouched for an article boundary.

        Detection only suggests boundaries, and a suggestion must not be
        exported: a proceedings PDF would dump every guessed article into the
        output before anyone read it. An article is confirmed once the user
        finishes it or cuts it by hand. A hand-made cut confirms both neighbours,
        because deciding that a new article starts there also settles where the
        previous one ends.
        """

        key = self._segment_key(segment)
        if key is not None and key in self._finished_segment_keys:
            return True
        if self._segment_has_manual_boundary(segment):
            return True
        following = segments[index + 1] if index + 1 < len(segments) else None
        return following is not None and self._segment_has_manual_boundary(following)

    def _exportable_segments(self, segments: list[dict[str, object]]) -> list[dict[str, object]]:
        """Keep the articles an export should produce.

        An article exports only once the user has vouched for its boundary, by
        finishing it or cutting it by hand. A PDF the extractor did not really
        segment is one article and has no boundary to confirm, so it still
        exports as a whole. Deleted articles never export.
        """

        if len(segments) <= 1:
            return [segment for segment in segments if not self._is_segment_deleted(segment)]
        return [
            segment
            for index, segment in enumerate(segments)
            if not self._is_segment_deleted(segment)
            and self._segment_is_confirmed(segment, segments, index)
        ]

    def delete_article(self, segment: dict[str, object] | None = None) -> bool:
        """Drop a detected article from every export without losing its blocks.

        False detections (an advertisement, publisher boilerplate) would
        otherwise have to be finished or merged away to leave the export alone.
        Deleting keeps the boundary, so its blocks stay owned and visible but
        grayed, and the article reappears in exports only after a restore.
        """

        if self.document is None:
            self._notify("Load a PDF first.")
            return False
        target = segment if segment is not None else (self._current_segment() or (None, None))[0]
        if target is None:
            self._notify("Select an article tab before deleting one.")
            return False
        key = self._segment_key(target)
        if key is None:
            self._notify("That article could not be identified; nothing was deleted.")
            return False
        if key in self._finished_segment_keys:
            self._notify("Reopen the finished article before deleting it.")
            return False
        if key in self._deleted_segment_keys:
            self._notify("That article is already deleted; use Restore to bring it back.")
            return False
        self._push_article_undo("delete article")
        self._deleted_segment_keys.append(key)
        self._sync_deleted_block_overlay()
        number = str(target.get("abstract_number", "")).strip() or "ABSN"
        self._notify(
            f"Deleted article {number}; its blocks stay visible but will not be exported. "
            "Right-click its tab to restore it, or press Ctrl+Z to undo."
        )
        self._refresh_article_buttons()
        return True

    def restore_article(self, segment: dict[str, object] | None = None) -> bool:
        """Un-delete a deleted article so it is exported again."""

        if self.document is None:
            self._notify("Load a PDF first.")
            return False
        target = segment if segment is not None else (self._current_segment() or (None, None))[0]
        if target is None:
            self._notify("Select an article tab before restoring one.")
            return False
        key = self._segment_key(target)
        if key is None or key not in self._deleted_segment_keys:
            self._notify("That article is not deleted.")
            return False
        self._push_article_undo("restore article")
        self._deleted_segment_keys.remove(key)
        self._sync_deleted_block_overlay()
        number = str(target.get("abstract_number", "")).strip() or "ABSN"
        self._notify(f"Restored article {number}; it will be exported again.")
        self._refresh_article_buttons()
        return True

    def delete_current_article(self) -> None:
        """Delete the working article, or restore it when already deleted."""

        if self.document is None:
            self._notify("Load a PDF first.")
            return
        current = self._current_segment()
        if current is None:
            self._notify("Select an article tab before deleting or restoring one.")
            return
        segment, key = current
        if key in self._deleted_segment_keys:
            self.restore_article(segment)
        else:
            self.delete_article(segment)

    def _refresh_delete_article_button(self, current: tuple | None = None) -> None:
        """Mirror the working article's deleted state on the delete/restore button."""

        if not hasattr(self, "delete_article_btn"):
            return
        if self.document is None or current is None:
            self.delete_article_btn.setEnabled(False)
            self.delete_article_btn.setText("Delete Article")
            return
        segment, key = current
        number = str(segment.get("abstract_number", "")).strip() or "ABSN"
        if key in self._deleted_segment_keys:
            self.delete_article_btn.setText(f"Restore {number}")
        else:
            self.delete_article_btn.setText(f"Delete {number}")
        self.delete_article_btn.setEnabled(True)

    def _refresh_article_buttons(self) -> None:
        """Reflect the current article state on the finish/reopen buttons."""

        if self.document is None:
            self.finish_article_btn.setEnabled(False)
            self.finish_article_btn.setText("Finish Article")
            self.reopen_article_btn.setEnabled(False)
            self._refresh_delete_article_button(None)
            self.metadata_panel.set_abstract_number("", enabled=False)
            self.author_affiliation_review_panel.set_link_review([], [], [], enabled=False)
            self._update_split_segment_button()
            self._refresh_articles_list()
            return
        # The abstract number is article-level, so the inspector mirrors the
        # current article rather than the selected block.
        current = self._current_segment()
        if current is None:
            self.finish_article_btn.setEnabled(False)
            self.finish_article_btn.setText("Finish Article")
            self.metadata_panel.set_abstract_number(self.document.abstract_number())
        else:
            segment, _key = current
            # A deleted article is skipped by exports, so finishing it would
            # export nothing; restoring is the way out.
            self.finish_article_btn.setEnabled(_key not in self._deleted_segment_keys)
            abstract_number = str(segment.get("abstract_number", "")).strip()
            self.finish_article_btn.setText(f"Finish {abstract_number}" if abstract_number else "Finish Article")
            self.metadata_panel.set_abstract_number(abstract_number)
        self.reopen_article_btn.setEnabled(bool(self._finished_segment_keys))
        self._refresh_delete_article_button(current)
        self._update_split_segment_button()
        self._refresh_link_review()
        self._refresh_articles_list()

    def _refresh_link_review(self) -> None:
        """Mirror the author/affiliation links and flagged issues in the inspector."""

        if self.document is None:
            self.author_affiliation_review_panel.set_link_review([], [], [], enabled=False)
            return
        self.author_affiliation_review_panel.set_link_review(
            self.document.authors,
            self.document.affiliations,
            self.document.metadata.get("link_issues", []),
        )

    def _refresh_articles_list(self) -> None:
        """Show one tab per detected article so false splits can be merged."""

        segments = self._document_segments() if self.document is not None else []
        previous_key = self._current_segment_key
        self.articles_tabs.blockSignals(True)
        while self.articles_tabs.count():
            widget = self.articles_tabs.widget(0)
            self.articles_tabs.removeTab(0)
            widget.deleteLater()
        for segment in segments:
            key = self._segment_key(segment)
            finished = key in self._finished_segment_keys
            tab_page = QWidget()
            page_layout = QVBoxLayout(tab_page)
            page_layout.setContentsMargins(8, 8, 8, 8)
            summary = QLabel(self._segment_summary(segment))
            summary.setWordWrap(True)
            if finished or segment.get("deleted"):
                summary.setStyleSheet("color: #9a9a9a;")
            page_layout.addWidget(summary)
            is_current = key is not None and key == self._current_segment_key
            current_marker = " (current)" if is_current else ""
            page_layout.addWidget(
                QLabel(
                    f"{len(segments)} detected; this is the working article{current_marker}."
                    if is_current
                    else f"{len(segments)} detected; click this tab to make it the working article."
                )
            )
            page_layout.addWidget(
                QLabel(
                    "Right-click this tab to name the article, merge, delete, restore, or reopen it."
                )
            )
            index = self.articles_tabs.addTab(
                tab_page,
                self._segment_tab_title(
                    segment,
                    finished,
                    is_current,
                    deleted=self._is_segment_deleted(segment),
                ),
            )
            self.articles_tabs.setTabToolTip(index, self._segment_summary(segment))
            if key == previous_key:
                self.articles_tabs.setCurrentIndex(index)
        if previous_key is None and segments:
            self.articles_tabs.setCurrentIndex(0)
        self.articles_tabs.blockSignals(False)
        if self.document is None:
            self.articles_hint.setText("Load a PDF to see detected articles.")
        elif not segments:
            self.articles_hint.setText(
                "No article boundary was detected, so the PDF exports as one article. "
                "Right-click the first line of each following article in the page and choose "
                "\"Start a new article here\" (or press Ctrl+Shift+N) to mark them by hand."
            )
        else:
            unfinished = len(
                [
                    segment
                    for segment in segments
                    if self._segment_key(segment) not in self._finished_segment_keys
                    and not self._is_segment_deleted(segment)
                ]
            )
            if not unfinished:
                self.articles_hint.setText(
                    f"All {len(segments)} articles are finished or deleted. Right-click a line in the page, "
                    "or use Reopen or Restore, to change one."
                )
            else:
                self.articles_hint.setText(
                    f"{len(segments)} articles detected; {unfinished} left to finish. Click a tab "
                    "to work on one, right-click a line in the page to start an article there, and "
                    "use Merge Into Previous for a split that cut an article in two."
                )
        self._update_merge_segments_button()

    @staticmethod
    def _segment_tab_title(
        segment: dict[str, object], finished: bool, current: bool = False, deleted: bool = False
    ) -> str:
        """Short tab caption: article number plus finished/deleted/working markers."""

        number = str(segment.get("abstract_number", "")).strip() or "ABSN"
        if deleted or segment.get("deleted"):
            return f"✕ {number}"
        if finished:
            return f"🔒 {number}"
        return f"● {number}" if current else number

    def _on_article_tab_clicked(self, index: int) -> None:
        """Make the clicked article tab the working article and show its page."""

        segment = self._segment_at_tab(index)
        if segment is None:
            return
        key = self._segment_key(segment)
        if key not in self._finished_segment_keys:
            if self._current_segment_key != key:
                self._current_segment_key = key
                self._refresh_article_buttons()
        start_page = int(segment.get("start_page", 1))
        self.viewer.render_page(start_page - 1)
        # The Selected Blocks panel follows the working article, so switching
        # tabs re-scopes it to the article that was just chosen.
        self._refresh_selected_blocks_view()
        self._log(
            f"Viewing article {segment.get('index')} "
            f"(ABSN {segment.get('abstract_number', '')}) from page {start_page}."
        )

    def _segment_at_tab(self, index: int) -> dict[str, object] | None:
        """Map a tab index back to its article segment."""

        if index < 0 or index >= self.articles_tabs.count():
            return None
        segments = self._document_segments()
        return segments[index] if 0 <= index < len(segments) else None

    def _show_article_tab_menu(self, position) -> None:
        """Per-article menu: name it, fold it back, or reopen it."""

        index = self.articles_tabs.tabBar().tabAt(position)
        segment = self._segment_at_tab(index)
        if segment is None:
            return
        key = self._segment_key(segment)
        if key is not None:
            self._current_segment_key = key
            self._refresh_article_buttons()
            self._refresh_selected_blocks_view()
        finished = key in self._finished_segment_keys
        number = str(segment.get("abstract_number", "")).strip() or "ABSN"
        previous_key = self._neighbour_boundary_key(key, -1)
        next_key = self._neighbour_boundary_key(key, 1)
        deleted = self._is_segment_deleted(segment)
        mergeable = not finished and not deleted
        menu = QMenu(self)
        rename_action = menu.addAction(f"Set article number for {number}...")
        previous_action = menu.addAction("Merge into the previous article")
        next_action = menu.addAction("Merge the next article into this one")
        merge_action = menu.addAction(f"Merge {number} with another article...")
        delete_action = menu.addAction(f"Delete {number} (keep visible, skip export)")
        restore_action = menu.addAction(f"Restore {number} (export it again)")
        reopen_action = menu.addAction("Reopen this article") if finished else None
        previous_action.setEnabled(
            mergeable
            and previous_key is not None
            and previous_key not in self._finished_segment_keys
        )
        next_action.setEnabled(
            mergeable and next_key is not None and next_key not in self._finished_segment_keys
        )
        merge_action.setEnabled(mergeable)
        delete_action.setEnabled(not finished and not deleted)
        restore_action.setEnabled(deleted)
        chosen = menu.exec(self.articles_tabs.mapToGlobal(position))
        if chosen is None:
            return
        if chosen is rename_action:
            self.set_article_number(segment)
            return
        if chosen is previous_action:
            self.merge_article_with_previous(segment)
            self._refresh_article_buttons()
            return
        if chosen is next_action:
            self.merge_article_with_next(segment)
            self._refresh_article_buttons()
            return
        if chosen is merge_action:
            self.merge_selected_segments()
            return
        if chosen is delete_action:
            self.delete_article(segment)
            return
        if chosen is restore_action:
            self.restore_article(segment)
            return
        if reopen_action is not None and chosen is reopen_action:
            self.reopen_article(segment)

    def _show_block_article_menu(self, block_id: str, global_pos) -> None:
        """Right-click menu on a line: cut a new article here or repair a bad cut.

        The line under the cursor becomes the selection first, so the action and
        its result land on the article the user pointed at rather than on
        whichever block happened to be selected before.
        """

        if self.document is None or not block_id:
            return
        if not any(block.id == block_id for block in self.document.blocks):
            return
        self._on_viewer_block_selected(block_id, False)
        ordered = self._ensure_reading_orders()
        starts_article = block_id in set(self._boundary_block_ids())
        is_first = bool(ordered) and ordered[0].id == block_id
        segment = self._segment_for_key(block_id)
        menu = QMenu(self)
        split_action = menu.addAction("Start a new article here")
        split_action.setEnabled(not starts_article and not is_first)
        previous_key = self._neighbour_boundary_key(block_id, -1)
        next_key = self._neighbour_boundary_key(block_id, 1)
        previous_action = menu.addAction("Merge this article into the previous one")
        previous_action.setEnabled(
            starts_article
            and previous_key is not None
            and previous_key not in self._finished_segment_keys
        )
        next_action = menu.addAction("Merge the next article into this one")
        next_action.setEnabled(
            starts_article
            and next_key is not None
            and next_key not in self._finished_segment_keys
        )
        menu.addSeparator()
        delete_action = menu.addAction("Delete this article (keep visible, skip export)")
        delete_action.setEnabled(
            starts_article
            and segment is not None
            and not self._is_segment_deleted(segment)
            and self._segment_key(segment) not in self._finished_segment_keys
        )
        restore_action = menu.addAction("Restore this article (export it again)")
        restore_action.setEnabled(
            starts_article and segment is not None and self._is_segment_deleted(segment)
        )
        number_action = menu.addAction("Set article number...")
        number_action.setEnabled(segment is not None)
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is split_action:
            self._split_here(block_id)
            return
        if chosen is previous_action:
            self.merge_article_with_previous(segment)
            self._refresh_article_buttons()
            return
        if chosen is next_action:
            self.merge_article_with_next(segment)
            self._refresh_article_buttons()
            return
        if chosen is delete_action:
            self.delete_article(segment)
            return
        if chosen is restore_action:
            self.restore_article(segment)
            return
        if chosen is number_action:
            self.set_article_number(segment)

    def _show_tree_article_menu(self, position) -> None:
        """Offer the same article actions for a line clicked in the tree."""

        item = self.tree.itemAt(position)
        while item is not None and not item.data(0, Qt.UserRole):
            item = item.parent()
        if item is None:
            return
        block_id = str(item.data(0, Qt.UserRole) or "")
        if block_id:
            self._show_block_article_menu(
                block_id, self.tree.viewport().mapToGlobal(position)
            )

    def _update_merge_segments_button(self) -> None:
        """Enable merging while at least two unfinished articles exist."""

        segments = self._document_segments()
        unfinished = [
            segment
            for segment in segments
            if self._segment_key(segment) not in self._finished_segment_keys
        ]
        self.merge_segments_btn.setEnabled(len(unfinished) >= 2)
        if not hasattr(self, "merge_previous_btn"):
            return
        current = self._current_segment()
        if current is None:
            self.merge_previous_btn.setEnabled(False)
            return
        current_segment, key = current
        previous = self._neighbour_segment(current_segment, -1)
        self.merge_previous_btn.setEnabled(
            key not in self._finished_segment_keys
            and key not in self._deleted_segment_keys
            and previous is not None
            and self._segment_key(previous) not in self._finished_segment_keys
            and not self._is_segment_deleted(previous)
        )

    def _update_split_segment_button(self) -> None:
        """Enable splitting while exactly one block is selected as the boundary."""

        if not hasattr(self, "split_segment_btn"):
            return
        self.split_segment_btn.setEnabled(
            self.document is not None and len(self._selected_block_ids) == 1
        )

    def _segment_summary(self, segment: dict[str, object]) -> str:
        """Describe one detected article as a single readable line."""

        index = segment.get("index", "?")
        number = str(segment.get("abstract_number", "")).strip() or "ABSN"
        start = int(segment.get("start_page", 0))
        end = int(segment.get("end_page", start))
        pages = f"p{start}" if start == end else f"p{start}-{end}"
        columns = segment.get("columns") or []
        column_text = "" if not columns else " col" + ",".join(str(column) for column in columns)
        state = " | deleted" if self._is_segment_deleted(segment) else (
            " | finished" if self._segment_key(segment) in self._finished_segment_keys else ""
        )
        # The stored vocabulary reads reading-order bounds as "split by hand",
        # but the boundary model records the real origin beside them.
        boundary_source = str(segment.get("boundary_source") or "")
        manual = (
            " | manual split"
            if boundary_source == SOURCE_MANUAL
            or (not boundary_source and segment_is_manual(segment))
            else ""
        )
        text = f"{index}. ABSN {number} | {pages}{column_text}{state}{manual}"
        snippet = ""
        if self.document is not None:
            title_blocks = sorted(
                (
                    block
                    for block in self.document.blocks
                    if block.role == "title" and block_matches_segment(block, segment)
                ),
                key=reading_order_key,
            )
            snippet = " ".join(" ".join(block.text.split()) for block in title_blocks)
        return f"{text} | {snippet[:70]}" if snippet else text

    # A conference marker printed on a line of its own ("P-605", "S100", "4349").
    MANUAL_ARTICLE_NUMBER = re.compile(
        r"^(?:[A-Za-z]{1,4}[-.]?)?\d{2,6}(?:[./-][A-Za-z0-9]{1,6})*$"
    )

    def _ensure_reading_orders(self) -> list:
        """Number every block by reading position, and return them in that order.

        A hand-made split is stored as reading-order bounds, which can only be
        compared when every block carries an index. Extraction already records
        one and merged blocks inherit it, so a document that is fully numbered
        is left untouched; anything else is numbered from the column-aware
        reading order the UI already uses.
        """

        if self.document is None:
            return []
        ordered = self._sort_reading_order(self.document.blocks)
        if all(block.metadata.get("reading_order") is not None for block in ordered):
            return ordered
        for position, block in enumerate(ordered, start=1):
            block.metadata["reading_order"] = position
        # A manual split is stored as reading-order bounds, so numbering the
        # blocks changes which article owns which block.
        self._invalidate_segment_identity()
        return ordered

    def _replace_article_segments(self, segments: list[dict[str, object]]) -> None:
        """Store a new segment list, renumbered into reading order.

        Article identity is the block an article starts at, so renumbering does
        not disturb which article the user is working on or which ones are
        finished: both stay attached to their own article.
        """

        if self.document is None:
            return
        ordered = sorted(
            (segment for segment in segments if isinstance(segment, dict)),
            key=lambda segment: (
                int(segment.get("start_page", 1)),
                min((int(column) for column in segment.get("columns") or []), default=0),
                int(segment["start_order"]) if segment.get("start_order") is not None else -1,
                int(segment.get("index", 0)),
            ),
        )
        for position, segment in enumerate(ordered, start=1):
            segment["index"] = position
        self.document.metadata["article_segments"] = ordered
        self._invalidate_segment_identity()

    def _manual_article_number(self, block) -> str:
        """Return the article number a block prints, for a hand-made split.

        Detection needs a label or parentheses ("Abstract No: 4349") because a
        bare token is ambiguous with a page number. A person drawing the
        boundary has already decided the block starts an article, so the bare
        marker those conference PDFs print ("P-605") counts here.
        """

        cleaned = " ".join(str(block.text or "").split())
        labelled = abstract_number_marker(cleaned)
        if labelled:
            return labelled
        if not self.MANUAL_ARTICLE_NUMBER.fullmatch(cleaned):
            return ""
        return cleaned

    def split_article_at_block(self, block_id: str) -> dict[str, object] | None:
        """Start a new article at one block and return the segment it created.

        Articles are boundaries over one reading stream, so a cut simply inserts
        a boundary: everything before it stays with the article that already
        owned it and everything from it on becomes a new article. That is the
        only way to separate two articles sharing a page *and* a column, and it
        cannot leave a block owned by two articles or by none.

        Returns ``None`` when the cut is void: an unknown block, the first line
        of the document, or a line that already starts an article.
        """

        if self.document is None:
            return None
        ordered = self._ensure_reading_orders()
        segmentation = self._article_segmentation(ordered)
        if segmentation is None:
            return None
        # The cut is keyed on the id: two lines of a PDF can be identical, and a
        # value comparison would then cut at the first copy instead.
        if not segmentation.insert_boundary(block_id, source=SOURCE_MANUAL):
            return None
        # Name each article from the marker line it starts with, when it prints
        # one; an article without a marker stays ABSN and is named by hand.
        self._name_articles_from_markers(segmentation, ordered)
        self._push_article_undo("split article")
        self._replace_article_segments(
            segmentation.to_legacy_segments(self.document.blocks)
        )
        self._current_segment_key = block_id
        self._report_segmentation_issues(segmentation)
        self._refresh_selected_blocks_view()
        return self._segment_for_key(block_id)

    def _split_here(self, block_id: str) -> None:
        """Split at one line from the UI and report the outcome without a dialog."""

        if self.document is None:
            self._notify("Load a PDF first.")
            return
        segment = self.split_article_at_block(block_id)
        if segment is None:
            self._notify(
                "That line already starts an article; pick the first line of the "
                "next article instead."
            )
            return
        self._refresh_article_buttons()
        number = str(segment.get("abstract_number", "")).strip() or "ABSN"
        self._notify(
            f"Article {segment.get('index')} (ABSN {number}) now starts at {block_id} "
            f"on page {segment.get('start_page')}."
        )

    def split_article_at_selection(self) -> None:
        """Start a new article at the selected block, splitting the PDF by hand."""

        if self.document is None:
            self._notify("Load a PDF first.")
            return
        selected = sorted(
            (block for block in self.document.blocks if block.id in self._selected_block_ids),
            key=reading_order_key,
        )
        if len(selected) != 1:
            self._notify(
                "Select exactly one line first: the first line of the article that "
                "starts at that point in the PDF."
            )
            return
        self._split_here(selected[0].id)

    def merge_selected_segments(self) -> None:
        """Fold falsely split articles into the current one after a picker."""

        if self.document is None:
            return
        current = self._current_segment()
        if current is None:
            self._notify("No unfinished article is available as the merge target.")
            return
        target_segment, target_key = current
        if target_key in self._finished_segment_keys:
            self._notify("Reopen the finished article before merging into it.")
            return
        candidates = [
            segment
            for segment in self._document_segments()
            if self._segment_key(segment) != target_key
            and self._segment_key(segment) not in self._finished_segment_keys
            and not self._is_segment_deleted(segment)
        ]
        if not candidates:
            self._notify("No other unfinished article was detected.")
            return
        chosen = self._choose_merge_candidates(target_segment, candidates)
        if not chosen:
            return
        self._merge_article_segments([*chosen, target_segment])

    def merge_article_with_previous(
        self, segment: dict[str, object] | None = None
    ) -> dict[str, object] | None:
        """Fold one article into the one before it, without a dialog.

        A wrong split is the commonest mistake while working through a PDF, so
        repairing it is one click instead of a picker and a confirmation.
        """

        if self.document is None:
            return None
        target = segment if segment is not None else (self._current_segment() or (None, None))[0]
        if target is None:
            self._notify("Select an article tab before merging articles.")
            return None
        previous = self._neighbour_segment(target, -1)
        if previous is None:
            self._notify("This is the first article; there is nothing before it to fold into.")
            return None
        return self._merge_article_segments([previous, target])

    def merge_article_with_next(
        self, segment: dict[str, object] | None = None
    ) -> dict[str, object] | None:
        """Fold the article after one into it, without a dialog."""

        if self.document is None:
            return None
        target = segment if segment is not None else (self._current_segment() or (None, None))[0]
        if target is None:
            self._notify("Select an article tab before merging articles.")
            return None
        following = self._neighbour_segment(target, 1)
        if following is None:
            self._notify("This is the last article; there is nothing after it to fold in.")
            return None
        return self._merge_article_segments([target, following])

    def _merge_article_segments(
        self, segments: list[dict[str, object]]
    ) -> dict[str, object] | None:
        """Fold several articles into one, keeping the earliest boundary.

        Merging removes boundaries instead of rewriting page ranges, so the
        articles that stay keep exactly the blocks they already owned and the
        merged one covers the whole span. Everything between the first and the
        last chosen article folds in with them.
        """

        if self.document is None:
            return None
        ordered = self._ensure_reading_orders()
        segmentation = self._article_segmentation(ordered)
        if segmentation is None:
            return None
        positions = sorted(
            {
                position
                for position in (
                    segmentation.article_position(self._segment_key(segment) or "")
                    for segment in segments
                )
                if position is not None
            }
        )
        if len(positions) < 2:
            self._notify("Merging needs at least two articles; nothing was changed.")
            return None
        target_position = positions[0]
        participants = [
            segmentation.boundaries[position].block_id
            for position in range(target_position, positions[-1] + 1)
        ]
        if any(block_id in self._finished_segment_keys for block_id in participants):
            self._notify("Reopen the finished article before merging it.")
            return None
        if any(block_id in self._deleted_segment_keys for block_id in participants):
            self._notify("Restore the deleted article before merging it.")
            return None
        merged = 0
        for block_id in participants[1:]:
            if segmentation.remove_boundary(block_id):
                merged += 1
        if not merged:
            self._notify("Nothing was changed; those articles are already one.")
            return None
        self._push_article_undo("merge articles")
        self._replace_article_segments(
            segmentation.to_legacy_segments(self.document.blocks)
        )
        key = segmentation.boundaries[target_position].block_id
        self._current_segment_key = key
        self._report_segmentation_issues(segmentation)
        self._refresh_selected_blocks_view()
        target = self._segment_for_key(key)
        number = str((target or {}).get("abstract_number", "")).strip() or "ABSN"
        if target is not None:
            self._notify(
                f"Merged {merged + 1} articles into one: ABSN {number} "
                f"(pages {target.get('start_page')}-{target.get('end_page')})."
            )
        return target

    def set_article_number(self, segment: dict[str, object] | None = None) -> bool:
        """Ask for an article's number and store it on that article only.

        Each article carries its own Abstract No, so naming one never renames
        the others; the tab and the exported XML follow the article it belongs to.
        """

        target = segment if segment is not None else (self._current_segment() or (None, None))[0]
        if self.document is None or target is None:
            self._notify("Select an article tab before naming one.")
            return False
        current = str(target.get("abstract_number", "")).strip()
        entered, accepted = QInputDialog.getText(
            self,
            "Article number",
            f"Abstract number for article {target.get('index', '?')} "
            f"(pages {target.get('start_page')}-{target.get('end_page')}):",
            text=current,
        )
        if not accepted:
            return False
        number = self._normalize_abstract_number(entered)
        if not number:
            self._notify('Enter a single identifier such as "4349" or "P-605".')
            return False
        self._push_article_undo("rename article")
        self._set_abstract_number(number, target)
        self._refresh_article_buttons()
        self._notify(f"Article {target.get('index', '?')} is now ABSN {number}.")
        return True

    def _choose_merge_candidates(
        self, target_segment: dict[str, object], candidates: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        """Ask which other detected articles should fold into the current one."""

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Merge ABSN {target_segment.get('abstract_number', '')} with...")
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            "Select the articles that were split by mistake; they fold into "
            f"ABSN {target_segment.get('abstract_number', '')}, along with anything "
            "between them. Ctrl-click for several."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        list_widget = QListWidget()
        list_widget.setSelectionMode(QListWidget.ExtendedSelection)
        for segment in candidates:
            list_widget.addItem(QListWidgetItem(self._segment_summary(segment)))
        layout.addWidget(list_widget, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(460, 320)
        if dialog.exec() != QDialog.Accepted:
            return []
        rows = sorted({index.row() for index in list_widget.selectedIndexes()})
        return [candidates[row] for row in rows if 0 <= row < len(candidates)]

    def _update_document_model(self) -> None:
        """Build document metadata from user-assigned roles only."""

        if self.document is None:
            return
        excluded_ids = set(self.document.metadata.get("abstract_number_block_ids", []))
        # A block tagged abstract_number is a marker as well: exclude it from
        # the title, abstract, and body just like an extractor-found one.
        role_marker_ids = {block.id for block in self.document.blocks if block.role == "abstract_number"}
        if role_marker_ids - excluded_ids:
            excluded_ids |= role_marker_ids
            self.document.metadata["abstract_number_block_ids"] = sorted(excluded_ids)
        blocks = [
            block
            for block in self.document.blocks
            if block.id not in excluded_ids and not self._looks_like_footer_noise(block)
        ]
        self.document.title = self._compose_title(blocks)
        abstract_blocks = [b for b in blocks if b.role == "abstract"]
        abstract_blocks.sort(key=reading_order_key)
        abstract_text = "\n".join(
            self._sanitize_xml_text(b.text)
            for b in abstract_blocks
            if not is_abstract_heading(b.text)
        )
        self.document.abstract = abstract_text
        author_blocks = [b for b in blocks if b.role in {"author", "corresponding_author"}]
        author_blocks.sort(key=reading_order_key)
        affiliation_blocks = [b for b in blocks if b.role == "affiliation"]
        affiliation_blocks.sort(key=reading_order_key)
        linked = self.linker.link_from_blocks(author_blocks, affiliation_blocks)
        self.document.authors = linked.authors
        self.document.corresponding_author = linked.corresponding_author
        self.document.affiliations = linked.affiliations
        self.document.metadata["link_issues"] = [asdict(issue) for issue in linked.issues]
        self._refresh_link_review()
        self._rebuild_paragraph_model()
        self._sync_auto_zones()

    def _log_link_issues(self) -> None:
        """Report the author/affiliation links that need a human decision."""

        if self.document is None:
            return
        issues = self.document.metadata.get("link_issues", [])
        if not issues:
            return
        errors = sum(1 for issue in issues if issue.get("severity") == "error")
        self._log(
            f"Author/affiliation matching: {errors} error(s), {len(issues) - errors} warning(s)."
        )
        for issue in issues:
            self._log(f"  [{issue.get('severity', 'info')}] {issue.get('message', '')}")

    def _document_for_segment(
        self, segment: dict[str, object], source_document: Document | None = None
    ) -> Document:
        """Build an exportable document view for one detected article range."""

        source = source_document or self.document
        if source is None:
            raise RuntimeError("Load a PDF first.")
        start_page = int(segment.get("start_page", 1))
        end_page = int(segment.get("end_page", start_page))
        source_by_id = {
            block.id: block
            for block in [*(source.raw_blocks or []), *source.blocks]
        }

        def belongs_to_segment(block) -> bool:
            """Include a merged block when any of its source lines belongs here."""

            if block_matches_segment(block, segment):
                return True
            return any(
                source_block is not None and block_matches_segment(source_block, segment)
                for source_id in (block.metadata or {}).get("merged_block_ids", [])
                if (source_block := source_by_id.get(str(source_id))) is not None
            )

        blocks = [
            replace(block, metadata=dict(block.metadata))
            for block in source.blocks
            if belongs_to_segment(block)
        ]
        block_ids = {block.id for block in blocks}
        raw_blocks = [
            replace(block, metadata=dict(block.metadata))
            for block in (source.raw_blocks or source.blocks)
            if belongs_to_segment(block)
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
        metadata["abstract_number"] = str(segment.get("abstract_number") or "ABSN")
        # A hand-made article usually has no marker block to exclude.
        marker_id = str(segment.get("abstract_number_block_id") or "")
        metadata["abstract_number_block_ids"] = [marker_id] if marker_id else []
        metadata["article_segment"] = dict(segment)
        metadata.pop("article_segments", None)
        abstract_number = str(metadata["abstract_number"])
        title = self._strip_abstract_number_from_title(self._compose_title(blocks), abstract_number)
        title = re.sub(
            r"^\s*\([A-Z][A-Z0-9./-]{1,30}\)\s+",
            "",
            title,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        author_blocks = sorted(
            (block for block in blocks if block.role in {"author", "corresponding_author"}),
            key=reading_order_key,
        )
        affiliation_blocks = sorted(
            (block for block in blocks if block.role == "affiliation"),
            key=reading_order_key,
        )
        linked = self.linker.link_from_blocks(author_blocks, affiliation_blocks)
        metadata["link_issues"] = [asdict(issue) for issue in linked.issues]
        return Document(
            title=title,
            authors=linked.authors,
            corresponding_author=linked.corresponding_author,
            affiliations=linked.affiliations,
            abstract="\n".join(
                self._sanitize_xml_text(block.text)
                for block in blocks
                if block.role == "abstract" and block.text.strip() and not is_abstract_heading(block.text)
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
        """Refresh proposed paragraphs; auto-accept clean single-block units."""

        if self.document is None:
            return
        excluded_ids = set(self.document.metadata.get("abstract_number_block_ids", []))
        source_lines = [
            block
            for block in (self.document.raw_blocks or self.document.blocks)
            # Marker lines are identifiers, never prose: drop them so they
            # cannot be auto-accepted into an exported body paragraph.
            if block.id not in excluded_ids
        ]
        proposals = self.paragraph_reconstructor.propose(source_lines)
        accepted = self.paragraph_reconstructor.accepted_from_blocks(self.document.blocks)
        # Manual merge units win; skip auto-accepting proposals that overlap them.
        manual_line_ids = {
            line_id
            for paragraph in accepted
            for line_id in paragraph.source_line_ids
        }
        for proposal in proposals:
            proposal_line_ids = set(proposal.source_line_ids)
            if proposal_line_ids & manual_line_ids:
                continue
            if self._is_clean_paragraph_proposal(proposal, source_lines):
                proposal.status = "accepted"
                proposal.role = self._proposal_role(proposal)
        self.document.paragraphs = proposals + accepted

    def _is_clean_paragraph_proposal(self, proposal, source_lines) -> bool:
        """A proposal is auto-acceptable when typography and column are uniform."""

        wanted = set(proposal.source_line_ids)
        lines = [block for block in source_lines if block.id in wanted]
        if len(lines) < 2:
            return True
        if len({round(block.font_size, 1) for block in lines}) > 1:
            return False
        if len({block.bold for block in lines}) > 1:
            return False
        return True

    def _proposal_role(self, proposal) -> str:
        """Inherit the most common non-unclassified role among source lines."""

        roles: list[str] = []
        by_id = {block.id: block for block in (self.document.raw_blocks or self.document.blocks)}
        for line_id in proposal.source_line_ids:
            block = by_id.get(line_id)
            if block is not None and block.role not in {"unclassified", ""}:
                roles.append(block.role)
        if not roles:
            return "body"
        return max(set(roles), key=roles.count)

    def _compose_title(self, blocks) -> str:
        """Build the title from blocks the user marked as title."""

        title_blocks = [block for block in blocks if block.role == "title"]
        if not title_blocks:
            return ""
        title_blocks.sort(key=reading_order_key)
        page1_blocks = [block for block in title_blocks if block.page == 1]
        candidates = page1_blocks if page1_blocks else title_blocks
        top = candidates[0]
        grouped = [top]
        for block in candidates[1:]:
            previous = grouped[-1]
            # A title reads down one column. Once the stream steps back up the
            # page it has wrapped to the next column, where the following
            # article lives, so the title ends at the previous line.
            if block.page != previous.page or block.y < previous.y:
                break
            if block.y - (previous.y + previous.height) > max(previous.height, block.height) * 1.6:
                break
            grouped.append(block)
        parts = [self._sanitize_xml_text(block.text).strip() for block in grouped if self._sanitize_xml_text(block.text).strip()]
        title = " ".join(parts) if parts else self._sanitize_xml_text(top.text)
        # Conference abstracts commonly prefix the title with an identifier such
        # as "(S100)". Keep it in extraction metadata, not in the article title.
        title = re.sub(r"^\s*\([A-Z][A-Z0-9./-]{1,30}\)\s+", "", title, count=1, flags=re.IGNORECASE).strip()
        return self._strip_abstract_number_from_title(title)

    def _current_article_block_ids(self) -> frozenset[str] | None:
        """Blocks of the article being worked on, or None when none is current.

        The Selected Blocks panel inspects one article at a time: a proceedings
        PDF holds many papers, and listing every block buries the few that
        belong to the tab the user chose. Until an article is current the whole
        document stays visible, so nothing is hidden before the first choice.
        """

        if self.document is None or self._current_segment_key is None:
            return None
        current = self._current_segment()
        if current is None:
            return None
        _segment, key = current
        if not key:
            return None
        segmentation = self._article_segmentation()
        if segmentation is None:
            return None
        article = segmentation.article_position(key)
        if article is None:
            return None
        return frozenset(segmentation.block_ids(article))

    def _populate_tree(self) -> None:
        """Populate the structure tree with blocks of the current article."""

        if self.document is None:
            return
        scope = self._current_article_block_ids()
        if scope is not None and not scope.issuperset(self._selected_block_ids):
            # Drop any selection left over from the article the user left, so
            # the panel, the page highlight and the merge queue stay in step.
            self._selected_block_ids &= scope
            viewer = getattr(self, "viewer", None)
            if viewer is not None:
                viewer.set_selected_blocks(
                    self._selected_block_ids, next(iter(self._selected_block_ids), None)
                )
        self.tree.blockSignals(True)
        self.tree.clear_dynamic_items()
        buckets: dict[str, list] = {
            "Title": [],
            "Authors": [],
            "Corresponding Author": [],
            "Affiliations": [],
            "Abstract": [],
            "Abstract Number": [],
            "Keywords": [],
        }
        for block in self.document.blocks:
            if scope is not None and block.id not in scope:
                continue
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
            elif block.role == "abstract_number":
                buckets["Abstract Number"].append(block)
            elif block.role == "keywords":
                buckets["Keywords"].append(block)
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

    def _refresh_selected_blocks_view(self) -> None:
        """Re-scope the Selected Blocks panel to the article now being worked on."""

        # A window assembled without Qt (tests) has no tree to fill.
        if getattr(self, "tree", None) is None:
            return
        self._populate_tree()
        self._sync_tree_selection()
        self._update_selection_status()
        self._update_selection_panel()

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
        locked = self._article_locked(block)
        prefix = "🔒 " if locked else ""
        item = self._tree_item(block.id, f"{prefix}{block.text[:60]}", block)
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
        if self._article_locked(block):
            self.tree.blockSignals(True)
            item.setText(1, block.role)
            self.tree.blockSignals(False)
            QMessageBox.information(
                self,
                "Article finished",
                "This block belongs to a finished article and is locked. Use Reopen Article to unlock it first.",
            )
            return
        valid_roles = {
            "title",
            "author",
            "corresponding_author",
            "affiliation",
            "abstract",
            "abstract_number",
            "keywords",
        }
        new_role = item.text(1).strip().lower()
        if new_role in valid_roles:
            if new_role == "title":
                if self.document is not None:
                    self.document.metadata["manual_title_block_id"] = str(block_id)
                    for other in self.document.blocks:
                        if other.id != block_id and other.role == "title":
                            other.role = "unclassified"
            previous_role = block.role
            block.role = new_role
            self._sync_block_abstract_number_role(block, previous_role)
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
        locked_blocks = self._locked_selection_blocks(selected_blocks)
        if locked_blocks:
            self._warn_locked_blocks(locked_blocks)
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
            selected_blocks.sort(key=reading_order_key)
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

        answer_summary: object = None
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
        self._adopt_abstract_number(answer_summary, selected_blocks)
        assignments = self._extract_role_assignments(raw_response)
        if not assignments:
            QMessageBox.information(self, "No changes", "The model did not return usable role assignments.")
            return

        by_id = {block.id: block for block in selected_blocks}
        changed: list[tuple[str, str]] = []
        zone_role_votes: dict[str, list[str]] = {}
        skipped_locked = 0
        for block_id, role, confidence in assignments:
            block = by_id.get(block_id)
            if block is None or role not in {
                "title",
                "author",
                "affiliation",
                "abstract",
                "abstract_number",
                "keywords",
                "unclassified",
            }:
                continue
            if self._article_locked(block):
                skipped_locked += 1
                continue
            block.role = role
            block.confidence = confidence
            if role == "abstract_number":
                # Only a person can untag a marker: if this model turns out to
                # be wrong, the exclusion stays until the user says otherwise.
                self._apply_abstract_number_role(block)
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
            if skipped_locked:
                QMessageBox.information(
                    self,
                    "Article finished",
                    f"{skipped_locked} selected block(s) belong to a finished article and were left locked.",
                )
                return
            QMessageBox.information(self, "No changes", "The selected blocks were not reclassified.")
            return
        if skipped_locked:
            self._log(f"Skipped {skipped_locked} locked block(s) during classification.")

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

    def _adopt_abstract_number(self, answer_summary: object, source_blocks: list | None = None) -> None:
        """Adopt an abstract number the model separated out of the title.

        The value replaces the regex-detected number so it is exported as the
        ``ABSN`` itemid and stripped from the article title.
        """

        if self.document is None or not isinstance(answer_summary, dict):
            return
        entry = answer_summary.get("abstract_number")
        if isinstance(entry, str):
            raw_value, raw_ids = entry, []
        elif isinstance(entry, dict):
            raw_value, raw_ids = entry.get("text", ""), entry.get("block_ids", [])
        else:
            return
        abstract_number = self._normalize_abstract_number(raw_value)
        if not abstract_number:
            return
        blocks = source_blocks if source_blocks is not None else self.document.blocks
        haystack = " ".join(str(block.text) for block in blocks)
        if not re.search(re.escape(abstract_number), haystack, flags=re.IGNORECASE):
            self._log(
                f"Ignored model abstract number {abstract_number}: not present in the selected text."
            )
            return
        candidate_ids = [str(block_id) for block_id in raw_ids] if isinstance(raw_ids, list) else []
        blocks_by_id = {block.id: block for block in self.document.blocks}
        # Only exclude blocks that hold nothing but the marker; a title block that
        # merely starts with the number must survive and be trimmed instead.
        marker_ids = [
            block_id
            for block_id in candidate_ids
            if block_id in blocks_by_id
            and self._is_abstract_number_marker(blocks_by_id[block_id].text, abstract_number)
        ]
        if marker_ids:
            self.document.metadata["abstract_number_block_ids"] = marker_ids
            for marker_id in marker_ids:
                marker_block = blocks_by_id.get(marker_id)
                if marker_block is not None and marker_block.role != "abstract_number":
                    marker_block.role = "abstract_number"
                    marker_block.confidence = 1.0
                    marker_block.metadata["role_source"] = "model"
        target: dict[str, object] | None = None
        for segment in self._document_segments():
            if any(
                block_id in blocks_by_id and block_matches_segment(blocks_by_id[block_id], segment)
                for block_id in candidate_ids
            ):
                target = segment
                break
        if target is None:
            current = self._current_segment()
            target = current[0] if current else None
        self._set_abstract_number(abstract_number, target)
        if target is not None and marker_ids:
            target["abstract_number_block_id"] = marker_ids[0]
        self._refresh_article_buttons()
        self._log(f"Adopted abstract number {abstract_number} from the model answer.")

    def _set_abstract_number(self, abstract_number: str, target: dict[str, object] | None) -> None:
        """Store an abstract number on the document and, when known, its segment.

        Naming an article does not change which block starts it, so the working
        and finished article bookkeeping stays valid as it is.
        """

        previous = self._normalize_abstract_number(self.document.metadata.get("abstract_number", ""))
        if previous and previous.casefold() != abstract_number.casefold():
            # Keep the old marker so a correcting edit still trims it off the title.
            aliases = self.document.metadata.get("abstract_number_aliases")
            if not isinstance(aliases, list):
                aliases = []
            if previous not in aliases:
                aliases.append(previous)
            self.document.metadata["abstract_number_aliases"] = aliases
        self.document.metadata["abstract_number"] = abstract_number
        if target is not None:
            target["abstract_number"] = abstract_number

    def _apply_abstract_number_role(self, block) -> None:
        """Record a block tagged ``abstract_number`` as the article marker.

        The block is excluded from the title, abstract, and body, and a marker
        that prints a bare identifier also becomes the exported Abstract No.
        """

        if self.document is None:
            return
        marker_ids = [
            str(value)
            for value in self.document.metadata.get("abstract_number_block_ids", [])
            if str(value)
        ]
        if block.id not in marker_ids:
            marker_ids.append(block.id)
            self.document.metadata["abstract_number_block_ids"] = marker_ids
        # A person may tag a bare marker such as "P-605", which auto-detection
        # rejects as ambiguous; for a hand-tagged block the bare token counts.
        value = abstract_number_marker(block.text) or self._manual_article_number(block)
        if not value:
            return
        target = next(
            (
                segment
                for segment in self._document_segments()
                if block_matches_segment(block, segment)
            ),
            None,
        )
        if target is None:
            current = self._current_segment()
            target = current[0] if current else None
        self._set_abstract_number(value, target)
        if target is not None:
            target["abstract_number_block_id"] = block.id

    def _clear_abstract_number_role(self, block_id: str) -> None:
        """Undo the marker metadata after a person untags a block.
        When the tagged marker is what started an article, untagging it also
        folds that article back into the one before it: the tag and the article
        boundary live and die together.
        """

        if self.document is None:
            return
        # Fold the marker's article back while the segments still remember that
        # this block started one; the cleanup below then erases the rest.
        self._remove_marker_boundary(block_id)
        marker_ids = [
            str(value)
            for value in self.document.metadata.get("abstract_number_block_ids", [])
            if str(value) and str(value) != block_id
        ]
        self.document.metadata["abstract_number_block_ids"] = marker_ids
        for segment in self._document_segments():
            if str(segment.get("abstract_number_block_id", "")) == block_id:
                segment.pop("abstract_number_block_id", None)

    def _remove_marker_boundary(self, block_id: str) -> None:
        """Fold back the article a tagged marker created, if it still exists."""

        segments = self._document_segments()
        if not segments:
            return
        started_here = [
            segment
            for segment in segments
            if str(segment.get("abstract_number_block_id", "")) == block_id
            and str(segment.get("boundary_source", "")) == SOURCE_MANUAL
            and int(segment.get("index", 0)) > 1
        ]
        if not started_here:
            return
        ordered = self._ensure_reading_orders()
        segmentation = self._article_segmentation(ordered)
        if segmentation is None:
            return
        boundary = next(
            (entry for entry in segmentation.boundaries if entry.block_id == block_id),
            None,
        )
        if boundary is None or boundary.marker_block_id != block_id:
            return
        if not segmentation.remove_boundary(block_id):
            return
        self._push_article_undo("merge marker article back")
        self._replace_article_segments(
            segmentation.to_legacy_segments(self.document.blocks)
        )
        self._report_segmentation_issues(segmentation)
        self._refresh_article_buttons()
        self._notify(
            f"Untagged marker {block_id}; its article folded into the one before it."
        )

    def _sync_block_abstract_number_role(self, block, previous_role: str) -> None:
        """Apply or undo marker metadata after a manual role change.

        Tagging a block ``abstract_number`` does more than record the marker:
        when the marker prints a fresh number it also starts a new article at
        that block, so the classification itself drives the article boundaries
        instead of a separate split step.
        """

        if block.role == "abstract_number":
            # Cut first: the marker starts the new article, so the number that
            # _apply_abstract_number_role records must land on the article the
            # marker starts, not on the one that happens to contain the block
            # before the cut exists.
            self._maybe_split_article_at_marker(block)
            self._apply_abstract_number_role(block)
        elif previous_role == "abstract_number":
            self._clear_abstract_number_role(block.id)

    # A running head or banner line is page furniture, never an article
    # marker, so tagging one by mistake must not cut the article there.
    MARKER_NOISE = re.compile(
        r"(?:\b\d{4}\b.*\b(?:symposium|congress|conference|meeting|society)\b"
        r"|\b(?:symposium|congress|conference|meeting|society)\b.*\b\d{4}\b"
        r"|\u00a9|copyright)",
        re.IGNORECASE,
    )

    def _marker_starts_article(self, block) -> bool:
        """Return True when tagging this marker should start a new article."""

        if self.MARKER_NOISE.search(str(block.text or "")):
            return False
        value = abstract_number_marker(str(block.text or ""))
        if not value:
            # A person tagged it deliberately: a bare marker such as "P-605"
            # still counts, but only when the line holds nothing else.
            cleaned = " ".join(str(block.text or "").split())
            return bool(self.MANUAL_ARTICLE_NUMBER.fullmatch(cleaned))
        return True

    def _maybe_split_article_at_marker(self, block) -> None:
        """Start a new article when a tagged marker prints a fresh number.
        The user classifies the roles of an article, and the moment they tag a
        fresh ``abstract_number`` the classification cuts the stream there: the
        article before the marker is complete, and everything from the marker
        on belongs to the next one. Tagging the same marker again (a corrected
        number) only renames the article it already starts.
        """

        if self.document is None:
            return
        number = self._manual_article_number(block)
        if not number or not self._marker_starts_article(block):
            return
        ordered = self._ensure_reading_orders()
        segmentation = self._article_segmentation(ordered)
        if segmentation is None:
            return
        position = next(
            (index for index, boundary in enumerate(segmentation.boundaries) if boundary.block_id == block.id),
            None,
        )
        if position is not None:
            # The marker already starts an article: adopt the corrected number
            # and remember the block as the marker, but never cut twice.
            current = segmentation.boundaries[position]
            if current.number != number or current.marker_block_id != block.id:
                segmentation.boundaries[position] = ArticleBoundary(
                    block_id=current.block_id,
                    source=current.source,
                    number=number,
                    marker_block_id=block.id,
                    deleted=current.deleted,
                )
                self._replace_article_segments(
                    segmentation.to_legacy_segments(self.document.blocks)
                )
                self._refresh_article_buttons()
                self._notify(f"Article at {block.id} renamed to ABSN {number}.")
            return
        if not segmentation.insert_boundary(
            block.id, source=SOURCE_MANUAL, number=number, marker_block_id=block.id
        ):
            return
        # The article before the marker may print its own number on its first
        # line; name it the same way a hand-made split does.
        self._name_articles_from_markers(segmentation, ordered)
        self._push_article_undo("split article at marker")
        self._replace_article_segments(
            segmentation.to_legacy_segments(self.document.blocks)
        )
        self._current_segment_key = block.id
        self._report_segmentation_issues(segmentation)
        self._refresh_selected_blocks_view()
        self._refresh_article_buttons()
        self._notify(
            f"Article {number} starts at {block.id}; the article before it is ready to finish."
        )

    def _sync_abstract_number_from_roles(self) -> str:
        """Adopt the Abstract No from a tagged marker when extraction missed it.

        A bare marker such as ``(S100)`` is now tagged by the classifier even
        though the extractor only matches it when more text follows the closing
        parenthesis, so without this the identifier would stay at the default
        ABSN. Runs once after load, so clearing the field later still sticks.
        """

        if self.document is None:
            return ""
        if self._normalize_abstract_number(self.document.metadata.get("abstract_number", "")):
            return ""
        for block in self.document.blocks:
            if block.role != "abstract_number":
                continue
            value = abstract_number_marker(block.text)
            if not value:
                continue
            target = next(
                (
                    segment
                    for segment in self._document_segments()
                    if block_matches_segment(block, segment)
                ),
                None,
            )
            self._set_abstract_number(value, target)
            if target is not None:
                target["abstract_number_block_id"] = block.id
            return value
        return ""

    @staticmethod
    def _normalize_abstract_number(value: object) -> str:
        """Return a bare abstract number, or an empty string when unusable."""

        cleaned = str(value or "").strip().strip("()[] \t")
        if not cleaned or cleaned.upper() == "ABSN":
            return ""
        if any(character.isspace() for character in cleaned):
            return ""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./-]{0,29}", cleaned):
            return ""
        return cleaned

    @staticmethod
    def _is_abstract_number_marker(text: str, abstract_number: str) -> bool:
        """Return true when a block holds nothing but the abstract-number marker."""

        remainder = re.sub(
            r"(?:abstract\s*(?:no\.?|number|nr\.?|id)?|absn)\s*[:#-]?",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        remainder = re.sub(re.escape(abstract_number), " ", remainder, flags=re.IGNORECASE)
        return not re.search(r"[A-Za-z0-9]", remainder)

    def _strip_abstract_number_from_title(self, title: str, abstract_number: str | None = None) -> str:
        """Remove a leading abstract-number marker from an article title."""

        if abstract_number is None:
            abstract_number = self.document.abstract_number() if self.document is not None else ""
        numbers = [str(abstract_number).strip()]
        if self.document is not None:
            aliases = self.document.metadata.get("abstract_number_aliases")
            if isinstance(aliases, list):
                # A corrected abstract number must still remove the marker left
                # behind by the earlier, wrong one.
                numbers.extend(str(alias).strip() for alias in aliases)
        for number in numbers:
            if not number or number.upper() == "ABSN":
                continue
            title = re.sub(
                rf"^\s*[\(\[]?{re.escape(number)}[\)\]]?\s*(?:\||\u2502|[:.\-\u2013\u2014])?\s+",
                "",
                title,
                count=1,
                flags=re.IGNORECASE,
            ).strip()
        return title

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
        locked_blocks = self._locked_selection_blocks(selected_blocks)
        if locked_blocks:
            self._warn_locked_blocks(locked_blocks)
            return

        if merge_source == "paragraph_continuation":
            selected_blocks = self._sort_paragraph_continuation_blocks(selected_blocks)
        else:
            selected_blocks = sorted(selected_blocks, key=reading_order_key)
        selected_ids = {block.id for block in selected_blocks}
        merged_block = self._merge_blocks(selected_blocks, merge_source=merge_source, join_with_space=join_with_space)
        remaining_blocks = [block for block in self.document.blocks if block.id not in selected_ids]
        remaining_blocks.append(merged_block)
        remaining_blocks = self._sort_reading_order(remaining_blocks)
        self.document.blocks = remaining_blocks
        # Fusing blocks changes which block starts an article.
        self._invalidate_segment_identity()
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
        return bool(block.role in {"title", "author", "corresponding_author", "affiliation", "abstract_number"})

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
        self.document.blocks.sort(key=reading_order_key)
        self._invalidate_segment_identity()
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
        if action_type == "article_segments":
            self._restore_article_snapshot(action)
            return
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

    def _restore_article_snapshot(self, action: dict[str, object]) -> None:
        """Put the articles back the way they were before a split or a merge."""

        if self.document is None:
            return
        before = action.get("before")
        if not isinstance(before, list):
            return
        self.document.metadata["article_segments"] = [
            dict(segment) for segment in before if isinstance(segment, dict)
        ]
        self._invalidate_segment_identity()
        known = {key for key in self._segment_boundary_ids() if key}
        finished_keys = action.get("finished_keys")
        self._finished_segment_keys = [
            str(key)
            for key in (finished_keys if isinstance(finished_keys, list) else [])
            if str(key) in known
        ]
        deleted_keys = action.get("deleted_keys")
        self._deleted_segment_keys = [
            str(key)
            for key in (deleted_keys if isinstance(deleted_keys, list) else [])
            if str(key) in known
        ]
        current_key = action.get("current_key")
        self._current_segment_key = str(current_key) if current_key in known else None
        self._sync_deleted_block_overlay()
        self._refresh_selected_blocks_view()
        self._refresh_article_buttons()
        self._notify(
            f"Undid {action.get('label', 'the article edit')}; "
            f"{len(self._document_segments())} article(s) restored."
        )

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
        selected_blocks.sort(key=reading_order_key)
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
        self.document.blocks.sort(key=reading_order_key)
        self._invalidate_segment_identity()
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
        # Standard inspector behavior: selecting a block anywhere (viewer,
        # tree, preview, review navigation) reveals its properties tab.
        self._show_work_tab(self.TAB_PROPERTIES)
        self.props.fields["Text"].setText(block.text)
        self.props.fields["Font"].setText(block.font_name or "-")
        self.props.fields["Font Size"].setText(f"{block.font_size:.1f}")
        # One decimal keeps long float lists readable and wrappable in the form.
        coords = ", ".join(f"{value:.1f}" for value in block.bbox)
        self.props.fields["Coordinates"].setText(f"x{coords} @ p{block.page}")
        self.props.fields["Page"].setText(str(block.page))
        role_source = str((block.metadata or {}).get("role_source", "") or "")
        confidence_label = f"{block.confidence:.2f}" + (f" ({role_source})" if role_source else "")
        self.props.fields["Confidence"].setText(confidence_label)
        self.props.fields["Detected Role"].setText(block.role)
        self.props.set_role(block.role, locked=self._article_locked(block))
        self._update_merged_details(block)

    def _on_viewer_block_selected(self, block_id: str, additive: bool) -> None:
        if self.document is None:
            return
        if not additive:
            self._range_anchor_block_id = block_id
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

    def _on_viewer_block_range_selected(self, block_id: str) -> None:
        """Select the inclusive document-order range from the current anchor."""
        if self.document is None:
            return
        if not getattr(self, "_range_anchor_block_id", None):
            self._on_viewer_block_selected(block_id, False)
            return
        ordered = sorted(self.document.blocks, key=reading_order_key)
        ids = [block.id for block in ordered]
        try:
            start = ids.index(self._range_anchor_block_id)
            end = ids.index(block_id)
        except ValueError:
            self._on_viewer_block_selected(block_id, False)
            return
        if start > end:
            start, end = end, start
        self._selected_zone_id = None
        self._selected_zone_ids.clear()
        self._selected_block_ids = set(ids[start : end + 1])
        self.viewer.set_selected_blocks(self._selected_block_ids, block_id)
        self.viewer.set_selected_zones(set())
        self._sync_tree_selection()
        self._update_selection_status()
        self._update_selection_panel()
        self._select_block_by_id(block_id, preserve_selection=True)
        self._update_html_preview()
        self.html_preview.scrollToAnchor(self._html_preview_anchor_for_block(block_id))

    def _on_viewer_blocks_rect_selected(self, block_ids: list) -> None:
        """Replace the merge selection with blocks intersecting a drag rectangle."""
        if self.document is None:
            return
        selected_ids = {str(block_id) for block_id in block_ids}
        self._selected_block_ids = selected_ids
        self._selected_zone_id = None
        self._selected_zone_ids.clear()
        self.viewer.set_selected_blocks(selected_ids, next(iter(selected_ids), None))
        self.viewer.set_selected_zones(set())
        self._sync_tree_selection()
        self._update_selection_status()
        self._update_selection_panel()
        self._update_html_preview()
        if selected_ids:
            primary_id = next((block.id for block in self.document.blocks if block.id in selected_ids), None)
            if primary_id:
                self._select_block_by_id(primary_id, preserve_selection=True)

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
        current = self._current_segment() if self._current_segment_key is not None else None
        if current is not None:
            segment, _key = current
            number = str(segment.get("abstract_number", "")).strip() or "ABSN"
            scope_note = f" Showing article {number} only; pick another tab to switch."
        else:
            scope_note = " Showing every detected block until an article is chosen."
        self.tree_hint.setText(
            f"Selected blocks: {block_count}; zones: {zone_count}.{scope_note} "
            "Use Ctrl-click for individual blocks or Ctrl+Shift-click for a range."
        )

    def _update_selection_panel(self) -> None:
        """Show the exact blocks currently queued for a merge."""

        self._update_split_segment_button()
        if self.document is None:
            self.selection_hint.setText("Load a PDF to start selecting blocks.")
            self.selection_list.clear()
            return
        selected_blocks = [block for block in self.document.blocks if block.id in self._selected_block_ids]
        selected_zones = [zone for zone in self.document.zones if zone.id in self._selected_zone_ids]
        selected_blocks.sort(key=reading_order_key)
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
        self.metadata_panel.set_merge_details(None)
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
            block.metadata["role_source"] = "heuristic"
        # Deterministic cleanup of predictable heuristic mistakes.
        refine_report = self.auto_refiner.refine(self.document.blocks)
        self._last_refine_report = refine_report
        self._review_block_ids = list(refine_report.review_ids)
        self._review_index = -1
        self._finished_segment_keys = []
        self._deleted_segment_keys = []
        self._current_segment_key = None
        self._invalidate_segment_identity()
        self.viewer.set_deleted_block_ids(set())
        if refine_report.changed_count:
            self._log(
                f"Auto-refined {refine_report.changed_count} block role(s); "
                f"{refine_report.review_count} need review."
            )
        self._sync_abstract_number_from_roles()
        self._update_document_model()
        self._log_link_issues()
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
        self.metadata_panel.set_merge_details(None)
        self.props.set_role(None)
        self._refresh_article_buttons()

    def _update_merged_details(self, block) -> None:
        """Show merge metadata for merged blocks and clear it otherwise."""

        metadata = block.metadata or {}
        merged_ids = metadata.get("merged_block_ids")
        if not merged_ids:
            self.metadata_panel.set_merge_details(None)
            return
        details = {
            "Block ID": block.id,
            "Merge Count": str(metadata.get("merge_count", len(merged_ids))),
            "Merge Source": str(metadata.get("merge_source", "unknown")),
            "Merged From": ", ".join(str(item) for item in merged_ids),
        }
        self.metadata_panel.set_merge_details(details)

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
        zone_locked = any(self._article_locked(block) for block in self._blocks_for_zone(zone))
        self.props.set_locked_state(zone_locked)
        self.props.apply_role_btn.setEnabled(not zone_locked)

    def apply_abstract_number(self) -> None:
        """Apply the reviewed abstract number to the current article."""

        if self.document is None:
            QMessageBox.information(self, "No document", "Load a PDF first.")
            return
        value = self.metadata_panel.abstract_number_editor.text().strip()
        if value and not self._normalize_abstract_number(value):
            QMessageBox.warning(
                self,
                "Invalid abstract number",
                "Enter a single identifier such as \"4349\" or \"S100\" without spaces or labels.",
            )
            return
        abstract_number = self._normalize_abstract_number(value) or "ABSN"
        current = self._current_segment()
        self._set_abstract_number(abstract_number, current[0] if current else None)
        self._update_document_model()
        self.viewer.set_blocks([asdict(block) for block in self.document.blocks])
        self._populate_tree()
        self._update_html_preview()
        self._refresh_article_buttons()
        self._log(f"Set abstract number to {abstract_number}.")

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
            zone_blocks = self._blocks_for_zone(zone)
            locked_zone_blocks = self._locked_selection_blocks(zone_blocks)
            if locked_zone_blocks:
                self._warn_locked_blocks(locked_zone_blocks)
                return
            self._push_zone_update_undo(zone)
            zone.label = new_role
            if zone.source == "auto":
                zone.source = "user"
            for block in self._blocks_for_zone(zone):
                previous_role = block.role
                block.role = new_role
                self._sync_block_abstract_number_role(block, previous_role)
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

        selected_blocks = [
            block for block in self.document.blocks if block.id in self._selected_block_ids
        ]
        if not selected_blocks:
            QMessageBox.information(self, "Select blocks", "Select at least one block to assign a role.")
            return
        # A document has exactly one title, so the title role stays a single block.
        if new_role == "title" and len(selected_blocks) != 1:
            QMessageBox.information(
                self, "Select one title block", "Select exactly one block to assign the title role."
            )
            return
        locked_blocks = self._locked_selection_blocks(selected_blocks)
        if locked_blocks:
            self._warn_locked_blocks(locked_blocks)
            return

        changed_blocks = []
        for block in selected_blocks:
            if block.role == new_role:
                continue
            previous_role = block.role
            block.role = new_role
            self._sync_block_abstract_number_role(block, previous_role)
            changed_blocks.append(block)
        if not changed_blocks:
            return

        title_block_id = next(
            (block.id for block in changed_blocks if new_role == "title"), None
        )
        if title_block_id is not None:
            self.document.metadata["manual_title_block_id"] = title_block_id
            for other in self.document.blocks:
                if other.id != title_block_id and other.role == "title":
                    other.role = "unclassified"
        self._selected_zone_id = None
        self._selected_zone_ids.clear()
        self._update_document_model()
        self.viewer.set_blocks([asdict(b) for b in self.document.blocks])
        self.viewer.set_zones([asdict(zone) for zone in self.document.zones])
        self._populate_tree()
        self._sync_tree_selection()
        block_id = title_block_id or next(iter(self._selected_block_ids))
        self.viewer.set_selected_blocks(self._selected_block_ids, block_id)
        self._update_selection_status()
        self._update_selection_panel()
        self._select_block_by_id(block_id, preserve_selection=True)
        self._update_selection_panel()
        self._log(f"Reassigned {len(changed_blocks)} block(s) to role '{new_role}'")

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
