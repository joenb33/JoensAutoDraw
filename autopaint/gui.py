from __future__ import annotations

import os
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
from autopaint.drawer import (
    BoundsViolation,
    RasterSize,
    render_tool_command_preview_on_mask,
    simulate_tool_commands,
)
from autopaint.failsafe import EmergencyStop, esc_backend_description
from autopaint.pipeline import build_execution_commands, create_plan, execute_draw
from autopaint.validation import DrawValidationError
from autopaint.image_processing import (
    RASTER_FILE_GLOB,
    RASTER_SUFFIXES,
    VECTOR_SUFFIXES,
    build_binary_mask_from_prepared,
    prepare_raster_image,
)
from autopaint.updater import download_update, fetch_latest_update, schedule_apply_update
from autopaint.types import Rect

UPDATE_CHECK_DELAY_MS = 1500
LIVE_PREVIEW_DEBOUNCE_MS = 250
SIDEBAR_WIDTH = 360
PREVIEW_MIN_SIZE = 300
PREVIEW_MAX_SIZE = 520
ACCENT_COLOR = "#4ea1ff"
SUCCESS_COLOR = "#22c55e"


class AutoPaintGui(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"JoensAutoDraw v{__version__}")
        self.geometry("1280x820")
        self.minsize(1100, 720)

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
        self.auto_exif_rotate = ctk.BooleanVar(value=True)
        self.use_alpha_mask = ctk.BooleanVar(value=True)
        self.auto_scale_epsilon = ctk.BooleanVar(value=True)
        self.clahe_clip = ctk.DoubleVar(value=0.0)
        self.trace_mode = ctk.StringVar(value="threshold")
        self.canny_low = ctk.IntVar(value=40)
        self.canny_high = ctk.IntVar(value=120)

        self._busy = False
        self._last_plan = None
        self._selected_rect: Rect | None = None
        self._preview_images: list[ctk.CTkImage] = []
        self._preview_canvas_size = PREVIEW_MIN_SIZE
        self._slider_value_labels: dict[str, ctk.CTkLabel] = {}
        self._pipeline_labels: dict[str, ctk.CTkLabel] = {}
        self._toolpath_stats_label: ctk.CTkLabel | None = None
        self._live_preview_after_id: str | None = None
        self._live_plan_generation = 0
        self._build_layout()
        self._bind_live_preview_traces()
        self._update_source_hint()
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
                            self.after(0, lambda: self._apply_update_and_restart(downloaded))
                        else:
                            self._append_log("Update downloaded but not installed. Restart update when ready.")

                    self.after(0, offer_restart)

                threading.Thread(target=download_worker, daemon=True).start()

            self.after(0, prompt)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_and_restart(self, downloaded: Path) -> None:
        self._append_log("Applying update and restarting...")
        try:
            self.quit()
            self.destroy()
        except Exception:
            pass
        schedule_apply_update(downloaded, pid=os.getpid())

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=SIDEBAR_WIDTH)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkScrollableFrame(self, width=SIDEBAR_WIDTH, corner_radius=0, fg_color="transparent")
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=16)
        sidebar.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkLabel(
            sidebar,
            text="JoensAutoDraw",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(
            sidebar,
            text="1. Source  →  2. Tune  →  3. Area  →  4. Draw",
            font=ctk.CTkFont(size=12),
            text_color="#9ca3af",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        self._add_path_controls(sidebar)
        tabs = ctk.CTkTabview(sidebar, corner_radius=10)
        tabs.grid(row=3, column=0, sticky="ew", pady=(8, 6))
        raster_tab = tabs.add("Mask")
        scan_tab = tabs.add("Scan")
        path_tab = tabs.add("Path")
        raster_tab.grid_columnconfigure(1, weight=1)
        scan_tab.grid_columnconfigure(1, weight=1)
        path_tab.grid_columnconfigure(1, weight=1)
        self._add_processing_controls(raster_tab)
        self._add_scan_controls(scan_tab)
        self._add_vector_controls(path_tab)
        self._source_hint_label = ctk.CTkLabel(
            sidebar,
            text="",
            justify="left",
            anchor="w",
            wraplength=SIDEBAR_WIDTH - 24,
            text_color="#9ca3af",
            font=ctk.CTkFont(size=11),
        )
        self._source_hint_label.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self._add_draw_controls(sidebar)

        main = ctk.CTkFrame(self, corner_radius=12)
        main.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=16)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=0)

        preview_shell = ctk.CTkFrame(main, corner_radius=12)
        preview_shell.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 8))
        preview_shell.grid_columnconfigure((0, 1), weight=1, uniform="preview")
        preview_shell.grid_rowconfigure(1, weight=1)

        header_row = ctk.CTkFrame(preview_shell, fg_color="transparent")
        header_row.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 8))
        header_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header_row,
            text="Live preview",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header_row,
            text="Inspect mask and toolpath before drawing",
            font=ctk.CTkFont(size=12),
            text_color="#9ca3af",
        ).grid(row=1, column=0, sticky="w")

        self.status_label = ctk.CTkLabel(
            header_row,
            text="Ready",
            anchor="e",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.status_label.grid(row=0, column=1, rowspan=2, sticky="e")

        mask_card = ctk.CTkFrame(preview_shell, corner_radius=10)
        mask_card.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))
        mask_card.grid_rowconfigure(1, weight=1)
        mask_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(mask_card, text="Mask / edges", anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4)
        )
        self.mask_preview = ctk.CTkLabel(
            mask_card,
            text="Load an image to preview",
            fg_color=("#1a1a1a", "#111111"),
            corner_radius=8,
        )
        self.mask_preview.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        path_card = ctk.CTkFrame(preview_shell, corner_radius=10)
        path_card.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))
        path_card.grid_rowconfigure(1, weight=1)
        path_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(path_card, text="Toolpath", anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4)
        )
        self.plan_preview = ctk.CTkLabel(
            path_card,
            text="Toolpath appears after tuning",
            fg_color=("#1a1a1a", "#111111"),
            corner_radius=8,
        )
        self.plan_preview.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        self._toolpath_stats_label = ctk.CTkLabel(
            preview_shell,
            text="Toolpath stats: waiting for source",
            anchor="w",
            justify="left",
            font=ctk.CTkFont(size=12),
            text_color="#cbd5e1",
        )
        self._toolpath_stats_label.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12)
        )
        preview_shell.bind("<Configure>", self._on_preview_shell_resize)

        action_shell = ctk.CTkFrame(main, corner_radius=12)
        action_shell.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        action_shell.grid_columnconfigure(0, weight=1)

        workflow_row = ctk.CTkFrame(action_shell, fg_color="transparent")
        workflow_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        workflow_row.grid_columnconfigure(tuple(range(4)), weight=1)
        for idx, (key, text) in enumerate(
            [
                ("import", "Import"),
                ("plan", "Tune"),
                ("area", "Area"),
                ("draw", "Draw"),
            ]
        ):
            frame = ctk.CTkFrame(workflow_row, corner_radius=8)
            frame.grid(row=0, column=idx, sticky="ew", padx=4)
            label = ctk.CTkLabel(frame, text=f"{text}\nwaiting", anchor="center", justify="center")
            label.pack(fill="both", expand=True, padx=8, pady=8)
            self._pipeline_labels[key] = label

        button_row = ctk.CTkFrame(action_shell, fg_color="transparent")
        button_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        button_row.grid_columnconfigure((0, 1, 2), weight=1)

        self.area_button = ctk.CTkButton(
            button_row,
            text="Select draw area",
            command=self.on_select_area,
            fg_color="#334155",
            hover_color="#475569",
        )
        self.area_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.plan_button = ctk.CTkButton(
            button_row,
            text="Refresh plan",
            command=self.on_build_plan,
            fg_color="#334155",
            hover_color="#475569",
        )
        self.plan_button.grid(row=0, column=1, sticky="ew", padx=6)

        self.draw_button = ctk.CTkButton(
            button_row,
            text="Draw now",
            command=self.on_draw_now,
            height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=SUCCESS_COLOR,
            hover_color="#16a34a",
        )
        self.draw_button.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        self.log_box = ctk.CTkTextbox(action_shell, height=110, corner_radius=10)
        self.log_box.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.log_box.insert("end", "Welcome to JoensAutoDraw.\nKeep Dry run enabled until the preview looks right.\n")
        self.log_box.insert("end", f"Emergency stop: {esc_backend_description()}.\n")
        self.log_box.configure(state="disabled")

        self._set_pipeline_step("import", "waiting")
        self._set_pipeline_step("plan", "waiting")
        self._set_pipeline_step("area", "waiting")
        self._set_pipeline_step("draw", "waiting")

    def _on_preview_shell_resize(self, event) -> None:
        if event.width < 100:
            return
        per_panel = max(PREVIEW_MIN_SIZE, min(PREVIEW_MAX_SIZE, (event.width - 48) // 2))
        if abs(per_panel - self._preview_canvas_size) < 24:
            return
        self._preview_canvas_size = per_panel
        if self._last_plan is not None:
            self._update_previews(self._last_plan, mode=self.mode.get())

    def _pipeline_step_title(self, step_key: str) -> str:
        return {
            "import": "Import",
            "plan": "Tune",
            "area": "Area",
            "draw": "Draw",
        }.get(step_key, step_key)

    def _set_pipeline_step(self, step_key: str, state: str) -> None:
        def update() -> None:
            label = self._pipeline_labels.get(step_key)
            if label is None:
                return
            title = self._pipeline_step_title(step_key)
            text = f"{title}\n{state}"
            color_map = {
                "waiting": "#9ca3af",
                "ready": "#fbbf24",
                "done": "#22c55e",
                "running": ACCENT_COLOR,
                "error": "#f87171",
            }
            label.configure(text=text, text_color=color_map.get(state, "#e5e7eb"))

        self.after(0, update)

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

        def update() -> None:
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
            scale_note = ""
            if self._selected_rect is not None and plan.source_width > 0:
                from autopaint.drawer import _build_fit_transform

                fit = _build_fit_transform(source_size, target_rect)
                scale_note = f", screen={fit.scale:.2f}px/src"
            self._toolpath_stats_label.configure(
                text=(
                    "Toolpath stats: "
                    f"commands={len(commands)}, strokes={stats.stroke_count}, "
                    f"draw_pixels={stats.draw_pixel_events}, moves={stats.move_events}, "
                    f"est. time={stats.estimated_seconds:.1f}s{scale_note}"
                )
            )

        self.after(0, update)

    def _add_path_controls(self, parent: ctk.CTkScrollableFrame) -> None:
        row = 0
        ctk.CTkLabel(parent, text="Source file", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=row, column=0, sticky="w", pady=(4, 8)
        )
        row += 1

        path_row = ctk.CTkFrame(parent, fg_color="transparent")
        path_row.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        path_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(path_row, textvariable=self.image_path).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(path_row, text="Browse", width=90, command=self.on_browse).grid(row=0, column=1)

    def _add_processing_controls(self, parent: ctk.CTkScrollableFrame) -> None:
        row = 0

        ctk.CTkLabel(parent, text="Trace mode").grid(row=row, column=0, sticky="w", pady=4)
        ctk.CTkSegmentedButton(
            parent,
            values=["threshold", "sketch"],
            variable=self.trace_mode,
        ).grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        row += 1

        self._add_slider(parent, row, "Threshold", self.threshold, 0, 255)
        row += 1
        self._add_slider(parent, row, "Canny low", self.canny_low, 1, 255)
        row += 1
        self._add_slider(parent, row, "Canny high", self.canny_high, 2, 255)
        row += 1
        self._add_slider(parent, row, "Blur (odd)", self.blur, 1, 21)
        row += 1
        self._add_slider(parent, row, "Contrast (CLAHE)", self.clahe_clip, 0.0, 8.0)
        row += 1
        self._add_slider(parent, row, "Morph close", self.morph_close, 0, 15)
        row += 1
        self._add_slider(parent, row, "Morph open", self.morph_open, 0, 15)
        row += 1

        ctk.CTkCheckBox(parent, text="Auto threshold (Otsu)", variable=self.use_otsu).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=6
        )
        row += 1
        ctk.CTkCheckBox(parent, text="Invert threshold", variable=self.invert).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=6
        )
        row += 1
        ctk.CTkCheckBox(parent, text="Auto EXIF rotation", variable=self.auto_exif_rotate).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=6
        )
        row += 1
        ctk.CTkCheckBox(parent, text="Use alpha as mask (PNG/logos)", variable=self.use_alpha_mask).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=6
        )
        row += 1
        ctk.CTkLabel(
            parent,
            text="Mask decides what pixels are drawable. Scan and Path use this same mask differently.",
            justify="left",
            anchor="w",
            wraplength=SIDEBAR_WIDTH - 40,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 4))

    def _add_scan_controls(self, parent) -> None:
        row = 0
        ctk.CTkLabel(
            parent,
            text="Scanline fill",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 8))
        row += 1
        self._add_slider(parent, row, "Row step", self.step, 1, 8)
        row += 1
        self._add_slider(parent, row, "Bridge gap", self.line_gap, 0, 8)
        row += 1
        ctk.CTkLabel(
            parent,
            text=(
                "Segments mode draws one horizontal stroke per masked run.\n"
                "Lower Row step fills more densely; Bridge gap joins small holes."
            ),
            justify="left",
            anchor="w",
            wraplength=SIDEBAR_WIDTH - 40,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 4))

    def _add_vector_controls(self, parent) -> None:
        row = 0
        ctk.CTkLabel(
            parent,
            text="Contour paths",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 8))
        row += 1
        self._add_slider(parent, row, "Contour epsilon", self.contour_epsilon, 0.5, 6.0)
        row += 1
        self._add_slider(parent, row, "Min contour area", self.min_contour_area, 0, 500)
        row += 1

        ctk.CTkLabel(parent, text="Contour scope").grid(row=row, column=0, sticky="w", pady=4)
        ctk.CTkSegmentedButton(
            parent,
            values=["external", "all", "largest"],
            variable=self.contour_scope,
        ).grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        row += 1
        ctk.CTkCheckBox(
            parent,
            text="Auto-scale contour epsilon to image size",
            variable=self.auto_scale_epsilon,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        row += 1
        self._add_slider(parent, row, "Point spacing (px)", self.vector_step, 0.5, 6.0)
        row += 1
        self._add_slider(parent, row, "Min path points", self.vector_min_points, 2, 20)
        row += 1
        self._add_slider(parent, row, "SVG jump split (px)", self.vector_jump_threshold, 2.0, 80.0)
        row += 1
        ctk.CTkLabel(
            parent,
            text=(
                "Contour mode draws outlines from the current mask, SVG, or G-code.\n"
                "Lower Point spacing creates denser mouse paths."
            ),
            justify="left",
            anchor="w",
            wraplength=SIDEBAR_WIDTH - 40,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 4))

    def _add_draw_controls(self, parent: ctk.CTkScrollableFrame) -> None:
        row = 5
        ctk.CTkLabel(parent, text="Draw", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(12, 8)
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
                (
                    "Supported files",
                    f"{RASTER_FILE_GLOB} *.svg *.gcode *.nc *.tap",
                ),
                ("Vector paths", "*.svg *.gcode *.nc *.tap"),
                ("Image files", RASTER_FILE_GLOB),
                ("All files", "*.*"),
            ],
        )
        if file_path:
            self.image_path.set(file_path)
            self._set_pipeline_step("import", "done")
            self._set_pipeline_step("plan", "ready")
            self._set_pipeline_step("draw", "waiting")
            self._schedule_live_preview(replan=True)
            self._update_source_hint()

    def _source_kind_label(self) -> str:
        path = self.image_path.get().strip()
        if not path:
            return "No source loaded."
        suffix = Path(path).suffix.lower()
        if suffix in RASTER_SUFFIXES:
            return "Raster image: import uses EXIF/alpha/CLAHE normalization before tracing."
        if suffix in VECTOR_SUFFIXES:
            return f"Vector file ({suffix}): Path tuning controls import sampling and path splits."
        return "Unknown source type."

    def _update_source_hint(self) -> None:
        if hasattr(self, "_source_hint_label"):
            self._source_hint_label.configure(text=self._source_kind_label())

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
            self.auto_exif_rotate,
            self.use_alpha_mask,
            self.auto_scale_epsilon,
            self.clahe_clip,
            self.trace_mode,
            self.canny_low,
            self.canny_high,
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
                prepared = prepare_raster_image(
                    processing.image_path,
                    auto_exif_rotate=processing.auto_exif_rotate,
                )
                mask = build_binary_mask_from_prepared(prepared, processing)
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
        try:
            processing = self._build_processing()
            contour_epsilon = float(self.contour_epsilon.get())
            mode = self.mode.get()
        except Exception:
            return

        def worker() -> None:
            try:
                plan = create_plan(
                    processing=processing,
                    contour_epsilon=contour_epsilon,
                )
            except Exception:
                return
            if generation != self._live_plan_generation:
                return
            self._last_plan = plan
            self._update_previews(plan, mode=mode)
            self._update_toolpath_stats(plan)
            for warning in plan.import_warnings:
                self._append_log(f"Import: {warning}")

            def finish() -> None:
                self._set_pipeline_step("plan", "done")
                if self._selected_rect is not None:
                    self._set_pipeline_step("draw", "ready")
                self.status_label.configure(text="Live preview updated")

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _array_to_preview_image(self, array: np.ndarray, canvas_size: int | None = None) -> ctk.CTkImage:
        size = canvas_size or self._preview_canvas_size
        size = max(PREVIEW_MIN_SIZE, min(PREVIEW_MAX_SIZE, size))
        rgb = cv2.cvtColor(array, cv2.COLOR_GRAY2RGB)
        src_h, src_w = rgb.shape[:2]
        scale = min(size / max(1, src_w), size / max(1, src_h))
        dst_w = max(1, int(round(src_w * scale)))
        dst_h = max(1, int(round(src_h * scale)))
        resized = cv2.resize(rgb, (dst_w, dst_h), interpolation=cv2.INTER_NEAREST)
        canvas = np.zeros((size, size, 3), dtype=np.uint8)
        x = (size - dst_w) // 2
        y = (size - dst_h) // 2
        canvas[y : y + dst_h, x : x + dst_w] = resized
        pil = Image.fromarray(canvas)
        return ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))

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
        trace = self.trace_mode.get()
        if trace not in {"threshold", "sketch"}:
            trace = "threshold"
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
            auto_exif_rotate=self.auto_exif_rotate.get(),
            use_alpha_mask=self.use_alpha_mask.get(),
            clahe_clip_limit=max(0.0, float(self.clahe_clip.get())),
            auto_scale_epsilon=self.auto_scale_epsilon.get(),
            trace_mode=trace,
            canny_low=max(1, int(round(self.canny_low.get()))),
            canny_high=max(2, int(round(self.canny_high.get()))),
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
                for warning in plan.import_warnings:
                    self._append_log(f"Import: {warning}")
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
                for warning in plan.import_warnings:
                    self._append_log(f"Import: {warning}")
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
        def to_ctk_image(array):
            return self._array_to_preview_image(array)

        def update_ui() -> None:
            preview_thickness = max(1, int(round(self.preview_stroke_px.get())))
            draw_conf = self._build_draw()
            source_size = RasterSize(width=plan.source_width, height=plan.source_height)
            target_rect = self._reference_rect(plan)
            commands = build_execution_commands(
                plan=plan,
                draw_mode=mode,
                draw_config=draw_conf,
                target_rect=target_rect,
                source_size=source_size,
            )
            planned = render_tool_command_preview_on_mask(
                plan.mask,
                commands,
                source_size=source_size,
                target_rect=target_rect,
                draw_config=draw_conf,
                thickness=preview_thickness,
                show_travel=self.show_travel_preview.get(),
            )
            mask_for_preview = plan.mask
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
