from __future__ import annotations

import io
import sys
from functools import partial

import pytest
from argus import desktop_backend_entry
from argus.apps import tui_launcher


def _frozen_console_streams(monkeypatch, encoding, *, platform_name="nt"):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict", newline="\n", write_through=True)
    stderr = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict", newline="\n", write_through=True)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_backend_entry, "verify_runtime_providers", lambda: {"ok": True})
    monkeypatch.setattr(desktop_backend_entry, "_install_windows_signal_zero_guard", lambda: None)
    monkeypatch.setattr(
        tui_launcher, "_configure_windows_console_encoding",
        partial(tui_launcher._configure_windows_console_encoding, platform_name=platform_name),
    )
    return stdout, stderr


@pytest.mark.parametrize("encoding", ["cp1252", "gbk"])
def test_frozen_cached_native_install_reports_progress_on_redirected_windows_streams(
    tmp_path, monkeypatch, encoding,
):
    from argus_hosting.trial import native_cli

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setattr(native_cli.platform, "system", lambda: "Windows")
    monkeypatch.setattr(native_cli.platform, "machine", lambda: "AMD64")
    executable = tmp_path / "runtime" / "copilot" / native_cli.VERSION / "copilot.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture-native-cli")
    verified = []
    monkeypatch.setattr(native_cli, "verify_cli", lambda path: verified.append(path))
    monkeypatch.setattr(
        native_cli.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("Cached CLI must not download"),
    )
    stdout, _stderr = _frozen_console_streams(monkeypatch, encoding)
    monkeypatch.setattr(sys, "argv", [
        "argus-backend", "-c",
        "from argus_hosting.trial.native_cli import install_native_copilot; "
        "install_native_copilot(); print('native-copilot-ready')",
    ])

    assert desktop_backend_entry._entrypoint() == 0
    assert verified == [executable]
    assert stdout.buffer.getvalue().decode("utf-8").splitlines() == [
        "正在检查已安装的 Copilot…", "native-copilot-ready",
    ]
