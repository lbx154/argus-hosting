"""Build the assembled hosting desktop with Argus and argus-hosting installed."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Checkout created by assemble.py")
    parser.add_argument("--backend-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    env = {**os.environ, "ARGUS_FROZEN_APPLICATION": "frozen:extend",
           "ARGUS_HOSTING_BUILD_ROOT": str(ROOT), "VITE_ARGUS_HOSTED_TRIAL": "1", "ARGUS_BUILD_PYTHON": sys.executable,
           "PYTHONPATH": os.pathsep.join((str(ROOT), str(source)))}
    # Windows's backend builder performs this same asset chain in its isolated
    # environment. All platforms inherit the shared workspace's hosted flag.
    if os.name != "nt":
        subprocess.run([sys.executable, "-m", "argus.release_tools.build_release"],
                       cwd=source, env=env, check=True)
    subprocess.run([sys.executable, str(source / "desktop-tauri/scripts/build-backend.py")],
                   cwd=source, env=env, check=True)
    binary = source / "desktop-tauri/build/argus-backend" / (
        "argus-backend.exe" if os.name == "nt" else "argus-backend")
    verification = [sys.executable, str(ROOT / "verify-frozen.py"), "--binary", str(binary)]
    if os.environ.get("ARGUS_TEST_NATIVE_COPILOT") == "1":
        verification.append("--native")
    subprocess.run(verification, env=env, check=True)
    if not args.backend_only:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        subprocess.run([npm, "--prefix", str(source / "desktop-tauri"), "run", "build:unsigned"],
                       cwd=source, env=env, check=True)


if __name__ == "__main__":
    main()
