# Changelog

All notable changes to JoensAutoDraw are documented here. The project follows
patch-level releases that are bumped automatically on every push to `main`.

## [0.1.19] - 2026-06-05

### Fixed
- Drawing now produces continuous strokes instead of isolated dots. Previously the
  cursor was only teleported with `SetCursorPos` plus a zero-delta movement pulse,
  which many surfaces (web canvases, games, several Paint variants) do not treat as
  real mouse movement. They therefore registered only the button-down and button-up
  points, yielding dots unless the user nudged the physical mouse during drawing.
- Movement is now injected as genuine, hardware-like input via `SendInput`
  (`MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK`), so the tool
  behaves like a human moving a real mouse: press left button, move, release. This
  works across any drawing surface without sending commands to the canvas itself.
  The exact target pixel is still pinned with `SetCursorPos` after each injected move.
