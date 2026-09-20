"""Tkinter GUI: Template tab (define regions + pattern) and Run tab
(preview, apply, undo). This is the only module that imports GUI code."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional

from PIL import Image, ImageTk

from image_renamer import extract, geometry, naming, runner
from image_renamer.profile import Field, LayoutGuard, Profile, ProfileError, ReferenceImage, default_profiles_dir

MAX_DISPLAY_DIM = 900


class RegionDialog(simpledialog.Dialog):
    """Asks for a field name and type when a region is drawn."""

    def __init__(self, parent, initial_name="", initial_type="text", initial_choices="intact,split"):
        self.initial_name = initial_name
        self.initial_type = initial_type
        self.initial_choices = initial_choices
        self.result_name: Optional[str] = None
        self.result_type: Optional[str] = None
        self.result_choices: Optional[list[str]] = None
        super().__init__(parent, title="Define field")

    def body(self, master):
        ttk.Label(master, text="Field name:").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value=self.initial_name)
        name_entry = ttk.Entry(master, textvariable=self.name_var)
        name_entry.grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(master, text="Field type:").grid(row=1, column=0, sticky="w")
        self.type_var = tk.StringVar(value=self.initial_type)
        type_combo = ttk.Combobox(
            master,
            textvariable=self.type_var,
            values=["digits", "letters", "alphanumeric", "date", "text", "choice"],
            state="readonly",
        )
        type_combo.grid(row=1, column=1, padx=4, pady=4)

        ttk.Label(master, text="Choices (if type=choice):").grid(row=2, column=0, sticky="w")
        self.choices_var = tk.StringVar(value=self.initial_choices)
        ttk.Entry(master, textvariable=self.choices_var).grid(row=2, column=1, padx=4, pady=4)
        ttk.Label(
            master, text="comma-separated, e.g. intact,split -- you'll\ntoggle this per photo in the Run tab",
            foreground="#888",
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        return name_entry

    def apply(self):
        self.result_name = self.name_var.get().strip().lower().replace(" ", "_")
        self.result_type = self.type_var.get()
        self.result_choices = [c.strip() for c in self.choices_var.get().split(",") if c.strip()]


class ImageRenamerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Image Batch Renamer")
        self.root.geometry("1100x700")

        self.profile = self._blank_profile()
        self.example_image: Optional[Image.Image] = None
        self.example_path: Optional[str] = None
        self.display_scale: float = 1.0
        self.tk_image = None

        self.drag_start: Optional[tuple[int, int]] = None
        self.drag_rect_id: Optional[int] = None
        self.selected_field_index: Optional[int] = None
        self.crop_selected: bool = False

        # State for moving/resizing an existing region (field or the
        # output-crop area) rather than drawing a brand-new one.
        self.drag_mode: Optional[str] = None  # None | "move" | "resize"
        self.drag_target = None  # field index, or "crop"
        self.drag_corner: Optional[str] = None  # "nw" | "ne" | "sw" | "se"
        self.drag_orig_canvas_rect: Optional[tuple[float, float, float, float]] = None
        self._preview_drag_rect: Optional[tuple[float, float, float, float]] = None

        self.preview_rows: list[runner.Row] = []
        self.choice_column_names: list[str] = []
        self.field_column_names: list[str] = []
        self.current_inspected_row: Optional[runner.Row] = None
        self.preview_valid_for_profile = False
        self.worker_thread: Optional[threading.Thread] = None
        self.cancel_flag = threading.Event()
        self.progress_queue: "queue.Queue" = queue.Queue()

        self._build_ui()

    # -- profile helpers ---------------------------------------------------

    def _blank_profile(self) -> Profile:
        return Profile(
            profile_name="Untitled",
            reference_image=ReferenceImage(0, 0, 1.0, ""),
            fields=[],
            filename_template="",
        )

    # -- UI construction -----------------------------------------------------

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)

        self.template_tab = ttk.Frame(notebook)
        self.run_tab = ttk.Frame(notebook)
        notebook.add(self.template_tab, text="Template")
        notebook.add(self.run_tab, text="Run")

        self._build_template_tab()
        self._build_run_tab()

    def _build_template_tab(self):
        top_bar = ttk.Frame(self.template_tab)
        top_bar.pack(side="top", fill="x", padx=4, pady=4)
        ttk.Button(top_bar, text="Load example image", command=self.load_example_image).pack(side="left")
        ttk.Button(top_bar, text="Load profile", command=self.load_profile).pack(side="left", padx=4)
        ttk.Button(top_bar, text="Save profile", command=self.save_profile).pack(side="left")
        ttk.Label(top_bar, text="Profile name:").pack(side="left", padx=(20, 2))
        self.profile_name_var = tk.StringVar(value=self.profile.profile_name)
        ttk.Entry(top_bar, textvariable=self.profile_name_var, width=24).pack(side="left")

        body = ttk.Frame(self.template_tab)
        body.pack(fill="both", expand=True, padx=4, pady=4)

        self.canvas = tk.Canvas(body, bg="#333333", width=MAX_DISPLAY_DIM, height=MAX_DISPLAY_DIM)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)

        side = ttk.Frame(body, width=320)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)

        ttk.Label(side, text="Fields").pack(anchor="w")
        self.field_listbox = tk.Listbox(side, height=12)
        self.field_listbox.pack(fill="x")
        self.field_listbox.bind("<<ListboxSelect>>", self._on_field_select)

        field_btns = ttk.Frame(side)
        field_btns.pack(fill="x", pady=4)
        ttk.Button(field_btns, text="Rename", command=self._rename_selected_field).pack(side="left")
        ttk.Button(field_btns, text="Delete", command=self._delete_selected_field).pack(side="left", padx=4)
        ttk.Label(
            side, text="Drag a region's body to move it, a corner to resize it.",
            foreground="#888", wraplength=300,
        ).pack(anchor="w", pady=(2, 0))

        ttk.Label(side, text="Output crop (optional):").pack(anchor="w", pady=(16, 0))
        self.draw_mode_var = tk.StringVar(value="field")
        crop_row = ttk.Frame(side)
        crop_row.pack(fill="x")
        ttk.Radiobutton(
            crop_row, text="Draw fields", value="field", variable=self.draw_mode_var,
        ).pack(side="left")
        ttk.Radiobutton(
            crop_row, text="Draw output crop", value="crop", variable=self.draw_mode_var,
        ).pack(side="left")
        ttk.Button(side, text="Clear output crop", command=self._clear_output_crop).pack(anchor="w", pady=(2, 0))
        ttk.Label(
            side,
            text="When set, every processed photo is saved cropped to this\narea under its new name; the untouched original is kept in\nan '_originals' subfolder.",
            foreground="#888", wraplength=300,
        ).pack(anchor="w")

        ttk.Label(side, text="Filename pattern:").pack(anchor="w", pady=(16, 0))
        self.pattern_var = tk.StringVar(value=self.profile.filename_template)
        pattern_entry = ttk.Entry(side, textvariable=self.pattern_var)
        pattern_entry.pack(fill="x")
        # Only a cheap syntax check on every keystroke -- no OCR. Building
        # the actual sample re-crops and re-OCRs every field, which used to
        # run on every single keystroke and made typing a pattern visibly
        # lag once there were more than a couple of fields. The real sample
        # is only computed when the operator clicks Preview, below.
        pattern_entry.bind("<KeyRelease>", lambda e: (self._check_template_syntax(), self._invalidate_preview()))

        ttk.Label(side, text="(click a field below to insert its placeholder)").pack(anchor="w")
        insert_frame = ttk.Frame(side)
        insert_frame.pack(fill="x", pady=4)
        self.insert_listbox = tk.Listbox(insert_frame, height=6)
        self.insert_listbox.pack(fill="x")
        self.insert_listbox.bind("<<ListboxSelect>>", self._insert_placeholder)

        sample_row = ttk.Frame(side)
        sample_row.pack(fill="x", pady=(16, 0))
        ttk.Label(sample_row, text="Sample filename:").pack(side="left")
        ttk.Button(sample_row, text="Preview", command=self._update_sample_name).pack(side="left", padx=6)
        self.sample_name_var = tk.StringVar(value="(click Preview to run OCR on the example image)")
        ttk.Label(side, textvariable=self.sample_name_var, foreground="#2a7", wraplength=300).pack(anchor="w")

        ttk.Label(side, text="Collision strategy:").pack(anchor="w", pady=(16, 0))
        self.collision_var = tk.StringVar(value=self.profile.collision_strategy)
        collision_combo = ttk.Combobox(
            side,
            textvariable=self.collision_var,
            values=["hash_suffix", "sequence"],
            state="readonly",
        )
        collision_combo.pack(fill="x")
        collision_combo.bind("<<ComboboxSelected>>", lambda e: self._invalidate_preview())

    def _build_run_tab(self):
        top_bar = ttk.Frame(self.run_tab)
        top_bar.pack(side="top", fill="x", padx=4, pady=4)
        ttk.Button(top_bar, text="Choose folder", command=self.choose_folder).pack(side="left")
        self.folder_var = tk.StringVar(value="")
        ttk.Label(top_bar, textvariable=self.folder_var).pack(side="left", padx=8)

        self.preview_btn = ttk.Button(top_bar, text="Preview", command=self.run_preview)
        self.preview_btn.pack(side="left", padx=(20, 4))
        self.cancel_btn = ttk.Button(top_bar, text="Cancel", command=self.cancel_preview, state="disabled")
        self.cancel_btn.pack(side="left")
        self.apply_btn = ttk.Button(top_bar, text="Apply", command=self.run_apply, state="disabled")
        self.apply_btn.pack(side="left", padx=4)
        self.undo_btn = ttk.Button(top_bar, text="Undo last run", command=self.run_undo)
        self.undo_btn.pack(side="left")

        self.progress = ttk.Progressbar(self.run_tab, mode="determinate")
        self.progress.pack(fill="x", padx=4)

        self.status_line_var = tk.StringVar(value="")
        ttk.Label(self.run_tab, textvariable=self.status_line_var).pack(anchor="w", padx=4)

        body = ttk.Frame(self.run_tab)
        body.pack(fill="both", expand=True, padx=4, pady=4)

        # The inspector is packed FIRST so it claims its fixed-width slice
        # of the body frame before the table's `expand=True` claims
        # everything else -- packing it after the table (as originally
        # written) starved it down to a near-zero-width sliver once the
        # table grew enough columns to want more space than the window had.
        inspector = ttk.Frame(body, width=480)
        inspector.pack(side="right", fill="y")
        inspector.pack_propagate(False)
        ttk.Label(inspector, text="Crop inspector").pack(anchor="w")
        canvas_frame = ttk.Frame(inspector)
        canvas_frame.pack(fill="both", expand=True)
        self.inspector_canvas = tk.Canvas(canvas_frame, width=460, height=700, bg="#222222")
        inspector_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.inspector_canvas.yview)
        self.inspector_canvas.configure(yscrollcommand=inspector_scroll.set)
        self.inspector_canvas.pack(side="left", fill="both", expand=True)
        inspector_scroll.pack(side="right", fill="y")
        self.inspector_canvas.bind(
            "<MouseWheel>",
            lambda e: self.inspector_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
        )

        self.toggle_frame = ttk.Frame(inspector)
        self.toggle_frame.pack(fill="x", pady=4)
        self.toggle_buttons: dict[str, ttk.Button] = {}

        table_frame = ttk.Frame(body)
        table_frame.pack(side="left", fill="both", expand=True)

        self.base_columns = ["original", "status", "proposed"]
        self.tree = ttk.Treeview(table_frame, columns=self.base_columns, show="headings", height=20)
        self.tree.heading("original", text="Current name", command=lambda: self._sort_tree("original"))
        self.tree.column("original", width=160)
        self.tree.heading("status", text="Status", command=lambda: self._sort_tree("status"))
        self.tree.column("status", width=70, anchor="center")
        self.tree.heading("proposed", text="Proposed name", command=lambda: self._sort_tree("proposed"))
        self.tree.column("proposed", width=220)

        tree_hscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=tree_hscroll.set)
        self.tree.pack(side="top", fill="both", expand=True)
        tree_hscroll.pack(side="bottom", fill="x")

        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click)

        self.tree.tag_configure("bad", background="#5a2020")

    # -- template tab logic ---------------------------------------------------

    def load_example_image(self):
        path = filedialog.askopenfilename(
            title="Choose an example image",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp")],
        )
        if not path:
            return
        try:
            image = extract.load_image(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not open image", str(exc))
            return
        self.example_image = image
        self.example_path = path
        width, height = image.size
        self.profile.reference_image = ReferenceImage(
            width=width, height=height, aspect_ratio=width / height,
            example_filename=os.path.basename(path),
        )
        self._render_canvas_image()

    def _render_canvas_image(self):
        if self.example_image is None:
            return
        width, height = self.example_image.size
        scale = min(MAX_DISPLAY_DIM / width, MAX_DISPLAY_DIM / height, 1.0)
        self.display_scale = scale
        disp = self.example_image.resize((max(1, round(width * scale)), max(1, round(height * scale))))
        self.tk_image = ImageTk.PhotoImage(disp)
        self.canvas.delete("all")
        self.canvas.config(width=disp.width, height=disp.height)
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image, tags="bg")
        self._redraw_regions()

    HANDLE_SIZE = 8

    def _region_canvas_rect(self, rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        width, height = self.example_image.size
        x0, y0, x1, y1 = rect
        return (
            x0 * width * self.display_scale, y0 * height * self.display_scale,
            x1 * width * self.display_scale, y1 * height * self.display_scale,
        )

    def _draw_handles(self, rect_canvas: tuple[float, float, float, float], color: str):
        cx0, cy0, cx1, cy1 = rect_canvas
        h = self.HANDLE_SIZE / 2
        for hx, hy in [(cx0, cy0), (cx1, cy0), (cx0, cy1), (cx1, cy1)]:
            self.canvas.create_rectangle(hx - h, hy - h, hx + h, hy + h, fill=color, outline="", tags="region")

    def _redraw_regions(self):
        self.canvas.delete("region")
        if self.example_image is None:
            return
        for i, f in enumerate(self.profile.fields):
            selected = i == self.selected_field_index
            rect_canvas = self._region_canvas_rect(f.rect)
            color = "#ffcc00" if selected else "#00ccff"
            self.canvas.create_rectangle(*rect_canvas, outline=color, width=2, tags="region")
            self.canvas.create_text(
                rect_canvas[0] + 3, rect_canvas[1] + 3, anchor="nw", text=f.name, fill=color, tags="region",
            )
            if selected:
                self._draw_handles(rect_canvas, color)

        if self.profile.output_crop is not None:
            rect_canvas = self._region_canvas_rect(self.profile.output_crop)
            color = "#ff5555" if self.crop_selected else "#aa3333"
            self.canvas.create_rectangle(*rect_canvas, outline=color, width=2, dash=(6, 3), tags="region")
            self.canvas.create_text(
                rect_canvas[0] + 3, rect_canvas[1] + 3, anchor="nw", text="output crop", fill=color, tags="region",
            )
            if self.crop_selected:
                self._draw_handles(rect_canvas, color)

    def _hit_test(self, x: float, y: float):
        """Returns (kind, target, corner): kind is "resize"/"move"/None,
        target is a field index or the string "crop", corner is one of
        "nw"/"ne"/"sw"/"se" for a resize. The currently-selected region is
        checked first so overlapping regions don't steal a click meant for
        the one already selected."""
        candidates: list[tuple] = [(i, f.rect) for i, f in enumerate(self.profile.fields)]
        if self.profile.output_crop is not None:
            candidates.append(("crop", self.profile.output_crop))

        def priority(item):
            target, _ = item
            is_selected = (target == "crop" and self.crop_selected) or (target == self.selected_field_index)
            return 0 if is_selected else 1

        candidates.sort(key=priority)
        tol = self.HANDLE_SIZE

        for target, rect in candidates:
            cx0, cy0, cx1, cy1 = self._region_canvas_rect(rect)
            for corner, (hx, hy) in {"nw": (cx0, cy0), "ne": (cx1, cy0), "sw": (cx0, cy1), "se": (cx1, cy1)}.items():
                if abs(x - hx) <= tol and abs(y - hy) <= tol:
                    return "resize", target, corner
        for target, rect in candidates:
            cx0, cy0, cx1, cy1 = self._region_canvas_rect(rect)
            if cx0 <= x <= cx1 and cy0 <= y <= cy1:
                return "move", target, None
        return None, None, None

    def _on_canvas_press(self, event):
        if self.example_image is None:
            return
        kind, target, corner = self._hit_test(event.x, event.y)
        self.drag_mode = kind
        self.drag_target = target
        self.drag_corner = corner
        self.drag_start = (event.x, event.y)

        if kind is not None:
            if target == "crop":
                self.crop_selected = True
                self.selected_field_index = None
                self.field_listbox.selection_clear(0, tk.END)
                rect = self.profile.output_crop
            else:
                self.selected_field_index = target
                self.crop_selected = False
                self.field_listbox.selection_clear(0, tk.END)
                self.field_listbox.selection_set(target)
                rect = self.profile.fields[target].rect
            self.drag_orig_canvas_rect = self._region_canvas_rect(rect)
            self._redraw_regions()
        else:
            self.drag_rect_id = self.canvas.create_rectangle(
                event.x, event.y, event.x, event.y, outline="#ffffff", width=1, tags="dragging"
            )

    def _on_canvas_drag(self, event):
        if self.drag_start is None:
            return
        if self.drag_mode is None:
            if self.drag_rect_id is None:
                return
            x0, y0 = self.drag_start
            self.canvas.coords(self.drag_rect_id, x0, y0, event.x, event.y)
            return

        x0, y0 = self.drag_start
        dx, dy = event.x - x0, event.y - y0
        ocx0, ocy0, ocx1, ocy1 = self.drag_orig_canvas_rect

        if self.drag_mode == "move":
            new_rect = (ocx0 + dx, ocy0 + dy, ocx1 + dx, ocy1 + dy)
        else:
            nx0, ny0, nx1, ny1 = ocx0, ocy0, ocx1, ocy1
            if "n" in self.drag_corner:
                ny0 = ocy0 + dy
            if "s" in self.drag_corner:
                ny1 = ocy1 + dy
            if "w" in self.drag_corner:
                nx0 = ocx0 + dx
            if "e" in self.drag_corner:
                nx1 = ocx1 + dx
            new_rect = (nx0, ny0, nx1, ny1)

        self._preview_drag_rect = new_rect
        self.canvas.delete("drag_preview")
        self.canvas.create_rectangle(*new_rect, outline="#ffffff", width=1, tags="drag_preview")

    def _on_canvas_release(self, event):
        if self.drag_start is None or self.example_image is None:
            self._reset_drag_state()
            return

        if self.drag_mode is None:
            x0, y0 = self.drag_start
            canvas_rect = (x0, y0, event.x, event.y)
            width, height = self.example_image.size
            rect = geometry.canvas_to_profile_rect(canvas_rect, self.display_scale, width, height)
            clamped = geometry.clamp_profile_rect(rect)
            self._reset_drag_state()
            if clamped is None:
                messagebox.showwarning("Region too small", "Draw a rectangle with a non-zero area.")
                return
            if self.draw_mode_var.get() == "crop":
                self.profile.output_crop = clamped
                self.crop_selected = True
                self._redraw_regions()
                self._invalidate_preview()
                return
            self._create_field_from_rect(clamped)
            return

        target = self.drag_target
        new_canvas_rect = self._preview_drag_rect
        self._reset_drag_state()
        if new_canvas_rect is None:
            return

        width, height = self.example_image.size
        rect = geometry.canvas_to_profile_rect(new_canvas_rect, self.display_scale, width, height)
        clamped = geometry.clamp_profile_rect(rect)
        if clamped is None:
            messagebox.showwarning("Region too small", "That change would leave a zero-area region; discarded.")
            self._redraw_regions()
            return

        if target == "crop":
            self.profile.output_crop = clamped
        else:
            self.profile.fields[target].rect = clamped
        self._redraw_regions()
        self._check_template_syntax()
        self._invalidate_preview()

    def _reset_drag_state(self):
        self.canvas.delete("dragging")
        self.canvas.delete("drag_preview")
        self.drag_start = None
        self.drag_rect_id = None
        self.drag_mode = None
        self.drag_target = None
        self.drag_corner = None
        self.drag_orig_canvas_rect = None
        self._preview_drag_rect = None

    def _create_field_from_rect(self, clamped: tuple[float, float, float, float]):
        dialog = RegionDialog(self.root)
        if not dialog.result_name:
            return
        if any(f.name == dialog.result_name for f in self.profile.fields):
            messagebox.showerror("Duplicate field", f"A field named {dialog.result_name!r} already exists.")
            return
        try:
            field = Field(
                name=dialog.result_name, type=dialog.result_type, rect=clamped,
                options=self._options_from_dialog(dialog),
            )
        except ProfileError as exc:
            messagebox.showerror("Invalid field", str(exc))
            return
        self.profile.fields.append(field)
        self._refresh_field_list()
        self._redraw_regions()
        self._invalidate_preview()

    def _clear_output_crop(self):
        self.profile.output_crop = None
        self.crop_selected = False
        self._redraw_regions()
        self._invalidate_preview()

    def _options_from_dialog(self, dialog: RegionDialog) -> dict:
        if dialog.result_type != "choice":
            return {}
        choices = dialog.result_choices or ["intact", "split"]
        if len(choices) < 2:
            messagebox.showwarning("Choices", "Need at least two comma-separated choices; using intact,split.")
            choices = ["intact", "split"]
        return {"choices": choices, "default": choices[0]}

    def _refresh_field_list(self):
        self.field_listbox.delete(0, tk.END)
        self.insert_listbox.delete(0, tk.END)
        for f in self.profile.fields:
            self.field_listbox.insert(tk.END, f"{f.name} ({f.type})")
            self.insert_listbox.insert(tk.END, f.name)

    def _on_field_select(self, _event):
        selection = self.field_listbox.curselection()
        self.selected_field_index = selection[0] if selection else None
        self.crop_selected = False
        self._redraw_regions()

    def _rename_selected_field(self):
        if self.selected_field_index is None:
            return
        f = self.profile.fields[self.selected_field_index]
        initial_choices = ",".join(f.options.get("choices", ["intact", "split"]))
        dialog = RegionDialog(self.root, initial_name=f.name, initial_type=f.type, initial_choices=initial_choices)
        if not dialog.result_name:
            return
        if dialog.result_name != f.name and any(x.name == dialog.result_name for x in self.profile.fields):
            messagebox.showerror("Duplicate field", f"A field named {dialog.result_name!r} already exists.")
            return
        old_name = f.name
        f.name = dialog.result_name
        f.type = dialog.result_type
        f.options = self._options_from_dialog(dialog)
        if old_name != f.name:
            self.pattern_var.set(self.pattern_var.get().replace(f"{{{old_name}}}", f"{{{f.name}}}"))
        self._refresh_field_list()
        self._redraw_regions()
        self._check_template_syntax()
        self._invalidate_preview()

    def _delete_selected_field(self):
        if self.selected_field_index is None:
            return
        del self.profile.fields[self.selected_field_index]
        self.selected_field_index = None
        self._refresh_field_list()
        self._redraw_regions()
        self._invalidate_preview()

    def _insert_placeholder(self, _event):
        selection = self.insert_listbox.curselection()
        if not selection:
            return
        name = self.insert_listbox.get(selection[0])
        self.pattern_var.set(self.pattern_var.get() + f"{{{name}}}")
        self._check_template_syntax()

    def _check_template_syntax(self):
        """Cheap, no-OCR check run on every keystroke/edit: just confirms
        the template's placeholders are all known fields. The actual
        sample filename (which requires cropping and OCR-ing every field
        against the example image) is only computed when the operator
        clicks Preview -- see _update_sample_name."""
        template = self.pattern_var.get()
        try:
            naming.validate_template(template, self.profile.field_names())
        except naming.TemplateError as exc:
            self.sample_name_var.set(f"Invalid: {exc}")
            return
        self.sample_name_var.set("(click Preview to run OCR on the example image)")

    def _update_sample_name(self):
        template = self.pattern_var.get()
        try:
            naming.validate_template(template, self.profile.field_names())
        except naming.TemplateError as exc:
            self.sample_name_var.set(f"Invalid: {exc}")
            return
        if self.example_image is None:
            self.sample_name_var.set("(load an example image)")
            return

        values = {}
        for f in self.profile.fields:
            if f.type == "choice":
                choices = f.options.get("choices") or ["intact", "split"]
                values[f.name] = f.options.get("default", choices[0])
                continue
            crop = extract.crop_field(self.example_image, f.rect)
            preprocessed = extract.preprocess_for_ocr(crop)
            try:
                raw = extract.ocr_engine(preprocessed)
            except Exception:  # noqa: BLE001 - OCR engine may be unavailable in dev
                raw = ""
            result = extract.extract_and_validate_field(f.name, f.type, raw, f.options, f.required)
            values[f.name] = result.value or f"<{f.name}>"

        ext = os.path.splitext(self.example_path or "")[1] or ".jpg"
        orig_stem = os.path.splitext(os.path.basename(self.example_path or "example"))[0]
        name = naming.build_filename(
            template, values, orig_stem=orig_stem, ext=ext, index=1,
            max_filename_length=self.profile.max_filename_length, today=date.today(),
        )
        self.sample_name_var.set(name or "(empty after sanitisation)")

    def save_profile(self):
        if self.example_image is None:
            messagebox.showerror("No example image", "Load an example image before saving.")
            return
        if not self._sync_profile_from_template_tab():
            return

        default_dir = default_profiles_dir()
        default_dir.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(
            title="Save profile",
            initialdir=str(default_dir),
            initialfile=f"{self.profile.profile_name}.json",
            defaultextension=".json",
            filetypes=[("Profile", "*.json")],
        )
        if not path:
            return
        self.profile.save(path)
        self.preview_valid_for_profile = False
        messagebox.showinfo("Saved", f"Profile saved to {path}")

    def load_profile(self):
        default_dir = default_profiles_dir()
        path = filedialog.askopenfilename(
            title="Load profile",
            initialdir=str(default_dir) if default_dir.exists() else str(Path.home()),
            filetypes=[("Profile", "*.json")],
        )
        if not path:
            return
        try:
            self.profile = Profile.load(path)
        except ProfileError as exc:
            messagebox.showerror("Could not load profile", str(exc))
            return
        self.profile_name_var.set(self.profile.profile_name)
        self.pattern_var.set(self.profile.filename_template)
        self.collision_var.set(self.profile.collision_strategy)
        self.example_image = None
        self.example_path = None
        self._refresh_field_list()
        self.canvas.delete("all")
        self.preview_valid_for_profile = False
        self._update_apply_state()

    # -- run tab logic ---------------------------------------------------

    def choose_folder(self):
        # Batches typically live next to (or ARE) the folder the example
        # image was loaded from, so start the picker there rather than
        # wherever the OS last remembers -- the operator can then usually
        # just confirm the folder, or step up one level to a sibling batch.
        initial_dir = None
        current = self.folder_var.get()
        if current and os.path.isdir(current):
            initial_dir = os.path.dirname(current) or current
        elif self.example_path:
            initial_dir = os.path.dirname(self.example_path)

        kwargs = {"title": "Choose a folder of images"}
        if initial_dir and os.path.isdir(initial_dir):
            kwargs["initialdir"] = initial_dir
        folder = filedialog.askdirectory(**kwargs)
        if not folder:
            return
        self.folder_var.set(folder)
        self.preview_valid_for_profile = False
        self._update_apply_state()

    def _sync_profile_from_template_tab(self) -> bool:
        """Copies the Template tab's live UI state (pattern text, collision
        strategy, profile name) onto self.profile. Region rects are already
        mutated in place as the operator drags them, but these three fields
        live in separate Tk variables and previously only reached the
        profile object inside save_profile() -- so Preview could silently
        run against a stale filename template until the operator saved.
        Returns False (after showing an error) if the pattern is invalid."""
        template = self.pattern_var.get()
        try:
            naming.validate_template(template, self.profile.field_names())
        except naming.TemplateError as exc:
            messagebox.showerror("Invalid template", str(exc))
            return False
        self.profile.filename_template = template
        self.profile.collision_strategy = self.collision_var.get()
        self.profile.profile_name = self.profile_name_var.get().strip() or self.profile.profile_name
        return True

    def run_preview(self):
        folder = self.folder_var.get()
        if not folder:
            messagebox.showerror("No folder", "Choose a folder first.")
            return
        if not self._sync_profile_from_template_tab():
            return
        if not self.profile.fields or not self.profile.filename_template:
            messagebox.showerror("No template", "Define fields and a filename pattern in the Template tab first.")
            return
        if not os.listdir(folder) and not os.path.isdir(folder):
            messagebox.showerror("Folder not found", folder)
            return

        self.tree.delete(*self.tree.get_children())
        self.preview_rows = []
        self.cancel_flag.clear()
        self.preview_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.apply_btn.config(state="disabled")
        self.progress.config(value=0, maximum=1)
        self.status_line_var.set("Running preview...")

        def worker():
            def progress(done, total):
                self.progress_queue.put(("progress", done, total))

            def cancel_check():
                return self.cancel_flag.is_set()

            try:
                rows = runner.preview(folder, self.profile, progress=progress, cancel_check=cancel_check)
                self.progress_queue.put(("done", rows))
            except Exception as exc:  # noqa: BLE001
                self.progress_queue.put(("error", str(exc)))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()
        self.root.after(100, self._poll_progress_queue)

    def cancel_preview(self):
        self.cancel_flag.set()

    def _poll_progress_queue(self):
        try:
            while True:
                message = self.progress_queue.get_nowait()
                kind = message[0]
                if kind == "progress":
                    _, done, total = message
                    self.progress.config(value=done, maximum=max(total, 1))
                elif kind == "done":
                    _, rows = message
                    self._on_preview_done(rows)
                    return
                elif kind == "error":
                    _, error = message
                    messagebox.showerror("Preview failed", error)
                    self._reset_preview_controls()
                    return
        except queue.Empty:
            pass
        if self.worker_thread and self.worker_thread.is_alive():
            self.root.after(100, self._poll_progress_queue)

    def _reset_preview_controls(self):
        self.preview_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.status_line_var.set("")

    def _choice_fields(self) -> list[Field]:
        return [f for f in self.profile.fields if f.type == "choice"]

    def _configure_choice_columns(self):
        choice_fields = self._choice_fields()
        self.choice_column_names = [f.name for f in choice_fields]
        self.field_column_names = [f.name for f in self.profile.fields]
        # Current name, Status, one column per field, Proposed name last --
        # the proposed name is the thing being confirmed, so it reads as
        # the conclusion after the fields that produced it.
        columns = ["original", "status", *self.field_column_names, "proposed"]
        self.tree["columns"] = columns
        self.tree.heading("original", text="Current name", command=lambda: self._sort_tree("original"))
        self.tree.column("original", width=160)
        self.tree.heading("status", text="Status", command=lambda: self._sort_tree("status"))
        self.tree.column("status", width=70, anchor="center")
        self.tree.heading("proposed", text="Proposed name", command=lambda: self._sort_tree("proposed"))
        self.tree.column("proposed", width=220)
        for name in self.field_column_names:
            self.tree.heading(name, text=name)
            self.tree.column(name, width=100, anchor="center")

    def _on_preview_done(self, rows: list[runner.Row]):
        self.preview_rows = rows
        self.preview_valid_for_profile = True
        self._reset_preview_controls()
        self._configure_choice_columns()

        ok_count = sum(1 for r in rows if r.status == runner.STATUS_OK)
        already = sum(1 for r in rows if r.status == runner.STATUS_ALREADY_PROCESSED)
        needs_attention = len(rows) - ok_count - already
        self.status_line_var.set(
            f"{ok_count} of {len(rows)} ready, {needs_attention} need attention"
            + (f" ({already} already processed)" if already else "")
        )

        def sort_key(r: runner.Row):
            return (0 if r.status in (runner.STATUS_OK, runner.STATUS_ALREADY_PROCESSED) else -1, r.original_name)

        self.tree.delete(*self.tree.get_children())
        for row in sorted(rows, key=sort_key):
            tags = ("bad",) if row.status not in (runner.STATUS_OK, runner.STATUS_ALREADY_PROCESSED) else ()
            field_values = [row.extracted.get(name, "") for name in self.field_column_names]
            self.tree.insert(
                "", "end", iid=row.original_path,
                values=(row.original_name, row.status, *field_values, row.proposed_name),
                tags=tags,
            )
        self._update_apply_state()

    def _tree_click_target(self, event):
        """Resolves a click to (row, field_name), or (None, None) if the
        click isn't on an editable field cell of an OK row."""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None, None
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id or not col_id:
            return None, None
        columns = self.tree["columns"]
        col_index = int(col_id.replace("#", "")) - 1
        if col_index < 0 or col_index >= len(columns):
            return None, None
        col_name = columns[col_index]
        if col_name not in self.field_column_names:
            return None, None
        row = next((r for r in self.preview_rows if r.original_path == row_id), None)
        if row is None or row.status != runner.STATUS_OK:
            return None, None
        return row, col_name

    def _apply_field_edit(self, row: runner.Row, col_name: str, new_value: str):
        row.extracted[col_name] = new_value
        runner.recompute_row_filename(row, self.profile, self.preview_rows, self.folder_var.get())

        self.tree.set(row.original_path, col_name, row.extracted[col_name])
        self.tree.set(row.original_path, "proposed", row.proposed_name)
        self.tree.set(row.original_path, "status", row.status)
        tags = ("bad",) if row.status not in (runner.STATUS_OK, runner.STATUS_ALREADY_PROCESSED) else ()
        self.tree.item(row.original_path, tags=tags)

    def _on_tree_click(self, event):
        row, col_name = self._tree_click_target(event)
        if row is None or col_name not in self.choice_column_names:
            return
        field = self.profile.get_field(col_name)
        choices = field.options.get("choices") or ["intact", "split"]
        new_value = runner.cycle_choice_value(row.extracted.get(col_name, ""), choices)
        self._apply_field_edit(row, col_name, new_value)

    def _on_tree_double_click(self, event):
        row, col_name = self._tree_click_target(event)
        if row is None:
            return
        current = row.extracted.get(col_name, "")
        new_value = simpledialog.askstring(
            "Fix extracted value", f"{col_name}:", initialvalue=current, parent=self.root,
        )
        if new_value is None or new_value == current:
            return
        self._apply_field_edit(row, col_name, new_value)

    def _sort_tree(self, column):
        items = [(self.tree.set(k, column), k) for k in self.tree.get_children("")]
        items.sort()
        for index, (_, k) in enumerate(items):
            self.tree.move(k, "", index)

    def _on_row_select(self, _event):
        selection = self.tree.selection()
        if not selection:
            return
        path = selection[0]
        row = next((r for r in self.preview_rows if r.original_path == path), None)
        if row is None:
            return
        self._render_inspector(row)

    def _render_inspector(self, row: runner.Row):
        self.inspector_canvas.delete("all")
        self.current_inspected_row = row
        for btn in self.toggle_buttons.values():
            btn.destroy()
        self.toggle_buttons.clear()

        if self.example_image is None:
            return
        try:
            image = extract.load_image(row.original_path)
        except Exception:  # noqa: BLE001
            return

        # Choice fields need to actually be legible to make a visual call
        # on, unlike text fields where only the extracted string matters --
        # show them first, much larger, with a toggle button right there.
        choice_fields = [f for f in self.profile.fields if f.type == "choice"]
        other_fields = [f for f in self.profile.fields if f.type != "choice"]

        y = 5
        for f in choice_fields:
            crop = extract.crop_field(image, f.rect)
            crop.thumbnail((440, 320))
            tk_crop = ImageTk.PhotoImage(crop)
            self.inspector_canvas.image_refs = getattr(self.inspector_canvas, "image_refs", [])
            self.inspector_canvas.image_refs.append(tk_crop)
            self.inspector_canvas.create_image(5, y, anchor="nw", image=tk_crop)
            value = row.extracted.get(f.name, "")
            self.inspector_canvas.create_text(
                5, y + crop.height + 6, anchor="nw", fill="white",
                font=("TkDefaultFont", 14, "bold"),
                text=f"{f.name}: {value}",
            )
            y += crop.height + 34

            btn = ttk.Button(
                self.toggle_frame, text=f"Toggle {f.name}",
                command=lambda name=f.name: self._toggle_choice_from_inspector(name),
            )
            btn.pack(side="left", padx=4)
            self.toggle_buttons[f.name] = btn

        for f in other_fields:
            crop = extract.crop_field(image, f.rect)
            crop.thumbnail((240, 60))
            tk_crop = ImageTk.PhotoImage(crop)
            self.inspector_canvas.image_refs = getattr(self.inspector_canvas, "image_refs", [])
            self.inspector_canvas.image_refs.append(tk_crop)
            self.inspector_canvas.create_image(5, y, anchor="nw", image=tk_crop)
            value = row.extracted.get(f.name, "")
            self.inspector_canvas.create_text(
                5, y + 62, anchor="nw", fill="white", text=f"{f.name}: {value!r}"
            )
            y += 90

        self.inspector_canvas.configure(scrollregion=(0, 0, 460, y + 10))

    def _toggle_choice_from_inspector(self, field_name: str):
        row = self.current_inspected_row
        if row is None or row.status != runner.STATUS_OK:
            return
        field = self.profile.get_field(field_name)
        choices = field.options.get("choices") or ["intact", "split"]
        row.extracted[field_name] = runner.cycle_choice_value(row.extracted.get(field_name, ""), choices)
        runner.recompute_row_filename(row, self.profile, self.preview_rows, self.folder_var.get())

        self.tree.set(row.original_path, field_name, row.extracted[field_name])
        self.tree.set(row.original_path, "proposed", row.proposed_name)
        self.tree.set(row.original_path, "status", row.status)
        tags = ("bad",) if row.status not in (runner.STATUS_OK, runner.STATUS_ALREADY_PROCESSED) else ()
        self.tree.item(row.original_path, tags=tags)
        self._render_inspector(row)

    def _update_apply_state(self):
        state = "normal" if self.preview_valid_for_profile and self.preview_rows else "disabled"
        self.apply_btn.config(state=state)

    def _invalidate_preview(self):
        """A field moved, resized, was added/removed/renamed, or the
        pattern/collision strategy/crop area changed. Per spec, Apply must
        not run against a preview computed before that change -- so require
        a fresh Preview rather than silently reusing stale proposed names."""
        if self.preview_valid_for_profile:
            self.preview_valid_for_profile = False
            self._update_apply_state()

    def run_apply(self):
        if not self.preview_valid_for_profile:
            messagebox.showerror("Preview is stale", "Run Preview again before applying.")
            return
        ok_rows = [r for r in self.preview_rows if r.status == runner.STATUS_OK]
        if not ok_rows:
            messagebox.showinfo("Nothing to do", "No rows are ready to rename.")
            return
        if not messagebox.askyesno("Apply renames", f"Rename {len(ok_rows)} file(s)?"):
            return

        folder = self.folder_var.get()
        report = runner.apply(self.preview_rows, folder, self.profile)
        message = f"Renamed {len(report.renamed)} file(s)."
        if report.failed:
            message += f"\n{len(report.failed)} failed: " + ", ".join(
                r.original_name for r, _ in report.failed[:5]
            )
        message += f"\nManifest: {report.manifest_path}"
        messagebox.showinfo("Apply complete", message)
        self.preview_valid_for_profile = False
        self._update_apply_state()

    def run_undo(self):
        folder = self.folder_var.get()
        if not folder:
            messagebox.showerror("No folder", "Choose a folder first.")
            return
        report = runner.undo_last_run(folder)
        if not report.manifest_path:
            messagebox.showinfo("Undo", "No manifest found in this folder.")
            return
        message = f"Restored {len(report.restored)} file(s)."
        if report.skipped:
            message += f"\n{len(report.skipped)} skipped."
        messagebox.showinfo("Undo complete", message)


def main():
    root = tk.Tk()
    ImageRenamerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
