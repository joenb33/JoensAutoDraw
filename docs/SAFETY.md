# Safety Model

AutoPaint controls the real mouse cursor. The design assumes mistakes are likely, so multiple **independent** stop layers exist.

---

## Safety layers

### 1. Manual emergency stop

- **`ESC`** is polled during drawing **and** during the countdown.
- On Windows, ESC is checked through:
  - Win32 `GetAsyncKeyState` (no admin required)
  - The `keyboard` package when available
- Triggers `EmergencyStop` and exits immediately.

### 2. Preflight validation

Before any mouse button is pressed, `preflight_draw()` verifies:

| Check | Failure |
|-------|---------|
| Draw area ≥ 5×5 px | `DrawValidationError` |
| Plan contains drawable paths | `DrawValidationError` |
| Command list is non-empty | `DrawValidationError` |
| Mapped stroke pixels stay inside the selected rectangle | `DrawValidationError` |
| Draw area near screen top-left | Warning printed (fail-safe conflict) |

This prevents many mid-draw surprises with clear error messages in the GUI log or CLI output.

### 3. PyAutoGUI corner fail-safe

- `pyautogui.FAILSAFE = True` for the duration of drawing.
- Moving the cursor to the **absolute top-left corner of the screen** aborts execution.

**Caution:** If your draw area overlaps `(0,0)`, normal drawing moves may accidentally trigger this. The preflight warning flags risky selections.

### 4. Hard bounds checks

During execution, every screen coordinate is validated against the user-selected rectangle. Any violation raises `BoundsViolation` and stops immediately.

### 5. Human confirmation delay

- Configurable **countdown** (default 3 seconds) before drawing starts.
- ESC is honored during countdown via interruptible sleep.

The GUI also shows a confirmation dialog before **Draw Now**.

### 6. Dry-run mode

- **Default in the GUI** (`Dry run` checked on startup).
- Logs planned actions without moving the mouse or pressing buttons.
- CLI: pass `--dry-run`. Without `--target-rect`, CLI dry-run uses a source-sized
  virtual target and never asks for mouse-position capture.

Always dry-run after changing speed, mode, or source file.

### 7. Mouse button cleanup

If drawing fails or is interrupted, AutoPaint attempts to release the left mouse button via both PyAutoGUI and Win32 `mouseUp` so the cursor is not left in a “stuck drawing” state.

---

## Operating guidelines

1. **Dry-run first** after any setting change.
2. **Preview** raster tuning with `--preview` or the GUI planned preview.
3. **Keep the target window visible** — do not scroll, zoom, or switch apps during drawing.
4. **Start with moderate speed** (GUI ~40–70) until output looks correct.
5. **Select a generous draw area** that fully contains the canvas.
6. **Avoid the screen top-left corner** when selecting area.
7. **Use ESC** if anything unexpected happens — do not grab the mouse manually unless necessary.

---

## Known risks

| Risk | Mitigation |
|------|------------|
| Focus stolen by another window | Pause/stop; do not draw until focus returns *(automatic detection planned)* |
| Target UI moves or resizes | Stop and reselect area |
| Popup dialogs intercept clicks | Close popups before drawing |
| Website anti-bot measures | Use only where automation is permitted |
| `keyboard` package blocked without admin | Win32 ESC fallback still active on Windows |
| Very high speed / sparse strokes | Lower speed; check `draw_pixels` in stats |

---

## CLI exit codes

| Code | Meaning |
|------|---------|
| `0` | Completed successfully |
| `2` | Emergency stop |
| `3` | Bounds violation |
| `4` | Preflight validation failed |

---

## Future safety enhancements

- Pause or stop when the active foreground window changes
- Optional runtime watchdog (max duration / max stroke count)
- On-screen verification sampling against the target canvas
- Dedicated background stop thread with low-level keyboard hook

---

## Ethics and responsibility

AutoPaint is a general-purpose automation tool. You are responsible for:

- Using it only where allowed (app terms, school/work policies, websites)
- Not disrupting shared systems or other users
- Testing in a safe environment before production use

When in doubt, use **dry-run** and ask whether automation is permitted.
