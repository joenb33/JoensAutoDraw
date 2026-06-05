from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from autopaint import updater, versioning
from autopaint.updater import (
    UpdateInfo,
    build_hidden_launcher_vbs,
    build_update_helper_script,
    download_update,
    fetch_latest_update,
    is_newer_version,
    parse_version,
)


def test_parse_version() -> None:
    assert parse_version("v1.2.3") == (1, 2, 3)


def test_is_newer_version() -> None:
    assert is_newer_version("0.2.0", "0.1.9")
    assert not is_newer_version("0.1.0", "0.1.0")
    assert not is_newer_version("0.1.0", "0.2.0")


def test_bump_patch_version_updates_init(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_path = tmp_path / "__init__.py"
    init_path.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    monkeypatch.setattr(versioning, "_INIT_PATH", init_path)

    assert versioning.bump_patch_version() == "1.2.4"
    assert versioning.read_version() == "1.2.4"


@patch("autopaint.updater.is_frozen_app", return_value=True)
@patch("autopaint.updater.__version__", "0.1.0")
@patch("autopaint.updater.json.load")
@patch("autopaint.updater.urllib.request.urlopen")
def test_fetch_latest_update_returns_info(mock_urlopen, mock_json_load, _frozen) -> None:
    mock_json_load.return_value = {
        "tag_name": "v0.2.0",
        "html_url": "https://github.com/joenb33/JoensAutoDraw/releases/tag/v0.2.0",
        "assets": [
            {
                "name": "JoensAutoDraw.exe",
                "browser_download_url": "https://example.com/JoensAutoDraw.exe",
            }
        ],
    }
    response = MagicMock()
    response.__enter__.return_value = response
    mock_urlopen.return_value = response

    update = fetch_latest_update()
    assert update is not None
    assert update.version == "0.2.0"
    assert update.download_url.endswith("JoensAutoDraw.exe")


@patch("autopaint.updater.is_frozen_app", return_value=False)
def test_fetch_latest_update_skips_when_not_frozen(_frozen) -> None:
    assert fetch_latest_update() is None


def test_build_update_helper_script_uses_powershell_without_find() -> None:
    target = Path(r"C:\Apps\JoensAutoDraw.exe")
    script = build_update_helper_script(
        pid=12345,
        new_exe=Path(r"C:\Apps\JoensAutoDraw.exe.0.2.0.new"),
        target=target,
    )

    assert "$PidToWait = 12345" in script
    assert "Get-Process -Id $PidToWait" in script
    assert "Get-Process -Name 'JoensAutoDraw'" in script
    assert "Move-Item -LiteralPath $NewExe -Destination $TargetExe" in script
    assert "Move-Item -LiteralPath $BackupExe -Destination $TargetExe" in script
    assert "JOENSAUTODRAW_UPDATE_SILENT" in script
    assert "JOENSAUTODRAW_UPDATE_NO_RELAUNCH" in script
    assert "Start-Process -FilePath $TargetExe" in script
    assert "Start-Process -LiteralPath" not in script
    assert "Launch requested (pid=$($started.Id))." in script
    assert "Could not relaunch updated app" in script
    assert "JoensAutoDraw-update.log" in script
    assert "Update installed. Click OK to start JoensAutoDraw." in script
    assert "findstr" not in script.lower()
    assert "tasklist" not in script.lower()
    assert "find.exe" not in script.lower()


def test_build_hidden_launcher_vbs_runs_powershell_hidden() -> None:
    vbs = build_hidden_launcher_vbs(Path(r"C:\Temp\JoensAutoDraw-update.ps1"))
    assert "WScript.Shell" in vbs
    assert "JoensAutoDraw-update.ps1" in vbs
    assert "powershell.exe" in vbs.lower()
    assert "-WindowStyle Hidden" in vbs
    assert ", 0, False" in vbs


class _ChunkedResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if size < 0:
            size = len(self._data) - self._offset
        start = self._offset
        self._offset = min(len(self._data), self._offset + size)
        return self._data[start : self._offset]


def test_download_update_writes_complete_exe_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "JoensAutoDraw.exe.9.9.9.new"
    monkeypatch.setattr(updater, "_update_staging_path", lambda _version: destination)
    monkeypatch.setattr(updater, "MIN_UPDATE_EXE_SIZE_BYTES", 4)
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _ChunkedResponse(b"MZfake-exe"),
    )

    result = download_update(
        UpdateInfo(
            version="9.9.9",
            download_url="https://example.com/JoensAutoDraw.exe",
            release_url="https://example.com/release",
        )
    )

    assert result == destination
    assert destination.read_bytes() == b"MZfake-exe"
    assert not Path(f"{destination}.part").exists()


def test_download_update_removes_partial_file_on_invalid_exe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "JoensAutoDraw.exe.9.9.9.new"
    monkeypatch.setattr(updater, "_update_staging_path", lambda _version: destination)
    monkeypatch.setattr(updater, "MIN_UPDATE_EXE_SIZE_BYTES", 4)
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _ChunkedResponse(b"not-an-exe"),
    )

    with pytest.raises(RuntimeError, match="Windows executable"):
        download_update(
            UpdateInfo(
                version="9.9.9",
                download_url="https://example.com/JoensAutoDraw.exe",
                release_url="https://example.com/release",
            )
        )

    assert not destination.exists()
    assert not Path(f"{destination}.part").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell helper is Windows-only")
def test_update_helper_replaces_target_and_removes_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(updater, "_PYINSTALLER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(updater, "MIN_UPDATE_EXE_SIZE_BYTES", 1)
    target = tmp_path / "JoensAutoDraw.exe"
    staged = tmp_path / "JoensAutoDraw.exe.9.9.9.new"
    script_path = tmp_path / "JoensAutoDraw-update.ps1"
    target.write_bytes(b"old")
    staged.write_bytes(b"new")
    script_path.write_text(
        updater.build_update_helper_script(pid=99999999, new_exe=staged, target=target),
        encoding="utf-8",
        newline="\r\n",
    )

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$env:JOENSAUTODRAW_UPDATE_SILENT='1'; "
                "$env:JOENSAUTODRAW_UPDATE_NO_RELAUNCH='1'; "
                f"& '{script_path}'"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == b"new"
    assert not staged.exists()
    assert not (tmp_path / "JoensAutoDraw.exe.bak").exists()
    assert "Replace succeeded." in (tmp_path / "JoensAutoDraw-update.log").read_text(
        encoding="utf-8"
    )
