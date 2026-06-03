from __future__ import annotations

import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import cv2
import numpy as np
from PIL import Image

from autopaint import __version__
from autopaint.config import DrawConfig, ProcessingConfig
from autopaint.drawer import BoundsViolation, RasterSize, simulate_tool_commands
from autopaint.failsafe import EmergencyStop, esc_backend_description
from autopaint.pipeline import build_execution_commands, create_plan, execute_draw, resolve_draw_polylines
from autopaint.validation import DrawValidationError
from autopaint.image_processing import build_binary_mask_from_gray, load_image_grayscale
from autopaint.planner import render_polyline_preview, render_segment_preview
from autopaint.updater import download_update, fetch_latest_update, schedule_apply_update
from autopaint.types import Rect

UPDATE_CHECK_DELAY_MS = 1500
LIVE_PREVIEW_DEBOUNCE_MS = 250
RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


class AutoPaintGui(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"JoensAutoDraw v{__version__}")
        self.geometry("980x680")
        self.minsize(900, 620)

        self.image_path = ctk.StringVar(value="")
        self.threshold = ctk.IntVar(value=140)
        self.blur = ctk.IntVar(value=5)
        self.step = ctk.IntVar(value=2)
        self.vector_step = ctk.DoubleVar(value=0.5)
        self.vector_min_points = ctk.IntVar(value=2)
        self.vector_jump_threshold = ctk.DoubleVar(value=10.0)
        self.line_gap = ctk.IntVar(value=2)
        self.speed = ctk.DoubleVar(value=70.0)
        self.step_pause_ms = ctk.DoubleVar(value=1.2)
        self.stroke_settle_ms = ctk.DoubleVar(value=3.0)
        self.preview_stroke_px = ctk.IntVar(value=2)
        self.countdown = ctk.IntVar(value=3)
        self.invert = ctk.BooleanVar(value=False)
        self.dry_run = ctk.BooleanVar(value=True)
        self.optimize_contour_travel = ctk.BooleanVar(value=True)
        self.compatibility_mode = ctk.BooleanVar(value=True)
        self.show_travel_preview = ctk.BooleanVar(value=True)
        self.mode = ctk.StringVar(value="contour")
        self.contour_epsilon = ctk.DoubleVar(value=1.2)
        self.use_otsu = ctk.BooleanVar(value=False)
        self.morph_close = ctk.IntVar(value=0)
        self.morph_open = ctk.IntVar(value=0)
        self.min_contour_area = ctk.IntVar(value=25)
        self.contour_scope = ctk.StringVar(value="external")

        self._busy = False
        self._last_plan = None
        self._selected_rect: Rect | None = None
        self._preview_images: list[ctk.CTkImage] = []
        self._slider_value_labels: dict[str, ctk.CTkLabel] = {}
        self._pipeline_labels: dict[str, ctk.CTkLabel] = {}
        self._toolpath_stats_label: ctk.CTkLabel | None = None
        self._live_preview_after_id: str | None = None
        self._live_plan_generation = 0
        self._build_layout()
        self._bind_live_preview_traces()
        self.after(UPDATE_CHECK_DELAY_MS, self._check_for_updates_on_startup)

    def _check_for_updates_on_startup(self) -> None:
        def worker() -> None:
            try:
                update = fetch_latest_update()
            except Exception:
                return
            if update is None:
                return

            def prompt() -> None:
                self._append_log(f"Update available: v{update.version} (current v{__version__}).")
                if not messagebox.askyesno(
                    "Update available",
                    f"A new version is available: v{update.version}\n"
                    f"You are running v{__version__}.\n\n"
                    "Download and install it now?",
                ):
                    self._append_log("Update skipped by user.")
                    return

                self._append_log(f"Downloading v{update.version}...")
                self.status_label.configure(text="Downloading update...")

                def download_worker() -> None:
                    try:
                        downloaded = download_update(update)
                    except Exception as exc:
                        self.after(
                            0,
                            lambda: (
                                self._append_log(f"Update download failed: {exc}"),
                                self.status_label.configure(text="Update failed"),
                            ),
                        )
                        return

                    def offer_restart() -> None:
                        self.status_label.configure(text="Update ready")
                        self._append_log(f"Update downloaded: {downloaded}")
                        if messagebox.askokcancel(
                            "Restart to update",
                            f"Version v{update.version} is ready.\n"
                            "Restart JoensAutoDraw now to apply the update?",
                        ):
                            schedule_apply_update(downloaded)
                        else:
                            self._append_log("Update will apply on next manual restart.")

                    self.after(0, offer_restart)

                threading.Thread(target=download_worker, daemon=True).start()

            self.after(0, prompt)

        threading.Thread(target=worker, daemon=True).start()

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, corner_radius=12)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="AutoPaint Control Center",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=14, pady=12)

        body = ctk.CTkFrame(self, corner_radius=12)
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(body, corner_radius=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        left.grid_columnconfigure(1, weight=1)

        self._add_path_controls(left)
        tabs = ctk.CTkTabview(left, corner_radius=10)
        tabs.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 6))
        raster_tab = tabs.add("Raster")
        vector_tab = tabs.add("Vector/CNC")
        raster_tab.grid_columnconfigure(1, weight=1)
        vector_tab.grid_columnconfigure(1, weight=1)
        self._add_processing_controls(raster_tab)
        self._add_vector_controls(vector_tab)
        self._add_draw_controls(left)

        right = ctk.CTkFrame(body, corner_radius=12)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1)
        right.grid_rowconfigure(5, weight=2)

        ctk.CTkLabel(
            right,
            text="Run Actions",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))

        self.status_label = ctk.CTkLabel(right, text="Ready", anchor="w")
        self.status_label.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        pipeline_panel = ctk.CTkFrame(right, corner_radius=10)
        pipeline_panel.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        pipeline_panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            pipeline_panel,
            text="Workflow",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        for idx, (key, text) in enumerate(
            [
                ("import", "1) Import source"),
                ("plan", "2) Build plan"),
                ("area", "3) Select area"),
                ("draw", "4) Draw"),
            ],
            start=1,
        ):
            label = ctk.CTkLabel(pipeline_panel, text=f"{text}: waiting", anchor="w")
            label.grid(row=idx, column=0, sticky="ew", padx=8, pady=2)
            self._pipeline_labels[key] = label

        log_box = ctk.CTkTextbox(right, corner_radius=10)
        log_box.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))
        log_box.insert("end", "Welcome to AutoPaint.\nStart with DRY RUN checked.\n")
        log_box.insert(
            "end",
            f"Emergency stop backends: {esc_backend_description()}.\n",
        )
        log_box.configure(state="disabled")
        self.log_box = log_box

        button_bar = ctk.CTkFrame(right, fg_color="transparent")
        button_bar.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))
        button_bar.grid_columnconfigure((0, 1, 2), weight=1)

        self.plan_button = ctk.CTkButton(
            button_bar, text="Build Plan", command=self.on_build_plan
        )
        self.plan_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.draw_button = ctk.CTkButton(
            button_bar, text="Draw Now", command=self.on_draw_now
        )
        self.draw_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.area_button = ctk.CTkButton(
            button_bar, text="Select Area", command=self.on_select_area
        )
        self.area_button.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        preview_panel = ctk.CTkFrame(right, corner_radius=10)
        preview_panel.grid(row=5, column=0, sticky="nsew", padx=12, pady=(0, 12))
        preview_panel.grid_columnconfigure((0, 1), weight=1)
        preview_panel.grid_rowconfigure(1, weight=1)
        preview_panel.grid_rowconfigure(2, weight=0)

        ctk.CTkLabel(preview_panel, text="Binary mask (live)").grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )
        ctk.CTkLabel(preview_panel, text="Toolpath preview (live)").grid(
            row=0, column=1, sticky="w", padx=8, pady=(8, 4)
        )

        self.mask_preview = ctk.CTkLabel(preview_panel, text="No preview yet")
        self.mask_preview.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.plan_preview = ctk.CTkLabel(preview_panel, text="No preview yet")
        self.plan_preview.grid(row=1, column=1, sticky="nsew", padx=8, pady=(0, 8))
        stats = ctk.CTkLabel(preview_panel, text="Toolpath stats: n/a", anchor="w", justify="left")
        stats.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        self._toolpath_stats_label = stats
        self._set_pipeline_step("import", "waiting")
        self._set_pipeline_step("plan", "waiting")
        self._set_pipeline_step("area", "waiting")
        self._set_pipeline_step("draw", "waiting")

    def _set_pipeline_step(self, step_key: str, state: str) -> None:
        label = self._pipeline_labels.get(step_key)
        if label is None:
            return
        prefix_map = {
            "import": "1) Import source",
            "plan": "2) Build plan",
            "area": "3) Select area",
            "draw": "4) Draw",
        }
        text = f"{prefix_map.get(step_key, step_key)}: {state}"
        color_map = {
            "waiting": "#9ca3af",
            "ready": "#fbbf24",
            "done": "#22c55e",
            "running": "#60a5fa",
            "error": "#f87171",
        }
        label.configure(text=text, text_color=color_map.get(state, "#e5e7eb"))

    def _reference_rect(self, plan) -> Rect:
        if self._selected_rect is not None:
            return self._selected_rect
        return Rect(left=0, top=0, right=plan.source_width, bottom=plan.source_height)

    def _estimate_runtime_seconds(self, plan, draw_conf: DrawConfig) -> float:
        source_size = RasterSize(width=plan.source_width, height=plan.source_height)
        target_rect = self._reference_rect(plan)
        commands = build_execution_commands(
            plan=plan,
            draw_mode=self.mode.get(),
            draw_config=draw_conf,
            target_rect=target_rect,
            source_size=source_size,
        )
        return simulate_tool_commands(
            commands=commands,
            source_size=source_size,
            target_rect=target_rect,
            draw_config=draw_conf,
        ).estimated_seconds

    def _update_toolpath_stats(self, plan) -> None:
        if self._toolpath_stats_label is None:
            return
        draw_conf = self._build_draw()
        source_size = RasterSize(width=plan.source_width, height=plan.source_height)
        target_rect = self._reference_rect(plan)
        commands = build_execution_commands(
            plan=plan,
            draw_mode=self.mode.get(),
            draw_config=draw_conf,
            target_rect=target_rect,
            source_size=source_size,
        )
        stats = simulate_tool_commands(
            commands=commands,
            source_size=source_size,
            target_rect=target_rect,
            draw_config=draw_conf,
        )
        self._toolpath_stats_label.configure(
            text=(
                "Toolpath stats: "
                f"commands={len(commands)}, strokes={stats.stroke_count}, "
                f"draw_pixels={stats.draw_pixel_events}, moves={stats.move_events}, "
                f"est. time={stats.estimated_seconds:.1f}s"
            )
        )

    def _add_path_controls(self, parent: ctk.CTkScrollableFrame) -> None:
        row = 0
        ctk.CTkLabel(parent, text="Input", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(6, 6)
        )
        row += 1

        ctk.CTkEntry(parent, textvariable=self.image_path).grid(
            row=row, column=0, sticky="ew", padx=(0, 8), pady=4
        )
        ctk.CTkButton(parent, text="Browse", width=110, command=self.on_browse).grid(
            row=row, column=1, sticky="e", pady=4
        )

    def _add_processing_controls(self, parent: ctk.CTkScrollableFrame) -> None:
        row = 0

        self._add_slider(parent, row, "Threshold", self.threshold, 0, 255)
        row += 1
        self._add_slider(parent, row, "Blur (odd)", self.blur, 1, 21)
        row += 1
        self._add_slider(parent, row, "Step", self.step, 1, 8)
        row += 1
        self._add_slider(parent, row, "Line gap", self.line_gap, 0, 8)
        row += 1
        self._add_slider(parent, row, "Contour epsilon", self.contour_epsilon, 0.5, 6.0)
        row += 1
        self._add_slider(parent, row, "Min contour area", self.min_contour_area, 0, 500)
        row += 1
        self._add_slider(parent, row, "Morph close", self.morph_close, 0, 15)
        row += 1
        self._add_slider(parent, row, "Morph open", self.morph_open, 0, 15)
        row += 1

        ctk.CTkLabel(parent, text="Contour scope").grid(row=row, column=0, sticky="w", pady=4)
        ctk.CTkSegmentedButton(
            parent,
            values=["external", "all", "largest"],
            variable=self.contour_scope,
        ).grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        row += 1

        ctk.CTkCheckBox(parent, text="Auto threshold (Otsu)", variable=self.use_otsu).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=6
        )
        row += 1
        ctk.CTkCheckBox(parent, text="Invert threshold", variable=self.invert).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=6
        )
        row += 1
        ctk.CTkLabel(
            parent,
            text="Tip: external = outer outlines only. Increase Min area to drop speckle.",
            justify="left",
            anchor="w",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 4))

    def _add_vector_controls(self, parent) -> None:
        row = 0
        self._add_slider(parent, row, "Vector step (px)", self.vector_step, 0.5, 6.0)
        row += 1
        self._add_slider(parent, row, "Min path points", self.vector_min_points, 2, 20)
        row += 1
        self._add_slider(parent, row, "Jump split (px)", self.vector_jump_threshold, 2.0, 80.0)
        row += 1
        ctk.CTkLabel(
            parent,
            text="Tip: Increase Jump split if paths break too much.\nLower it if pen-down crosses gaps.",
            justify="left",
            anchor="w",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 4))

    def _add_draw_controls(self, parent: ctk.CTkScrollableFrame) -> None:
        row = 3
        ctk.CTkLabel(parent, text="Drawing", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(18, 6)
        )
        row += 1

        ctk.CTkLabel(parent, text="Mode").grid(row=row, column=0, sticky="w")
        ctk.CTkSegmentedButton(
            parent,
            values=["contour", "segments"],
            variable=self.mode,
        ).grid(row=row, column=1, sticky="ew", pady=4)
        row += 1

        self._add_slider(parent, row, "Speed", self.speed, 1.0, 100.0)
        row += 1
        self._add_slider(parent, row, "Step pause (ms)", self.step_pause_ms, 0.0, 8.0)
        row += 1
        self._add_slider(parent, row, "Pen settle (ms)", self.stroke_settle_ms, 0.0, 20.0)
        row += 1
        self._add_slider(parent, row, "Preview stroke (px)", self.preview_stroke_px, 1, 6)
        row += 1
        self._add_slider(parent, row, "Countdown", self.countdown, 0, 8)
        row += 1

        ctk.CTkCheckBox(parent, text="Dry run (safe)", variable=self.dry_run).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=6
        )
        row += 1
        ctk.CTkCheckBox(
            parent,
            text="Optimize contour travel order",
            variable=self.optimize_contour_travel,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        row += 1
        ctk.CTkCheckBox(
            parent,
            text="Compatibility mode (recommended)",
            variable=self.compatibility_mode,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        row += 1
        ctk.CTkCheckBox(
            parent,
            text="Show travel preview (pen-up moves)",
            variable=self.show_travel_preview,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)

    def _add_slider(self, parent, row, label, variable, frm, to) -> None:
        ctk.CTkLabel(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ctk.CTkSlider(parent, variable=variable, from_=frm, to=to).grid(
            row=row, column=1, sticky="ew", pady=4
        )
        value_label = ctk.CTkLabel(parent, text="")
        value_label.grid(row=row, column=2, sticky="e", padx=(8, 0), pady=4)

        slider_key = f"{label}_{row}"
        self._slider_value_labels[slider_key] = value_label

        def update_value_label(*_args) -> None:
            raw = variable.get()
            if isinstance(raw, float):
                text = f"{raw:.3f}" if abs(raw) < 1 else f"{raw:.2f}"
            else:
                text = str(raw)
            value_label.configure(text=text)

        variable.trace_add("write", update_value_label)
        update_value_label()

    def _append_log(self, text: str) -> None:
        def update() -> None:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"{text}\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.after(0, update)

    def _set_busy(self, busy: bool, status: str) -> None:
        def update() -> None:
            self._busy = busy
            self.status_label.configure(text=status)
            state = "disabled" if busy else "normal"
            self.plan_button.configure(state=state)
            self.draw_button.configure(state=state)
            self.area_button.configure(state=state)

        self.after(0, update)

    def on_browse(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Select source file",
            filetypes=[
                ("Supported files", "*.png *.jpg *.jpeg *.bmp *.webp *.svg *.gcode *.nc *.tap"),
                ("Vector paths", "*.svg *.gcode *.nc *.tap"),
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.webp"),
                ("All files", "*.*"),
            ],
        )
        if file_path:
            self.image_path.set(file_path)
            self._set_pipeline_step("import", "done")
            self._set_pipeline_step("plan", "ready")
            self._set_pipeline_step("draw", "waiting")
            self._schedule_live_preview(replan=True)

    def _is_raster_source(self) -> bool:
        path = self.image_path.get().strip()
        if not path:
            return False
        return Path(path).suffix.lower() in RASTER_SUFFIXES

    def _bind_live_preview_traces(self) -> None:
        replan_vars = (
            self.threshold,
            self.blur,
            self.step,
            self.line_gap,
            self.contour_epsilon,
            self.invert,
            self.use_otsu,
            self.morph_close,
            self.morph_open,
            self.min_contour_area,
            self.contour_scope,
            self.vector_step,
            self.vector_min_points,
            self.vector_jump_threshold,
            self.mode,
        )
        preview_only_vars = (self.preview_stroke_px, self.show_travel_preview, self.optimize_contour_travel)

        for variable in replan_vars:
            variable.trace_add("write", lambda *_args: self._schedule_live_preview(replan=True))
        for variable in preview_only_vars:
            variable.trace_add("write", lambda *_args: self._schedule_live_preview(replan=False))

    def _schedule_live_preview(self, *, replan: bool) -> None:
        if not self.image_path.get() or self._busy:
            return
        if self._live_preview_after_id is not None:
            self.after_cancel(self._live_preview_after_id)
        self._live_preview_after_id = self.after(
            LIVE_PREVIEW_DEBOUNCE_MS,
            lambda: self._run_live_preview(replan=replan),
        )

    def _run_live_preview(self, *, replan: bool) -> None:
        self._live_preview_after_id = None
        if not self.image_path.get() or self._busy:
            return

        if replan and self._is_raster_source():
            try:
                processing = self._build_processing()
                gray = load_image_grayscale(processing.image_path)
                mask = build_binary_mask_from_gray(gray, processing)
                self._show_mask_preview(mask)
            except Exception:
                pass
            self._run_live_plan_worker()
            return

        if replan:
            self._run_live_plan_worker()
            return

        if self._last_plan is not None:
            self._update_previews(self._last_plan, mode=self.mode.get())
            self._update_toolpath_stats(self._last_plan)

    def _run_live_plan_worker(self) -> None:
        self._live_plan_generation += 1
        generation = self._live_plan_generation

        def worker() -> None:
            try:
                processing = self._build_processing()
                plan = create_plan(
                    processing=processing,
                    contour_epsilon=float(self.contour_epsilon.get()),
                )
            except Exception:
                return
            if generation != self._live_plan_generation:
                return
            self._last_plan = plan
            mode = self.mode.get()
            self._update_previews(plan, mode=mode)
            self._update_toolpath_stats(plan)

            def finish() -> None:
                self._set_pipeline_step("plan", "done")
                if self._selected_rect is not None:
                    self._set_pipeline_step("draw", "ready")
                self.status_label.configure(text="Live preview updated")

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _array_to_preview_image(self, array: np.ndarray) -> ctk.CTkImage:
        canvas_size = 280
        rgb = cv2.cvtColor(array, cv2.COLOR_GRAY2RGB)
        src_h, src_w = rgb.shape[:2]
        scale = min(canvas_size / max(1, src_w), canvas_size / max(1, src_h))
        dst_w = max(1, int(round(src_w * scale)))
        dst_h = max(1, int(round(src_h * scale)))
        resized = cv2.resize(rgb, (dst_w, dst_h), interpolation=cv2.INTER_NEAREST)
        canvas = np.zeros((canvas_size, canvas_size, 3), dtype=np.uint8)
        x = (canvas_size - dst_w) // 2
        y = (canvas_size - dst_h) // 2
        canvas[y : y + dst_h, x : x + dst_w] = resized
        pil = Image.fromarray(canvas)
        return ctk.CTkImage(light_image=pil, dark_image=pil, size=(280, 280))

    def _show_mask_preview(self, mask: np.ndarray) -> None:
        mask_img = self._array_to_preview_image(mask)

        def update() -> None:
            self._preview_images = [mask_img, *self._preview_images[1:2]]
            self.mask_preview.configure(image=mask_img, text="")

        self.after(0, update)

    def _build_processing(self) -> ProcessingConfig:
        blur = int(round(self.blur.get()))
        if blur % 2 == 0:
            blur += 1
        scope = self.contour_scope.get()
        if scope not in {"external", "all", "largest"}:
            scope = "external"
        return ProcessingConfig(
            image_path=Path(self.image_path.get()),
            threshold=int(round(self.threshold.get())),
            blur_kernel=max(1, blur),
            sample_step=max(1, int(round(self.step.get()))),
            max_line_gap=max(0, int(round(self.line_gap.get()))),
            invert=self.invert.get(),
            vector_sample_step=max(0.5, float(self.vector_step.get())),
            vector_min_polyline_points=max(2, int(round(self.vector_min_points.get()))),
            vector_jump_threshold_px=max(1.0, float(self.vector_jump_threshold.get())),
            use_otsu=self.use_otsu.get(),
            morph_close_kernel=max(0, int(round(self.morph_close.get()))),
            morph_open_kernel=max(0, int(round(self.morph_open.get()))),
            contour_mode=scope,
            min_contour_area=max(0, int(round(self.min_contour_area.get()))),
        )

    def _build_draw(self) -> DrawConfig:
        speed_percent = float(self.speed.get())
        speed_ratio = max(0.0, min(1.0, speed_percent / 100.0))
        # Recalibrated for practical GUI speeds: 70 should be fast.
        # Non-linear curve gives finer control at lower speeds.
        slowest = 0.012
        fastest = 0.0
        move_duration = slowest - (slowest - fastest) * (speed_ratio**1.6)
        base_step_pause = max(0.0, float(self.step_pause_ms.get()) / 1000.0)
        # Speed should also affect per-point cadence.
        effective_step_pause = max(0.0, base_step_pause * (1.0 - 0.9 * speed_ratio))
        return DrawConfig(
            move_duration=move_duration,
            countdown_seconds=max(0, int(round(self.countdown.get()))),
            dry_run=self.dry_run.get(),
            step_pause_seconds=effective_step_pause,
            stroke_settle_seconds=max(0.0, float(self.stroke_settle_ms.get()) / 1000.0),
            optimize_contour_travel=self.optimize_contour_travel.get(),
            compatibility_mode=self.compatibility_mode.get(),
        )

    def on_build_plan(self) -> None:
        if self._busy:
            return
        if not self.image_path.get():
            messagebox.showwarning("Missing image", "Choose an image first.")
            return
        processing = self._build_processing()
        contour_epsilon = float(self.contour_epsilon.get())
        mode = self.mode.get()

        def worker() -> None:
            try:
                self._set_busy(True, "Building plan...")
                self._set_pipeline_step("plan", "running")
                plan = create_plan(
                    processing=processing,
                    contour_epsilon=contour_epsilon,
                )
                self._last_plan = plan
                self._append_log(
                    f"Plan ready ({plan.source_kind}): {plan.source_width}x{plan.source_height}, "
                    f"{plan.polyline_count} polylines, {plan.segment_count} segments, "
                    f"{plan.command_count} tool commands."
                )
                self._update_previews(plan, mode=mode)
                self._update_toolpath_stats(plan)
                self._set_busy(False, "Plan ready")
                self._set_pipeline_step("plan", "done")
                if self._selected_rect is not None:
                    self._set_pipeline_step("draw", "ready")
            except Exception as exc:
                self._append_log(f"Plan failed: {exc}")
                self._set_busy(False, "Error")
                self._set_pipeline_step("plan", "error")

        threading.Thread(target=worker, daemon=True).start()

    def on_draw_now(self) -> None:
        if self._busy:
            return
        if not self.image_path.get():
            messagebox.showwarning("Missing image", "Choose an image first.")
            return

        if not messagebox.askokcancel(
            "Safety check",
            "Use ESC or move mouse to top-left to stop.\nContinue?",
        ):
            return
        processing = self._build_processing()
        draw_conf = self._build_draw()
        contour_epsilon = float(self.contour_epsilon.get())
        mode = self.mode.get()
        selected_rect = self._selected_rect

        def worker() -> None:
            try:
                self._set_busy(True, "Preparing draw...")
                self._set_pipeline_step("draw", "running")
                plan = create_plan(
                    processing=processing,
                    contour_epsilon=contour_epsilon,
                )
                self._last_plan = plan
                self._update_previews(plan, mode=mode)
                self._update_toolpath_stats(plan)
                self._append_log(
                    f"Starting draw in {mode} mode from {plan.source_kind} "
                    f"(dry_run={draw_conf.dry_run})..."
                )
                if mode == "segments" and plan.segment_count == 0 and plan.polyline_count > 0:
                    self._append_log("Segments unavailable for this source, using contour paths.")
                if selected_rect is None:
                    raise RuntimeError("No draw area selected. Click Select Area first.")
                execute_draw(
                    draw_mode=mode,
                    plan=plan,
                    draw_config=draw_conf,
                    target_rect=selected_rect,
                )
                self._append_log("Draw completed.")
                self._set_busy(False, "Completed")
                self._set_pipeline_step("draw", "done")
            except (EmergencyStop, BoundsViolation) as stop:
                self._append_log(f"Stopped safely: {stop}")
                self._set_busy(False, "Stopped safely")
                self._set_pipeline_step("draw", "error")
            except DrawValidationError as validation_error:
                self._append_log(f"Preflight blocked draw: {validation_error}")
                self._set_busy(False, "Preflight failed")
                self._set_pipeline_step("draw", "error")
            except Exception as exc:
                self._append_log(f"Draw failed: {exc}")
                self._set_busy(False, "Error")
                self._set_pipeline_step("draw", "error")

        threading.Thread(target=worker, daemon=True).start()

    def _update_previews(self, plan, mode: str) -> None:
        def render_travel_preview(base: np.ndarray) -> np.ndarray:
            source_size = RasterSize(width=plan.source_width, height=plan.source_height)
            draw_conf = self._build_draw()
            ordered = resolve_draw_polylines(
                plan=plan,
                draw_mode=mode,
                draw_config=draw_conf,
                target_rect=self._reference_rect(plan),
                source_size=source_size,
            )
            if not self.show_travel_preview.get() or len(ordered) < 2:
                return base
            travel = base.copy()
            for idx in range(1, len(ordered)):
                prev = ordered[idx - 1].points[-1]
                cur = ordered[idx].points[0]
                cv2.line(
                    travel,
                    (prev.x, prev.y),
                    (cur.x, cur.y),
                    color=96,
                    thickness=1,
                    lineType=cv2.LINE_8,
                )
            return travel

        def to_ctk_image(array):
            return self._array_to_preview_image(array)

        def update_ui() -> None:
            preview_thickness = max(1, int(round(self.preview_stroke_px.get())))
            use_contour = mode == "contour" or not plan.segments
            if use_contour:
                planned = render_polyline_preview(
                    mask=plan.mask, polylines=plan.polylines, thickness=preview_thickness
                )
            else:
                planned = render_segment_preview(
                    mask=plan.mask, segments=plan.segments, thickness=preview_thickness
                )
            mask_for_preview = render_travel_preview(plan.mask)
            mask_img = to_ctk_image(mask_for_preview)
            plan_img = to_ctk_image(planned)
            self._preview_images = [mask_img, plan_img]
            self.mask_preview.configure(image=mask_img, text="")
            self.plan_preview.configure(image=plan_img, text="")

        self.after(0, update_ui)

    def on_select_area(self) -> None:
        if self._busy:
            return
        messagebox.showinfo(
            "Select draw area",
            "A full-screen overlay opens.\nClick and drag to select your drawing area.",
        )
        self.iconify()
        self.after(200, self._open_area_overlay)

    def _open_area_overlay(self) -> None:
        start = {"x": 0, "y": 0}
        rect_id = {"value": None}

        overlay = ctk.CTkToplevel(self)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-topmost", True)
        overlay.attributes("-alpha", 0.25)
        overlay.configure(fg_color="black")

        canvas = tk.Canvas(overlay, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_text(
            40,
            40,
            anchor="nw",
            fill="white",
            text="Drag to select draw area. Press ESC to cancel.",
            font=("Segoe UI", 16, "bold"),
        )

        def on_press(event) -> None:
            start["x"], start["y"] = event.x, event.y
            if rect_id["value"] is not None:
                canvas.delete(rect_id["value"])
            rect_id["value"] = canvas.create_rectangle(
                event.x, event.y, event.x, event.y, outline="#4ea1ff", width=2
            )

        def on_drag(event) -> None:
            if rect_id["value"] is not None:
                canvas.coords(rect_id["value"], start["x"], start["y"], event.x, event.y)

        def on_release(event) -> None:
            x1, y1 = start["x"], start["y"]
            x2, y2 = event.x, event.y
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            if right - left < 5 or bottom - top < 5:
                self._append_log("Area too small. Drag a larger rectangle.")
                return
            self._selected_rect = Rect(left=left, top=top, right=right, bottom=bottom)
            self._append_log(
                f"Selected area: left={left}, top={top}, width={right-left}, height={bottom-top}"
            )
            self.status_label.configure(text="Area selected")
            self._set_pipeline_step("area", "done")
            if self._last_plan is not None:
                self._set_pipeline_step("draw", "ready")
            overlay.destroy()
            self.deiconify()
            self.lift()

        def on_cancel(_event=None) -> None:
            overlay.destroy()
            self.deiconify()
            self.lift()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", on_cancel)


def run_gui() -> None:
    app = AutoPaintGui()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
