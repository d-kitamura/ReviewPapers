"""Folder resolution for student-paper reviews."""

from __future__ import annotations

import os
from typing import Optional

ENV_VAR = "PAPERS_REVIEW_ROOT"
RESULTS_DIR = "review_results"
RETURN_DIR = "返却"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT_MEMO_FILE = os.path.join(_REPO_ROOT, ".review_root")


def _read_memo() -> Optional[str]:
    try:
        with open(ROOT_MEMO_FILE, "r", encoding="utf-8") as handle:
            path = handle.read().strip()
        return path if path and os.path.isdir(path) else None
    except OSError:
        return None


def save_review_root(path: str) -> None:
    with open(ROOT_MEMO_FILE, "w", encoding="utf-8") as handle:
        handle.write(path)


def resolve_review_root() -> Optional[str]:
    env = os.environ.get(ENV_VAR)
    if env and os.path.isdir(env):
        return os.path.normpath(env)
    return _read_memo()


def results_dir(review_folder: str) -> str:
    return os.path.join(review_folder, RESULTS_DIR)


def return_dir(review_folder: str) -> str:
    return os.path.join(review_folder, RETURN_DIR)
