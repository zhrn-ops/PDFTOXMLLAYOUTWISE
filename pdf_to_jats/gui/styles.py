"""Application stylesheet."""

APP_STYLE = """
QMainWindow { background: #f6f3ee; }
QTreeWidget, QListWidget, QTextEdit, QPlainTextEdit {
    background: white;
    border: 1px solid #c9c1b8;
    border-radius: 6px;
    color: #161616;
}
QTreeWidget::item {
    color: #161616;
    padding: 4px 2px;
}
QTreeWidget::item:selected {
    background: #d8e8ff;
    color: #111111;
}
QHeaderView::section {
    background: #2a2a2a;
    color: white;
    padding: 6px;
    border: none;
}
QGroupBox {
    color: #1f1f1f;
    font-weight: 600;
}
QLabel {
    color: #1f1f1f;
}
QPushButton {
    padding: 8px 12px;
    border-radius: 6px;
    background: #222;
    color: white;
}
QPushButton:hover { background: #444; }
"""
