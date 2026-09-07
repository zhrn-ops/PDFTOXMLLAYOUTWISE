"""Application entry point."""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from pdf_to_jats.gui.main_window import MainWindow
from pdf_to_jats.utils.logger import configure_logging


def main() -> int:
    """Start the desktop application."""
    configure_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
