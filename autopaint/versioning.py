from __future__ import annotations

import re
from pathlib import Path

_VERSION_PATTERN = re.compile(r'(__version__\s*=\s*")(\d+\.\d+\.\d+)(")')
_INIT_PATH = Path(__file__).resolve().parent / "__init__.py"


def read_version() -> str:
    match = _VERSION_PATTERN.search(_INIT_PATH.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError("Could not parse __version__ from autopaint/__init__.py")
    return match.group(2)


def bump_patch_version() -> str:
    content = _INIT_PATH.read_text(encoding="utf-8")
    match = _VERSION_PATTERN.search(content)
    if match is None:
        raise ValueError("Could not parse __version__ from autopaint/__init__.py")

    major, minor, patch = (int(part) for part in match.group(2).split("."))
    new_version = f"{major}.{minor}.{patch + 1}"
    updated = _VERSION_PATTERN.sub(rf"\g<1>{new_version}\g<3>", content, count=1)
    _INIT_PATH.write_text(updated, encoding="utf-8")
    return new_version
