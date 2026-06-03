from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from autopaint import __version__

GITHUB_REPO = "joenb33/JoensAutoDraw"
EXE_NAME = "JoensAutoDraw.exe"
USER_AGENT = f"JoensAutoDraw/{__version__}"


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    release_url: str


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_exe_path() -> Path | None:
    if not is_frozen_app():
        return None
    return Path(sys.executable).resolve()


def parse_version(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lstrip("v")
    parts = cleaned.split(".")
    return tuple(int(part) for part in parts)


def is_newer_version(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def fetch_latest_update(timeout: float = 8.0) -> UpdateInfo | None:
    if os.environ.get("JOENSAUTODRAW_SKIP_UPDATE") == "1":
        return None
    if not is_frozen_app():
        return None

    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    tag = str(payload.get("tag_name", "")).lstrip("v")
    if not tag or not is_newer_version(tag, __version__):
        return None

    download_url = None
    for asset in payload.get("assets") or []:
        if asset.get("name") == EXE_NAME:
            download_url = asset.get("browser_download_url")
            break
    if not download_url:
        return None

    return UpdateInfo(
        version=tag,
        download_url=str(download_url),
        release_url=str(payload.get("html_url", "")),
    )


def download_update(info: UpdateInfo, timeout: float = 180.0) -> Path:
    destination = Path(tempfile.gettempdir()) / f"{EXE_NAME}.{info.version}.new"
    request = urllib.request.Request(info.download_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as handle:
        handle.write(response.read())
    return destination


def schedule_apply_update(new_exe: Path) -> None:
    current = current_exe_path()
    if current is None:
        raise RuntimeError("Updates can only be applied to the packaged executable.")

    helper = Path(tempfile.gettempdir()) / "JoensAutoDraw-update.bat"
    helper.write_text(
        "\n".join(
            [
                "@echo off",
                "ping 127.0.0.1 -n 2 >nul",
                f'move /Y "{new_exe}" "{current}"',
                f'start "" "{current}"',
                "del \"%~f0\"",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd", "/c", str(helper)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    sys.exit(0)
