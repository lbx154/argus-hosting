"""Hosted client policy through the installed Argus boundary."""
from __future__ import annotations

from pathlib import Path

from argus.core.mission_view import load_mission_view
from argus.life.event_log import JsonlEventSink


def emit(root, event_type, ts, **payload):
    JsonlEventSink(None, life_dir=root).append({"type": event_type, "ts": ts, **payload})
    return load_mission_view(root)


def test_a_spent_trial_allowance_reads_as_the_allowance_not_the_model_service(tmp_path: Path) -> None:
    """The hosted meter's 402 parked a task as a provider fence; its row said
    the model service refused calls and carried the raw record in the line."""
    emit(
        tmp_path, "life.manager.intent.completed", 1, item_id="task-1", objective="比较三种算法的样本效率",
        vertical="research", stages=[],
    )
    stop = (
        "审阅者的会话在得出结论前就结束了，这一轮没有评审意见。 技术记录：exit=1; "
        "fatal_error=query: HTTP 402: 402 试用额度不足，任务已暂停。请联系试用版管理员。"
    )
    view = emit(
        tmp_path, "life.mission.completed", 2, item_id="task-1", success=False, status="parked",
        park_reason="provider_fence", outcome_class="ended", stop_kind="provider_fence", stop_reason=stop,
        failure_kind="allowance_exhausted", failure_cause=stop, recoverable=True,
        outcome={"interruption_kind": "provider_fence", "resumable": True},
    )

    entry = view["timeline"][-1]
    assert entry["title"] == "任务已暂停，因为试用额度已用完；进度已保存，可以继续。"
    assert entry["detail"] == "试用额度已用完，这一轮停了下来，文件都保留着。"
    assert "fatal_error=query: HTTP 402" in entry["technical"]
    for said in (entry["title"], entry["detail"]):
        assert "模型服务" not in said and "技术记录" not in said and "管理员" not in said
