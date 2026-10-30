"""The trial client's answers to Argus core's hosting questions.

``argus.core.hosting.hosting()`` returns this module; ``NO_HOSTING`` there
lists the same names with the answers Argus uses when no trial is installed.
The trial key, model and Copilot provider are answered here. The questions
only a hosted trial runtime can answer (training capture, daemon launch
tickets, workspace plugins) go to the runtime registered under the
``argus.hosting`` entry point, which this module never names.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from argus.core.hosting import hosted_runtime

from . import CLIENT_MODEL as client_model
from . import REASONING_EFFORT as reasoning_effort
from .attention import reason as attention_reason
from .attention import record_failure
from .client import apply_trial_provider as apply_provider
from .client import profile_path
from .client import runtime_redactions as redactions
from .client import trial_enabled as enabled
from .client import trial_model_options as model_options

__all__ = [
    "apply_provider", "arm_daemon_launch", "attention_reason", "capture_runtime_call",
    "client_model", "complete_daemon_fork", "copilot_session_store", "daemon_launch_payload",
    "enabled", "model_options", "plugin_middleware", "plugin_setup_actions",
    "prepare_daemon_fork", "reasoning_effort", "record_failure", "redactions",
    "wait_daemon_claim",
]


def copilot_session_store():
    """The trial Copilot home's usage store, or ``None`` outside the trial."""
    return profile_path().parent / "copilot-trial-home" / "session-store.db" if enabled() else None


def capture_runtime_call(ctx, options):
    return hosted_runtime().capture_runtime_call(ctx, options)


def daemon_launch_payload(config):
    return hosted_runtime().daemon_launch_payload(config)


def arm_daemon_launch(payload, config):
    return hosted_runtime().arm_daemon_launch(payload, config)


def prepare_daemon_fork(config):
    return hosted_runtime().prepare_daemon_fork(config)


def wait_daemon_claim(handoff):
    return hosted_runtime().wait_daemon_claim(handoff)


def complete_daemon_fork(handoff, *, forked=True):
    return hosted_runtime().complete_daemon_fork(handoff, forked=forked)


def plugin_setup_actions():
    return hosted_runtime().plugin_setup_actions()


def plugin_middleware(name):
    return hosted_runtime().plugin_middleware(name)


def auto_model(backend, env):
    return client_model if provider_active(backend, env) else ""


def effort_override(env):
    return reasoning_effort if enabled(env) else None


def supports_reasoning_effort(model):
    return model == client_model


def auth_source(env):
    return "trial provider" if enabled(env) else ""


@contextmanager
def provider_setup():
    from .client import TRIAL_ENV

    previous = os.environ.get(TRIAL_ENV)
    os.environ[TRIAL_ENV] = "0"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(TRIAL_ENV, None)
        else:
            os.environ[TRIAL_ENV] = previous


def provider_configured(values):
    from .client import TRIAL_ENV

    return {**values, TRIAL_ENV: "0"}


def prepare_call(backend, options):
    from dataclasses import replace

    if not provider_active(backend):
        return options
    model, effort = model_options(options.model, options.reasoning_effort)
    if blocked := attention_reason():
        raise ValueError(blocked)
    return replace(options, model=model, reasoning_effort=effort)


def finish_call(backend, result, *, known_values=()):
    from argus.core.secret_guard import redact_secrets_text

    if (provider_active(backend)
            and (result.exit_code != 0 or result.fatal_error)
            and result.stop_kind not in {
                "budget_exhausted", "cost_unreconciled", "operator_pause", "operator_abort", "daemon_shutdown",
            }):
        diagnostic = redact_secrets_text(str(result.fatal_error or ""), known_values=known_values).strip()
        result.fatal_error = record_failure(diagnostic)
        if diagnostic:
            result.fatal_error += "\n具体原因：" + diagnostic[:1200]
        result.stop_kind = "permanent_error"
    return result


def failure_kind(diagnostic):
    from argus.core.http_status import has_http_status

    low = diagnostic.casefold()
    if allowance_exhausted(low):
        return "provider_fence"
    if has_http_status(low, {400}) and "unsupported trial request" in low:
        return "permanent_error"
    return None


def allowance_exhausted(diagnostic):
    import re

    return bool(re.search(
        r"trial_budget_exhausted|trial_quota_exceeded|insufficient trial tokens|试用额度",
        str(diagnostic or ""), re.IGNORECASE,
    ))


def failure_message(diagnostic, chinese):
    if allowance_exhausted(diagnostic):
        return _WORDING["allowance_exhausted"][int(chinese)]
    return ""


def is_service_failure(diagnostic):
    return not allowance_exhausted(diagnostic)


def usage_pricing(provider):
    if provider_active(provider):
        return "not_billed", "hosted_trial", 0.0, "hosted_trial"
    return None


def model_change_error():
    return "the hosted trial chooses its own model" if enabled() else ""


def prompt_completion_enabled():
    return not enabled() and hosted_runtime().prompt_completion_enabled()


def sibling_libraries_enabled():
    return not enabled()


_WORDING = {'cause_allowance_exhausted': ('The trial allowance is used up, so this round stopped; all files '
                               'are kept.',
                               '试用额度已用完，这一轮停了下来，文件都保留着。'),
 'why_allowance_exhausted': ('the trial allowance is used up', '试用额度已用完'),
 'allowance_exhausted': ('The trial allowance is used up, so the task is paused; all files are '
                         'kept.',
                         '试用额度已用完，任务已暂停，文件都保留着。'),
 'update_resume_after_top_up': ('Once the allowance is topped up, click Resume to continue; '
                                'nothing retries on its own until then.',
                                '额度补上后点「继续运行」就能接着做；在那之前不会自动重试。')}

def wording(kind):
    return _WORDING.get(kind)


def operator_update(*, park, reason, chinese):
    if park != "provider_fence" or not allowance_exhausted(reason):
        return ""
    resume = _WORDING["update_resume_after_top_up"][int(chinese)]
    return failure_message(reason, chinese) + "\n" + resume


def failure_cause(diagnostic):
    if not allowance_exhausted(diagnostic):
        return None
    from argus.engineer.round_stop_signals import BackendFailureCause

    return BackendFailureCause("allowance_exhausted", diagnostic.strip()[:240])


def failure_review(cause, diagnostic, exit_code):
    if cause.kind != "allowance_exhausted":
        return None
    from argus.core.models import ReviewDecision

    return ReviewDecision(
        status="blocked",
        reason=("The trial allowance is used up, so this round stopped before it "
                "could be judged; all files are kept. "
                f"Technical record: error={diagnostic}"),
        next_action="Once the allowance is topped up, click Resume to continue.",
        backend_unavailable=True, backend_fatal_error=diagnostic,
        backend_exit_code=exit_code, backend_stop_kind="provider_fence",
    )


def provider_active(backend, env=None):
    return str(backend).strip().lower() == "copilot" and enabled(env)
