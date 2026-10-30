"""Isolate installed Argus while exercising hosting clients and services."""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_argus_state_roots(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Point every argus state root at a throwaway directory for this test."""
    root = Path(tmp_path_factory.mktemp("argus-home"))
    # Entry points (trial runtimes, the life-worker boot, the TUI launcher)
    # configure their process by writing os.environ directly, which monkeypatch
    # cannot undo. One such write, configure_plugins()'s ARGUS_PLUGINS_PREINSTALL,
    # made every later web-app startup on that xdist worker download and
    # pip-install CrystalPilot, about 1 GB each, until CI ran out of disk quota.
    environment = dict(os.environ)

    # Ambient ARGUS_SKILL_* vars steer backend/model/budget resolution, so a
    # developer shell that exports e.g. ARGUS_SKILL_RUNNER_BACKEND silently
    # changes what the suite exercises: that one var outranks the
    # ARGUS_SKILL_LIFE_BACKEND a test sets, so a guard the test expects to trip
    # never fires and the CLI launches the real cockpit instead. COPILOT_HOME is
    # just as stateful: if the developer runs pytest from inside a Copilot-backed
    # Argus session, child-env tests inherit the real worker home and stop
    # exercising the "no explicit home was chosen" path. Host roots and
    # preinstall declarations would point a test at a real host's installs.
    # Start from a clean slate; a test that needs a value sets it itself.
    for name in [k for k in os.environ if k.startswith(("ARGUS_SKILL_", "ARGUS_WORKBENCH_", "ARGUS_PLUGIN", "ARGUS_VERTICALS_", "ARGUS_DESKTOP_", "ARGUS_TRIAL_"))]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("COPILOT_HOME", raising=False)

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    # Model resolution inspects Codex's provider config to decide whether an
    # OpenAI model id is valid. Never let a developer's ~/.codex/config.toml
    # change default-model assertions. CODEX_HOME remains overridable by tests
    # that deliberately exercise a custom provider.
    monkeypatch.setenv("CODEX_HOME", str(root / "codex-home"))

    # ARGUS_SKILL_SOURCE_ROOT deliberately stays UNSET (the loop above already
    # dropped any developer-shell value). Setting it now ARMS the source-root
    # startup preflight, so an ambient throwaway path would make every real
    # daemon/WebAPI boot in the suite refuse startup; the self-maintenance
    # machine that once read it as a maintenance source tree is gone. A test
    # that exercises the preflight sets its own root.
    yield
    # monkeypatch may have recorded such a write as the value to put back.
    monkeypatch.undo()
    for name in os.environ.keys() - environment.keys():
        del os.environ[name]
    os.environ.update(environment)

def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    """Keep collection-time imports in safe mode.

    The per-test fixture above re-points every state root, but module import
    happens before it runs; safe mode keeps any import-time side effect from
    reaching a real sandbox escape.
    """
    os.environ.setdefault("ARGUS_SKILL_SAFE_MODE", "1")

@pytest.fixture(autouse=True)
def _isolate_working_directory(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Give every test its own working directory instead of the checkout.

    Much of the runtime resolves "which project am I?" from the process cwd —
    ``resolve_project_root``, ``resolve_vertical``, ``PIPELINE_STATE.json``
    lookups, the daemon's own workdir. Under pytest that cwd was the source
    checkout, so a test would silently adopt the repository as its project. The
    visible symptom was log lines like ``no Manager vertical resolved for
    .../argus; using research only as a compatibility fallback`` during
    unrelated tests; the invisible one is any test that writes project state
    into the tree it is testing.

    A test that genuinely needs a specific directory still calls
    ``monkeypatch.chdir`` itself and wins, since fixtures run before the test
    body.
    """
    workdir = tmp_path_factory.mktemp("workdir")
    monkeypatch.chdir(workdir)
    return workdir

@pytest.fixture(autouse=True)
def _no_real_copilot_help_probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Never run the installed Copilot CLI to feature-detect ``--session-id``.

    The adapter probes the executable's help text once per process before it
    binds a new session identity. A developer machine with Copilot on PATH
    would otherwise make the suite spawn a real Node binary and change which
    argv/identity path every Copilot-backed test exercises. Default to "no
    such flag"; a test that exercises the binding stubs the probe itself.
    """
    from argus.agent_cli import copilot_session as _copilot_session

    _copilot_session.reset_copilot_session_id_support_cache()
    monkeypatch.setattr(_copilot_session, "_copilot_help_text", lambda executable: "")
    yield
    _copilot_session.reset_copilot_session_id_support_cache()

@pytest.fixture(autouse=True)
def _no_host_sandbox(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent CLIs in tests are fakes that write where the test reads, so they
    run without the host's sandbox; a test of the sandbox asks for
    ``host_sandbox``."""
    if "host_sandbox" not in request.fixturenames:
        monkeypatch.setattr("argus.core.sandbox.sandbox_tool", lambda: None)

@pytest.fixture
def host_sandbox() -> str:
    from argus.core.sandbox import sandbox_tool

    tool = sandbox_tool()
    if tool is None or Path(tool).name != "bwrap":
        pytest.skip("needs bubblewrap")
    return tool

@pytest.fixture(autouse=True)
def _no_stop_leaks_between_tests():
    """The process-wide stop flag outlives a test by design — it exists so a
    wait deep inside a mission can see a signal. One test setting it once made
    an unrelated external-work test read `stop_requested` instead of the
    outcome that had actually arrived."""
    from argus.core import process_stop

    process_stop.clear_stop()
    yield
    process_stop.clear_stop()

@pytest.fixture
def redact_secrets_on(monkeypatch):
    """Opt into Argus's credential masking, which is off by default."""
    monkeypatch.setenv("ARGUS_SKILL_REDACT_SECRETS", "1")


@pytest.fixture
def platform_process_env() -> dict[str, str]:
    """Minimal public OS bootstrap values, never an ambient credential copy.

    Node/OpenSSL on Windows fails its CSPRNG initialization without SystemRoot.
    Explicitly isolated child-env tests must retain this OS prerequisite and
    the QA runner's temporary directories while still discarding user config.
    """
    if os.name != "nt":
        return {}
    return {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP")
            if name in os.environ}
