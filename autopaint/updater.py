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
BACKUP_EXE_NAME = "JoensAutoDraw.exe.bak"
UPDATE_LOG_NAME = "JoensAutoDraw-update.log"
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


def update_log_path(target_exe: Path) -> Path:
    return target_exe.parent / UPDATE_LOG_NAME


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


def _batch_quote(value: str) -> str:
    return value.replace("%", "%%").replace('"', '""')


def build_update_helper_script(*, pid: int, new_exe: Path, target: Path) -> str:
    """Hidden CMD helper: wait for exit, rename-replace exe, relaunch, log to app folder."""
    new_path = _batch_quote(str(new_exe.resolve()))
    target_path = _batch_quote(str(target.resolve()))
    target_dir = _batch_quote(str(target.parent.resolve()))
    backup_path = _batch_quote(str(target.parent / BACKUP_EXE_NAME))
    log_path = _batch_quote(str(update_log_path(target)))
    return "\r\n".join(
        [
            "@echo off",
            "setlocal EnableExtensions EnableDelayedExpansion",
            f'set "NEW={new_path}"',
            f'set "TARGET={target_path}"',
            f'set "TARGET_DIR={target_dir}"',
            f'set "BACKUP={backup_path}"',
            f'set "LOG={log_path}"',
            f"set /a PID={int(pid)}",
            ">>\"%LOG%\" echo.",
            ">>\"%LOG%\" echo [%date% %time%] JoensAutoDraw updater started (pid=%PID%).",
            ">>\"%LOG%\" echo NEW=\"%NEW%\"",
            ">>\"%LOG%\" echo TARGET=\"%TARGET%\"",
            ">>\"%LOG%\" echo [%date% %time%] Waiting for process %PID% to exit...",
            "set /a WAIT=0",
            ":waitpid",
            "rem Do not use find with PID digits — it matches substrings in other PIDs (e.g. 135300).",
            'tasklist /FI "PID eq %PID%" /NH 2>nul | findstr /I /C:"No tasks" >nul',
            "if errorlevel 1 (",
            "  timeout /t 1 /nobreak >nul",
            "  set /a WAIT+=1",
            "  if !WAIT! lss 120 goto waitpid",
            ")",
            ">>\"%LOG%\" echo [%date% %time%] Process wait finished (waited !WAIT!s).",
            "rem Allow PyInstaller one-file temp extraction to finish cleanup",
            "timeout /t 4 /nobreak >nul",
            ">>\"%LOG%\" echo [%date% %time%] Waiting for JoensAutoDraw.exe processes to exit...",
            "set /a IMAGEWAIT=0",
            ":waitimage",
            'tasklist /FI "IMAGENAME eq JoensAutoDraw.exe" /NH 2>nul | findstr /I /C:"No tasks" >nul',
            "if errorlevel 1 (",
            "  timeout /t 1 /nobreak >nul",
            "  set /a IMAGEWAIT+=1",
            "  if !IMAGEWAIT! lss 90 goto waitimage",
            ")",
            ">>\"%LOG%\" echo [%date% %time%] No running JoensAutoDraw.exe processes.",
            "timeout /t 2 /nobreak >nul",
            'if not exist "%NEW%" (',
            '  >>"%LOG%" echo ERROR: Staging file missing: "%NEW%"',
            "  goto :fail",
            ")",
            'for %%A in ("%NEW%") do set NEW_SIZE=%%~zA',
            "if !NEW_SIZE! lss 500000 (",
            '  >>"%LOG%" echo ERROR: Staging file too small: !NEW_SIZE! bytes',
            "  goto :fail",
            ")",
            "set /a RETRY=0",
            ":replacetry",
            'if exist "%BACKUP%" del /F /Q "%BACKUP%" >>"%LOG%" 2>&1',
            'if exist "%TARGET%" (',
            f'  ren "%TARGET%" "{BACKUP_EXE_NAME}" >>"%LOG%" 2>&1',
            ")",
            'move /Y "%NEW%" "%TARGET%" >>"%LOG%" 2>&1',
            "if %ERRORLEVEL% neq 0 (",
            "  set /a RETRY+=1",
            "  if !RETRY! lss 40 (",
            "    timeout /t 1 /nobreak >nul",
            "    goto :replacetry",
            "  )",
            '  >>"%LOG%" echo ERROR: Could not replace executable after !RETRY! attempts.',
            '  if exist "%TARGET%" del /F /Q "%TARGET%" >>"%LOG%" 2>&1',
            '  if exist "%BACKUP%" move /Y "%BACKUP%" "%TARGET%" >>"%LOG%" 2>&1',
            "  goto :fail",
            ")",
            'if exist "%BACKUP%" del /F /Q "%BACKUP%" >>"%LOG%" 2>&1',
            ">>\"%LOG%\" echo [%date% %time%] Replace succeeded.",
            "timeout /t 2 /nobreak >nul",
            'start "" /D "%TARGET_DIR%" "%TARGET%"',
            ">>\"%LOG%\" echo [%date% %time%] Launched updated app.",
            "del \"%~f0\"",
            "exit /b 0",
            ":fail",
            ">>\"%LOG%\" echo [%date% %time%] Update failed. See log above.",
            'mshta "javascript:var s=new ActiveXObject(''WScript.Shell'');s.Popup(''JoensAutoDraw could not apply the update automatically.\\n\\nOpen JoensAutoDraw-update.log in the app folder for details.'',0,''JoensAutoDraw Update'',48);close()"',
            "exit /b 1",
        ]
    )


def _windows_subprocess_kwargs() -> dict[str, object]:
    if sys.platform != "win32":
        return {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    creationflags = (
        create_no_window
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
    )
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": creationflags, "startupinfo": startupinfo}


def schedule_apply_update(new_exe: Path, *, pid: int | None = None) -> None:
    current = current_exe_path()
    if current is None:
        raise RuntimeError("Updates can only be applied to the packaged executable.")

    helper = Path(tempfile.gettempdir()) / "JoensAutoDraw-update.bat"
    helper.write_text(
        build_update_helper_script(
            pid=pid if pid is not None else os.getpid(),
            new_exe=new_exe.resolve(),
            target=current,
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    subprocess.Popen(
        ["cmd.exe", "/c", str(helper)],
        close_fds=True,
        **_windows_subprocess_kwargs(),
    )
    os._exit(0)
