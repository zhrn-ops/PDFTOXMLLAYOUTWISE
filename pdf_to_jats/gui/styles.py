"""Application stylesheet."""

APP_STYLE = """
QMainWindow, QWidget, QSplitter, QScrollArea {
    background: #1e1e1e;
    color: #e6e6e6;
}
QTreeWidget, QListWidget, QTextEdit, QPlainTextEdit {
    background: #2b2b2b;
    border: 1px solid #606060;
    border-radius: 4px;
    color: #e6e6e6;
}
QTreeWidget::item {
    color: #e6e6e6;
    padding: 4px 2px;
}
QTreeWidget::item:selected {
    background: #155d86;
    color: #ffffff;
}
QHeaderView::section {
    background: #252525;
    color: #e6e6e6;
    padding: 6px;
    border: none;
}
QGroupBox {
    color: #e6e6e6;
    font-weight: 600;
    border: 1px solid #606060;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 8px;
}
QComboBox {
    color: #e6e6e6;
    background: #303030;
    border: 1px solid #606060;
    border-radius: 4px;
    padding: 4px 8px;
}
QComboBox QAbstractItemView {
    color: #e6e6e6;
    background: #303030;
}
QLineEdit {
    color: #e6e6e6;
    background: #303030;
    border: 1px solid #606060;
    border-radius: 4px;
    padding: 4px 8px;
}
QLabel {
    color: #e6e6e6;
}
QPushButton {
    padding: 8px 12px;
    border-radius: 6px;
    background: #222;
    color: white;
    border: 1px solid #606060;
}
QPushButton:hover { background: #444; }
QPushButton:disabled {
    background: #2b2b2b;
    color: #6f6f6f;
    border: 1px solid #3c3c3c;
}
QComboBox:disabled, QLineEdit:disabled {
    color: #6f6f6f;
    background: #2b2b2b;
}
QTabWidget::pane {
    border: 1px solid #606060;
    border-radius: 4px;
    background: #2b2b2b;
}
QTabBar::tab {
    background: #252525;
    color: #e6e6e6;
    padding: 5px 14px;
    border: 1px solid #606060;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 3px;
}
QTabBar::tab:selected {
    background: #155d86;
    color: #ffffff;
}
QTabBar::tab:!selected:hover {
    background: #303030;
}
QTabBar::tab:disabled {
    color: #6f6f6f;
}
"""
