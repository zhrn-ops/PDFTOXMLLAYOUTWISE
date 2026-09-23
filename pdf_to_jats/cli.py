"""Headless command-line entry point for PDF to ANI XML conversion.

This reuses the exact same pipeline the desktop application runs
(extract -> classify -> auto-refine -> export), so the XML produced here
matches what the "Auto Pipeline" button writes, without any clicking.

Usage::

    python pdf_to_jats/cli.py paper.pdf
    python pdf_to_jats/cli.py "papers/*.pdf" -o output/batch
    python pdf_to_jats/cli.py C:/path/to/folder
"""

from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The pipeline only needs Qt for the (invisible) window that hosts it, so run
# without a display server.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pdf_to_jats.gui.main_window import MainWindow
from pdf_to_jats.utils.config import load_config
from pdf_to_jats.utils.logger import configure_logging


def collect_pdfs(inputs: list[Path]) -> list[Path]:
    """Expand files, folders, and glob patterns into a sorted PDF list."""

    found: list[Path] = []
    for raw in inputs:
        text = str(raw)
        if any(ch in text for ch in "*?["):
            found.extend(Path(p) for p in glob.glob(text, recursive=True))
            continue
        if raw.is_dir():
            found.extend(sorted(raw.rglob("*.pdf")))
            continue
        found.append(raw)
    unique = {path.resolve() for path in found if path.is_file() and path.suffix.lower() == ".pdf"}
    return sorted(unique)


class Converter:
    """One invisible MainWindow reused across PDFs for a whole batch."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.output_root = (output_dir or self.window.config.generated_xml_dir).resolve()

    def convert(self, pdf_path: Path, isolate: bool) -> list[Path]:
        """Run the pipeline for one PDF and return the XML files it rewrote."""

        pdf_path = pdf_path.resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        # Each PDF gets its own folder in a batch, otherwise every run
        # overwrites the same article.xml and prior work is lost.
        target = self.output_root / pdf_path.stem if isolate else self.output_root
        target.mkdir(parents=True, exist_ok=True)
        self.window.config.generated_xml_dir = target

        def snapshot() -> dict[Path, int]:
            return {
                path.resolve(): path.stat().st_mtime_ns
                for path in target.glob("*.xml")
            }

        before = snapshot()
        self.window._load_pdf_path(str(pdf_path))
        if self.window.document is None:
            raise RuntimeError(f"Extraction produced no document for {pdf_path}")
        self.window.run_auto_pipeline()
        self.app.processEvents()
        after = snapshot()
        return sorted(path for path, mtime in after.items() if before.get(path) != mtime)

    def review_ids(self) -> list[str]:
        return list(self.window._review_block_ids)

    def block_count(self) -> int:
        return len(self.window.document.blocks) if self.window.document else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf_to_jats",
        description="Convert scientific PDFs into Elsevier ANI XML without the GUI.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="PDF files, folders, or glob patterns to convert",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Folder for the generated XML (default: output/generated_xml)",
    )
    parser.add_argument(
        "-f",
        "--flat",
        action="store_true",
        help="Write every XML into one folder instead of a per-PDF subfolder",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    output_dir = args.output_dir if args.output_dir is not None else load_config().generated_xml_dir
    pdfs = collect_pdfs(args.inputs)
    if not pdfs:
        print("No PDFs matched the given inputs.", file=sys.stderr)
        return 1

    isolate = not args.flat
    converter = Converter(output_dir)
    failures = 0
    for pdf_path in pdfs:
        try:
            written = converter.convert(pdf_path, isolate=isolate)
        except (OSError, RuntimeError, ValueError) as error:
            print(f"[FAIL] {pdf_path.name}: {error}", file=sys.stderr)
            failures += 1
            continue
        review = converter.review_ids()
        status = f"{len(review)} flagged for review" if review else "no review flags"
        print(
            f"[OK] {pdf_path.name} | {converter.block_count()} blocks | {status} "
            f"| {len(written)} XML file(s)"
        )
        for path in written:
            print(f"       {path}")

    total = len(pdfs)
    print(f"\nConverted {total - failures}/{total} PDF(s) into {converter.output_root}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
