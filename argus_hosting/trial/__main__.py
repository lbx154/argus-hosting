"""Set up the hosted client with its own command."""
from __future__ import annotations

import argparse
import subprocess
import sys

from . import TRIAL_URL
from .client import setup_trial


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Connect Argus to the hosted gateway")
    parser.add_argument("--url", default=TRIAL_URL)
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args(argv)
    try:
        return setup_trial(args.url, non_interactive=args.non_interactive)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"argus-trial: setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
