"""PDF preview widget."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget


class PdfPageLabel(QLabel):
    """Clickable page renderer with block and zone interaction."""

    blockSelected = Signal(str, bool)
    zoneSelected = Signal(str, bool)
    zoneMoved = Signal(str, list)
    zoneCreated = Signal(list)
    zoneEditFinished = Signal(str)

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._zone_drag_id: str | None = None
        self._zone_drag_start: QPointF | None = None
        self._zone_drag_bbox: list[float] | None = None
        self._zone_drag_handle: str | None = None
        self._draw_start: QPointF | None = None
        self._draw_current: QPointF | None = None
        self._drawing_zone = False
        self.setMouseTracking(True)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        additive = bool(event.modifiers() & Qt.ControlModifier)
        hit = self._hit_test(event.position() if hasattr(event, "position") else event.pos(), prefer_blocks=additive)
        if hit:
            kind, item_id = hit.split(":", 1)
            if kind == "handle":
                zone_id, handle_name = item_id.split(":", 1)
                self.zoneSelected.emit(zone_id, additive)
                self._zone_drag_handle = handle_name
                self._zone_drag_id = zone_id
                self._zone_drag_start = event.position() if hasattr(event, "position") else event.pos()
                self._zone_drag_bbox = self._current_zone_bbox(zone_id)
                event.accept()
                return
            if kind == "zone":
                self.zoneSelected.emit(item_id, additive)
                self._zone_drag_handle = self._zone_handle_hit_test(item_id, event.position() if hasattr(event, "position") else event.pos())
                self._zone_drag_id = item_id
                self._zone_drag_start = event.position() if hasattr(event, "position") else event.pos()
                self._zone_drag_bbox = self._current_zone_bbox(item_id)
            else:
                self.blockSelected.emit(item_id, additive)
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._drawing_zone = True
            self._draw_start = event.position() if hasattr(event, "position") else event.pos()
            self._draw_current = self._draw_start
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._zone_drag_id and self._zone_drag_start and self._zone_drag_bbox:
            current = event.position() if hasattr(event, "position") else event.pos()
            dx = current.x() - self._zone_drag_start.x()
            dy = current.y() - self._zone_drag_start.y()
            scale = float(self.property("pageScale") or 1.0)
            x0, y0, x1, y1 = self._zone_drag_bbox
            if self._zone_drag_handle:
                nx0, ny0, nx1, ny1 = x0, y0, x1, y1
                if "left" in self._zone_drag_handle:
                    nx0 = min(x0 + (dx / scale), x1 - 5)
                if "right" in self._zone_drag_handle:
                    nx1 = max(x1 + (dx / scale), x0 + 5)
                if "top" in self._zone_drag_handle:
                    ny0 = min(y0 + (dy / scale), y1 - 5)
                if "bottom" in self._zone_drag_handle:
                    ny1 = max(y1 + (dy / scale), y0 + 5)
                self.zoneMoved.emit(self._zone_drag_id, [nx0, ny0, nx1, ny1])
            else:
                self.zoneMoved.emit(
                    self._zone_drag_id,
                    [x0 + (dx / scale), y0 + (dy / scale), x1 + (dx / scale), y1 + (dy / scale)],
                )
            event.accept()
            return
        if self._drawing_zone and self._draw_start:
            self._draw_current = event.position() if hasattr(event, "position") else event.pos()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        finished_zone_id = self._zone_drag_id
        self._zone_drag_id = None
        self._zone_drag_start = None
        self._zone_drag_bbox = None
        self._zone_drag_handle = None
        if finished_zone_id:
            self.zoneEditFinished.emit(finished_zone_id)
        if self._drawing_zone and self._draw_start and self._draw_current:
            scale = float(self.property("pageScale") or 1.0)
            x0 = min(self._draw_start.x(), self._draw_current.x()) / scale
            y0 = min(self._draw_start.y(), self._draw_current.y()) / scale
            x1 = max(self._draw_start.x(), self._draw_current.x()) / scale
            y1 = max(self._draw_start.y(), self._draw_current.y()) / scale
            if abs(x1 - x0) > 2 and abs(y1 - y0) > 2:
                self.zoneCreated.emit([x0, y0, x1, y1])
        self._drawing_zone = False
        self._draw_start = None
        self._draw_current = None
        super().mouseReleaseEvent(event)

    def _hit_test(self, pos: QPointF, prefer_blocks: bool = False) -> str | None:
        regions = self.property("blockRegions") or []
        selected_zone_id = self.property("selectedZoneId")
        selected_zone_ids = self.property("selectedZoneIds") or set()
        x = float(pos.x())
        y = float(pos.y())
        if prefer_blocks:
            for region in reversed(regions):
                rect = region.get("rect")
                if (
                    region.get("kind") == "block"
                    and rect
                    and rect[0] <= x <= rect[2]
                    and rect[1] <= y <= rect[3]
                ):
                    return f"block:{region.get('id')}"
            for region in reversed(regions):
                rect = region.get("rect")
                if (
                    region.get("kind") == "zone"
                    and rect
                    and rect[0] <= x <= rect[2]
                    and rect[1] <= y <= rect[3]
                ):
                    return f"zone:{region.get('id')}"
        for region in reversed(regions):
            rect = region.get("rect")
            if (
                region.get("kind") == "zone"
                and rect
                and rect[0] <= x <= rect[2]
                and rect[1] <= y <= rect[3]
                and (
                    str(region.get("id")) == str(selected_zone_id)
                    or str(region.get("id")) in {str(value) for value in selected_zone_ids}
                )
            ):
                return f"zone:{region.get('id')}"
        # Prioritize text blocks over zones so paragraph clicks do not grab the enclosing overlay.
        for region in reversed(regions):
            rect = region.get("rect")
            if rect and rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]:
                if region.get("kind") == "block":
                    return f"block:{region.get('id')}"
        for region in reversed(regions):
            rect = region.get("rect")
            if (
                region.get("kind") == "zone"
                and rect
                and rect[0] <= x <= rect[2]
                and rect[1] <= y <= rect[3]
            ):
                return f"zone:{region.get('id')}"
        return None

    def _current_zone_bbox(self, zone_id: str) -> list[float] | None:
        zones = self.property("zones") or []
        for zone in zones:
            if str(zone.get("id")) == zone_id:
                bbox = zone.get("bbox")
                if isinstance(bbox, list) and len(bbox) == 4:
                    return [float(v) for v in bbox]
        return None

    def _zone_handle_hit_test(self, zone_id: str, pos: QPointF) -> str | None:
        zones = self.property("zones") or []
        scale = float(self.property("pageScale") or 1.0)
        x = float(pos.x())
        y = float(pos.y())
        handle_size = 12.0
        for zone in zones:
            if str(zone.get("id")) != zone_id:
                continue
            bbox = zone.get("bbox")
            if not (isinstance(bbox, list) and len(bbox) == 4):
                return None
            rect = fitz.Rect(bbox)
            scaled = fitz.Rect(rect.x0 * scale, rect.y0 * scale, rect.x1 * scale, rect.y1 * scale)
            corners = {
                "top_left": (scaled.x0, scaled.y0),
                "top_right": (scaled.x1, scaled.y0),
                "bottom_left": (scaled.x0, scaled.y1),
                "bottom_right": (scaled.x1, scaled.y1),
            }
            for name, (hx, hy) in corners.items():
                if abs(x - hx) <= handle_size and abs(y - hy) <= handle_size:
                    return name
        return None


class PDFViewer(QWidget):
    """Render one PDF page at a time and draw block and zone overlays."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pdf_path: Path | None = None
        self._doc: fitz.Document | None = None
        self._page_index = 0
        self._page_scale = 1.6
        self._blocks: list[dict[str, Any]] = []
        self._zones: list[dict[str, Any]] = []
        self._regions: list[dict[str, Any]] = []
        self._selected_block_id: str | None = None
        self._selected_block_ids: set[str] = set()
        self._selected_zone_id: str | None = None
        self._selected_zone_ids: set[str] = set()
        self._draft_zone_rect: list[float] | None = None
        self._continuation_links: list[tuple[str, str]] = []
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.prev_btn = QPushButton("Prev Page")
        self.next_btn = QPushButton("Next Page")
        self.zoom_out_btn = QPushButton("Zoom -")
        self.zoom_in_btn = QPushButton("Zoom +")
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.next_btn)
        controls.addWidget(self.zoom_out_btn)
        controls.addWidget(self.zoom_in_btn)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.label = PdfPageLabel("Drop a PDF to preview it here.")
        self.label.setWordWrap(True)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.label)
        layout.addWidget(area)
        self.prev_btn.clicked.connect(self.previous_page)
        self.next_btn.clicked.connect(self.next_page)
        self.zoom_out_btn.clicked.connect(lambda: self.set_zoom(max(self._page_scale - 0.2, 0.6)))
        self.zoom_in_btn.clicked.connect(lambda: self.set_zoom(min(self._page_scale + 0.2, 4.0)))
        self.label.setProperty("pageScale", self._page_scale)

    def load_pdf(self, pdf_path: str | Path) -> None:
        self._pdf_path = Path(pdf_path)
        self._doc = fitz.open(self._pdf_path)
        self._page_index = 0
        self.render_page(0)

    def render_page(self, page_index: int) -> None:
        if self._doc is None or self._doc.page_count == 0:
            return
        page_index = max(0, min(page_index, self._doc.page_count - 1))
        self._page_index = page_index
        page = self._doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(self._page_scale, self._page_scale), alpha=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        self._paint_overlays(QPixmap.fromImage(image))

    def set_zoom(self, scale: float) -> None:
        self._page_scale = scale
        self.label.setProperty("pageScale", self._page_scale)
        self.render_page(self._page_index)

    def next_page(self) -> None:
        if self._doc is None:
            return
        self.render_page(self._page_index + 1)

    def previous_page(self) -> None:
        if self._doc is None:
            return
        self.render_page(self._page_index - 1)

    def set_blocks(self, blocks: list[dict[str, Any]]) -> None:
        self._blocks = blocks
        if self._doc is not None:
            self.render_page(self._page_index)

    def set_zones(self, zones: list[dict[str, Any]]) -> None:
        self._zones = zones
        self.label.setProperty("zones", self._zones)
        if self._doc is not None:
            self.render_page(self._page_index)

    def set_selected_zone(self, zone_id: str | None) -> None:
        self._selected_zone_id = zone_id
        self._selected_zone_ids = {zone_id} if zone_id else set()
        self.label.setProperty("selectedZoneId", self._selected_zone_id)
        self.label.setProperty("selectedZoneIds", self._selected_zone_ids)
        if self._doc is not None:
            self.render_page(self._page_index)

    def set_selected_zones(self, zone_ids: set[str], primary_zone_id: str | None = None) -> None:
        self._selected_zone_ids = set(zone_ids)
        self._selected_zone_id = primary_zone_id if primary_zone_id in self._selected_zone_ids else next(iter(self._selected_zone_ids), None)
        self.label.setProperty("selectedZoneId", self._selected_zone_id)
        self.label.setProperty("selectedZoneIds", self._selected_zone_ids)
        if self._doc is not None:
            self.render_page(self._page_index)

    def set_selected_blocks(self, block_ids: set[str], primary_block_id: str | None = None) -> None:
        """Update highlighted selected blocks and optionally jump to the primary block."""

        self._selected_block_ids = set(block_ids)
        self._selected_block_id = primary_block_id
        if self._continuation_links:
            selected = {str(block_id) for block_id in self._selected_block_ids}
            link_ids = {block_id for link in self._continuation_links for block_id in link}
            if not link_ids.issubset(selected):
                self._continuation_links = []
        if primary_block_id:
            target = next((block for block in self._blocks if str(block.get("id")) == primary_block_id), None)
            if target is not None:
                self.render_page(int(target.get("page", 1)) - 1)
                return
        if self._doc is not None:
            self.render_page(self._page_index)

    def set_draft_zone(self, bbox: list[float] | None) -> None:
        self._draft_zone_rect = bbox
        if self._doc is not None:
            self.render_page(self._page_index)

    def set_continuation_links(self, links: list[tuple[str, str]]) -> None:
        """Show paragraph continuation arrows from source block to next block."""

        self._continuation_links = [(str(source), str(target)) for source, target in links]
        if self._doc is not None:
            self.render_page(self._page_index)

    def selected_zone_id(self) -> str | None:
        return self._selected_zone_id

    def _paint_overlays(self, pixmap: QPixmap) -> None:
        if self._doc is None:
            return
        self._regions = []
        painter = QPainter(pixmap)
        base_pen = QPen(QColor(220, 38, 38), 2)
        selected_pen = QPen(QColor(34, 197, 94), 3)
        multi_selected_pen = QPen(QColor(37, 99, 235), 3)
        zone_pen = QPen(QColor(245, 158, 11), 2, Qt.DashLine)
        selected_zone_pen = QPen(QColor(234, 88, 12), 3)
        draft_pen = QPen(QColor(168, 85, 247), 2, Qt.DotLine)
        continuation_pen = QPen(QColor(220, 38, 38), 4)
        handle_brush = QColor(234, 88, 12)
        visible_block_rects: dict[str, fitz.Rect] = {}
        for block in self._blocks:
            if int(block.get("page", 0)) != self._page_index + 1:
                continue
            bbox = block.get("bbox") or [0, 0, 0, 0]
            rect = fitz.Rect(bbox)
            scaled = fitz.Rect(rect.x0 * self._page_scale, rect.y0 * self._page_scale, rect.x1 * self._page_scale, rect.y1 * self._page_scale)
            block_id = str(block.get("id"))
            if block_id == self._selected_block_id:
                painter.setPen(selected_pen)
            elif block_id in self._selected_block_ids:
                painter.setPen(multi_selected_pen)
            else:
                painter.setPen(base_pen)
            painter.drawRect(int(scaled.x0), int(scaled.y0), int(scaled.width), int(scaled.height))
            visible_block_rects[block_id] = scaled
            self._regions.append({"id": block_id, "kind": "block", "rect": [scaled.x0, scaled.y0, scaled.x1, scaled.y1]})
        for zone in self._zones:
            if int(zone.get("page", 0)) != self._page_index + 1:
                continue
            bbox = zone.get("bbox") or [0, 0, 0, 0]
            zone_id = str(zone.get("id"))
            zone_bboxes = zone.get("bboxes") or [bbox]
            scaled_union = fitz.Rect(bbox)
            scaled_union = fitz.Rect(
                scaled_union.x0 * self._page_scale,
                scaled_union.y0 * self._page_scale,
                scaled_union.x1 * self._page_scale,
                scaled_union.y1 * self._page_scale,
            )
            painter.setPen(selected_zone_pen if zone_id in self._selected_zone_ids else zone_pen)
            for zone_bbox in zone_bboxes:
                rect = fitz.Rect(zone_bbox)
                scaled = fitz.Rect(
                    rect.x0 * self._page_scale,
                    rect.y0 * self._page_scale,
                    rect.x1 * self._page_scale,
                    rect.y1 * self._page_scale,
                )
                painter.drawRect(int(scaled.x0), int(scaled.y0), int(scaled.width), int(scaled.height))
                self._regions.append(
                    {
                        "id": zone_id,
                        "kind": "zone",
                        "source": str(zone.get("source", "auto")),
                        "rect": [scaled.x0, scaled.y0, scaled.x1, scaled.y1],
                    }
                )
            if zone_id == self._selected_zone_id:
                painter.setBrush(handle_brush)
                handles = {
                    "top_left": (scaled_union.x0, scaled_union.y0),
                    "top_right": (scaled_union.x1, scaled_union.y0),
                    "bottom_left": (scaled_union.x0, scaled_union.y1),
                    "bottom_right": (scaled_union.x1, scaled_union.y1),
                }
                for handle_name, (hx, hy) in handles.items():
                    painter.drawRect(int(hx - 5), int(hy - 5), 10, 10)
                    self._regions.append(
                        {
                            "id": f"{zone_id}:{handle_name}",
                            "kind": "handle",
                            "rect": [hx - 12, hy - 12, hx + 12, hy + 12],
                        }
                    )
        if self._draft_zone_rect:
            rect = fitz.Rect(self._draft_zone_rect)
            scaled = fitz.Rect(rect.x0 * self._page_scale, rect.y0 * self._page_scale, rect.x1 * self._page_scale, rect.y1 * self._page_scale)
            painter.setPen(draft_pen)
            painter.drawRect(int(scaled.x0), int(scaled.y0), int(scaled.width), int(scaled.height))
        if self._continuation_links:
            painter.setPen(continuation_pen)
            painter.setBrush(QBrush(QColor(220, 38, 38)))
            for source_id, target_id in self._continuation_links:
                source = visible_block_rects.get(source_id)
                target = visible_block_rects.get(target_id)
                if source is None or target is None:
                    continue
                start_x = source.x1
                start_y = source.y0 + source.height / 2
                end_x = target.x0
                end_y = target.y0 + target.height / 2
                if target.x0 <= source.x0:
                    start_x = source.x0 + source.width / 2
                    start_y = source.y1
                    end_x = target.x0 + target.width / 2
                    end_y = target.y0
                painter.drawLine(int(start_x), int(start_y), int(end_x), int(end_y))
                direction_x = 1 if end_x >= start_x else -1
                arrow_size = 12
                points = [
                    QPointF(end_x, end_y),
                    QPointF(end_x - direction_x * arrow_size, end_y - arrow_size / 2),
                    QPointF(end_x - direction_x * arrow_size, end_y + arrow_size / 2),
                ]
                painter.drawPolygon(points)
        painter.end()
        self.label.setProperty("blockRegions", self._regions)
        self.label.setPixmap(pixmap)
