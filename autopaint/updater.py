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


def _update_staging_path(version: str) -> Path:
    current = current_exe_path()
    filename = f"{EXE_NAME}.{version}.new"
    if current is not None:
        return current.parent / filename
    return Path(tempfile.gettempdir()) / filename


def download_update(info: UpdateInfo, timeout: float = 180.0) -> Path:
    destination = _update_staging_path(info.version)
    request = urllib.request.Request(info.download_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as handle:
        handle.write(response.read())
    return destination


def build_update_helper_script(*, pid: int, new_exe: Path, target: Path) -> str:
    """PowerShell script: wait for exit, copy replace, brief pause, relaunch."""
    new_path = str(new_exe.resolve()).replace("'", "''")
    target_path = str(target.resolve()).replace("'", "''")
    target_dir = str(target.parent.resolve()).replace("'", "''")
    return "\n".join(
        [
            "$ErrorActionPreference = 'SilentlyContinue'",
            f"$PidToWait = {int(pid)}",
            f"$NewExe = '{new_path}'",
            f"$TargetExe = '{target_path}'",
            f"$TargetDir = '{target_dir}'",
            "$Deadline = (Get-Date).AddSeconds(120)",
            "while ((Get-Process -Id $PidToWait -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $Deadline)) {",
            "  Start-Sleep -Milliseconds 500",
            "}",
            "Start-Sleep -Seconds 5",
            "$ProcDeadline = (Get-Date).AddSeconds(60)",
            "while ((Get-Process -Name 'JoensAutoDraw' -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $ProcDeadline)) {",
            "  Start-Sleep -Milliseconds 500",
            "}",
            "Start-Sleep -Seconds 4",
            "$Copied = $false",
            "for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {",
            "  try {",
            "    Copy-Item -LiteralPath $NewExe -Destination $TargetExe -Force",
            "    if (Test-Path -LiteralPath $TargetExe) { $Copied = $true; break }",
            "  } catch { }",
            "  Start-Sleep -Seconds 1",
            "}",
            "Remove-Item -LiteralPath $NewExe -Force -ErrorAction SilentlyContinue",
            "if (-not $Copied) { exit 1 }",
            "Start-Sleep -Seconds 5",
            "try {",
            "  Add-Type -AssemblyName PresentationFramework",
            "  [System.Windows.MessageBox]::Show(",
            "    'Update installed successfully. JoensAutoDraw will start now.',",
            "    'JoensAutoDraw Update',",
            "    'OK',",
            "    'Information'",
            "  ) | Out-Null",
            "} catch { Start-Sleep -Seconds 2 }",
            "Start-Process -LiteralPath $TargetExe -WorkingDirectory $TargetDir",
            "Remove-Item -LiteralPath $PSCommandPath -Force",
        ]
    )


def schedule_apply_update(new_exe: Path, *, pid: int | None = None) -> None:
    current = current_exe_path()
    if current is None:
        raise RuntimeError("Updates can only be applied to the packaged executable.")

    helper = Path(tempfile.gettempdir()) / "JoensAutoDraw-update.ps1"
    helper.write_text(
        build_update_helper_script(
            pid=pid if pid is not None else os.getpid(),
            new_exe=new_exe.resolve(),
            target=current,
        ),
        encoding="utf-8",
    )
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(helper),
        ],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    os._exit(0)
