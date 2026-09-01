"""ReviewPapers GUI entry point."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox

from packages.papers import paths
from packages.papers.domain import PapersDomain
from packages.review import app as review_app


def _ask_review_root() -> str | None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "原稿フォルダの選択",
        "査読対象のPDFを直接入れたフォルダを選んでください。\n"
        "同じ学生の別バージョンも、異なるファイル名で置けば個別に表示されます。",
    )
    selected = filedialog.askdirectory(title="学生原稿フォルダ")
    root.destroy()
    if not selected:
        return None
    selected = os.path.normpath(selected)
    paths.save_review_root(selected)
    return selected


def main() -> None:
    review_app.ensure_dpi_awareness()
    review_folder = paths.resolve_review_root() or _ask_review_root()
    if not review_folder:
        print("原稿フォルダが指定されなかったため終了します。")
        return

    root = tk.Tk()
    review_app.ReviewApp(root, PapersDomain(review_folder))
    root.mainloop()


if __name__ == "__main__":
    main()
