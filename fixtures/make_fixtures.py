"""Create private-data-free sample papers for exercising the GUI."""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import fitz  # noqa: E402

from packages.papers import paths  # noqa: E402

DEV_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev_root")
PAPERS = [
    "架空太郎_卒業論文_v1.pdf",
    "架空太郎_卒業論文_v2.pdf",
    "架空花子_学会原稿.pdf",
]


def _make_pdf(path: str) -> None:
    document = fitz.open()
    for page_number in range(3):
        page = document.new_page(width=595, height=842)
        page.insert_text((60, 80), f"[DUMMY PAPER] {os.path.basename(path)}", fontsize=14)
        page.insert_text((60, 115), f"Section {page_number + 1}", fontsize=12)
        for line in range(22):
            page.insert_text((60, 150 + line * 24),
                             f"This is dummy manuscript text ({page_number + 1}-{line + 1}).",
                             fontsize=10)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    document.save(path)
    document.close()


def main() -> str:
    os.makedirs(DEV_ROOT, exist_ok=True)
    for filename in PAPERS:
        _make_pdf(os.path.join(DEV_ROOT, filename))
    os.makedirs(paths.results_dir(DEV_ROOT), exist_ok=True)
    os.makedirs(paths.return_dir(DEV_ROOT), exist_ok=True)
    print(f"ダミー原稿を作成しました: {DEV_ROOT}")
    print("PowerShellでの起動方法:")
    print(f'  $env:PAPERS_REVIEW_ROOT = "{DEV_ROOT}"')
    print("  pixi run gui")
    return DEV_ROOT


if __name__ == "__main__":
    main()
