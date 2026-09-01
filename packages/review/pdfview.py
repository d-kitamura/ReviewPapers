"""PyMuPDF(fitz) を使ったPDFページ描画とクリック座標⇔PDF座標の変換。

座標系: fitz / Tk Canvas はどちらも左上原点・y下向き。zoom はピクセル/ポイント倍率
（fitz.Matrix(zoom, zoom) に渡す値そのもの）。呼び出し側は Canvas のスクロール
オフセットを差し引いたキャンバス内座標を渡すこと。
"""

from __future__ import annotations

import fitz
from PIL import Image


def open_pdf(path: str) -> fitz.Document:
    return fitz.open(path)


def render_page(doc: fitz.Document, page_number: int, zoom: float) -> Image.Image:
    page = doc[page_number]
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def page_size_pt(doc: fitz.Document, page_number: int) -> tuple[float, float]:
    page = doc[page_number]
    return page.rect.width, page.rect.height


def canvas_to_pdf_point(canvas_x: float, canvas_y: float, zoom: float) -> tuple[float, float]:
    return canvas_x / zoom, canvas_y / zoom


def pdf_point_to_canvas(pdf_x: float, pdf_y: float, zoom: float) -> tuple[float, float]:
    return pdf_x * zoom, pdf_y * zoom
