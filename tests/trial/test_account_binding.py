"""Hosted client policy through the installed Argus boundary."""
from __future__ import annotations

from pathlib import Path

from argus.agent_cli.copilot_home import (
    COPILOT_ACCOUNT_HOME_KNOB,
    apply_copilot_account,
    copilot_account_home,
)


def test_account_binding_does_not_override_an_explicit_trial(tmp_path: Path) -> None:
    env = {
        COPILOT_ACCOUNT_HOME_KNOB: str(tmp_path / "dedicated"),
        "ARGUS_SKILL_COPILOT_TRIAL": "1",
        "COPILOT_HOME": str(tmp_path / "trial"),
        "COPILOT_PROVIDER_API_KEY": "fake-trial-key",
    }
    assert copilot_account_home(env) is None
    assert apply_copilot_account(dict(env)) == env
