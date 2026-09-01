"""Student-paper behavior plugged into the shared PDF commenting GUI."""

from __future__ import annotations

import os
import subprocess
import sys
from tkinter import messagebox
from typing import Callable, Optional

from packages.papers import discovery, paths
from packages.review.domain import ReviewDomain, Target
from packages.review.record import ReviewRecord
from packages.review.reportpdf import generate_comment_pdf


class PapersDomain(ReviewDomain):
    window_title = "ReviewPapers - 学生原稿添削"

    def __init__(self, review_folder: str):
        self.review_folder = review_folder

    def load_targets(self) -> list[Target]:
        papers = discovery.discover_papers(self.review_folder)
        return [Target(paper.id, paper.name, "", paper.path) for paper in papers]

    def pdf_path(self, target: Target) -> Optional[str]:
        return target.ref if target.ref and os.path.exists(target.ref) else None

    def record_path(self, target: Target) -> str:
        return os.path.join(paths.results_dir(self.review_folder), f"{target.id}.json")

    def return_pdf_path(self, target: Target) -> str:
        stem = os.path.splitext(os.path.basename(target.ref))[0] if target.ref else target.id
        return os.path.join(paths.return_dir(self.review_folder), f"添削結果_{stem}.pdf")

    def actions(self) -> list[tuple[str, Callable[[object], None]]]:
        return [
            ("添削結果PDF発行（未発行のみ）", self.issue_review_pdfs),
            ("返却フォルダを開く", self.open_return_dir),
        ]

    def issue_review_pdfs(self, app) -> None:
        pending: list[tuple[Target, ReviewRecord]] = []
        for target in app.targets:
            record = self.load_record(self.record_path(target))
            if record is not None and (not record.issued_at or record.issued_at < record.updated_at):
                pending.append((target, record))

        if not pending:
            messagebox.showinfo("発行", "未発行の添削結果はありません。")
            return

        def task() -> None:
            issued = 0
            errors: list[str] = []
            for target, record in pending:
                try:
                    source_pdf = self.pdf_path(target)
                    if not source_pdf:
                        errors.append(f"{target.name}: 原稿PDFが見つかりません")
                        continue
                    output = self.return_pdf_path(target)
                    generate_comment_pdf(
                        record,
                        source_pdf,
                        output,
                        title=f"添削コメント　{record.target_name}",
                    )
                    self.mark_issued(self.record_path(target), record)
                    app.log(f"発行: {target.name} → {output}")
                    issued += 1
                except Exception as exc:  # noqa: BLE001 - show per-file errors in the GUI
                    errors.append(f"{target.name}: {exc}")
                    app.log(f"発行エラー ({target.name}): {exc}")

            app.root.after(0, app._refresh_target_labels)
            message = f"{issued}件の添削結果PDFを発行しました。\n出力先: {paths.return_dir(self.review_folder)}"
            if errors:
                message += "\n\nエラー:\n" + "\n".join(errors)
            app.root.after(0, lambda: messagebox.showinfo("発行完了", message))

        app.run_async(task, f"添削結果PDF発行中（{len(pending)}件）…")

    def open_return_dir(self, app) -> None:
        directory = paths.return_dir(self.review_folder)
        os.makedirs(directory, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(directory)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", directory])
