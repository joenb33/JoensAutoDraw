# AutoPaint

**AutoPaint** turns images and vector files into mouse-drawn strokes inside a screen area you choose. Point it at MS Paint, a browser canvas, or any app that draws with the left mouse button — AutoPaint plans the paths and executes them for you.

Built as a **safety-first** desktop tool for Windows: dry-run by default in the GUI, preflight checks before drawing, multiple emergency stops, and strict bounds validation.

> **Use responsibly.** Only automate targets where it is allowed. You are responsible for complying with app terms and local laws.

---

## Features

| Area | What you get |
|------|----------------|
| **Inputs** | PNG, JPG, BMP, WebP, SVG, G-code (`.gcode`, `.nc`, `.tap`) |
| **Raster pipeline** | Grayscale → blur → threshold → segments or OpenCV contours |
| **Vector pipeline** | SVG path sampling, G-code `G0`/`G1` import with pen-up detection |
| **Drawing modes** | **Contour** (outline polylines) or **Segments** (horizontal scan lines) |
| **GUI** | Dark control panel, live previews, drag-to-select draw area, workflow steps |
| **Safety** | ESC stop, PyAutoGUI corner fail-safe, preflight validation, dry-run, countdown |
| **Quality** | Aspect-preserving fit, continuous pixel walking (no dot-collapse), travel optimization |

---

## Requirements

- **Windows 10/11**
- **Python 3.10+**
- A visible drawing target (e.g. MS Paint, browser canvas)

---

## Quick start

### 1. Clone and install

```powershell
git clone https://github.com/joenb33/JoensAutoDraw.git
cd JoensAutoDraw

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Launch the GUI (recommended)

```powershell
python -m autopaint.gui
```

### 3. Typical workflow

1. **Browse** — pick a source file  
2. **Build Plan** — inspect mask and planned strokes in the preview panels  
3. **Select Area** — drag a rectangle over the target canvas (avoid the screen’s top-left corner if possible)  
4. Leave **Dry run** checked for the first attempt  
5. **Draw Now** — confirm the safety dialog  
6. Uncheck **Dry run** and draw for real when satisfied  

**Emergency stop:** press `ESC`, or move the mouse to the **top-left corner** of the screen.

---

## CLI usage

```powershell
python -m autopaint.main --image ".\examples\cat.png" --preview --dry-run
```

Interactive area selection (terminal prompts for top-left and bottom-right), then countdown and draw.

### Common flags

| Flag | Default | Description |
|------|---------|-------------|
| `--image` | *(required)* | Source file path |
| `--mode` | `contour` | `contour` or `segments` |
| `--threshold` | `140` | Binarization threshold (0–255) |
| `--blur` | `5` | Gaussian blur kernel (odd integer) |
| `--step` | `2` | Row sampling step (segments mode) |
| `--contour-epsilon` | `1.2` | Contour simplification (higher = fewer points) |
| `--speed` | `0.002` | Mouse move duration (seconds) |
| `--countdown` | `3` | Seconds before drawing starts |
| `--preview` | off | Show OpenCV preview windows |
| `--dry-run` | off | Print actions without moving the mouse |
| `--invert` | off | Invert threshold logic |

### Vector / CNC flags

| Flag | Default | Description |
|------|---------|-------------|
| `--vector-step` | `1.0` | Sample spacing for SVG/G-code (px) |
| `--vector-min-points` | `2` | Drop paths shorter than this |
| `--vector-jump-threshold` | `8.0` | Split SVG paths at large jumps (px) |

### Compatibility / performance

| Flag | Description |
|------|-------------|
| `--step-pause-ms` | Pause between drag steps (default `1.2`) |
| `--stroke-settle-ms` | Pause after mouse-down (default `3.0`) |
| `--no-optimize-contour-travel` | Disable polyline reordering for shorter pen-up travel |
| `--fast-input-backend` | Win32-only input (faster, less compatible with some apps) |

Run `python -m autopaint.main --help` for the full list.

---

## Drawing modes

### Segments (scan lines)

Best for **filled raster art** and bold logos. Converts black pixels into horizontal strokes row by row. Often the most reliable mode in MS Paint.

### Contour (outlines)

Best for **line art, SVG, and CNC paths**. Traces OpenCV contours (raster) or imported polylines (vector). Produces cleaner outlines with less fill overlap.

Vector sources always execute as contours. If you pick **Segments** for a vector file, AutoPaint falls back to contour paths automatically.

---

## GUI tips

- **Compatibility mode** (on by default) uses a hybrid input backend that works well with MS Paint. Turn it off only if you need the fast Win32 backend and have tested your target app.
- **Speed** — start around 40–70. Very high speeds reduce anchor points; if strokes look sparse, lower the speed.
- **Toolpath stats** — check `draw_pixels` before drawing. Very low values often mean sparse output; rebuild the plan after tuning speed or contour epsilon.
- **Travel preview** — grey lines show pen-up moves between contour paths (when travel optimization is enabled).

---

## Project layout

```
AutoPaint/
├── autopaint/
│   ├── main.py           # CLI entry point
│   ├── gui.py            # Desktop GUI
│   ├── pipeline.py       # Plan + execute orchestration
│   ├── planner.py        # Segments & contour planning
│   ├── drawer.py         # Coordinate mapping & mouse execution
│   ├── toolpath.py       # Polyline → tool commands
│   ├── vector_import.py  # SVG & G-code import
│   ├── validation.py     # Preflight checks
│   ├── failsafe.py       # Emergency stop & countdown
│   ├── image_processing.py
│   ├── config.py
│   └── types.py
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SAFETY.md
│   └── USAGE.md
├── tests/
├── examples/
│   └── cat.png           # Sample image for trying the CLI
├── requirements.txt
├── requirements-dev.txt
└── LICENSE
```

---

## Development

Install dev dependencies and run tests:

```powershell
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module design and [docs/SAFETY.md](docs/SAFETY.md) for the safety model.

---

## Troubleshooting

| Symptom | Things to try |
|---------|----------------|
| **Only dots, no lines** | Lower **Speed**; keep **Compatibility mode** on; prefer **Segments** for filled raster art |
| **Missing detail** | Lower `--step`, reduce `--contour-epsilon`, adjust `--threshold` |
| **Too dense / slow** | Raise `--step` or `--contour-epsilon`, increase speed slightly |
| **Nothing in Paint** | Confirm draw area covers the canvas; run dry-run first; check preflight errors in the log |
| **Tool menus pop up** | Draw area may be too close to the screen top-left fail-safe zone; avoid clicking without dragging |
| **OpenCV preview missing** | Ensure a desktop session is active (not headless) |
| **ESC stop unreliable** | Run as administrator if the `keyboard` package cannot hook ESC; Win32 fallback still works on Windows |
| **Web canvas blocked** | Many sites block synthetic input — use only where permitted |

---

## Roadmap

- [ ] Multi-color / pen palette switching  
- [ ] Per-app presets (MS Paint, browser canvas, etc.)  
- [ ] Pause when the active window changes  
- [ ] Optional path simplification profiles (quality vs speed)  

---

## License

MIT — see [LICENSE](LICENSE).

---

## Documentation

- [Usage guide (GUI & CLI)](docs/USAGE.md)  
- [Architecture](docs/ARCHITECTURE.md)  
- [Safety model](docs/SAFETY.md)
