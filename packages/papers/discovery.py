"""Discover paper PDFs and derive stable IDs and display labels from filenames."""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Paper:
    id: str
    name: str
    path: str


def discover_papers(directory: str) -> list[Paper]:
    if not os.path.isdir(directory):
        return []
    found: list[Paper] = []
    for path in sorted(glob.glob(os.path.join(glob.escape(directory), "*.pdf"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        # ファイル名全体をIDにする。同じ学生の別バージョンも別レコードになる。
        found.append(Paper(stem, stem, os.path.abspath(path)))
    return found
