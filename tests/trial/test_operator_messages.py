"""Hosted client policy through the installed Argus boundary."""
from __future__ import annotations

import pytest
from argus.core.operator_messages import humanize_runtime_reason, render_operator_update


@pytest.mark.parametrize(
    ("hint", "said", "resume"),
    [
        ("继续论文", "试用额度已用完，任务已暂停，文件都保留着。", "额度补上后点「继续运行」就能接着做"),
        ("Continue the paper", "The trial allowance is used up, so the task is paused; all files are kept.",
         "Once the allowance is topped up, click Resume to continue"),
    ],
)
def test_a_spent_trial_allowance_is_said_plainly(hint: str, said: str, resume: str) -> None:
    """2026-10-28 trial: a 402 from the trial meter read as the model service
    restricting calls, sent the person to an administrator, and named a Run
    button the page did not show."""
    for reason in (
        "审阅者的会话在得出结论前就结束了。 技术记录：exit=1; fatal_error=query: HTTP 402: 402 试用额度不足，任务已暂停。请联系试用版管理员。",
        "HTTP 402: Insufficient trial tokens for this request.",
        'HTTP 402 {"error": {"code": "trial_budget_exhausted"}}',
    ):
        assert humanize_runtime_reason(reason, language_hint=hint) == said
        text = render_operator_update(
            title="Research", status="parked", park_reason="provider_fence", reason=reason, language_hint=hint,
        )
        assert text.splitlines() == [said, text.splitlines()[1]]
        assert resume in text
        for absent in ("管理员", "administrator", "模型服务", "model service", "技术记录", "HTTP 402"):
            assert absent not in text
