from __future__ import annotations

from io import BytesIO
import json
import urllib.error
import urllib.request

import pytest

from careloop.runtime_lite.llm import OpenAICompatibleLiteLLMClient
from careloop.runtime_lite.runner import LiteCareLoopRunner


AZURE_FILTER_BODY = {
    "error": {
        "cause": json.dumps(
            {
                "error": {
                    "message": (
                        "The response was filtered due to the prompt triggering Azure OpenAI's "
                        "content management policy. Please modify your prompt and retry."
                    ),
                    "param": "prompt",
                    "code": "content_filter",
                    "status": 400,
                    "innererror": {
                        "code": "ResponsibleAIPolicyViolation",
                        "content_filter_result": {
                            "hate": {"filtered": False, "severity": "safe"},
                            "jailbreak": {"detected": False, "filtered": False},
                            "self_harm": {"filtered": False, "severity": "safe"},
                            "sexual": {"filtered": False, "severity": "safe"},
                            "violence": {"filtered": True, "severity": "medium"},
                        },
                    },
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "code": 400,
        "message": "模型服务调用失败",
        "status": "FAILED_RESPONSE",
    }
}


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")


def _http_error(body: dict = AZURE_FILTER_BODY) -> urllib.error.HTTPError:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return urllib.error.HTTPError(
        url="https://gateway.example/v1/chat/completions",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=BytesIO(raw),
    )


def _client(**overrides) -> OpenAICompatibleLiteLLMClient:
    values = {
        "base_url": "https://gateway.example/v1",
        "api_key": "secret-not-logged",
        "model": "simulator-model",
        "max_retries": 0,
        "retry_until_success": False,
        "retry_backoff_seconds": 0,
        "retry_max_delay_seconds": 0,
    }
    values.update(overrides)
    return OpenAICompatibleLiteLLMClient(**values)


def test_explicit_content_filter_retries_once_with_verbatim_clinical_wrapper(monkeypatch):
    requests: list[dict] = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        if len(requests) == 1:
            raise _http_error()
        return _Response({"choices": [{"message": {"role": "assistant", "content": "accepted"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _client()
    original_system = "Return strict JSON. Preserve the exact injury timeline."
    original_user = '{"clinical_fact":"胸腹部多处刀刺伤并伴失血性休克"}'

    assert client.complete(purpose="world_director", system=original_system, user=original_user) == "accepted"
    assert len(requests) == 2
    assert requests[0]["messages"][0]["content"] == original_system
    assert requests[0]["messages"][1]["content"] == original_user
    assert requests[1]["messages"][0]["content"].endswith(original_system)
    assert requests[1]["messages"][1]["content"].endswith(original_user)
    assert "刀刺伤" in requests[1]["messages"][1]["content"]
    assert "失血性休克" in requests[1]["messages"][1]["content"]

    first, second = client.calls
    assert first["status"] == "retrying_with_clinical_context_wrapper"
    assert first["content_filter_rejection"] is True
    assert first["content_filter_filtered_categories"] == [{"category": "violence", "severity": "medium"}]
    assert first["content_filter_semantic_preservation"] == "verbatim_original_prompts_wrapped_no_deletion"
    assert second["status"] == "ok"
    assert second["content_filter_retry_used"] is True
    assert second["content_filter_retry_count"] == 1
    assert second["content_filter_retry_history"][0]["retry_mode"] == "clinical_context_wrapper_v1"
    assert second["original_provider_prompt_sha256"] != second["provider_prompt_sha256"]


def test_second_content_filter_rejection_fails_instead_of_looping_or_fabricating(monkeypatch):
    requests = []

    def always_filtered(request, timeout):
        requests.append(request)
        raise _http_error()

    monkeypatch.setattr(urllib.request, "urlopen", always_filtered)
    client = _client()

    with pytest.raises(RuntimeError, match=r"HTTP 400.*content_filter"):
        client.complete(purpose="patient_actor", system="medical actor", user="injury facts")

    assert len(requests) == 2
    assert client.calls[-1]["content_filter_retry_used"] is True
    assert client.calls[-1]["content_filter_retry_count"] == 1
    assert client.calls[-1]["status"] == "error"


def test_unrelated_bad_request_is_not_retried(monkeypatch):
    requests = []
    body = {"error": {"message": "Unknown model route", "code": "invalid_request"}}

    def bad_request(request, timeout):
        requests.append(request)
        raise _http_error(body)

    monkeypatch.setattr(urllib.request, "urlopen", bad_request)
    client = _client()

    with pytest.raises(RuntimeError, match=r"HTTP 400"):
        client.complete(purpose="world_director", system="system", user="user")

    assert len(requests) == 1
    assert client.calls[-1].get("content_filter_retry_used") is False


def test_content_filter_parser_handles_nested_escaped_azure_payload():
    client = _client()
    detail = json.dumps(AZURE_FILTER_BODY, ensure_ascii=False)

    parsed = client._content_filter_rejection_info(400, detail)

    assert parsed is not None
    assert "responsible_ai_policy_violation" in parsed["provider_signal"]
    assert parsed["filtered_categories"] == [{"category": "violence", "severity": "medium"}]
    assert client._content_filter_rejection_info(401, detail) is None
    assert client._content_filter_rejection_info(400, '{"error":"invalid model"}') is None


def test_runner_persists_safe_content_filter_retry_lineage():
    client = _client()
    client.calls.append(
        {
            "purpose": "world_director",
            "status": "ok",
            "content_filter_retry_used": True,
            "content_filter_retry_count": 1,
            "content_filter_retry_mode": "clinical_context_wrapper_v1",
            "content_filter_retry_history": [
                {
                    "http_status": 400,
                    "provider_signal": "responsible_ai_policy_violation+content_filter_code",
                    "filtered_categories": [{"category": "violence", "severity": "medium"}],
                    "retry_mode": "clinical_context_wrapper_v1",
                    "semantic_preservation": "verbatim_original_prompts_wrapped_no_deletion",
                }
            ],
            "content_filter_semantic_preservation": "verbatim_original_prompts_wrapped_no_deletion",
            "original_provider_prompt_sha256": "a" * 64,
            "provider_prompt_sha256": "b" * 64,
            "error_detail_preview": "must never be copied into a trajectory",
        }
    )
    runner = LiteCareLoopRunner.__new__(LiteCareLoopRunner)

    metadata = runner._safe_llm_call_metadata(client, temperature=0.1)

    assert metadata["content_filter_retry_used"] is True
    assert metadata["content_filter_retry_history"][0]["filtered_categories"] == [
        {"category": "violence", "severity": "medium"}
    ]
    assert metadata["original_provider_prompt_sha256"] == "a" * 64
    assert metadata["provider_prompt_sha256"] == "b" * 64
    assert "error_detail_preview" not in metadata


def test_content_filter_signal_after_preview_boundary_still_triggers_retry(monkeypatch):
    requests = []
    body = {
        "padding": "x" * 1500,
        "error": {
            "code": "content_filter",
            "innererror": {
                "code": "ResponsibleAIPolicyViolation",
                "content_filter_result": {"violence": {"filtered": True, "severity": "medium"}},
            },
        },
    }

    def fake_urlopen(request, timeout):
        requests.append(request)
        if len(requests) == 1:
            raise _http_error(body)
        return _Response({"choices": [{"message": {"content": "accepted"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _client()

    assert client.complete(purpose="world_director", system="system", user="user") == "accepted"
    assert len(requests) == 2
    assert client.calls[0]["content_filter_rejection"] is True
    assert client.calls[0]["content_filter_provider_signal"]


def test_prompt_pair_hash_preserves_role_boundary():
    client = _client()
    assert client._prompt_pair_sha256("a\nb", "c") != client._prompt_pair_sha256("a", "b\nc")
