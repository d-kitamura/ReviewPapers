"""Create linked review PDFs with rendered mathematics and copyable TeX source."""

from __future__ import annotations

import os
from typing import Optional

import fitz

from packages.review import texrender
from packages.review.record import ReviewRecord, sorted_pins

A4_W, A4_H = 595, 842
MARGIN = 36
FONT_SIZE = 9
CODE_FONT_SIZE = 7.5
SMALL_FONT_SIZE = 8
TITLE_FONT_SIZE = 13
LINE_HEIGHT = 15
CODE_LINE_HEIGHT = 11
COMMENT_GAP = 12
PIN_RADIUS = 7
PIN_FILL_OPACITY = 0.5
LINK_VIEW_PADDING = 6

_C_BLACK = (0, 0, 0)
_C_LINK = (0.1, 0.3, 0.8)
_C_PIN_FILL = (1.0, 0.9, 0.2)
_C_CODE_BG = (0.95, 0.95, 0.95)
_C_CODE_BORDER = (0.78, 0.78, 0.78)
_C_ERROR = (0.7, 0.05, 0.08)
_FONT_NAME = "JaFont"


def _find_japanese_font() -> Optional[str]:
    candidates = [
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\YuGothR.ttc",
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ]
    return next((path for path in candidates if os.path.exists(path)), None)


_FONT_FILE = _find_japanese_font()


def _text(page: fitz.Page, x: float, y: float, text: str,
          fontsize: float = FONT_SIZE, color: tuple = _C_BLACK) -> None:
    options: dict = {"fontsize": fontsize, "color": color}
    if _FONT_FILE:
        options.update({"fontname": _FONT_NAME, "fontfile": _FONT_FILE})
    else:
        options["fontname"] = "helv"
    page.insert_text(fitz.Point(x, y), text, **options)


def _code_text(page: fitz.Page, x: float, y: float, text: str) -> None:
    page.insert_text(
        fitz.Point(x, y), text, fontsize=CODE_FONT_SIZE,
        fontname="cour", color=_C_BLACK)


def _character_width(character: str, fontsize: float = FONT_SIZE) -> float:
    if character == "\t":
        return fontsize * 2
    if character.isspace():
        return fontsize * 0.42
    return fontsize * (0.56 if ord(character) < 128 else 1.0)


def _append_text_run(runs: list[dict], character: str, width: float) -> None:
    if runs and runs[-1]["kind"] == "text":
        runs[-1]["value"] += character
        runs[-1]["width"] += width
    else:
        runs.append({"kind": "text", "value": character, "width": width,
                     "height": LINE_HEIGHT})


def _formula_run(source: str, maximum_width: float) -> dict:
    try:
        artifact = texrender.render_formula(source)
        scale = min(1.0, maximum_width / artifact.width)
        return {
            "kind": "formula",
            "value": artifact,
            "width": artifact.width * scale,
            "height": artifact.height * scale,
        }
    except Exception as exc:  # one malformed formula must not abort PDF publication
        message = f"[TeXエラー: {exc}] {source}"
        return {"kind": "error", "value": message, "width": maximum_width,
                "height": LINE_HEIGHT}


def _plain_text_blocks(text: str, maximum_width: float) -> list[dict]:
    blocks: list[dict] = []
    runs: list[dict] = []
    line_width = 0.0

    def flush(force: bool = False) -> None:
        nonlocal runs, line_width
        if runs or force:
            blocks.append({"kind": "line", "runs": runs,
                           "height": max([LINE_HEIGHT] + [run["height"] for run in runs])})
        runs = []
        line_width = 0.0

    for character in text.replace(r"\$", "$"):
        if character == "\n":
            flush(force=True)
            continue
        width = _character_width(character)
        if runs and line_width + width > maximum_width:
            flush()
        _append_text_run(runs, character, width)
        line_width += width
    flush()
    return blocks


def _build_comment_blocks(comment: str, maximum_width: float) -> tuple[list[dict], list[str]]:
    """Build drawable blocks; return raw formula sources separately for tests/export."""
    try:
        segments = texrender.parse_comment(comment)
    except Exception as exc:
        blocks = _plain_text_blocks(comment, maximum_width)
        error_blocks = _plain_text_blocks(f"TeXエラー: {exc}", maximum_width)
        for block in error_blocks:
            block["color"] = _C_ERROR
        return blocks + error_blocks, []

    blocks: list[dict] = []
    current_runs: list[dict] = []
    current_width = 0.0

    def flush_line(force: bool = False) -> None:
        nonlocal current_runs, current_width
        if current_runs or force:
            blocks.append({
                "kind": "line",
                "runs": current_runs,
                "height": max([LINE_HEIGHT] + [run["height"] for run in current_runs]),
            })
        current_runs = []
        current_width = 0.0

    for segment in segments:
        if segment.kind == "text":
            for character in segment.source.replace(r"\$", "$"):
                if character == "\n":
                    flush_line(force=True)
                    continue
                width = _character_width(character)
                if current_runs and current_width + width > maximum_width:
                    flush_line()
                _append_text_run(current_runs, character, width)
                current_width += width
            continue

        formula = _formula_run(segment.source, maximum_width)
        if segment.kind == "display_math":
            flush_line()
            blocks.append({"kind": "display", "run": formula,
                           "height": max(LINE_HEIGHT, formula["height"]) + 8})
            continue
        if current_runs and current_width + formula["width"] > maximum_width:
            flush_line()
        current_runs.append(formula)
        current_width += formula["width"]
    flush_line()

    sources = [segment.source for segment in segments if segment.kind != "text"]
    if sources:
        blocks.append({"kind": "source_label", "height": 17})
        chars_per_line = max(20, int((maximum_width - 12) / (CODE_FONT_SIZE * 0.60)))
        code_lines: list[str] = []
        for source_index, source in enumerate(sources):
            if source_index:
                code_lines.append("")
            for original_line in source.splitlines() or [""]:
                if not original_line:
                    code_lines.append("")
                else:
                    code_lines.extend(
                        original_line[index:index + chars_per_line]
                        for index in range(0, len(original_line), chars_per_line))
        blocks.append({
            "kind": "source_box",
            "lines": code_lines,
            "height": len(code_lines) * CODE_LINE_HEIGHT + 12,
        })
    return blocks, sources


def _draw_formula(page: fitz.Page, x: float, y: float, run: dict) -> None:
    if run["kind"] == "error":
        _text(page, x, y + FONT_SIZE + 1, run["value"], color=_C_ERROR)
        return
    artifact = run["value"]
    rect = fitz.Rect(x, y, x + run["width"], y + run["height"])
    with fitz.open(artifact.pdf_path) as formula_document:
        page.show_pdf_page(rect, formula_document, 0, keep_proportion=True, overlay=True)


def _draw_blocks(page: fitz.Page, x: float, y: float,
                 maximum_width: float, blocks: list[dict]) -> float:
    for block in blocks:
        kind = block["kind"]
        if kind == "line":
            line_height = block["height"]
            cursor_x = x
            for run in block["runs"]:
                if run["kind"] == "text":
                    baseline = y + (line_height + FONT_SIZE) / 2 - 1
                    _text(page, cursor_x, baseline, run["value"],
                          color=block.get("color", _C_BLACK))
                else:
                    formula_y = y + (line_height - run["height"]) / 2
                    _draw_formula(page, cursor_x, formula_y, run)
                cursor_x += run["width"]
            y += line_height
        elif kind == "display":
            run = block["run"]
            formula_x = x + max(0, (maximum_width - run["width"]) / 2)
            _draw_formula(page, formula_x, y + 4, run)
            y += block["height"]
        elif kind == "source_label":
            _text(page, x, y + FONT_SIZE + 1, "TeXソース（コピー用）:",
                  fontsize=SMALL_FONT_SIZE)
            y += block["height"]
        elif kind == "source_box":
            box = fitz.Rect(x, y, x + maximum_width, y + block["height"])
            page.draw_rect(box, fill=_C_CODE_BG, color=_C_CODE_BORDER, width=0.6)
            baseline = y + CODE_FONT_SIZE + 6
            for line in block["lines"]:
                _code_text(page, x + 6, baseline, line)
                baseline += CODE_LINE_HEIGHT
            y += block["height"]
    return y


def _new_comment_page(doc: fitz.Document, title: str,
                      continuation: bool) -> tuple[fitz.Page, float]:
    page = doc.new_page(width=A4_W, height=A4_H)
    heading = title + ("（続き）" if continuation else "")
    _text(page, MARGIN, MARGIN + TITLE_FONT_SIZE, heading, TITLE_FONT_SIZE)
    return page, MARGIN + TITLE_FONT_SIZE + 24


def generate_comment_pdf(record: ReviewRecord, pdf_path: str, output_path: str,
                         title: str, comment_title: str = "コメント一覧") -> None:
    """Generate a review PDF while preserving TeX source as selectable text."""
    doc = fitz.open()
    page, y = _new_comment_page(doc, title, False)
    _text(page, MARGIN, y, comment_title, fontsize=11)
    y += 24

    link_entries: list[tuple[int, fitz.Rect, object]] = []
    comment_targets: dict[str, tuple[int, float]] = {}
    pins = sorted_pins(record.comments)

    if not pins:
        _text(page, MARGIN, y, "コメントはありません。")

    content_width = A4_W - 2 * MARGIN - 16
    for number, pin in enumerate(pins, start=1):
        blocks, _sources = _build_comment_blocks(pin.text, content_width)
        content_height = sum(block["height"] for block in blocks)
        linked_content_height = sum(
            block["height"] for block in blocks
            if block["kind"] not in {"source_label", "source_box"}
        )
        entry_height = 20 + content_height + COMMENT_GAP
        if y + entry_height > A4_H - MARGIN:
            page, y = _new_comment_page(doc, title, True)

        entry_top = y
        label = f"{number}.  原稿 {pin.page + 1}頁"
        _text(page, MARGIN, y, label, color=_C_LINK)
        y += 18
        content_top = y
        y = _draw_blocks(page, MARGIN + 16, y, content_width, blocks)
        y += COMMENT_GAP

        page_number = page.number
        comment_targets[pin.id] = (
            page_number,
            max(page.rect.y0, entry_top - FONT_SIZE - LINK_VIEW_PADDING),
        )
        link_entries.append((
            page_number,
            fitz.Rect(
                MARGIN, entry_top - FONT_SIZE, A4_W - MARGIN,
                content_top + linked_content_height,
            ),
            pin,
        ))

    source_start = len(doc)
    with fitz.open(pdf_path) as source:
        doc.insert_pdf(source)

    pin_numbers = {pin.id: number for number, pin in enumerate(pins, start=1)}
    for pin in record.comments:
        target_page = source_start + pin.page
        if target_page >= len(doc):
            continue
        source_page = doc[target_page]
        center = fitz.Point(pin.pdf_x, pin.pdf_y)
        source_page.draw_circle(
            center,
            PIN_RADIUS,
            fill=_C_PIN_FILL,
            color=_C_BLACK,
            width=1,
            fill_opacity=PIN_FILL_OPACITY,
        )
        number_text = str(pin_numbers[pin.id])
        _text(source_page, pin.pdf_x - len(number_text) * 2.5,
              pin.pdf_y + SMALL_FONT_SIZE / 2, number_text, SMALL_FONT_SIZE)
        comment_target = comment_targets.get(pin.id)
        if comment_target:
            page_number, target_y = comment_target
            source_page.insert_link({
                "kind": fitz.LINK_GOTO,
                "from": fitz.Rect(pin.pdf_x - PIN_RADIUS, pin.pdf_y - PIN_RADIUS,
                                  pin.pdf_x + PIN_RADIUS, pin.pdf_y + PIN_RADIUS),
                "page": page_number,
                "to": fitz.Point(MARGIN, target_y),
            })

    for page_number, link_rect, pin in link_entries:
        target_page = source_start + pin.page
        if target_page < len(doc):
            target = doc[target_page]
            target_y = max(
                target.rect.y0,
                pin.pdf_y - PIN_RADIUS - LINK_VIEW_PADDING,
            )
            doc[page_number].insert_link({
                "kind": fitz.LINK_GOTO,
                "from": link_rect,
                "page": target_page,
                "to": fitz.Point(pin.pdf_x, target_y),
            })

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    doc.save(output_path, garbage=4, deflate=True)
    doc.close()
