"""Parse TeX fragments in comments and render them safely with Tectonic.

Supported syntax is deliberately small:

* ``$...$`` for inline mathematics
* ``\\begin{align}...\\end{align}``
* ``\\begin{align*}...\\end{align*}``

The original comment text remains the source of truth. Rendered formula PDFs are
content-addressed cache files that can always be regenerated.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import fitz
from PIL import Image

SegmentKind = Literal["text", "inline_math", "display_math"]
_RENDERER_VERSION = "1"
_ALIGN_START_RE = re.compile(r"\\begin\{(align\*?)\}")
_FORBIDDEN_RE = re.compile(
    r"\\(?:documentclass|usepackage|input|include|includegraphics|write18|write|"
    r"openin|openout|read|catcode|csname|newcommand|renewcommand|providecommand|"
    r"def|gdef|edef|xdef|let|futurelet|special|loop)\b",
    re.IGNORECASE,
)


class TeXSyntaxError(ValueError):
    """Raised when a comment contains unsupported or incomplete TeX markup."""


class TeXRenderError(RuntimeError):
    """Raised when Tectonic cannot render a valid formula fragment."""


@dataclass(frozen=True)
class CommentSegment:
    kind: SegmentKind
    source: str


@dataclass(frozen=True)
class FormulaArtifact:
    source: str
    pdf_path: str
    width: float
    height: float


def _is_escaped(text: str, position: int) -> bool:
    backslashes = 0
    index = position - 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _append_text(segments: list[CommentSegment], value: str) -> None:
    if not value:
        return
    if segments and segments[-1].kind == "text":
        previous = segments[-1]
        segments[-1] = CommentSegment("text", previous.source + value)
    else:
        segments.append(CommentSegment("text", value))


def parse_comment(text: str) -> list[CommentSegment]:
    """Split a comment into ordinary text, inline math, and align blocks."""
    segments: list[CommentSegment] = []
    text_start = 0
    index = 0
    while index < len(text):
        align_match = _ALIGN_START_RE.match(text, index)
        if align_match:
            _append_text(segments, text[text_start:index])
            environment = align_match.group(1)
            end_marker = rf"\end{{{environment}}}"
            end = text.find(end_marker, align_match.end())
            if end < 0:
                raise TeXSyntaxError(f"{end_marker} が見つかりません。")
            end += len(end_marker)
            source = text[index:end]
            validate_formula_source(source, display=True)
            segments.append(CommentSegment("display_math", source))
            index = end
            text_start = index
            continue

        if text[index] == "$" and not _is_escaped(text, index):
            if index + 1 < len(text) and text[index + 1] == "$":
                raise TeXSyntaxError("$$...$$ は未対応です。align* 環境を使用してください。")
            _append_text(segments, text[text_start:index])
            end = index + 1
            while end < len(text):
                if text[end] == "$" and not _is_escaped(text, end):
                    break
                end += 1
            if end >= len(text):
                raise TeXSyntaxError("インライン数式を閉じる $ が見つかりません。")
            source = text[index:end + 1]
            if source == "$$":
                raise TeXSyntaxError("空のインライン数式は使用できません。")
            validate_formula_source(source, display=False)
            segments.append(CommentSegment("inline_math", source))
            index = end + 1
            text_start = index
            continue
        index += 1

    _append_text(segments, text[text_start:])
    return segments


def validate_formula_source(source: str, *, display: bool) -> None:
    if len(source) > 5000:
        raise TeXSyntaxError("1つの数式は5000文字以内にしてください。")
    forbidden = _FORBIDDEN_RE.search(source)
    if forbidden:
        raise TeXSyntaxError(f"コメント内では {forbidden.group(0)} を使用できません。")

    if display:
        start = _ALIGN_START_RE.fullmatch(source[:source.find("}") + 1])
        if not start:
            raise TeXSyntaxError("表示数式は align または align* 環境で記述してください。")
        environment = start.group(1)
        inner = source[start.end(): -len(rf"\end{{{environment}}}")]
    else:
        inner = source[1:-1]
    if re.search(r"\\begin\{|\\end\{", inner):
        raise TeXSyntaxError("数式環境の入れ子は現在サポートしていません。")


def math_sources(text: str) -> list[str]:
    return [segment.source for segment in parse_comment(text) if segment.kind != "text"]


def default_cache_dir() -> str:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]) / "ReviewPapers" / "tex-cache"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ReviewPapers" / "tex-cache"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _document_source(formula_source: str) -> str:
    return "\n".join([
        r"\documentclass[preview,border=2pt]{standalone}",
        r"\usepackage{amsmath}",
        r"\usepackage{amssymb}",
        r"\usepackage{bm}",
        r"\pagestyle{empty}",
        r"\begin{document}",
        formula_source,
        r"\end{document}",
        "",
    ])


def _tectonic_executable() -> str:
    executable = shutil.which("tectonic")
    if not executable:
        raise TeXRenderError(
            "Tectonicが見つかりません。ReviewPapersフォルダで pixi install を実行してください。"
        )
    return executable


def render_formula(source: str, cache_dir: str | None = None,
                   timeout_seconds: int = 240) -> FormulaArtifact:
    """Compile one formula to a tightly cropped vector PDF and cache it."""
    display = source.startswith(r"\begin{align")
    validate_formula_source(source, display=display)
    cache = Path(cache_dir or default_cache_dir())
    cache.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        (_RENDERER_VERSION + "\0" + source).encode("utf-8")
    ).hexdigest()
    cached_pdf = cache / f"{digest}.pdf"

    if not cached_pdf.exists():
        with tempfile.TemporaryDirectory(prefix="reviewpapers-tex-") as temporary:
            temporary_path = Path(temporary)
            tex_path = temporary_path / "formula.tex"
            output_dir = temporary_path / "out"
            output_dir.mkdir()
            tex_path.write_text(_document_source(source), encoding="utf-8")
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                completed = subprocess.run(
                    [
                        _tectonic_executable(), "-X", "compile", "--untrusted",
                        "--outdir", str(output_dir), str(tex_path),
                    ],
                    cwd=temporary,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    creationflags=creationflags,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TeXRenderError("数式のコンパイルが時間切れになりました。") from exc
            generated_pdf = output_dir / "formula.pdf"
            if completed.returncode != 0 or not generated_pdf.exists():
                details = (completed.stderr or completed.stdout or "不明なTeXエラー").strip()
                details = "\n".join(details.splitlines()[-8:])
                raise TeXRenderError(details)
            descriptor, temporary_cache_name = tempfile.mkstemp(
                prefix=f"{digest}.", suffix=".tmp.pdf", dir=cache)
            os.close(descriptor)
            temporary_cache = Path(temporary_cache_name)
            try:
                shutil.copy2(generated_pdf, temporary_cache)
                os.replace(temporary_cache, cached_pdf)
            finally:
                temporary_cache.unlink(missing_ok=True)

    try:
        with fitz.open(cached_pdf) as document:
            if len(document) != 1:
                raise TeXRenderError("数式レンダラーが複数ページを生成しました。")
            rect = document[0].rect
            if rect.width <= 0 or rect.height <= 0:
                raise TeXRenderError("数式レンダラーが空のページを生成しました。")
            width, height = float(rect.width), float(rect.height)
    except TeXRenderError:
        raise
    except Exception as exc:
        cached_pdf.unlink(missing_ok=True)
        raise TeXRenderError(f"数式キャッシュを開けません: {exc}") from exc
    return FormulaArtifact(source, str(cached_pdf), width, height)


def render_formula_image(source: str, cache_dir: str | None = None,
                         scale: float = 2.5) -> Image.Image:
    artifact = render_formula(source, cache_dir)
    with fitz.open(artifact.pdf_path) as document:
        pixmap = document[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
