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
_PYINSTALLER_SETTLE_SECONDS = 12
MIN_UPDATE_EXE_SIZE_BYTES = 500_000


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
    partial = Path(f"{destination}.part")
    request = urllib.request.Request(info.download_url, headers={"User-Agent": USER_AGENT})
    if partial.exists():
        partial.unlink()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        _validate_downloaded_exe(partial)
        partial.replace(destination)
        return destination
    except Exception:
        try:
            partial.unlink()
        except OSError:
            pass
        raise


def _validate_downloaded_exe(path: Path) -> None:
    size = path.stat().st_size
    if size < MIN_UPDATE_EXE_SIZE_BYTES:
        raise RuntimeError(f"Downloaded update is too small ({size} bytes).")
    with path.open("rb") as handle:
        magic = handle.read(2)
    if magic != b"MZ":
        raise RuntimeError("Downloaded update does not look like a Windows executable.")


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def build_update_helper_script(*, pid: int, new_exe: Path, target: Path) -> str:
    """Hidden PowerShell helper: wait, replace exe, settle, confirm, relaunch."""
    new_path = _ps_quote(str(new_exe.resolve()))
    target_path = _ps_quote(str(target.resolve()))
    target_dir = _ps_quote(str(target.parent.resolve()))
    backup_path = _ps_quote(str(target.parent / BACKUP_EXE_NAME))
    log_path = _ps_quote(str(update_log_path(target)))
    return "\r\n".join(
        [
            "$ErrorActionPreference = 'Continue'",
            f"$PidToWait = {int(pid)}",
            f"$NewExe = '{new_path}'",
            f"$TargetExe = '{target_path}'",
            f"$TargetDir = '{target_dir}'",
            f"$BackupExe = '{backup_path}'",
            f"$Log = '{log_path}'",
            f"$MinExeSize = {MIN_UPDATE_EXE_SIZE_BYTES}",
            "function Write-UpdateLog([string]$Message) {",
            "  $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message",
            "  Add-Content -LiteralPath $Log -Value $line -Encoding UTF8",
            "}",
            "function Show-UpdatePopup([string]$Text, [int]$Icon) {",
            "  if ($env:JOENSAUTODRAW_UPDATE_SILENT -eq '1') {",
            "    Write-UpdateLog \"Popup suppressed: $Text\"",
            "    return",
            "  }",
            "  try {",
            "    $ws = New-Object -ComObject WScript.Shell",
            "    $null = $ws.Popup($Text, 0, 'JoensAutoDraw Update', $Icon)",
            "  } catch { }",
            "}",
            "function Get-RunningTargetProcess {",
            "  Get-Process -Name 'JoensAutoDraw' -ErrorAction SilentlyContinue | Where-Object {",
            "    try { $_.Path -ieq $TargetExe } catch { $false }",
            "  }",
            "}",
            "Write-UpdateLog ''",
            "Write-UpdateLog \"JoensAutoDraw updater started (pid=$PidToWait).\"",
            "Write-UpdateLog \"NEW=$NewExe\"",
            "Write-UpdateLog \"TARGET=$TargetExe\"",
            "Write-UpdateLog 'Waiting for process to exit...'",
            "$deadline = (Get-Date).AddSeconds(120)",
            "$waited = 0",
            "while ((Get-Process -Id $PidToWait -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {",
            "  Start-Sleep -Milliseconds 500",
            "  $waited++",
            "}",
            "Write-UpdateLog \"Process wait finished (polls=$waited).\"",
            f"Start-Sleep -Seconds {_PYINSTALLER_SETTLE_SECONDS}",
            "Write-UpdateLog 'Waiting for target JoensAutoDraw.exe processes to exit...'",
            "$procDeadline = (Get-Date).AddSeconds(90)",
            "$imageWait = 0",
            "while ((Get-RunningTargetProcess) -and ((Get-Date) -lt $procDeadline)) {",
            "  Start-Sleep -Milliseconds 500",
            "  $imageWait++",
            "}",
            "Write-UpdateLog \"No target JoensAutoDraw.exe processes (polls=$imageWait).\"",
            "Start-Sleep -Seconds 2",
            "if (-not (Test-Path -LiteralPath $NewExe)) {",
            "  Write-UpdateLog \"ERROR: Staging file missing: $NewExe\"",
            "  Show-UpdatePopup 'Update failed. Open JoensAutoDraw-update.log in the app folder.' 48",
            "  exit 1",
            "}",
            "$newSize = (Get-Item -LiteralPath $NewExe).Length",
            "if ($newSize -lt $MinExeSize) {",
            "  Write-UpdateLog \"ERROR: Staging file too small: $newSize bytes\"",
            "  Show-UpdatePopup 'Update failed. Open JoensAutoDraw-update.log in the app folder.' 48",
            "  exit 1",
            "}",
            "$copied = $false",
            "for ($attempt = 0; $attempt -lt 40; $attempt++) {",
            "  try {",
            "    if (Test-Path -LiteralPath $TargetExe) {",
            "      if (Test-Path -LiteralPath $BackupExe) {",
            "        Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue",
            "      }",
            f"      Rename-Item -LiteralPath $TargetExe -NewName '{BACKUP_EXE_NAME}' -Force",
            "    }",
            "    Move-Item -LiteralPath $NewExe -Destination $TargetExe -Force",
            "    if (Test-Path -LiteralPath $TargetExe) { $copied = $true; break }",
            "  } catch {",
            "    Write-UpdateLog \"Replace attempt $attempt failed: $($_.Exception.Message)\"",
            "    if (-not (Test-Path -LiteralPath $TargetExe) -and (Test-Path -LiteralPath $BackupExe)) {",
            "      Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction SilentlyContinue",
            "    }",
            "  }",
            "  Start-Sleep -Seconds 1",
            "}",
            "if (-not $copied) {",
            "  Write-UpdateLog 'ERROR: Could not replace executable.'",
            "  if (-not (Test-Path -LiteralPath $TargetExe) -and (Test-Path -LiteralPath $BackupExe)) {",
            "    Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction SilentlyContinue",
            "  }",
            "  Show-UpdatePopup 'Update failed. Open JoensAutoDraw-update.log in the app folder.' 48",
            "  exit 1",
            "}",
            "if (Test-Path -LiteralPath $BackupExe) {",
            "  Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue",
            "}",
            "Write-UpdateLog 'Replace succeeded.'",
            f"Start-Sleep -Seconds {_PYINSTALLER_SETTLE_SECONDS}",
            "Write-UpdateLog 'PyInstaller temp settle complete.'",
            "Show-UpdatePopup 'Update installed. Click OK to start JoensAutoDraw.' 64",
            "if ($env:JOENSAUTODRAW_UPDATE_NO_RELAUNCH -eq '1') {",
            "  Write-UpdateLog 'Relaunch skipped by environment.'",
            "} else {",
            "  Start-Sleep -Seconds 3",
            "  try {",
            "    $started = Start-Process -FilePath $TargetExe -WorkingDirectory $TargetDir -PassThru -ErrorAction Stop",
            "    Write-UpdateLog \"Launch requested (pid=$($started.Id)).\"",
            "    $runningTarget = $null",
            "    for ($launchPoll = 0; $launchPoll -lt 10; $launchPoll++) {",
            "      $runningTarget = Get-RunningTargetProcess",
            "      if ($runningTarget) { break }",
            "      Start-Sleep -Milliseconds 500",
            "    }",
            "    if ($runningTarget) {",
            "      Write-UpdateLog 'Launched updated app.'",
            "    } else {",
            "      Write-UpdateLog 'WARNING: Updated app process exited shortly after launch.'",
            "      Show-UpdatePopup 'Update installed, but JoensAutoDraw closed immediately after starting. Open JoensAutoDraw.exe manually.' 48",
            "    }",
            "  } catch {",
            "    Write-UpdateLog \"ERROR: Could not relaunch updated app: $($_.Exception.Message)\"",
            "    Show-UpdatePopup 'Update installed, but JoensAutoDraw could not be started automatically. Open JoensAutoDraw.exe manually.' 48",
            "  }",
            "}",
            "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue",
            "exit 0",
        ]
    )


def build_hidden_launcher_vbs(script_path: Path) -> str:
    """Run PowerShell hidden (window style 0)."""
    quoted = str(script_path.resolve()).replace('"', '""')
    ps_command = (
        f"powershell.exe -NoProfile -ExecutionPolicy Bypass "
        f'-WindowStyle Hidden -File "{quoted}"'
    )
    ps_quoted = ps_command.replace('"', '""')
    return (
        "Set sh = CreateObject(\"WScript.Shell\")\r\n"
        f'sh.Run "{ps_quoted}", 0, False\r\n'
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

    temp_dir = Path(tempfile.gettempdir())
    helper = temp_dir / "JoensAutoDraw-update.ps1"
    launcher = temp_dir / "JoensAutoDraw-update.vbs"
    legacy_bat = temp_dir / "JoensAutoDraw-update.bat"
    if legacy_bat.exists():
        try:
            legacy_bat.unlink()
        except OSError:
            pass
    helper.write_text(
        build_update_helper_script(
            pid=pid if pid is not None else os.getpid(),
            new_exe=new_exe.resolve(),
            target=current,
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    launcher.write_text(build_hidden_launcher_vbs(helper), encoding="utf-8", newline="\r\n")
    subprocess.Popen(
        ["wscript.exe", "//nologo", str(launcher)],
        close_fds=True,
        **_windows_subprocess_kwargs(),
    )
    os._exit(0)
