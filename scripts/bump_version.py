from __future__ import annotations

import argparse

from autopaint.versioning import bump_patch_version, read_version


def main() -> int:
    parser = argparse.ArgumentParser(description="Read or bump JoensAutoDraw patch version.")
    parser.add_argument("--write", action="store_true", help="Bump patch version in autopaint/__init__.py")
    args = parser.parse_args()

    if args.write:
        print(bump_patch_version())
    else:
        print(read_version())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
