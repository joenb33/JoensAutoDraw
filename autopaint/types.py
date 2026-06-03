from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


@dataclass(frozen=True)
class Segment:
    y: int
    x_start: int
    x_end: int


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class Polyline:
    points: tuple[Point, ...]


ToolCommandKind = Literal["move", "down", "draw", "up"]


@dataclass(frozen=True)
class ToolCommand:
    kind: ToolCommandKind
    x: int | None = None
    y: int | None = None

