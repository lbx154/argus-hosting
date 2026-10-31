"""Exercise client discovery and private stdio in the actual frozen backend.

Uses a local status gateway and a synthetic key; no public trial is charged.
The executable runs outside both source trees with Python import paths cleared.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

KEY = "argus_trial_" + "a" * 64


class Gateway(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/trial/status" or self.headers.get("Authorization") != "Bearer " + KEY:
            self.send_error(403)
            return
        body = json.dumps({"tokens_remaining": 900, "token_limit": 1000, "tokens_used": 100}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def verify(binary: Path, *, native: bool = False) -> None:
    binary = binary.resolve()
    gateway = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="hosting-frozen-") as temporary:
            root = Path(temporary)
            env = {name: value for name, value in os.environ.items()
                   if not name.startswith(("PYTHON", "ARGUS_", "COPILOT_", "GH_", "GITHUB_"))}
            env.update(ARGUS_SKILL_HOME=str(root), ARGUS_SKILL_COPILOT_TRIAL="1",
                       PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

            def run(*arguments, request=None, seconds=90):
                result = subprocess.run([str(binary), *arguments], cwd=root, env=env,
                                        input=json.dumps(request) + "\n" if request else None,
                                        text=True, capture_output=True, timeout=seconds)
                assert KEY not in result.stdout + result.stderr, "Private key leaked from frozen runtime"
                assert result.returncode == 0, result.stdout + result.stderr
                return result.stdout

            run("--verify-frozen-runtime")
            discovery = (
                "import sys; from pathlib import Path; from importlib.util import find_spec; from importlib.metadata import entry_points; "
                "from argus.core.hosting import hosting, hosted_runtime, NO_HOSTING; "
                "assert sys.frozen; assert find_spec('argus.trial') is None; e=list(entry_points(group='argus.hosting.client')); "
                "assert len(e)==1; provider=e[0].load(); assert hosting() is provider; "
                "assert hosted_runtime() is NO_HOSTING; "
                "from argus.core.config_snapshot import build_config_snapshot; "
                "from argus_hosting.trial.client import TRIAL_ENV; "
                "assert build_config_snapshot(env={TRIAL_ENV:'1'})['trial_mode']; "
                "assert not build_config_snapshot(env={TRIAL_ENV:'0'})['trial_mode']; "
                "import argus_hosting.trial.desktop as d; "
                "assert Path(d.__file__).resolve().is_relative_to(Path(sys._MEIPASS).resolve()); "
                "print('Frozen client entry point loaded from the packaged payload.')"
            )
            print(run("-c", discovery).strip())
            # Run the real private preparation protocol, replacing only the
            # native download and paid model call. Status still crosses HTTP;
            # the request's Key travels over stdin and the candidate is private.
            prepare = f"""
import os, sys
from pathlib import Path
from types import SimpleNamespace
from argus.core import backend_readiness
from argus.tools import setup
from argus_hosting.trial import client, desktop, native_cli

desktop.TRIAL_URL = {f"http://127.0.0.1:{gateway.server_port}"!r}
native_cli.install_native_copilot = lambda **kwargs: sys.executable
backend_readiness.check_backend_readiness = lambda *args, **kwargs: SimpleNamespace(ok=True)
def model_reply(*args, **kwargs):
    candidate = client.profile_path()
    assert candidate.is_file()
    if os.name != 'nt':
        assert candidate.stat().st_mode & 0o777 == 0o600
    return True
setup._verify_setup_smoke = model_reply
raise SystemExit(desktop.main())
"""
            events = [json.loads(line) for line in run(
                "-c", prepare, request={"action": "prepare", "api_key": KEY},
            ).splitlines()]
            assert events[-1]["event"] == "complete" and events[-1]["balance"]["tokens_remaining"] == 900
            assert not (root / "copilot-trial.json").exists(), "Preparation changed the active profile"
            print("Frozen private preparation completed with a local gateway and a model test double.")
            (root / "copilot-trial.json").write_text(json.dumps({
                "base_url": f"http://127.0.0.1:{gateway.server_port}/v1", "api_key": KEY,
            }), encoding="utf-8")
            (root / "copilot-trial.json").chmod(0o600)
            run("-c", "from argus_hosting.trial import attention; attention.record_failure('provider_stream_incomplete')")
            status = json.loads(run("-m", "argus_hosting.trial.desktop", request={"action": "status"}))
            assert status["event"] == "balance" and status["tokens_remaining"] == 900
            assert status["paused"] and not status["stale"]
            resumed = json.loads(run("-m", "argus_hosting.trial.desktop", request={"action": "resume"}))
            assert resumed["event"] == "balance" and not resumed["paused"] and not resumed["stale"]
            assert KEY not in (root / "trial-status.json").read_text()
            print("Frozen stdio status and explicit resume reached the local gateway; key remained private.")
            if native:
                print(run("-c", "from argus_hosting.trial.native_cli import install_native_copilot; "
                          "install_native_copilot(); print('Frozen native Copilot installation verified.')",
                          seconds=600).strip())
    finally:
        gateway.shutdown()
        gateway.server_close()
        thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--native", action="store_true", help="Download and verify the pinned standalone Copilot CLI")
    args = parser.parse_args()
    verify(args.binary, native=args.native)


if __name__ == "__main__":
    main()
