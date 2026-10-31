"""Fetch Argus sources and apply the hosting-owned desktop additions.

The destination is a disposable build checkout, never another maintained copy
of the Argus desktop. Patch conflicts fail before installing or building.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def assemble(repository: str, revision: str, destination: Path, workspace_patch: Path | None = None) -> Path:
    if Path(repository).exists():
        repository = str(Path(repository).resolve())
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Use a new build destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "fetch", "--depth", "1", repository, revision], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"], check=True)
    workspace_patch = (workspace_patch or ROOT.parent / "frontend/workspace/hosting.patch").resolve()
    if not workspace_patch.is_file():
        raise FileNotFoundError("Merge the hosting workspace sources first, or pass --workspace-patch")
    patches = [ROOT / "patches/trial.patch", workspace_patch]
    for patch in patches:
        subprocess.run(["git", "-C", str(destination), "apply", "--check", str(patch)], check=True)
    for patch in patches:
        subprocess.run(["git", "-C", str(destination), "apply", str(patch)], check=True)
    # Use the same hosted web tests as the approved workspace build. The
    # ordinary web tests remain in Argus; their policy assertions belong to
    # that build, while the copied source now implements hosting policy.
    web_tests = destination / "frontend/web/src/test"
    shutil.rmtree(web_tests)
    shutil.copytree(workspace_patch.parent / "test", web_tests)
    shutil.copytree(ROOT / "overlay", destination, dirs_exist_ok=True)
    shutil.copytree(ROOT / "tests", destination / "desktop-tauri/tests", dirs_exist_ok=True)
    commit = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
    print(json.dumps({"argus_commit": commit, "source": str(destination)}))
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--argus-repository", default="https://github.com/lbx154/Argus.git")
    parser.add_argument("--argus-ref", default="dev")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--workspace-patch", type=Path, help="Use the shared hosting workspace patch from another checkout")
    args = parser.parse_args()
    assemble(args.argus_repository, args.argus_ref, args.destination, args.workspace_patch)


if __name__ == "__main__":
    main()
