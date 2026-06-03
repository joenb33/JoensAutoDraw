# Usage Guide

This guide walks through AutoPaint’s recommended workflow for both the GUI and CLI.

---

## Before you start

1. Open your **target app** (e.g. MS Paint) and choose a **brush/tool** that draws with the **left mouse button**.
2. Set brush size and color in the target app — AutoPaint does not change colors yet.
3. Make sure the canvas is **fully visible** and will not scroll or resize during drawing.
4. Keep **`ESC`** ready as an emergency stop.

---

## GUI workflow

### Step 1 — Import source

Click **Browse** and select a file:

| Type | Extensions |
|------|------------|
| Raster | `.png`, `.jpg`, `.jpeg`, `.bmp`, `.webp` |
| Vector | `.svg` |
| CNC / G-code | `.gcode`, `.nc`, `.tap` |

The workflow panel marks **Import source** as done.

### Step 2 — Tune parameters

Use the tabs **Raster** and **Vector/CNC** depending on your file type.

#### Raster tab

| Control | Effect |
|---------|--------|
| **Threshold** | Higher = less of the image becomes “ink” |
| **Blur** | Smooths noise before thresholding (odd values) |
| **Step** | Row spacing for segment mode (higher = fewer lines) |
| **Line gap** | Bridges small white gaps inside a scan line |
| **Contour epsilon** | Simplifies contour paths (higher = fewer vertices) |
| **Invert threshold** | Flip which tones become strokes |

#### Vector/CNC tab

| Control | Effect |
|---------|--------|
| **Vector step** | Distance between sampled points along paths |
| **Min path points** | Drops very short paths |
| **Jump split** | Splits paths when the pen jumps farther than this (pen-up detection) |

#### Drawing section

| Control | Effect |
|---------|--------|
| **Mode** | `contour` for outlines, `segments` for horizontal fill lines |
| **Speed** | Overall draw speed (lower = denser, more reliable strokes) |
| **Step pause / Pen settle** | Fine-tune timing for stubborn apps |
| **Dry run** | Plan and log without moving the mouse |
| **Optimize contour travel** | Reorder paths to reduce pen-up travel |
| **Compatibility mode** | Recommended for MS Paint |
| **Show travel preview** | Overlay pen-up moves on the source preview |

Click **Build Plan**. Review:

- **Source / Travel Preview** — input mask with optional travel lines  
- **Planned Preview** — what will be drawn  
- **Toolpath stats** — command count, strokes, `draw_pixels`, estimated time  

### Step 3 — Select draw area

Click **Select Area**. The window minimizes and a fullscreen overlay appears.

- **Click and drag** over the region where drawing should happen.  
- The rectangle should fully contain the intended canvas area.  
- Avoid placing the top-left corner of the selection at the **screen’s (0,0)** — that overlaps PyAutoGUI’s fail-safe zone.  
- Press **ESC** on the overlay to cancel.

### Step 4 — Draw

1. Enable **Dry run**, click **Draw Now**, and read the log.  
2. Disable **Dry run** when ready.  
3. Confirm the safety dialog.  
4. Wait for the countdown — press **ESC** during countdown to abort.  
5. Do not move or cover the target window while drawing.

---

## CLI workflow

### Basic raster draw

```powershell
python -m autopaint.main --image ".\logo.png" --mode segments --dry-run
python -m autopaint.main --image ".\logo.png" --mode segments
```

### Contour draw with preview

```powershell
python -m autopaint.main --image ".\logo.png" --mode contour --preview --contour-epsilon 1.2
```

### SVG import

```powershell
python -m autopaint.main --image ".\art.svg" --vector-step 0.5 --mode contour
```

### Area selection (CLI)

The CLI prompts you to:

1. Place the mouse at the **top-left** of the draw area → Enter  
2. Place the mouse at the **bottom-right** → Enter  

Use the same area you would select in the GUI.

---

## Choosing a mode

| Source | Recommended mode | Why |
|--------|------------------|-----|
| Filled logo / photo | **Segments** | Reliable horizontal fill in MS Paint |
| Line art / outline | **Contour** | Cleaner edges, less overdraw |
| SVG / G-code | **Contour** | Native polyline execution |

---

## Interpreting toolpath stats

Example:

```text
Toolpath stats: commands=1325, strokes=81, draw_pixels=8500, moves=81, est. time=12.3s
```

| Field | Meaning |
|-------|---------|
| `commands` | Raw tool commands (move, down, draw, up) |
| `strokes` | Number of pen-down strokes |
| `draw_pixels` | Estimated drag steps after path densification |
| `moves` | Pen-up repositioning moves |
| `est. time` | Rough duration including countdown |

If `draw_pixels` is very low compared to visual complexity, lower **Speed** or simplify less (reduce contour epsilon).

---

## Exit codes (CLI)

| Code | Meaning |
|------|---------|
| `0` | Success |
| `2` | Emergency stop (`ESC` or fail-safe) |
| `3` | Bounds violation |
| `4` | Preflight validation failed |

---

## See also

- [Architecture](ARCHITECTURE.md) — how modules connect  
- [Safety](SAFETY.md) — stop mechanisms and guidelines  
- [README](../README.md) — install and overview  
