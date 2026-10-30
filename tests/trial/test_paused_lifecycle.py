"""Hosted client policy through the installed Argus boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from argus.life.memory import BacklogItem, LifeMemory
from argus.life.runtime_failure_circuit import active_runtime_failure_circuit
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@dataclass
class _Outcome:
    success: bool
    status: str
    stop_kind: str | None = None
    recoverable: bool = False
    stop_reason: str = ""
    rounds: int = 1
    park_reason: str = ""


class _CompleteRunner:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, **_kwargs: Any) -> _Outcome:
        self.calls += 1
        return _Outcome(success=True, status="done")


class _TurnedAwayOnceRunner(_CompleteRunner):
    """The model service turns the first task away for good; then it is back."""

    def __init__(self, stop_reason: str) -> None:
        super().__init__()
        self.stop_reason = stop_reason

    def execute(self, **kwargs: Any) -> _Outcome:
        if self.calls:
            return super().execute(**kwargs)
        self.calls += 1
        return _Outcome(
            success=False,
            status="parked",
            park_reason="provider_fence",
            stop_kind="provider_fence",
            stop_reason=self.stop_reason,
        )


def test_a_spent_trial_allowance_parks_without_calling_the_model_service_down(tmp_path) -> None:
    """The hosted meter's 402 is the allowance, not an outage: no outage
    circuit opens, the record names the allowance, and the receipt says so
    plainly without sending the person to an administrator."""
    memory = LifeMemory.open(tmp_path / "life")
    memory.backlog.add(BacklogItem.new(
        title="CartPole 三算法样本效率与稳定性研究",
        objective="比较三种算法",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    stop = (
        "审阅者的会话在得出结论前就结束了，这一轮没有评审意见。 技术记录：exit=1; "
        "fatal_error=query: HTTP 402: 402 试用额度不足，任务已暂停。请联系试用版管理员。"
    )
    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_TurnedAwayOnceRunner(stop),
        sink=sink,
        config=LifeSupervisorConfig(poll_interval_seconds=0.0),
    )

    assert supervisor.run()["stopped_by"] == "parked"

    assert active_runtime_failure_circuit(tmp_path / "life") is None
    completed = next(event for event in sink.events if event.get("type") == "life.mission.completed")
    assert completed["park_reason"] == "provider_fence"
    assert completed["failure_kind"] == "allowance_exhausted"
    from argus.core.transcript import read_turns

    receipt = next(turn for turn in read_turns(tmp_path / "life") if turn.get("mission_result"))
    # No operator turn here, so the receipt is in English.
    assert receipt["text"].startswith("The trial allowance is used up, so the task is paused; all files are kept.")
    assert "click Resume" in receipt["text"]
    for absent in ("administrator", "model service", "Technical record", "402"):
        assert absent not in receipt["text"]
