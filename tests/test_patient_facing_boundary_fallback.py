from types import SimpleNamespace
from urllib.error import HTTPError

from careloop.runtime_lite.runner import LiteCareLoopRunner


class _Trajectory:
    def __init__(self):
        self.events = []
        self.transcript = []

    def add_event(self, **kwargs):
        self.events.append(kwargs)


def _runner_with_failure(exc):
    runner = object.__new__(LiteCareLoopRunner)
    runner.config = SimpleNamespace(enable_patient_facing_boundary_check=True)
    runner.trajectory = _Trajectory()
    runner.current_sim_time = "T+0min"
    runner.simulator_llm = object()
    runner._complete = lambda *args, **kwargs: (_ for _ in ()).throw(exc)
    return runner


def test_auxiliary_boundary_rewriter_http_error_does_not_abort_doctor_turn():
    exc = HTTPError("https://provider.invalid", 400, "content filter", {}, None)
    runner = _runner_with_failure(exc)
    original = "我刚才调用了 Clinical Workspace，现在建议立即去急诊。"

    rewritten = runner._apply_patient_facing_boundary_check(1, original, source="care_envelope")

    assert rewritten
    assert "Clinical Workspace" not in rewritten
    assert "立即去急诊" in rewritten
    assert len(runner.trajectory.events) == 1
    event = runner.trajectory.events[0]
    assert event["event_type"] == "patient_facing_boundary_check"
    assert event["visibility"] == "internal_audit"
    assert event["content"]["status"] == "rewritten_by_fallback"
    assert event["content"]["auxiliary_rewriter_failure"] == {
        "error_type": "HTTPError",
        "http_status": 400,
        "fallback_policy": "deterministic_patient_boundary_rewrite",
    }
    assert "provider.invalid" not in str(event)


def test_auxiliary_boundary_rewriter_timeout_uses_non_destructive_fallback():
    runner = _runner_with_failure(TimeoutError("secret endpoint detail"))
    original = "系统提示我后台工具调用完成；请今天复诊。"

    rewritten = runner._apply_patient_facing_boundary_check(2, original, source="care_envelope")

    assert rewritten
    assert "系统提示我" not in rewritten
    assert "后台工具调用" not in rewritten
    assert "今天复诊" in rewritten
    event = runner.trajectory.events[0]
    assert event["content"]["status"] == "rewritten_by_fallback"
    assert event["content"]["rewrite_applied"] is True
    assert event["content"]["auxiliary_rewriter_failure"]["error_type"] == "TimeoutError"
    assert "secret endpoint detail" not in str(event)
