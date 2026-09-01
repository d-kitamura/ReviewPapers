"""Tkinter GUI for placing, editing, and publishing comments on PDF pages."""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from packages.review import pdfview, record as record_mod, texrender
from packages.review.domain import ReviewDomain, Target
from packages.review.record import CommentPin, ReviewRecord

PIN_RADIUS = 10
PAGE_GAP_PX = 16
RESIZE_DEBOUNCE_MS = 150
DRAG_THRESHOLD_PX = 4


def ensure_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def detect_os_scale_factor() -> float:
    if sys.platform == "win32":
        try:
            import ctypes
            dpi = ctypes.windll.user32.GetDpiForSystem()
            if dpi:
                return dpi / 96.0
        except Exception:
            pass
    return 1.0


class ReviewApp:
    UI_FONT_FAMILY = "03スマートフォントUI"

    def __init__(self, root: tk.Tk, domain: ReviewDomain):
        self.root = root
        self.domain = domain
        root.title(domain.window_title)
        self.UI_SCALE = detect_os_scale_factor()
        self._apply_ui_scale()
        root.geometry(f"{int(1200 * self.UI_SCALE)}x{int(760 * self.UI_SCALE)}")

        self.targets: list[Target] = []
        self.current_target: Target | None = None
        self.current_pdf_path: str | None = None
        self.doc = None
        self.page_index = 0
        self.zoom = 1.0
        self.record: ReviewRecord | None = None
        self.page_layout: list[dict] = []
        self._photos: list[ImageTk.PhotoImage] = []
        self._resize_after_id: str | None = None
        self._drag_start: tuple[int, int] | None = None
        self._is_dragging = False
        self._target_status: dict[str, str] = {}
        self.comment_window: tk.Toplevel | None = None
        self._comment_dialog_geometry: str | None = None
        self._worker: threading.Thread | None = None

        self._build_ui()
        self._build_comment_window()
        self._load_targets()

    def _apply_ui_scale(self) -> None:
        import tkinter.font as tkfont
        if self.UI_FONT_FAMILY in tkfont.families(self.root):
            for name in tkfont.names(self.root):
                tkfont.nametofont(name).configure(family=self.UI_FONT_FAMILY)
        self.root.option_add("*TCombobox*Listbox.font", tkfont.nametofont("TkDefaultFont"))
        linespace = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        ttk.Style(self.root).configure("Treeview", rowheight=linespace + int(8 * self.UI_SCALE))

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text=f"{self.domain.target_label}:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.target_cb = ttk.Combobox(top, state="readonly", width=36)
        self.target_cb.grid(row=0, column=1, sticky="w")
        self.target_cb.bind("<<ComboboxSelected>>", self._on_target_selected)

        nav = ttk.Frame(top)
        nav.grid(row=0, column=2, padx=(16, 0))
        ttk.Button(nav, text="◀", width=3, command=lambda: self._scroll_to_page(-1)).pack(side="left")
        self.page_label = ttk.Label(nav, text="- / -", width=10, anchor="center")
        self.page_label.pack(side="left", padx=4)
        ttk.Button(nav, text="▶", width=3, command=lambda: self._scroll_to_page(1)).pack(side="left")

        body = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg="#808080")
        self.vsb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        hsb = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self._on_canvas_yscroll, xscrollcommand=hsb.set,
                              yscrollincrement=int(20 * self.UI_SCALE))
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_button_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_b1_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_button_release)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        bottom = ttk.Frame(self.root, padding=8)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="コメント一覧表示", command=self._show_comment_window).pack(side="left")
        for index, (label, callback) in enumerate(self.domain.actions()):
            ttk.Button(bottom, text=label, command=lambda cb=callback: cb(self)).pack(
                side="left", padx=((16 if index == 0 else 8), 0))
        self.status = ttk.Label(bottom, text="準備完了", anchor="e")
        self.status.pack(side="right")

    def _load_targets(self) -> None:
        try:
            self.targets = self.domain.load_targets()
        except Exception as exc:
            messagebox.showerror(f"{self.domain.target_label}一覧の読み込みエラー", str(exc))
            self.targets = []
        self._refresh_target_labels()
        if self.targets:
            self.target_cb.current(0)
            self._load_target(self.targets[0])
        else:
            self.target_cb.set("")
            self.record = None
            self._clear_canvas("原稿PDFが見つかりません。")
            self._refresh_comment_tree()

    def _target_label(self, target: Target) -> str:
        return f"{target.display}{self._target_status.get(target.id, '')}"

    def _refresh_target_labels(self) -> None:
        self._target_status = {}
        for target in self.targets:
            pdf = self.domain.pdf_path(target)
            if not pdf:
                status = self.domain.status_labels["missing"]
            else:
                record_path = self.domain.record_path(target)
                if not os.path.exists(record_path):
                    status = self.domain.status_labels["unrecorded"]
                elif record_mod.issued_status(record_path):
                    status = self.domain.status_labels["unissued"]
                else:
                    status = ""
            self._target_status[target.id] = status
        self.target_cb.configure(values=[self._target_label(target) for target in self.targets])
        if self.current_target:
            index = next((i for i, target in enumerate(self.targets)
                          if target.id == self.current_target.id), -1)
            if index >= 0:
                self.target_cb.current(index)

    def _on_target_selected(self, _event=None) -> None:
        index = self.target_cb.current()
        if 0 <= index < len(self.targets):
            self._load_target(self.targets[index])

    def _load_target(self, target: Target) -> None:
        if self.doc is not None:
            self.doc.close()
        self.current_target = target
        self.doc = None
        self.current_pdf_path = None
        self.page_index = 0
        pdf_path = self.domain.pdf_path(target)
        if not pdf_path:
            self.record = None
            self._clear_canvas("この原稿のPDFが見つかりません。")
            self._refresh_comment_tree()
            return
        self.current_pdf_path = pdf_path
        self.doc = pdfview.open_pdf(pdf_path)
        record_path = self.domain.record_path(target)
        self.record = self.domain.load_record(record_path)
        if self.record is None:
            self.record = self.domain.new_record(target, pdf_path)
        self._refresh_comment_tree()
        self._rebuild_page_layout()

    def _clear_canvas(self, message: str) -> None:
        self.canvas.delete("all")
        self.canvas.create_text(20, 20, anchor="nw", text=message, fill="white")
        self.page_label.configure(text="- / -")
        self.page_layout = []
        self._photos = []

    def _recompute_zoom_to_fit_width(self) -> bool:
        self.canvas.update_idletasks()
        canvas_width = self.canvas.winfo_width()
        if canvas_width <= 1 or self.doc is None:
            return False
        margin = int(8 * self.UI_SCALE)
        width, _ = pdfview.page_size_pt(self.doc, 0)
        self.zoom = max(0.1, (canvas_width - margin) / width)
        return True

    def _rebuild_page_layout(self) -> None:
        if self.doc is None or not self._recompute_zoom_to_fit_width():
            return
        saved_fraction = self.canvas.yview()[0]
        gap = int(PAGE_GAP_PX * self.UI_SCALE)
        self._photos = []
        self.page_layout = []
        self.canvas.delete("all")
        y = 0
        max_width = 0
        for page_number in range(len(self.doc)):
            image = pdfview.render_page(self.doc, page_number, self.zoom)
            photo = ImageTk.PhotoImage(image)
            self._photos.append(photo)
            self.canvas.create_image(0, y, anchor="nw", image=photo, tags=("page",))
            self.page_layout.append({"y0": y, "width": image.width, "height": image.height})
            max_width = max(max_width, image.width)
            y += image.height + gap
        total_height = max(0, y - gap)
        self.canvas.configure(scrollregion=(0, 0, max_width, total_height))
        self._draw_pins()
        self.canvas.yview_moveto(saved_fraction)
        self._update_current_page_label(saved_fraction)

    def _on_canvas_configure(self, _event) -> None:
        if self._resize_after_id is not None:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(RESIZE_DEBOUNCE_MS, self._on_resize_debounced)

    def _on_resize_debounced(self) -> None:
        self._resize_after_id = None
        if self.doc is not None:
            self._rebuild_page_layout()

    def _draw_pins(self) -> None:
        self.canvas.delete("pin")
        if self.record is None:
            return
        ordered = record_mod.sorted_pins(self.record.comments)
        for number, pin in enumerate(ordered, start=1):
            if pin.page >= len(self.page_layout):
                continue
            x, y_in_page = pdfview.pdf_point_to_canvas(pin.pdf_x, pin.pdf_y, self.zoom)
            y = self.page_layout[pin.page]["y0"] + y_in_page
            self.canvas.create_oval(x - PIN_RADIUS, y - PIN_RADIUS, x + PIN_RADIUS, y + PIN_RADIUS,
                                    fill="yellow", outline="black", tags=("pin", pin.id))
            self.canvas.create_text(x, y, text=str(number), tags=("pin", pin.id))

    def _page_at_canvas_y(self, canvas_y: float) -> int | None:
        for index, layout in enumerate(self.page_layout):
            if layout["y0"] <= canvas_y < layout["y0"] + layout["height"]:
                return index
        return None

    def _scroll_to_page(self, delta: int) -> None:
        if self.doc is None or not self.page_layout:
            return
        target = max(0, min(len(self.doc) - 1, self.page_index + delta))
        total_height = self.page_layout[-1]["y0"] + self.page_layout[-1]["height"]
        fraction = self.page_layout[target]["y0"] / total_height if total_height else 0
        self.canvas.yview_moveto(fraction)
        self._update_current_page_label(fraction)

    def _on_canvas_yscroll(self, first: str, last: str) -> None:
        self.vsb.set(first, last)
        self._update_current_page_label(float(first))

    def _update_current_page_label(self, top_fraction: float) -> None:
        if self.doc is None or not self.page_layout:
            self.page_label.configure(text="- / -")
            return
        total_height = self.page_layout[-1]["y0"] + self.page_layout[-1]["height"]
        center_y = top_fraction * total_height + self.canvas.winfo_height() / 2
        page = self._page_at_canvas_y(center_y)
        if page is None:
            page = 0 if center_y < 0 else len(self.doc) - 1
        self.page_index = page
        self.page_label.configure(text=f"{page + 1} / {len(self.doc)}")

    def _on_canvas_button_press(self, event) -> None:
        self._drag_start = (event.x, event.y)
        self._is_dragging = False
        self.canvas.scan_mark(event.x, event.y)

    def _on_canvas_b1_motion(self, event) -> None:
        if self._drag_start is None:
            return
        start_x, start_y = self._drag_start
        if abs(event.x - start_x) + abs(event.y - start_y) > DRAG_THRESHOLD_PX * self.UI_SCALE:
            self._is_dragging = True
        if self._is_dragging:
            self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_canvas_button_release(self, event) -> None:
        was_dragging = self._is_dragging
        self._drag_start = None
        self._is_dragging = False
        if was_dragging or self.doc is None or self.record is None:
            return
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        page = self._page_at_canvas_y(canvas_y)
        if page is None:
            return
        pdf_x, pdf_y = pdfview.canvas_to_pdf_point(
            canvas_x, canvas_y - self.page_layout[page]["y0"], self.zoom)
        for link in self.doc[page].get_links():
            rect = link["from"]
            if rect.x0 <= pdf_x <= rect.x1 and rect.y0 <= pdf_y <= rect.y1:
                uri = link.get("uri", "")
                if uri:
                    webbrowser.open(uri)
                    return
        self._open_comment_dialog(page, pdf_x, pdf_y)

    def _on_mousewheel(self, event) -> None:
        units = -int(event.delta / 120)
        if units == 0:
            units = -1 if event.delta < 0 else 1
        self.canvas.yview_scroll(units * 3, "units")

    def _open_comment_dialog(self, page: int, pdf_x: float, pdf_y: float,
                             existing: CommentPin | None = None) -> None:
        """Open a resizable TeX-source editor with an asynchronous rendered preview."""
        dialog = tk.Toplevel(self.root)
        dialog.title("コメント" if existing is None else "コメント編集")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        dialog.minsize(int(560 * self.UI_SCALE), int(420 * self.UI_SCALE))
        if self._comment_dialog_geometry:
            dialog.geometry(self._comment_dialog_geometry)
        else:
            width = min(int(760 * self.UI_SCALE), int(self.root.winfo_screenwidth() * 0.85))
            height = min(int(720 * self.UI_SCALE), int(self.root.winfo_screenheight() * 0.88))
            dialog.geometry(f"{width}x{height}")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        ttk.Label(dialog, text="コメント（TeXソース）:").grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        panes = ttk.Panedwindow(dialog, orient="vertical")
        panes.grid(row=1, column=0, sticky="nsew", padx=10)

        editor = ttk.LabelFrame(panes, text="入力")
        editor.rowconfigure(1, weight=1)
        editor.columnconfigure(0, weight=1)
        text_widget = tk.Text(editor, wrap="word", undo=True, padx=8, pady=8)

        def insert_align_template() -> None:
            prefix = "\\begin{align*}\n"
            template = prefix + "\n\\end{align*}"
            insertion_index = text_widget.index("insert")
            text_widget.edit_separator()
            text_widget.insert(insertion_index, template)
            text_widget.edit_separator()
            text_widget.mark_set(
                "insert", f"{insertion_index}+{len(prefix)}c")
            text_widget.see("insert")
            text_widget.focus_set()
            schedule_preview()

        ttk.Button(
            editor, text="align*挿入", command=insert_align_template,
        ).grid(row=0, column=0, columnspan=2, sticky="e", padx=(4, 2), pady=(2, 4))
        source_scrollbar = ttk.Scrollbar(editor, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=source_scrollbar.set)
        text_widget.grid(row=1, column=0, sticky="nsew")
        source_scrollbar.grid(row=1, column=1, sticky="ns")

        preview_frame = ttk.LabelFrame(panes, text="数式表示プレビュー")
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        preview_widget = tk.Text(preview_frame, wrap="word", padx=8, pady=8,
                                 state="disabled", cursor="arrow")
        preview_widget.tag_configure("error", foreground="#b00020")
        preview_scrollbar = ttk.Scrollbar(
            preview_frame, orient="vertical", command=preview_widget.yview)
        preview_widget.configure(yscrollcommand=preview_scrollbar.set)
        preview_widget.grid(row=0, column=0, sticky="nsew")
        preview_scrollbar.grid(row=0, column=1, sticky="ns")
        panes.add(editor, weight=3)
        panes.add(preview_frame, weight=2)

        if existing:
            text_widget.insert("1.0", existing.text)

        preview_status = ttk.Label(
            dialog,
            text="$...$、\\begin{align}...\\end{align}、align* に対応します。",
            foreground="#555555",
        )
        preview_status.grid(row=2, column=0, sticky="w", padx=10, pady=(4, 0))
        buttons = ttk.Frame(dialog)
        buttons.grid(row=3, column=0, pady=10)

        preview_after_id: list[str | None] = [None]
        preview_poll_id: list[str | None] = [None]
        preview_revision = [0]
        preview_photos: list[ImageTk.PhotoImage] = []
        preview_results: queue.Queue[tuple[int, list, str | None]] = queue.Queue()
        dialog_closed = [False]

        def show_preview(parts, error: str | None, revision: int) -> None:
            if dialog_closed[0] or revision != preview_revision[0]:
                return
            preview_widget.configure(state="normal")
            preview_widget.delete("1.0", "end")
            preview_photos.clear()
            if error:
                preview_widget.insert("end", "TeXエラー:\n" + error, "error")
                preview_status.configure(text="TeXエラーがあります。原文はそのまま保存できます。")
            elif not parts:
                preview_widget.insert("end", "コメントを入力すると、ここに表示結果が出ます。")
                preview_status.configure(text="数式プレビュー待機中")
            else:
                available_width = max(120, preview_widget.winfo_width() - 36)
                for segment, rendered in parts:
                    if segment.kind == "text":
                        preview_widget.insert("end", segment.source.replace(r"\$", "$"))
                        continue
                    if isinstance(rendered, Exception):
                        preview_widget.insert(
                            "end", f"[TeXエラー: {rendered}]", "error")
                        continue
                    image = rendered
                    if image.width > available_width:
                        ratio = available_width / image.width
                        image = image.resize(
                            (available_width, max(1, int(image.height * ratio))),
                            Image.Resampling.LANCZOS,
                        )
                    photo = ImageTk.PhotoImage(image)
                    preview_photos.append(photo)
                    if segment.kind == "display_math":
                        preview_widget.insert("end", "\n")
                    preview_widget.image_create("end", image=photo, padx=2, pady=2)
                    if segment.kind == "display_math":
                        preview_widget.insert("end", "\n")
                formula_count = sum(1 for segment, _ in parts if segment.kind != "text")
                error_count = sum(1 for _segment, value in parts if isinstance(value, Exception))
                if error_count:
                    preview_status.configure(text=f"TeXエラー {error_count}件（原文は保存可能）")
                else:
                    preview_status.configure(
                        text=f"プレビュー更新済み（数式 {formula_count}件）"
                        if formula_count else "通常テキストのみ")
            preview_widget.configure(state="disabled")

        def render_preview(text: str, revision: int) -> None:
            try:
                segments = texrender.parse_comment(text)
                parts = []
                for segment in segments:
                    if segment.kind == "text":
                        parts.append((segment, None))
                    else:
                        try:
                            image = texrender.render_formula_image(
                                segment.source, scale=max(1.8, self.UI_SCALE))
                            parts.append((segment, image))
                        except Exception as exc:  # keep other formulas visible
                            parts.append((segment, exc))
                preview_results.put((revision, parts, None))
            except Exception as exc:
                preview_results.put((revision, [], str(exc)))

        def poll_preview_results() -> None:
            if dialog_closed[0]:
                return
            try:
                while True:
                    revision, parts, error = preview_results.get_nowait()
                    show_preview(parts, error, revision)
            except queue.Empty:
                pass
            preview_poll_id[0] = dialog.after(50, poll_preview_results)

        def start_preview() -> None:
            preview_after_id[0] = None
            preview_revision[0] += 1
            revision = preview_revision[0]
            source = text_widget.get("1.0", "end-1c")
            preview_status.configure(text="数式プレビューを更新中...")
            threading.Thread(
                target=render_preview, args=(source, revision), daemon=True).start()

        def schedule_preview(_event=None) -> None:
            if text_widget.edit_modified():
                text_widget.edit_modified(False)
            if preview_after_id[0] is not None:
                dialog.after_cancel(preview_after_id[0])
            preview_after_id[0] = dialog.after(450, start_preview)

        def close_dialog() -> None:
            dialog_closed[0] = True
            if preview_after_id[0] is not None:
                dialog.after_cancel(preview_after_id[0])
            if preview_poll_id[0] is not None:
                dialog.after_cancel(preview_poll_id[0])
            self._comment_dialog_geometry = dialog.geometry()
            dialog.destroy()

        def save() -> None:
            text = text_widget.get("1.0", "end-1c").strip()
            if not text:
                close_dialog()
                return
            if existing:
                existing.text = text
            else:
                self.record.comments.append(record_mod.new_comment_pin(page, pdf_x, pdf_y, text))
            self._save_record()
            self._draw_pins()
            self._refresh_comment_tree()
            close_dialog()

        def delete() -> None:
            if existing and self.record:
                self.record.comments.remove(existing)
                self._save_record()
                self._draw_pins()
                self._refresh_comment_tree()
            close_dialog()

        ttk.Button(buttons, text="保存", command=save).pack(side="left", padx=4)
        ttk.Button(buttons, text="プレビュー更新", command=start_preview).pack(side="left", padx=4)
        if existing:
            ttk.Button(buttons, text="削除", command=delete).pack(side="left", padx=4)
        ttk.Button(buttons, text="キャンセル", command=close_dialog).pack(side="left", padx=4)
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.bind("<Escape>", lambda _event: close_dialog())
        dialog.bind("<Control-Return>", lambda _event: (save(), "break")[1])
        text_widget.bind("<<Modified>>", schedule_preview)
        text_widget.edit_modified(False)
        preview_poll_id[0] = dialog.after(50, poll_preview_results)
        dialog.after(100, start_preview)
        text_widget.focus_set()

    def _build_comment_window(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("コメント一覧")
        window.geometry(f"{int(520 * self.UI_SCALE)}x{int(700 * self.UI_SCALE)}")
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        ttk.Label(window, text="コメント一覧（ダブルクリックで編集）",
                  font=("", 0, "bold")).pack(anchor="w", padx=8, pady=(8, 0))
        frame = ttk.Frame(window, padding=8)
        frame.pack(fill="both", expand=True)
        columns = ("no", "page", "text")
        self.comment_tree = ttk.Treeview(frame, columns=columns, show="headings", height=15)
        self.comment_tree.heading("no", text="番号")
        self.comment_tree.heading("page", text="頁")
        self.comment_tree.heading("text", text="コメント")
        self.comment_tree.column("no", width=50, anchor="center", stretch=False)
        self.comment_tree.column("page", width=50, anchor="center", stretch=False)
        self.comment_tree.column("text", width=360)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.comment_tree.yview)
        self.comment_tree.configure(yscrollcommand=scrollbar.set)
        self.comment_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.comment_tree.bind("<Double-1>", self._on_comment_double_click)
        self.comment_window = window

    def _show_comment_window(self) -> None:
        if self.comment_window:
            self.comment_window.deiconify()
            self.comment_window.lift()
            self.comment_window.focus_force()

    def _refresh_comment_tree(self) -> None:
        self.comment_tree.delete(*self.comment_tree.get_children())
        if self.record is None:
            return
        for number, pin in enumerate(record_mod.sorted_pins(self.record.comments), start=1):
            snippet = pin.text if len(pin.text) <= 60 else pin.text[:60] + "…"
            self.comment_tree.insert("", "end", iid=pin.id,
                                     values=(number, pin.page + 1, snippet.replace("\n", " ")))

    def _on_comment_double_click(self, _event) -> None:
        selection = self.comment_tree.selection()
        if not selection or self.record is None:
            return
        pin = next((pin for pin in self.record.comments if pin.id == selection[0]), None)
        if pin is None:
            return
        if pin.page < len(self.page_layout):
            total_height = self.page_layout[-1]["y0"] + self.page_layout[-1]["height"]
            target_y = self.page_layout[pin.page]["y0"] + pin.pdf_y * self.zoom
            fraction = max(0, (target_y - int(16 * self.UI_SCALE)) / total_height)
            self.canvas.yview_moveto(fraction)
            self._update_current_page_label(fraction)
        self._open_comment_dialog(pin.page, pin.pdf_x, pin.pdf_y, pin)

    def _save_record(self) -> None:
        if self.record is None or self.current_target is None:
            return
        self.domain.save_record(self.domain.record_path(self.current_target), self.record)
        self._refresh_target_labels()

    def run_async(self, function, status_text: str) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("実行中", "前の処理が完了するまでお待ちください。")
            return
        self.status.configure(text=status_text)

        def runner() -> None:
            try:
                function()
            except Exception as exc:  # noqa: BLE001 - surface background failures
                import traceback
                self.log("エラー: " + str(exc))
                self.log(traceback.format_exc())
                self.root.after(0, lambda: messagebox.showerror("エラー", str(exc)))
            finally:
                self.root.after(0, lambda: self.status.configure(text="準備完了"))

        self._worker = threading.Thread(target=runner, daemon=True)
        self._worker.start()

    def log(self, message: str) -> None:
        print(message)


def run(domain: ReviewDomain) -> None:
    ensure_dpi_awareness()
    root = tk.Tk()
    ReviewApp(root, domain)
    root.mainloop()
