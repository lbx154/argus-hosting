"""Hosted client policy through the installed Argus boundary."""
from __future__ import annotations

import pytest
from argus.engineer.round_stop_signals import (
    backend_failure_cause,
    infrastructure_failure_review_decision,
)


def test_a_spent_trial_allowance_waits_for_resume_instead_of_retrying() -> None:
    record = "query: HTTP 402: 402 试用额度不足，任务已暂停。请联系试用版管理员。"
    decision = infrastructure_failure_review_decision(
        cause=backend_failure_cause(record, exit_code=1), fatal_error=record, exit_code=1,
    )

    # Waiting does not bring an allowance back: the task waits for Resume.
    assert decision.backend_stop_kind == "provider_fence"
    assert decision.reason.startswith("The trial allowance is used up")
    assert "retries" not in decision.reason
    assert "click Resume" in (decision.next_action or "")



@pytest.mark.parametrize(("record", "kind", "line"), [
    # The hosted trial's meter: its allowance, not the model service's quota.
    ("HTTP 402: Insufficient trial tokens for this request.", "allowance_exhausted",
     "HTTP 402: Insufficient trial tokens for this request."),
    ("query: HTTP 402: 402 试用额度不足，任务已暂停。请联系试用版管理员。", "allowance_exhausted",
     "query: HTTP 402: 402 试用额度不足，任务已暂停。请联系试用版管理员。"),
    ('HTTP 402 {"error": {"code": "trial_budget_exhausted"}}', "allowance_exhausted",
     'HTTP 402 {"error": {"code": "trial_budget_exhausted"}}'),
])
def test_hosted_allowance_names_the_actual_cause(record, kind, line):
    cause = backend_failure_cause(record, exit_code=1)
    assert cause.kind == kind and cause.line == line
