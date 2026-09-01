"""Domain boundary used by the generic PDF commenting GUI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from packages.review import record as record_mod
from packages.review.record import ReviewRecord


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    note: str = ""
    ref: Any = None

    @property
    def display(self) -> str:
        return self.name + (f"（{self.note}）" if self.note else "")


class ReviewDomain:
    window_title = "PDF添削"
    target_label = "原稿"
    status_labels = {"missing": "（PDFなし）", "unrecorded": "（未着手）", "unissued": "（未発行）"}

    def load_targets(self) -> list[Target]:
        raise NotImplementedError

    def pdf_path(self, target: Target) -> Optional[str]:
        raise NotImplementedError

    def record_path(self, target: Target) -> str:
        raise NotImplementedError

    def load_record(self, path: str) -> Optional[ReviewRecord]:
        return record_mod.load_record(path)

    def save_record(self, path: str, record: ReviewRecord) -> None:
        record_mod.save_record(path, record)

    def mark_issued(self, path: str, record: ReviewRecord) -> None:
        record_mod.mark_issued(path, record)

    def new_record(self, target: Target, pdf_path: str) -> ReviewRecord:
        return ReviewRecord(
            target_id=target.id,
            target_name=target.name,
            pdf_relpath=os.path.basename(pdf_path),
            pdf_mtime=os.path.getmtime(pdf_path),
        )

    def actions(self) -> list[tuple[str, Callable[[Any], None]]]:
        return []
