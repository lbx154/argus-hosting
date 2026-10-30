"""Hosted provider policy at the Argus integration boundary."""
from __future__ import annotations

import json

from argus.core.config_snapshot import build_config_snapshot
from argus.core.knob_store import write_persisted_knobs


def test_trial_snapshot_identifies_hosted_model_without_exposing_key() -> None:
    write_persisted_knobs({"ARGUS_SKILL_COPILOT_TRIAL": "1", "ARGUS_SKILL_MODEL": "argus-trial"})
    snapshot = build_config_snapshot(env={})
    assert all(row["model"] == "argus-trial" for row in snapshot["roles"])
    assert all(row["reasoning_effort"] == "high" for row in snapshot["roles"])
    assert "api_key" not in json.dumps(snapshot)
    assert "trial_mode" not in build_config_snapshot(env={"ARGUS_SKILL_COPILOT_TRIAL": "0"})
