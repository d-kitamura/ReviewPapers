"""Comment-only review records with atomic JSON persistence."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CommentPin:
    id: str
    page: int
    pdf_x: float
    pdf_y: float
    text: str


@dataclass
class ReviewRecord:
    target_id: str
    target_name: str
    pdf_relpath: str
    pdf_mtime: float = 0.0
    comments: list[CommentPin] = field(default_factory=list)
    updated_at: str = ""
    issued_at: str = ""


def new_comment_pin(page: int, pdf_x: float, pdf_y: float, text: str) -> CommentPin:
    return CommentPin(uuid.uuid4().hex, page, pdf_x, pdf_y, text)


SAME_LINE_TOLERANCE_PT = 14.0


def sorted_pins(pins: list[CommentPin],
                tolerance: float = SAME_LINE_TOLERANCE_PT) -> list[CommentPin]:
    """Sort pins in reading order: page, top-to-bottom, then left-to-right per line."""
    out: list[CommentPin] = []
    for page in sorted({pin.page for pin in pins}):
        band: list[CommentPin] = []
        band_top = 0.0
        for pin in sorted((pin for pin in pins if pin.page == page), key=lambda p: p.pdf_y):
            if band and pin.pdf_y - band_top > tolerance:
                out.extend(sorted(band, key=lambda p: p.pdf_x))
                band = []
            if not band:
                band_top = pin.pdf_y
            band.append(pin)
        out.extend(sorted(band, key=lambda p: p.pdf_x))
    return out


def _decode(data: dict) -> ReviewRecord:
    comments = [CommentPin(**comment) for comment in data.get("comments", [])]
    known = {field.name for field in dataclasses.fields(ReviewRecord)}
    values = {key: value for key, value in data.items() if key in known and key != "comments"}
    values["comments"] = comments
    return ReviewRecord(**values)


def load_record(path: str) -> Optional[ReviewRecord]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return _decode(json.load(handle))


def _write_atomic(path: str, record: ReviewRecord) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(dataclasses.asdict(record), handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def save_record(path: str, record: ReviewRecord) -> None:
    record.updated_at = dt.datetime.now().isoformat(timespec="seconds")
    _write_atomic(path, record)


def mark_issued(path: str, record: ReviewRecord) -> None:
    record.issued_at = dt.datetime.now().isoformat(timespec="seconds")
    _write_atomic(path, record)


def issued_status(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        issued_at = data.get("issued_at", "")
        return "" if issued_at and issued_at >= data.get("updated_at", "") else "unissued"
    except Exception:
        return "unissued"
