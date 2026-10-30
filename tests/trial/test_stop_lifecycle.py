"""Hosted client policy through the installed Argus boundary."""
from __future__ import annotations

from pathlib import Path

import pytest
from argus.adapters.agent_cli_backend import _raw_backend_stop_kind
from argus.agent_cli._event_consumers import EventConsumerMixin
from argus.core.models import RunnerResult
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import ReviewerConfig


class _StoppedEngineer:
    def __init__(self, stop_kind: str) -> None:
        self.stop_kind = stop_kind
        self.calls = 0

    def run_exec(self, **_kwargs) -> RunnerResult:
        self.calls += 1
        return RunnerResult(
            exit_code=-1,
            fatal_error=f"stopped: {self.stop_kind}",
            stop_kind=self.stop_kind,  # type: ignore[arg-type]
        )


class _ReviewerMustNotRun:
    def evaluate(self, **_kwargs):  # pragma: no cover - contract assertion
        raise AssertionError("reviewer must not run after an external stop")


def _run_engineer(
    tmp_path: Path,
    stop_kind: str,
) -> tuple[str, _StoppedEngineer, list[dict]]:
    events: list[dict] = []
    backend = _StoppedEngineer(stop_kind)
    engine = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=_ReviewerMustNotRun(),
        engineer_config=EngineerConfig(model="test"),
        reviewer_config=ReviewerConfig(model="test"),
    )
    status, _rounds, _message, _reason, _thread = engine.run(
        objective="test stop handling",
        engineer_prompt_builder=lambda _next, _static=True: "work",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )
    return status, backend, events


def test_copilot_trial_quota_error_pauses_after_one_attempt(tmp_path: Path) -> None:
    message = "402 Insufficient trial tokens for this request."
    state = (None, False, False, None)
    for event in [
        {"type": "session.error", "data": {
            "errorType": "query", "message": message, "statusCode": 402,
        }},
        {"type": "result", "sessionId": "trial-session", "exitCode": 1},
    ]:
        state = EventConsumerMixin._consume_copilot_event(
            event=event, thread_id=state[0], agent_messages=[],
            turn_completed=state[1], turn_failed=state[2], fatal_error=state[3],
        )
    assert state == ("trial-session", False, True, f"query: HTTP 402: {message}")
    stop_kind = _raw_backend_stop_kind(fatal_error=state[3], exit_code=1)
    assert stop_kind == "provider_fence"
    status, backend, events = _run_engineer(tmp_path, stop_kind)
    assert status == "parked"
    assert backend.calls == 1
    assert not any(event["type"] == "round.backend_failure.backoff" for event in events)


@pytest.mark.parametrize("message", [
    "402 Insufficient trial tokens for this request.",
    '402 {"error":{"code":"trial_quota_exceeded","message":"Insufficient trial tokens for this request."}}',
    "HTTP 402: Payment Required",
])
def test_trial_quota_receipts_are_provider_fences(message: str) -> None:
    assert _raw_backend_stop_kind(fatal_error=message, exit_code=1) == "provider_fence"


def test_rejected_trial_request_does_not_retry_as_a_backend_outage(tmp_path: Path) -> None:
    message = (
        "HTTP 400: 400 Unsupported trial request; use text/tool "
        "Chat Completions with model argus-trial."
    )
    stop_kind = _raw_backend_stop_kind(fatal_error=message, exit_code=1)

    assert stop_kind == "permanent_error"
    status, backend, events = _run_engineer(tmp_path, stop_kind)
    assert status == "error"
    assert backend.calls == 1
    assert not any(event["type"] == "round.backend_failure.backoff" for event in events)
    assert _raw_backend_stop_kind(
        fatal_error="HTTP 400: context length exceeded", exit_code=1,
    ) == "backend_unavailable"
