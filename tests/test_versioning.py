from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autopaint import versioning
from autopaint.updater import (
    build_update_helper_script,
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


def test_build_update_helper_script_waits_for_process_and_copies() -> None:
    script = build_update_helper_script(
        pid=12345,
        new_exe=Path(r"C:\Apps\JoensAutoDraw.exe.0.2.0.new"),
        target=Path(r"C:\Apps\JoensAutoDraw.exe"),
    )

    assert "$PidToWait = 12345" in script
    assert "Get-Process -Id $PidToWait" in script
    assert "Get-Process -Name 'JoensAutoDraw'" in script
    assert "Copy-Item -LiteralPath $NewExe -Destination $TargetExe -Force" in script
    assert "Start-Process -LiteralPath $TargetExe" in script
