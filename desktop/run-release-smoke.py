"""Select this platform's newly built installer and run the live release smoke."""
import argparse
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
args = parser.parse_args()
pattern = {"win32": "nsis/*.exe", "darwin": "dmg/*.dmg", "linux": "appimage/*.AppImage"}[sys.platform]
packages = list((args.source / "desktop-tauri/src-tauri/target/release/bundle").glob(pattern))
if len(packages) != 1:
    raise SystemExit(f"Expected exactly one installer, found {len(packages)}")
command = [sys.executable, str(Path(__file__).with_name("smoke-trial-release.py")), str(packages[0])]
if sys.platform == "linux":
    command = ["xvfb-run", "-a", "dbus-run-session", "--", *command]
subprocess.run(command, check=True)
