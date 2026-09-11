from __future__ import annotations

"""LLM boundary for runtime_lite.

The real runtime can wrap the existing CareLoop_FCCT-1 WorkerRunner or any external
provider.  The protocol here is deliberately text-first: prompts stay natural,
and only small boundary decisions are parsed by the runtime.
"""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
import random
import re
import sys
import time
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any, Protocol


class LiteLLMClient(Protocol):
    def complete(self, *, purpose: str, system: str, user: str, temperature: float = 0.4) -> str:
        """Return a text completion for one lite runtime purpose."""


@dataclass
class OpenAICompatibleLiteLLMClient:
    """Small OpenAI-compatible chat-completions client.

    This is intentionally independent from the legacy WorkerRunner.  It sends
    natural-language system/user prompts and expects plain text back.  API keys
    are read from configuration or environment and are never included in logs.
    """

    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 120.0
    purpose_timeouts: dict[str, float] = field(default_factory=dict)
    max_tokens: int | None = None
    max_retries: int = 2
    retry_until_success: bool = True
    retry_backoff_seconds: float = 30.0
    retry_max_delay_seconds: float = 60.0
    stream: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    omit_temperature: bool = False
    retry_without_temperature_on_unsupported: bool = True
    retry_with_lower_max_tokens_on_provider_limit: bool = True
    retry_with_clinical_context_on_content_filter: bool = True
    status_dir: str | None = None
    status_file_name: str = "llm_status.json"
    calls: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        base_url_env: str = "CARELOOP_LITE_BASE_URL",
        api_key_env: str = "CARELOOP_LITE_API_KEY",
        model_env: str = "CARELOOP_LITE_MODEL",
        timeout_seconds: float = 120.0,
        purpose_timeouts: dict[str, float] | None = None,
        max_tokens: int | None = None,
        max_retries: int = 2,
        retry_until_success: bool = True,
        retry_backoff_seconds: float = 30.0,
        retry_max_delay_seconds: float = 60.0,
    ) -> "OpenAICompatibleLiteLLMClient":
        base_url = os.environ.get(base_url_env, "").strip()
        api_key = os.environ.get(api_key_env, "").strip()
        selected_model = (model or os.environ.get(model_env, "")).strip()
        missing = [
            name
            for name, value in [
                (base_url_env, base_url),
                (api_key_env, api_key),
                (model_env if not model else "model", selected_model),
            ]
            if not value
        ]
        if missing:
            raise RuntimeError("missing runtime_lite LLM configuration: " + ", ".join(missing))
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=selected_model,
            timeout_seconds=timeout_seconds,
            purpose_timeouts=purpose_timeouts or {},
            max_tokens=max_tokens,
            max_retries=max_retries,
            retry_until_success=retry_until_success,
            retry_backoff_seconds=retry_backoff_seconds,
            retry_max_delay_seconds=retry_max_delay_seconds,
        )

    def complete(self, *, purpose: str, system: str, user: str, temperature: float = 0.4) -> str:
        sanitized_system = self._sanitize_prompt_for_provider(system)
        sanitized_user = self._sanitize_prompt_for_provider(user)
        prompt_sanitized = sanitized_system != str(system or "") or sanitized_user != str(user or "")
        request_system = sanitized_system
        request_user = sanitized_user
        original_provider_prompt_sha256 = self._prompt_pair_sha256(sanitized_system, sanitized_user)
        bounded_max_attempts = max(1, int(self.max_retries) + 1)
        max_attempts_label: int | str = "unbounded" if self.retry_until_success else bounded_max_attempts
        last_error: BaseException | None = None
        include_temperature = not self.omit_temperature
        temperature_fallback_used = False
        max_tokens_fallback_used = False
        content_filter_retry_used = False
        content_filter_retry_history: list[dict[str, Any]] = []
        timeout_seconds = self._timeout_for_purpose(purpose)
        attempt = 0
        while True:
            attempt += 1
            while True:
                request = self._build_request(system=request_system, user=request_user, temperature=temperature, include_temperature=include_temperature)
                self.calls.append(
                    {
                        "purpose": purpose,
                        "model": self.model,
                        "base_url": self._redacted_base_url(),
                        "temperature": temperature if include_temperature else None,
                        "temperature_omitted": not include_temperature,
                        "max_tokens": self.max_tokens,
                        "stream": self.stream,
                        "timeout_seconds": timeout_seconds,
                        "attempt": attempt,
                        "max_attempts": max_attempts_label,
                        "retry_until_success": bool(self.retry_until_success),
                        "provider_prompt_sanitized": prompt_sanitized,
                        "provider_prompt_sha256": self._prompt_pair_sha256(request_system, request_user),
                        "original_provider_prompt_sha256": original_provider_prompt_sha256,
                        "content_filter_retry_used": content_filter_retry_used,
                        "content_filter_retry_count": len(content_filter_retry_history),
                        "content_filter_retry_mode": "clinical_context_wrapper_v1" if content_filter_retry_used else "none",
                        "content_filter_semantic_preservation": (
                            "verbatim_original_prompts_wrapped_no_deletion"
                            if content_filter_retry_used
                            else "original_sanitized_prompt"
                        ),
                    }
                )
                if content_filter_retry_history:
                    self.calls[-1]["content_filter_retry_history"] = [dict(item) for item in content_filter_retry_history]
                started_at = time.time()
                self._emit_progress(
                    "start",
                    purpose=purpose,
                    attempt=attempt,
                    max_attempts=max_attempts_label,
                    include_temperature=include_temperature,
                    timeout_seconds=timeout_seconds,
                )
                try:
                    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                        if self.stream:
                            result_text = self._extract_stream_text(response)
                        else:
                            raw = response.read().decode("utf-8")
                            data = json.loads(raw)
                            result_text = self._extract_text(data)
                            self.calls[-1].update(self._response_metadata(data, result_text))
                    if not str(result_text or "").strip():
                        self.calls[-1]["status"] = "empty_response_retry"
                        raise RuntimeError("runtime_lite LLM response returned empty text")
                    self.calls[-1]["status"] = "ok"
                    self._emit_progress(
                        "done",
                        purpose=purpose,
                        attempt=attempt,
                        max_attempts=max_attempts_label,
                        status="ok",
                        elapsed_seconds=time.time() - started_at,
                        timeout_seconds=timeout_seconds,
                    )
                    return result_text
                except urllib.error.HTTPError as exc:
                    # Parse the complete provider response so an explicit nested
                    # content-filter marker cannot be missed merely because it
                    # occurs after the 1,000-character audit preview boundary.
                    detail = self._sanitize_for_logs(exc.read().decode("utf-8", errors="replace"))
                    detail_preview = detail[:1000]
                    if self._should_retry_without_temperature(
                        http_status=exc.code,
                        detail=detail,
                        include_temperature=include_temperature,
                        fallback_used=temperature_fallback_used,
                    ):
                        self.calls[-1]["status"] = "retrying_without_temperature"
                        self.calls[-1]["error_type"] = "HTTPError"
                        self.calls[-1]["http_status"] = exc.code
                        self.calls[-1]["error_detail_preview"] = detail_preview
                        self.calls[-1]["temperature_retry_reason"] = "provider_rejected_temperature_parameter"
                        self._emit_progress(
                            "retry",
                            purpose=purpose,
                            attempt=attempt,
                            max_attempts=max_attempts_label,
                            status="retrying_without_temperature",
                            elapsed_seconds=time.time() - started_at,
                            http_status=exc.code,
                            error_type="HTTPError",
                            timeout_seconds=timeout_seconds,
                        )
                        # Cache the provider capability at the client level.
                        # JD Cloud rejects temperature for the tested models;
                        # without this, every later node pays one predictable
                        # failing HTTP round trip before succeeding.
                        self.omit_temperature = True
                        include_temperature = False
                        temperature_fallback_used = True
                        continue
                    lower_max_tokens = self._provider_reported_max_tokens_limit(detail)
                    if self._should_retry_with_lower_max_tokens(
                        http_status=exc.code,
                        detail=detail,
                        fallback_used=max_tokens_fallback_used,
                        lower_max_tokens=lower_max_tokens,
                    ):
                        previous_max_tokens = self.max_tokens
                        self.calls[-1]["status"] = "retrying_with_lower_max_tokens"
                        self.calls[-1]["error_type"] = "HTTPError"
                        self.calls[-1]["http_status"] = exc.code
                        self.calls[-1]["error_detail_preview"] = detail_preview
                        self.calls[-1]["max_tokens_retry_reason"] = "provider_reported_completion_token_limit"
                        self.calls[-1]["provider_max_tokens_limit"] = lower_max_tokens
                        self._emit_progress(
                            "retry",
                            purpose=purpose,
                            attempt=attempt,
                            max_attempts=max_attempts_label,
                            status="retrying_with_lower_max_tokens",
                            elapsed_seconds=time.time() - started_at,
                            http_status=exc.code,
                            error_type="HTTPError",
                            timeout_seconds=timeout_seconds,
                        )
                        # Cache the provider capability at the client level.
                        # Some gateways expose model-specific completion-token
                        # ceilings lower than the user's desired cap.  Once the
                        # gateway tells us the ceiling, keep using the highest
                        # accepted value instead of failing every later node.
                        if lower_max_tokens is not None and previous_max_tokens is not None:
                            self.max_tokens = min(previous_max_tokens, lower_max_tokens)
                        max_tokens_fallback_used = True
                        continue
                    content_filter_info = self._content_filter_rejection_info(exc.code, detail)
                    if (
                        content_filter_info is not None
                        and self.retry_with_clinical_context_on_content_filter
                        and not content_filter_retry_used
                    ):
                        safe_event = {
                            "http_status": int(exc.code),
                            "provider_signal": str(content_filter_info.get("provider_signal") or "content_filter"),
                            "filtered_categories": list(content_filter_info.get("filtered_categories") or []),
                            "retry_mode": "clinical_context_wrapper_v1",
                            "semantic_preservation": "verbatim_original_prompts_wrapped_no_deletion",
                        }
                        content_filter_retry_history.append(safe_event)
                        self.calls[-1]["status"] = "retrying_with_clinical_context_wrapper"
                        self.calls[-1]["error_type"] = "HTTPError"
                        self.calls[-1]["http_status"] = exc.code
                        self.calls[-1]["content_filter_rejection"] = True
                        self.calls[-1]["content_filter_provider_signal"] = safe_event["provider_signal"]
                        self.calls[-1]["content_filter_filtered_categories"] = safe_event["filtered_categories"]
                        self.calls[-1]["content_filter_retry_mode"] = safe_event["retry_mode"]
                        self.calls[-1]["content_filter_semantic_preservation"] = safe_event["semantic_preservation"]
                        self.calls[-1]["content_filter_retry_count"] = len(content_filter_retry_history)
                        self.calls[-1]["content_filter_retry_history"] = [dict(item) for item in content_filter_retry_history]
                        self._emit_progress(
                            "retry",
                            purpose=purpose,
                            attempt=attempt,
                            max_attempts=max_attempts_label,
                            status="retrying_with_clinical_context_wrapper",
                            elapsed_seconds=time.time() - started_at,
                            http_status=exc.code,
                            error_type="HTTPError",
                            timeout_seconds=timeout_seconds,
                        )
                        request_system, request_user = self._wrap_verbatim_prompt_for_clinical_content_filter(
                            sanitized_system,
                            sanitized_user,
                        )
                        content_filter_retry_used = True
                        continue
                    retryable = self._is_retryable_http_status(exc.code)
                    message = f"runtime_lite LLM HTTP {exc.code} for purpose={purpose}: {detail_preview}"
                    self.calls[-1]["status"] = "error"
                    self.calls[-1]["error_type"] = "HTTPError"
                    self.calls[-1]["http_status"] = exc.code
                    self.calls[-1]["error_detail_preview"] = detail_preview
                    self._emit_progress(
                        "error",
                        purpose=purpose,
                        attempt=attempt,
                        max_attempts=max_attempts_label,
                        status="error",
                        elapsed_seconds=time.time() - started_at,
                        http_status=exc.code,
                        error_type="HTTPError",
                        timeout_seconds=timeout_seconds,
                    )
                    last_error = RuntimeError(message)
                    if not retryable or self._retry_budget_exhausted(attempt, bounded_max_attempts):
                        raise last_error from exc
                    if exc.code == 429:
                        self.calls[-1]["rate_limit_retry_delay_seconds"] = self._rate_limit_retry_delay_seconds(exc, attempt)
                    break
                except urllib.error.URLError as exc:
                    reason = self._sanitize_for_logs(str(exc.reason))
                    message = f"runtime_lite LLM network error for purpose={purpose}: {reason}"
                    self.calls[-1]["status"] = "error"
                    self.calls[-1]["error_type"] = "URLError"
                    self.calls[-1]["error_detail_preview"] = reason[:1000]
                    self._emit_progress(
                        "error",
                        purpose=purpose,
                        attempt=attempt,
                        max_attempts=max_attempts_label,
                        status="error",
                        elapsed_seconds=time.time() - started_at,
                        error_type="URLError",
                        timeout_seconds=timeout_seconds,
                    )
                    last_error = RuntimeError(message)
                    if self._retry_budget_exhausted(attempt, bounded_max_attempts):
                        raise last_error from exc
                    break
                except TimeoutError as exc:
                    reason = self._sanitize_for_logs(str(exc) or "timed out")
                    message = f"runtime_lite LLM timeout for purpose={purpose}: {reason}"
                    self.calls[-1]["status"] = "error"
                    self.calls[-1]["error_type"] = "TimeoutError"
                    self.calls[-1]["error_detail_preview"] = reason[:1000]
                    self._emit_progress(
                        "error",
                        purpose=purpose,
                        attempt=attempt,
                        max_attempts=max_attempts_label,
                        status="error",
                        elapsed_seconds=time.time() - started_at,
                        error_type="TimeoutError",
                        timeout_seconds=timeout_seconds,
                    )
                    last_error = RuntimeError(message)
                    if self._retry_budget_exhausted(attempt, bounded_max_attempts):
                        raise last_error from exc
                    break
                except OSError as exc:
                    # Some urllib/socket/SSL read timeouts surface as a lower-level
                    # OSError subclass instead of urllib.error.URLError or the
                    # built-in TimeoutError.  Treat these as transient provider or
                    # network failures so a long CareLoop trajectory is not killed
                    # by one slow gateway read.  If all retries fail, the raised
                    # RuntimeError keeps the LLM purpose in the message for audit.
                    reason = self._sanitize_for_logs(str(exc) or type(exc).__name__)
                    is_timeout_like = "timed out" in reason.lower() or "timeout" in type(exc).__name__.lower()
                    error_type = "OSErrorTimeout" if is_timeout_like else "OSError"
                    message = f"runtime_lite LLM network error for purpose={purpose}: {reason}"
                    self.calls[-1]["status"] = "error"
                    self.calls[-1]["error_type"] = error_type
                    self.calls[-1]["error_detail_preview"] = reason[:1000]
                    self._emit_progress(
                        "error",
                        purpose=purpose,
                        attempt=attempt,
                        max_attempts=max_attempts_label,
                        status="error",
                        elapsed_seconds=time.time() - started_at,
                        error_type=error_type,
                        timeout_seconds=timeout_seconds,
                    )
                    last_error = RuntimeError(message)
                    if self._retry_budget_exhausted(attempt, bounded_max_attempts):
                        raise last_error from exc
                    break
                except json.JSONDecodeError as exc:
                    message = f"runtime_lite LLM response was not valid JSON envelope for purpose={purpose}: {exc}"
                    self.calls[-1]["status"] = "error"
                    self.calls[-1]["error_type"] = "JSONDecodeError"
                    self._emit_progress(
                        "error",
                        purpose=purpose,
                        attempt=attempt,
                        max_attempts=max_attempts_label,
                        status="error",
                        elapsed_seconds=time.time() - started_at,
                        error_type="JSONDecodeError",
                        timeout_seconds=timeout_seconds,
                    )
                    last_error = RuntimeError(message)
                    if self._retry_budget_exhausted(attempt, bounded_max_attempts):
                        raise last_error from exc
                    break
                except RuntimeError as exc:
                    if not self._is_retryable_runtime_response_error(exc):
                        raise
                    reason = self._sanitize_for_logs(str(exc) or type(exc).__name__)
                    message = f"runtime_lite LLM retryable response error for purpose={purpose}: {reason}"
                    self.calls[-1]["status"] = "error"
                    self.calls[-1]["error_type"] = "RetryableResponseError"
                    self.calls[-1]["error_detail_preview"] = reason[:1000]
                    self._emit_progress(
                        "error",
                        purpose=purpose,
                        attempt=attempt,
                        max_attempts=max_attempts_label,
                        status="error",
                        elapsed_seconds=time.time() - started_at,
                        error_type="RetryableResponseError",
                        timeout_seconds=timeout_seconds,
                    )
                    last_error = RuntimeError(message)
                    if self._retry_budget_exhausted(attempt, bounded_max_attempts):
                        raise last_error from exc
                    break
            retry_delay = None
            if self.calls and isinstance(self.calls[-1].get("rate_limit_retry_delay_seconds"), (int, float)):
                retry_delay = float(self.calls[-1]["rate_limit_retry_delay_seconds"])
            self._sleep_before_retry(attempt, override_seconds=retry_delay)

    def _retry_budget_exhausted(self, attempt: int, bounded_max_attempts: int) -> bool:
        return (not self.retry_until_success) and attempt >= bounded_max_attempts

    def _is_retryable_http_status(self, status: int) -> bool:
        # Retry transient provider / gateway / rate-limit failures.  Do not
        # infinite-loop on configuration or request-shape errors such as bad key,
        # forbidden model, missing route, or an invalid request body after the
        # explicit temperature/max_tokens capability fallbacks have already run.
        return int(status) in {408, 409, 425, 429, 500, 502, 503, 504}

    def _is_retryable_runtime_response_error(self, exc: RuntimeError) -> bool:
        lowered = str(exc or "").lower()
        return (
            "llm response did not contain text" in lowered
            or "streaming llm response did not contain text" in lowered
            or "llm response returned empty text" in lowered
        )

    def _prompt_pair_sha256(self, system: str, user: str) -> str:
        # Canonical JSON preserves the system/user boundary. A bare newline
        # concatenation is ambiguous when either prompt itself contains newlines.
        payload = json.dumps(
            {"system": str(system or ""), "user": str(user or "")},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def _content_filter_rejection_info(self, http_status: int, detail: str) -> dict[str, Any] | None:
        """Recognize provider-side prompt filtering without treating every 400 as retryable.

        The JD Cloud route used for CL120 can surface an Azure OpenAI
        ``ResponsibleAIPolicyViolation`` as a nested, escaped JSON string.  Only
        explicit provider content-filter signals enter this branch; malformed
        requests, authentication errors, unavailable models, and other 4xx
        responses remain non-retryable.
        """

        if int(http_status) not in {400, 403, 422}:
            return None
        text = str(detail or "")
        lowered = text.lower()
        signal_tokens = {
            "responsible_ai_policy_violation": "responsibleaipolicyviolation",
            "content_filter_code": "content_filter",
            "content_management_policy": "content management policy",
            "content_filtering_policy": "content filtering polic",
            "prompt_filtered": "prompt triggering",
        }
        matched_signals = [name for name, token in signal_tokens.items() if token in lowered]
        if not matched_signals:
            return None

        categories: list[dict[str, str]] = []
        for category in ("hate", "jailbreak", "self_harm", "sexual", "violence"):
            patterns = [
                rf'\\?"{category}\\?"\s*:\s*\{{\s*\\?"filtered\\?"\s*:\s*(true|false)(?:\s*,\s*\\?"severity\\?"\s*:\s*\\?"([^"\\]+)\\?")?',
                rf'\\?"{category}\\?"\s*:\s*\{{\s*\\?"detected\\?"\s*:\s*(true|false)\s*,\s*\\?"filtered\\?"\s*:\s*(true|false)',
            ]
            for index, pattern in enumerate(patterns):
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if not match:
                    continue
                filtered = match.group(1) if index == 0 else match.group(2)
                severity = match.group(2) if index == 0 else None
                if str(filtered).lower() == "true":
                    item = {"category": category}
                    if severity:
                        item["severity"] = str(severity).lower()
                    categories.append(item)
                break
        return {
            "provider_signal": "+".join(matched_signals),
            "filtered_categories": categories,
        }

    def _wrap_verbatim_prompt_for_clinical_content_filter(self, system: str, user: str) -> tuple[str, str]:
        """Add legitimate medical context while retaining both prompts verbatim.

        This is not a safety-filter bypass and it performs no euphemizing,
        deletion, encoding, or model substitution.  It disambiguates a
        deidentified clinical simulation from a harmful request, then encloses
        the already PII-sanitized original system and user text byte-for-byte.
        If the provider rejects this single transparent retry, the required node
        still fails rather than fabricating a fallback result.
        """

        system_prefix = (
            "[CARELOOP DEIDENTIFIED CLINICAL-SIMULATION CONTEXT]\n"
            "This is a closed-world, deidentified, synthetic patient-care simulation used for clinical-safety "
            "evaluation. Sensitive topics such as injuries, violence, self-harm, sexual health, substance use, "
            "or stigma may appear only as medically relevant case facts. This is not a request for harmful "
            "instructions. Follow the original task and output contract exactly. Preserve every clinical fact, "
            "uncertainty, chronology, actor constraint, and safety requirement; do not add, remove, soften, or "
            "reinterpret content.\n\n"
            "[ORIGINAL SYSTEM INSTRUCTIONS — VERBATIM]\n"
        )
        user_prefix = (
            "[CARELOOP DEIDENTIFIED SYNTHETIC CLINICAL INPUT — VERBATIM]\n"
            "Treat the enclosed material solely as the factual input to the original clinical-simulation task. "
            "Preserve it exactly and return only the requested output.\n\n"
        )
        return system_prefix + str(system or ""), user_prefix + str(user or "")

    def _timeout_for_purpose(self, purpose: str) -> float:
        """Return the request timeout for one LLM purpose.

        The global timeout remains the default.  Optional purpose-specific
        overrides are infrastructure safeguards for slow background nodes; they
        must not encode clinical flow rules.  Invalid non-positive overrides are
        ignored so a malformed per-purpose entry cannot accidentally disable the
        client.
        """

        default = float(self.timeout_seconds)
        normalized = str(purpose or "").strip()
        for key in (normalized, normalized.lower(), "*"):
            if key not in self.purpose_timeouts:
                continue
            try:
                value = float(self.purpose_timeouts[key])
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return default

    def _build_request(
        self,
        *,
        system: str,
        user: str,
        temperature: float,
        include_temperature: bool,
    ) -> urllib.request.Request:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **self.extra_body,
        }
        if self.stream:
            payload["stream"] = True
        if include_temperature:
            payload["temperature"] = temperature
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return urllib.request.Request(
            self._chat_completions_url(),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                **self.extra_headers,
            },
        )

    def _should_retry_without_temperature(
        self,
        *,
        http_status: int,
        detail: str,
        include_temperature: bool,
        fallback_used: bool,
    ) -> bool:
        if not self.retry_without_temperature_on_unsupported or fallback_used or not include_temperature:
            return False
        if http_status not in {400, 422}:
            return False
        lowered = str(detail or "").lower()
        if "temperature" not in lowered:
            return False
        return any(token in lowered for token in ["unsupported", "not supported", "unknown", "invalid", "unrecognized", "不支持", "无效", "未知"])

    def _provider_reported_max_tokens_limit(self, detail: str) -> int | None:
        text = str(detail or "")
        lowered = text.lower()
        if "max_tokens" not in lowered and "max tokens" not in lowered and "completion tokens" not in lowered and "tokens" not in lowered:
            return None
        patterns = [
            r"supports\s+at\s+most\s+([0-9][0-9,]*)\s+(?:completion\s+)?tokens?",
            r"at\s+most\s+([0-9][0-9,]*)\s+(?:completion\s+)?tokens?",
            r"maximum(?:\s+allowed)?(?:\s+completion)?\s+tokens?(?:\s+is|\s*:)\s*([0-9][0-9,]*)",
            r"max[_\s-]?tokens?[^0-9]{0,80}(?:less\s+than\s+or\s+equal\s+to|no\s+more\s+than|cannot\s+exceed|must\s+not\s+exceed|must\s+be\s+<=|<=)\s*([0-9][0-9,]*)",
            r"(?:less\s+than\s+or\s+equal\s+to|no\s+more\s+than|cannot\s+exceed|must\s+not\s+exceed|must\s+be\s+<=|<=)\s*([0-9][0-9,]*)\s+(?:completion\s+)?tokens?",
            r"max[_\s-]?tokens?[^0-9]{0,80}(?:不能|不可|不得|不应)?(?:超过|大于)\s*([0-9][0-9,]*)",
            r"(?:不能|不可|不得|不应)?(?:超过|大于)\s*([0-9][0-9,]*)\s*(?:个)?(?:completion\s*)?tokens?",
            r"最多(?:支持|允许)?\s*([0-9][0-9,]*)\s*(?:个)?\s*(?:completion\s*)?tokens?",
        ]
        for pattern in patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if not match:
                continue
            raw = match.group(1).replace(",", "")
            try:
                value = int(raw)
            except ValueError:
                continue
            if value > 0:
                return value
        return None

    def _should_retry_with_lower_max_tokens(
        self,
        *,
        http_status: int,
        detail: str,
        fallback_used: bool,
        lower_max_tokens: int | None,
    ) -> bool:
        if not self.retry_with_lower_max_tokens_on_provider_limit or fallback_used:
            return False
        if self.max_tokens is None or lower_max_tokens is None:
            return False
        if http_status not in {400, 422}:
            return False
        if lower_max_tokens >= self.max_tokens:
            return False
        lowered = str(detail or "").lower()
        if "max_tokens" not in lowered and "max tokens" not in lowered and "completion tokens" not in lowered:
            return False
        return any(token in lowered for token in ["too large", "at most", "maximum", "invalid", "过大", "最多", "上限"])

    def _chat_completions_url(self) -> str:
        base = self.base_url.strip().split("?")[0].rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _extract_text(self, data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and message.get("content") is not None:
                    content = message.get("content")
                    if isinstance(content, list):
                        return "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
                    return str(content)
                if first.get("text") is not None:
                    return str(first.get("text"))
        # Tolerate a subset of Responses-style payloads in case a gateway maps
        # chat-completions to a newer response envelope.
        if data.get("output_text") is not None:
            return str(data.get("output_text"))
        output = data.get("output")
        if isinstance(output, list):
            chunks: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                for content in item.get("content") or []:
                    if isinstance(content, dict) and content.get("text") is not None:
                        chunks.append(str(content.get("text")))
            if chunks:
                return "".join(chunks)
        raise RuntimeError("runtime_lite LLM response did not contain text")

    def _response_metadata(self, data: dict[str, Any], result_text: str) -> dict[str, Any]:
        """Return safe envelope metadata useful for diagnosing blank outputs.

        The trajectory must never store full provider payloads, prompts, or raw
        errors.  These fields are non-sensitive structural hints that explain why
        a call was treated as successful or empty.
        """

        metadata: dict[str, Any] = {
            "response_text_empty": not str(result_text or "").strip(),
        }
        choices = data.get("choices")
        if isinstance(choices, list):
            metadata["choices_count"] = len(choices)
            if choices and isinstance(choices[0], dict):
                first = choices[0]
                if first.get("finish_reason") is not None:
                    metadata["finish_reason"] = str(first.get("finish_reason"))[:120]
                message = first.get("message") if isinstance(first.get("message"), dict) else {}
                if message:
                    metadata["message_content_present"] = message.get("content") is not None
                    metadata["message_role"] = str(message.get("role") or "")[:80]
        if data.get("id") is not None:
            metadata["response_id_present"] = True
        metadata.update(self._usage_metadata(data.get("usage")))
        return metadata

    def _usage_metadata(self, usage_payload: Any) -> dict[str, Any]:
        """Normalize provider token usage without inventing unavailable fields.

        Providers expose cache accounting under different names. CareLoop_2_2 keeps
        the legacy OpenAI chat-completions fields while adding provider-neutral
        input/output/cached/uncached fields. Unknown cached-token values remain
        absent/null rather than being estimated.
        """

        if not isinstance(usage_payload, dict):
            return {"provider_usage_available": False}

        usage = usage_payload
        metadata: dict[str, Any] = {"provider_usage_available": True}

        def nonnegative_int(value: Any) -> int | None:
            if isinstance(value, bool):
                return None
            if isinstance(value, int) and value >= 0:
                return value
            return None

        prompt_tokens = nonnegative_int(usage.get("prompt_tokens"))
        completion_tokens = nonnegative_int(usage.get("completion_tokens"))
        total_tokens = nonnegative_int(usage.get("total_tokens"))
        input_tokens = nonnegative_int(usage.get("input_tokens"))
        output_tokens = nonnegative_int(usage.get("output_tokens"))

        # Legacy fields retained for existing reports/tests.
        if prompt_tokens is not None:
            metadata["prompt_tokens"] = prompt_tokens
        if completion_tokens is not None:
            metadata["completion_tokens"] = completion_tokens
        if total_tokens is not None:
            metadata["total_tokens"] = total_tokens

        # Provider-neutral aliases.
        if input_tokens is None:
            input_tokens = prompt_tokens
        if output_tokens is None:
            output_tokens = completion_tokens
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        if input_tokens is not None:
            metadata["input_tokens"] = input_tokens
        if output_tokens is not None:
            metadata["output_tokens"] = output_tokens
        if total_tokens is not None:
            metadata["total_tokens"] = total_tokens

        cached_candidates: list[int] = []
        cache_read_candidates: list[int] = []
        cache_write_candidates: list[int] = []

        for key in ("cached_input_tokens", "cached_tokens", "cache_read_input_tokens"):
            value = nonnegative_int(usage.get(key))
            if value is not None:
                cached_candidates.append(value)
                cache_read_candidates.append(value)
        for key in ("cache_creation_input_tokens", "cache_write_input_tokens"):
            value = nonnegative_int(usage.get(key))
            if value is not None:
                cache_write_candidates.append(value)

        for details_key in ("prompt_tokens_details", "input_tokens_details"):
            details = usage.get(details_key)
            if isinstance(details, dict):
                value = nonnegative_int(details.get("cached_tokens"))
                if value is not None:
                    cached_candidates.append(value)
                    cache_read_candidates.append(value)

        cached_input_tokens = max(cached_candidates) if cached_candidates else None
        if cached_input_tokens is None:
            metadata["cached_input_tokens"] = None
            metadata["uncached_input_tokens"] = None
        else:
            metadata["cached_input_tokens"] = cached_input_tokens
            metadata["uncached_input_tokens"] = max(0, input_tokens - cached_input_tokens) if input_tokens is not None else None
        if cache_read_candidates:
            metadata["provider_cache_read_tokens"] = max(cache_read_candidates)
        if cache_write_candidates:
            metadata["provider_cache_write_tokens"] = max(cache_write_candidates)
        return metadata

    def _extract_stream_text(self, response: Any) -> str:
        chunks: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip() if isinstance(raw_line, bytes) else str(raw_line).strip()
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
                if delta.get("content") is not None:
                    chunks.append(str(delta.get("content") or ""))
                message = first.get("message") if isinstance(first.get("message"), dict) else {}
                if message.get("content") is not None:
                    chunks.append(str(message.get("content") or ""))
                if first.get("finish_reason") in {"stop", "STOP"}:
                    break
        if not "".join(chunks).strip():
            raise RuntimeError("runtime_lite streaming LLM response did not contain text")
        return "".join(chunks)

    def _redacted_base_url(self) -> str:
        return self.base_url.strip().split("?")[0]

    def _sanitize_prompt_for_provider(self, value: Any) -> str:
        """Remove personal identifiers that can trip provider safety filters.

        This is an infrastructure boundary, not a clinical-world rule.  CareLoop
        can realistically simulate that a patient has registered contact or
        billing identifiers, but the exact digits of a private phone number,
        identity card, or bank card are not medically meaningful for the tested
        doctor's reasoning.  Sending those raw digits to an external LLM gateway
        can cause mandatory nodes to fail before any clinical judgment happens.

        Keep the replacement semantically thin: preserve that the identifier was
        supplied/registered, but do not expose the actual digits to the provider.
        """

        text = str(value or "")
        if not text:
            return text

        # Mainland China mobile numbers, with optional +86 and light separators.
        # These were the concrete cause of the JD Cloud 400 failures seen in L06.
        text = re.sub(
            r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}(?!\d)",
            "[联系电话已登记，号码已脱敏]",
            text,
        )
        # Mainland China identity card numbers.  They are never needed for
        # clinical reasoning inside the LLM prompt.
        text = re.sub(
            r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?!\d)",
            "[身份证件号码已登记，号码已脱敏]",
            text,
        )
        # Bank/card-like long digit sequences only when nearby text explicitly
        # identifies them as payment/bank/insurance-card identifiers.  The narrow
        # prefix prevents redacting clinically meaningful numeric measurements.
        text = re.sub(
            r"(?i)(银行卡号|银行卡|卡号|医保卡号|社保卡号|bank\s*card|card\s*number)([^\n\r]{0,24}?)(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)",
            lambda match: f"{match.group(1)}{match.group(2)}[卡号已登记，号码已脱敏]",
            text,
        )
        return text

    def _sanitize_for_logs(self, value: Any) -> str:
        text = self._sanitize_prompt_for_provider(value)
        if self.api_key:
            text = text.replace(self.api_key, "[REDACTED_API_KEY]")
        text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
        text = re.sub(
            r"(?i)(api[_-]?key|access[_-]?token|token|key)=([^&\s]+)",
            lambda match: f"{match.group(1)}=[REDACTED]",
            text,
        )
        return text

    def _progress_enabled(self) -> bool:
        raw = os.environ.get("CARELOOP_LITE_PROGRESS", "")
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _emit_progress(
        self,
        event: str,
        *,
        purpose: str,
        attempt: int,
        max_attempts: int | str,
        status: str | None = None,
        elapsed_seconds: float | None = None,
        include_temperature: bool | None = None,
        timeout_seconds: float | None = None,
        http_status: int | None = None,
        error_type: str | None = None,
        retry_sleep_seconds: float | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "protocol": "careloop.llm_status.v1",
            "updated_at": self._utc_timestamp(),
            "phase": "llm_call",
            "event": event,
            "purpose": purpose,
            "model": self.model,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "stream": bool(self.stream),
            "max_tokens": self.max_tokens,
            "status": status or event,
            "safe_sidecar": True,
            "redaction_policy": "no_prompt_no_response_no_api_key_no_authorization_header",
        }
        if timeout_seconds is not None:
            payload["timeout_seconds"] = timeout_seconds
        if include_temperature is not None:
            payload["temperature_sent"] = bool(include_temperature)
        if elapsed_seconds is not None:
            payload["elapsed_seconds"] = elapsed_seconds
        if http_status is not None:
            payload["http_status"] = http_status
        if error_type:
            payload["error_type"] = error_type
        if retry_sleep_seconds is not None:
            payload["retry_sleep_seconds"] = retry_sleep_seconds
            payload["phase"] = "retry_sleep"
        self._write_status_sidecar(payload)

        if not self._progress_enabled():
            return
        parts = [
            "[runtime_lite.llm]",
            f"event={event}",
            f"purpose={purpose}",
            f"model={self.model}",
            f"attempt={attempt}/{max_attempts}",
            f"stream={str(self.stream).lower()}",
            f"max_tokens={self.max_tokens if self.max_tokens is not None else 'unset'}",
        ]
        if timeout_seconds is not None:
            parts.append(f"timeout_seconds={timeout_seconds:g}")
        if include_temperature is not None:
            parts.append(f"temperature_sent={str(include_temperature).lower()}")
        if status:
            parts.append(f"status={status}")
        if elapsed_seconds is not None:
            parts.append(f"elapsed={elapsed_seconds:.2f}s")
        if http_status is not None:
            parts.append(f"http_status={http_status}")
        if error_type:
            parts.append(f"error_type={error_type}")
        if retry_sleep_seconds is not None:
            parts.append(f"retry_sleep={retry_sleep_seconds:.1f}s")
        print(" ".join(parts), file=sys.stderr, flush=True)

    def _write_status_sidecar(self, payload: dict[str, Any]) -> None:
        status_dir = str(self.status_dir or "").strip()
        if not status_dir:
            return
        try:
            directory = Path(status_dir)
            directory.mkdir(parents=True, exist_ok=True)
            file_name = str(self.status_file_name or "llm_status.json").strip() or "llm_status.json"
            path = directory / file_name
            tmp_path = path.with_name(f".{path.name}.tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        except Exception:
            # Observability must never alter benchmark semantics or kill an LLM call.
            return

    def _utc_timestamp(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _rate_limit_retry_delay_seconds(self, exc: urllib.error.HTTPError, attempt: int) -> float:
        retry_after = self._retry_after_seconds(exc)
        if retry_after is None:
            return self._retry_delay_seconds()
        return max(self._retry_delay_seconds(), retry_after)

    def _retry_after_seconds(self, exc: urllib.error.HTTPError) -> float | None:
        try:
            raw = str(exc.headers.get("Retry-After") or "").strip()
        except Exception:
            raw = ""
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            return None

    def _sleep_before_retry(self, attempt: int, *, override_seconds: float | None = None) -> None:
        delay = max(0.0, float(override_seconds)) if override_seconds is not None else self._retry_delay_seconds()
        latest = self.calls[-1] if self.calls else {}
        if self.calls:
            self.calls[-1]["retry_sleep_seconds"] = delay
        self._emit_progress(
            "retry_sleep",
            purpose=str(latest.get("purpose") or "unknown"),
            attempt=attempt,
            max_attempts=latest.get("max_attempts") or ("unbounded" if self.retry_until_success else max(1, int(self.max_retries) + 1)),
            status=str(latest.get("status") or "retrying"),
            timeout_seconds=latest.get("timeout_seconds") if isinstance(latest.get("timeout_seconds"), (int, float)) else None,
            http_status=latest.get("http_status") if isinstance(latest.get("http_status"), int) else None,
            error_type=str(latest.get("error_type") or "") or None,
            retry_sleep_seconds=delay,
        )
        if delay > 0:
            time.sleep(delay)

    def _retry_delay_seconds(self) -> float:
        """Return the real-world wait before retrying a transient LLM failure.

        This is infrastructure time, not simulated clinical-world time.  The
        default 30-60 second jitter avoids hammering a busy API gateway when many
        long CareLoop cases run concurrently, while still preserving the formal
        rule that a required LLM node is retried until it returns a usable
        response.
        """

        minimum = max(0.0, float(self.retry_backoff_seconds))
        maximum = max(minimum, float(self.retry_max_delay_seconds))
        if maximum <= minimum:
            return minimum
        return random.uniform(minimum, maximum)


@dataclass
class ScriptedLiteLLMClient:
    """Deterministic no-network client for smoke tests.

    It is not a clinical-quality simulator.  It only lets the thin runtime chain
    run end-to-end before a real provider is attached.
    """

    replies: dict[str, list[str]] = field(default_factory=dict)
    calls: list[dict[str, str]] = field(default_factory=list)

    def complete(self, *, purpose: str, system: str, user: str, temperature: float = 0.4) -> str:
        self.calls.append({"purpose": purpose, "system": system, "user": user})
        queue = self.replies.get(purpose)
        if queue:
            return queue.pop(0)
        return self._fallback(purpose, user)

    def _payload(self, user: str) -> dict[str, Any]:
        try:
            parsed = json.loads(user)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _recent_transcript(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        direct = payload.get("recent_transcript")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        trajectory = payload.get("trajectory_so_far") if isinstance(payload.get("trajectory_so_far"), dict) else {}
        recent = trajectory.get("recent_transcript")
        if isinstance(recent, list):
            return [item for item in recent if isinstance(item, dict)]
        return []

    def _estimated_turn(self, payload: dict[str, Any]) -> int:
        turns: list[int] = []
        trajectory = payload.get("trajectory_so_far") if isinstance(payload.get("trajectory_so_far"), dict) else {}
        counts = trajectory.get("counts") if isinstance(trajectory.get("counts"), dict) else {}
        for item in self._recent_transcript(payload):
            try:
                turns.append(int(item.get("turn") or 0))
            except (TypeError, ValueError):
                pass
        try:
            event_count = int(counts.get("events") or 0)
        except (TypeError, ValueError):
            event_count = 0
        return max(turns) if turns else max(1, event_count // 8)

    def _actor_focus_from_payload(self, payload: dict[str, Any]) -> str:
        for key in ["actor_focus", "actor_id"]:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        context = payload.get("case_context_full_for_director") if isinstance(payload.get("case_context_full_for_director"), dict) else {}
        initial_chat = context.get("initial_chat") if isinstance(context.get("initial_chat"), dict) else {}
        speaker = str(initial_chat.get("speaker") or "").strip()
        return speaker or "patient"

    def _pending_receipts(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        care_state = payload.get("care_system_state") if isinstance(payload.get("care_system_state"), dict) else {}
        pending = care_state.get("pending_receipts")
        if isinstance(pending, list):
            return [item for item in pending if isinstance(item, dict)]
        recent = care_state.get("recent_receipts")
        if isinstance(recent, list):
            return [
                item
                for item in recent
                if isinstance(item, dict) and str(item.get("status") or "").startswith("registered")
            ]
        return []

    def _scripted_actor_identity(self, payload: dict[str, Any]) -> dict[str, str]:
        identity = {
            "actor_id": "patient",
            "actor_role": "patient",
            "display": "患者本人",
            "speaker_display": "患者本人",
            "speaker_category": "patient",
            "relationship_to_patient": "self",
        }
        actor_focus_identity = (
            payload.get("actor_focus_identity")
            if isinstance(payload.get("actor_focus_identity"), dict)
            else {}
        )
        actor_identity = payload.get("actor_identity") if isinstance(payload.get("actor_identity"), dict) else {}
        for source in [actor_focus_identity, actor_identity]:
            for key, value in source.items():
                if isinstance(value, str) and value.strip():
                    identity[key] = value.strip()
        if identity.get("display") and not identity.get("speaker_display"):
            identity["speaker_display"] = identity["display"]
        if identity.get("speaker_display") and not identity.get("display"):
            identity["display"] = identity["speaker_display"]
        return identity

    def _scripted_world_event_for_receipt(self, receipt: dict[str, Any], *, turn: int, actor_focus: str) -> dict[str, Any]:
        operation = str(receipt.get("operation") or "")
        receipt_id = str(receipt.get("receipt_id") or "")
        base_metadata: dict[str, Any] = {"care_system_updates": []}
        if receipt_id:
            base_metadata["care_system_updates"].append({"receipt_id": receipt_id, "action": "mark_completed"})
        else:
            base_metadata["care_system_updates"].append({"operation": operation, "match": "latest_pending", "action": "mark_completed"})

        if operation == "care_system.order_test":
            base_metadata["care_system_updates"] = [
                {
                    **({"receipt_id": receipt_id} if receipt_id else {"operation": operation, "match": "latest_pending"}),
                    "action": "mark_result_returned",
                    "result": {
                        "summary": "离线渐进 smoke：检查/检验结果已经回传，需要医生复核并决定下一步。",
                        "scripted_progression_fixture": True,
                    },
                }
            ]
            title = "检查结果回传"
            description = "患者按医生登记的检查安排完成了检查，系统里已有结果回传，等待医生解读。"
            doctor_visible = "检查/检验结果已回传，请复核并决定下一步。"
        elif operation == "care_system.track_result":
            base_metadata["care_system_updates"] = [
                {
                    **({"receipt_id": receipt_id} if receipt_id else {"operation": operation, "match": "latest_pending"}),
                    "action": "mark_result_returned",
                    "result": {
                        "summary": "离线渐进 smoke：被追踪的报告已经返回。",
                        "scripted_progression_fixture": True,
                    },
                }
            ]
            title = "追踪结果返回"
            description = "医生登记追踪的报告有了新回传，患者/家属看到系统提示后回来询问。"
            doctor_visible = "此前登记追踪的报告已经返回。"
        elif operation == "care_system.prescribe":
            base_metadata["care_system_updates"] = [
                {
                    **({"receipt_id": receipt_id} if receipt_id else {"operation": operation, "match": "latest_pending"}),
                    "action": "mark_patient_executed",
                }
            ]
            title = "患者开始执行用药方案"
            description = "患者拿到或开始按医生登记的用药方案执行，但仍需要确认疗效和不良反应。"
            doctor_visible = ""
        elif operation == "care_system.schedule_followup":
            base_metadata["care_system_updates"] = [
                {
                    **({"receipt_id": receipt_id} if receipt_id else {"operation": operation, "match": "latest_pending"}),
                    "action": "completed_followup",
                }
            ]
            title = "到达随访节点"
            description = "虚拟时间推进到医生安排的随访节点，患者/家属回来反馈当前执行情况。"
            doctor_visible = ""
        elif operation == "care_system.call_emergency":
            title = "急救升级被执行"
            description = "患者/家属按医生的急救升级建议开始联系急救或前往急诊，责任链转向线下急救系统。"
            doctor_visible = "患者/家属已经开始执行急救/急诊升级建议。"
        elif operation == "care_system.referral":
            title = "线下转诊开始执行"
            description = "患者/家属开始按转诊或线下就诊建议行动，正在进入线下医疗责任链。"
            doctor_visible = "患者/家属已经开始执行转诊/线下就诊安排。"
        else:
            title = "患者开始执行医生侧安排"
            description = "患者/家属开始执行医生登记的下一步安排，后续需要医生根据反馈继续处理。"
            doctor_visible = ""

        if doctor_visible:
            base_metadata["doctor_visible_summary"] = doctor_visible
        return {
            "event_id": f"scripted_receipt_progress_T{turn:03d}",
            "title": title,
            "description": description,
            "status": "committed",
            "visible_to_doctor": bool(doctor_visible),
            "affected_actors": [actor_focus or "patient"],
            "time_request": {
                "reason": "离线渐进 smoke 用合理时间推进到医生安排的真实世界后果。",
                "scene_change": "从线上解释推进到执行/结果/随访反馈节点。",
            },
            "metadata": base_metadata,
        }

    def _fallback(self, purpose: str, user: str) -> str:
        if purpose == "world_director":
            payload = self._payload(user)
            actor_focus = self._actor_focus_from_payload(payload)
            turn = self._estimated_turn(payload)
            pending_receipts = self._pending_receipts(payload)
            world_events: list[dict[str, Any]] = []
            next_world_beat = "让患者/家属根据医生建议补充信息或执行下一步。"
            actor_goal = "自然回应医生，说明自己现在最真实的困惑、能否执行建议，以及是否愿意调取资料。"
            if pending_receipts:
                event = self._scripted_world_event_for_receipt(pending_receipts[0], turn=turn, actor_focus=actor_focus)
                world_events.append(event)
                next_world_beat = event["description"]
                actor_goal = "把已经发生的执行、结果回流或随访节点告诉医生，并请医生决定下一步。"
            elif turn >= 2:
                generic_beats = [
                    (
                        "患者/家属整理旧报告",
                        "患者/家属开始翻找旧报告、药盒或外院截图，但资料还不完整，需要医生说明最关键要补哪一项。",
                        "告诉医生自己正在找资料，并说明目前缺哪一项或看不懂哪里。",
                    ),
                    (
                        "家属协助确认执行条件",
                        "患者/家属开始商量交通、陪同、费用或检查预约等现实执行条件，发现仍有一处卡点需要医生帮忙取舍。",
                        "告诉医生现实执行上还卡在哪里，请医生给最低安全可行方案。",
                    ),
                    (
                        "患者开始按建议行动",
                        "患者/家属已经开始按前一步建议行动或准备复查，同时担心过程中出现变化时该不该升级线下就医。",
                        "告诉医生已经开始行动，并确认哪些变化需要立刻改走线下急诊或复诊。",
                    ),
                ]
                title, description, actor_goal = generic_beats[(turn - 2) % len(generic_beats)]
                world_events.append(
                    {
                        "event_id": f"scripted_lived_progress_T{turn:03d}",
                        "title": title,
                        "description": description,
                        "status": "committed",
                        "visible_to_doctor": False,
                        "affected_actors": [actor_focus or "patient"],
                        "time_request": {
                            "reason": "离线渐进 smoke 避免多轮停留在同一句问答。",
                            "scene_change": "从初始求助推进到执行准备/资料整理状态。",
                        },
                        "metadata": {"scripted_progression_fixture": True},
                    }
                )
                next_world_beat = description
            return json.dumps(
                {
                    "scene_summary": "离线渐进 smoke：根据医生建议与现有 receipt 推动一个小的真实世界后果。",
                    "doctor_move_read": "读取医生刚才的回复后，判断是否需要推进场景或继续问诊。",
                    "next_world_beat": next_world_beat,
                    "world_events": world_events,
                    "actor_focus": actor_focus,
                    "actor_situation_goal": actor_goal,
                    "should_continue": True,
                    "living_state_update": {
                        "current_scene": "离线 smoke 的执行/反馈节点",
                        "open_threads": ["需要医生根据患者执行或结果反馈继续推进"],
                        "scripted_progression_fixture": True,
                    },
                    "director_rationale": "scripted fallback 不代表真实模拟质量；这里仅用于验证世界事件、时间、演员情境和 receipt 更新链路不会原地打转。",
                },
                ensure_ascii=False,
            )
        if purpose == "timekeeper":
            return '{"elapsed_minutes":5,"visible_time_phrase":"过了几分钟","rationale":"线上即时交流，仅有短暂思考和沟通时间。"}'
        if purpose == "actor_situation_messenger":
            payload = self._payload(user)
            identity = self._scripted_actor_identity(payload)
            actor_id = str(identity.get("actor_id") or "patient")
            actor_role = str(identity.get("actor_role") or actor_id)
            display = str(identity.get("speaker_display") or identity.get("display") or actor_id)
            category = str(identity.get("speaker_category") or "patient")
            relationship = str(identity.get("relationship_to_patient") or ("self" if category == "patient" else ""))
            lived_packet = payload.get("actor_lived_world_packet") if isinstance(payload.get("actor_lived_world_packet"), dict) else {}
            committed_events = [
                item for item in lived_packet.get("committed_lived_events") or [] if isinstance(item, dict)
            ]
            knows_now = ["医生刚回复了建议。"]
            scene = "仍在原来的求助场景中"
            immediate_goal = "继续向医生说明情况并问下一步怎么办"
            if committed_events:
                descriptions = [
                    str(item.get("description") or item.get("title") or "").strip()
                    for item in committed_events[:3]
                    if str(item.get("description") or item.get("title") or "").strip()
                ]
                knows_now = descriptions or ["刚刚发生了新的执行或结果反馈。"]
                scene = "已经推进到执行/结果/随访反馈节点"
                immediate_goal = "把刚发生的变化告诉医生，请医生复核并安排下一步。"
            return json.dumps(
                {
                    "actor_id": actor_id,
                    "actor_role": actor_role,
                    "speaker_display": display,
                    "speaker_category": category,
                    "relationship_to_patient": relationship,
                    "scene": scene,
                    "elapsed_time_visible": "过了几分钟",
                    "what_actor_knows_now": knows_now,
                    "what_actor_feels_now": ["担心", "但比刚才更知道下一步了"],
                    "practical_constraints": ["仍需要医生把后续处理说清楚", "可能需要继续调资料、复核结果或确认执行"],
                    "immediate_goal": immediate_goal,
                    "speaking_guidance": "自然、口语化，不要像病例汇报。",
                },
                ensure_ascii=False,
            )
        if purpose in {"patient_actor", "family_actor"}:
            payload = self._payload(user)
            situation = payload.get("actor_situation") if isinstance(payload.get("actor_situation"), dict) else {}
            knows = [str(item).strip() for item in situation.get("what_actor_knows_now") or [] if str(item).strip()]
            if knows:
                known = knows[0]
                if any(token in known for token in ["结果", "报告", "回传", "检查", "药敏", "耐药"]):
                    return f"医生，报告/结果这边有变化：{known} 你帮我看看是不是要改治疗或复查？"
                if any(token in known for token in ["随访", "执行", "行动", "复查"]):
                    return f"医生，我按前面说的往下做了：{known} 现在我想确认下一步怎么才算安全。"
                if any(token in known for token in ["交通", "陪同", "费用", "预约", "卡点"]):
                    return f"医生，现实执行上有点卡：{known} 如果不能完全按理想方案做，最低安全的一步是什么？"
                return f"医生，我这边有个进展：{known} 你帮我把下一步说具体一点好吗？"
            transcript = self._recent_transcript(payload)
            prior_actor_messages = [item for item in transcript if str(item.get("event_type") or "") in {"patient_message", "family_message"}]
            if len(prior_actor_messages) >= 2:
                return "医生，我按你说的开始整理资料/准备执行了，但还有点担心会不会耽误。你能不能帮我把接下来一两步说得更具体？"
            return "医生，我大概明白了，但我还有点拿不准。你能不能告诉我现在最要紧的一步是什么？如果要查以前的资料，我这边可以配合。"
        if purpose == "closure_judge":
            return (
                '{"status":"open","rationale":"目前只是初步交流，还没有形成可验证的长程照护闭环。",'
                '"evidence":[],"unresolved_threads":["需要继续问诊、资料调取或执行随访计划"],'
                '"if_continued_next_focus":"推动患者执行下一步并观察结果。"}'
            )
        if purpose == "probability_estimator":
            return (
                '{"probability":0.5,"descriptor":"moderate",'
                '"basis":"离线 smoke fallback：真实运行时应由 LLM 根据 case、轨迹、人物状态和现实事实估计。",'
                '"factors":[{"name":"fallback","effect":"neutral"}],'
                '"not_occurrence_decision":true}'
            )
        if purpose == "mainline_balance_judge":
            return (
                '{"status":"balanced","mainline_read":"scripted smoke：仍在验证运行链路",'
                '"friction_read":"未做真实摩擦判断","medical_value_of_recent_turns":"not_assessed",'
                '"action_recommendation":"continue_as_is","world_director_hint":"继续自然推进，不要机械闭环。",'
                '"evaluation_note":"scripted fallback 不评价真实主线平衡。","rationale":"no-network smoke fallback"}'
            )
        if purpose == "anti_retcon_world_state_guard":
            return '{"overall":"ok","event_reviews":[],"continuity_notes":[],"rationale":"scripted smoke fallback：不修改导演事件。"}'
        if purpose == "clinical_contingency_event_sampler":
            return '{"should_sample":false,"reason_if_none":"scripted smoke fallback：不生成随机临床事件。","candidate_events":[],"rationale":"no-network smoke fallback"}'
        if purpose == "diagnostic_service_simulator":
            return '{"service_read":"scripted smoke fallback：不额外推进检查服务。","candidate_events":[],"service_annotations":["no-network smoke fallback"],"rationale":"真实运行由 GPT-5.5 判断服务时间和结果回流。"}'
        if purpose == "actor_realism_degrader":
            return (
                '{"cooperation_tendency":"case_dependent","comprehension_reliability":"fluctuating",'
                '"execution_reliability":"fluctuating","trust_state":"cautious",'
                '"likely_distortions":["scripted smoke 不模拟真实错漏"],'
                '"actor_speaking_guidance":["自然口语化，不要像病历汇报"],'
                '"what_not_to_overdo":["不要默认完美执行"],"rationale":"no-network smoke fallback"}'
            )
        if purpose == "longitudinal_stability_horizon_planner":
            return (
                '{"horizon_name":"scripted smoke durable longitudinal management horizon", "minimum_followup_cycles":1,'
                '"cycle_interval_rationale":"真实运行时由 LLM 根据 case 定义。",'
                '"management_requirements":["至少一次后续反馈显示计划可执行或风险边界清楚"],'
                '"risk_boundary_requirements":["无未关闭高风险窗口"],'
                '"execution_requirements":["关键行动已执行或已有安全责任链"],'
                '"feedback_adjustment_reconfirmation_needed":["反馈→调整→再确认证据"],'
                '"must_not_have":["仍有未关闭高风险窗口","仅观察/转诊/支持治疗话术"],'
                '"evidence_needed":["随访、结果回流或执行反馈证据"],'
                '"equivalent_paths_allowed":["真实低风险治愈路径"],"rationale":"no-network smoke fallback"}'
            )
        if purpose == "stability_horizon_verifier":
            return (
                '{"terminal_durable_longitudinal_management_satisfied":false,'
                '"terminal_stable_management_satisfied":false,"evidence_satisfied":[], '
                '"remaining_requirements":["scripted smoke 不证明持久性长期管理闭环"],'
                '"recommended_closure_status":"soft_closed",'
                '"recommended_closure_kind":"milestone_closed_but_not_terminal",'
                '"premature_closure_risk":"high",'
                '"if_continued_next_focus":"继续真实随访验证。","rationale":"no-network smoke fallback"}'
            )
        if purpose == "clinical_memory_steward":
            return (
                '{"backstage_memory":{"case_now":"离线 smoke fallback：当前仍处于需要继续推进的照护过程。",'
                '"why_this_case_is_hard":["真实约束和执行情况仍需持续跟踪"],'
                '"next_high_value_information":["患者/家属下一步是否能执行医生建议"]},'
                '"active_clinical_problem_list":[{"problem":"当前主要医疗问题","status":"active_or_recent",'
                '"supporting_evidence":["scripted fallback has limited evidence"],'
                '"contradicting_or_missing_evidence":["需要真实 LLM 运行后细化"],'
                '"current_plan_or_treatment":"继续依据医生回复和患者执行反馈推进",'
                '"next_decision_point":"观察下一轮执行反馈","source_anchors":[]}],'
                '"action_responsibility_ledger":[],'
                '"real_world_constraint_model":[{"constraint":"执行可及性","state":"unknown",'
                '"stability":"unknown","doctor_adaptation":"not_assessed","source_anchors":[]}],'
                '"open_threads":[{"thread":"继续推进到可验证闭环","why_it_matters":"CareLoop_FCCT-1 关注真实执行闭环","source_anchors":[]}],'
                '"resolved_or_dormant_threads":[],"uncertainties":[{"uncertainty":"真实执行结果",'
                '"impact":"决定是否能闭环","source_anchors":[]}]}'
            )
        if purpose == "evaluation_contract_synthesizer":
            return (
                '{"evaluation_contract":{'
                '"contract_version":"careloop.evaluation_contract.synthesized.v1",'
                '"care_goal":"离线 smoke fallback：把患者从当前未解决的医疗困境推进到可验证的安全责任链或闭环。",'
                '"minimum_safe_closure":["识别核心风险或照护问题。","形成可执行下一步、责任转交、结果追踪或随访安排。","确认患者/家属理解关键安全网。"],'
                '"acceptable_closure_types":{"closed":"核心风险已被行动化并有执行/结果/安全交接证据。","soft_closed":"已有清晰责任链，但执行或结果证据尚未完全回流。","open":"仍缺核心信息、行动化、执行反馈或随访责任链。"},'
                '"false_closure_traps":["患者道谢。","症状暂时好转。","只有泛泛建议。","只有 pending receipt 而没有执行或责任链证据。"],'
                '"expected_closure_evidence":["医生获取并使用关键病史/资料。","医生把建议转化为现实可执行步骤。","轨迹中出现执行、结果回流、随访或安全交接证据。"],'
                '"must_not_miss":["case-specific hidden-but-discoverable red flags","medication/result/follow-up responsibilities"],'
                '"tool_use_expectations":["当 case 涉及病历、检查、用药、可及性或随访时，应使用医生工作区或 care-system receipt。"],'
                '"dynamic_reweighting_triggers":["患者拒绝或执行失败。","结果回流或异常。","系统/可及性障碍。","医生遗漏或错误安抚。"]},'
                '"synthesis_rationale":"scripted fallback 只保证不完整 case 在离线测试中也有后台评估参照。",'
                '"discoverability_and_actionability_notes":["not_assessed_in_scripted_smoke"],'
                '"limitations":["not_real_llm_simulation"]}'
            )
        if purpose == "trajectory_evaluator":
            return (
                '{"overall":"smoke_only_not_quality_evaluation",'
                '"evaluation_mode":"terminal_or_unsafe",'
                '"summary":"离线 scripted smoke 只能证明 runtime_lite 链路可运行，不能代表真实评测质量。",'
                '"simulation_validity":{"status":"limited","rationale":"scripted fallback 不模拟真实长程医疗剧情，只用于工程烟测。","limitations":["not_real_llm_simulation"]},'
                '"doctor_performance_dimensions":{'
                '"information_seeking":"not_assessed",'
                '"real_world_executability":"not_assessed",'
                '"longitudinal_closure":"not_assessed",'
                '"safety_and_adaptation":"not_assessed"},'
                '"prior_contract_weights":[{"dimension":"runtime_chain_integrity","relative_weight":"high",'
                '"source":"evaluation_weight_reconciler scripted fallback","reason":"离线 smoke 只验证链路完整性。"}],'
                '"trajectory_emergent_weights":[],'
                '"final_trajectory_specific_weights":[{"dimension":"runtime_chain_integrity","relative_weight":"high",'
                '"reason":"离线 smoke 不产生真实轨迹动态权重。","evidence":"evaluation_weight_reconciler scripted fallback"}],'
                '"trajectory_specific_weights":[{"dimension":"runtime_chain_integrity","relative_weight":"high","reason":"离线 smoke 只验证链路完整性。","evidence":"scripted client"}],'
                '"critical_notes":["需要接入真实被测医生和真实模拟 LLM 后再评价。"]}'
            )
        if purpose == "fragment_trajectory_evaluator":
            return (
                '{"evaluation_mode":"fragment_nonterminal",'
                '"overall":"insufficient_fragment",'
                '"summary":"离线 scripted smoke 的非闭环片段只验证 fragment evaluator 链路，不代表真实医疗质量。",'
                '"fragment_validity":{"status":"limited","reason":"scripted fallback 不模拟真实长程医疗剧情，只用于工程烟测。","limitations":["not_real_llm_simulation","nonterminal_fragment"]},'
                '"fragment_scope":{"start_state":"scripted smoke start","stop_state":"scripted smoke stop","terminal_closure_reached":false,"milestones_reached":[],"why_not_terminal":"scripted smoke 没有真实终局证据。"},'
                '"observed_doctor_capabilities":[],"observed_doctor_failures_or_risks":[],'
                '"doctor_performance_dimensions":{"information_seeking":"not_assessed","risk_triage":"not_assessed","real_world_executability":"not_assessed","longitudinal_followup":"not_assessed","communication_and_trust":"not_assessed","adaptation_to_noise":"not_assessed","discontinuity_takeover":"not_assessed","misunderstanding_and_wrong_execution_recovery":"not_assessed","concealment_and_sensitive_history_recovery":"not_assessed"},'
                '"prior_contract_weights":[{"dimension":"runtime_chain_integrity","relative_weight":"high","source":"scripted fallback","reason":"离线 smoke 只验证链路完整性。"}],'
                '"trajectory_emergent_weights":[],"final_trajectory_specific_weights":[{"dimension":"fragment_evaluator_routing","relative_weight":"high","reason":"确认非终局轨迹走片段评估链。","evidence":"scripted client"}],'
                '"trajectory_specific_weights":[{"dimension":"fragment_evaluator_routing","relative_weight":"high","reason":"确认非终局轨迹走片段评估链。","evidence":"scripted client"}],'
                '"responsibility_attribution":{"doctor_responsibility":[],"patient_or_family_factors":[],"system_or_framework_factors":["scripted smoke"]},'
                '"administrative_drift_assessment":{"status":"none","reason":"scripted smoke 不评价行政偏航。","medical_thread_to_restore":"not_assessed"},'
                '"not_yet_tested":["real clinical simulation","terminal closure"],"missed_opportunities":[],"critical_failures":[],"evidence":["scripted client"],'
                '"evaluation_claim_strength":"low","if_continued_next_focus":"接入真实 LLM 后再评价。","next_test_recommendation":"用真实 API 运行后检查片段评估质量。"}'
            )
        if purpose == "evaluation_repair_evaluator":
            return (
                '{"evaluation_mode":"fragment_nonterminal",'
                '"overall":"insufficient_fragment",'
                '"summary":"离线 scripted repair fallback 只证明评估修复链路可运行，不代表真实医疗质量。",'
                '"simulation_validity":{"status":"limited","rationale":"scripted fallback 不模拟真实长程医疗剧情。","limitations":["not_real_llm_simulation","repair_fallback"]},'
                '"clinical_agency":{"level":"not_yet_tested","rationale":"离线 smoke 无法评价真实医生贡献。","key_evidence":[]},'
                '"doctor_contribution_to_progress":"contaminated",'
                '"doctor_performance_dimensions":{"information_seeking":"not_assessed","risk_triage":"not_assessed","diagnostic_reasoning_under_uncertainty":"not_assessed","test_selection_and_result_interpretation":"not_assessed","treatment_decision_and_response_tracking":"not_assessed","real_world_executability":"not_assessed","cross_institution_continuity":"not_assessed"},'
                '"responsibility_attribution":{"doctor_responsibility":[],"external_clinician_or_health_system_contribution":[],"patient_or_family_factors":[],"system_or_framework_factors":["scripted repair fallback"]},'
                '"not_yet_tested":["real clinical simulation","terminal closure"],"missed_opportunities":[],"critical_failures":[],"evidence":["scripted client"],'
                '"evaluation_claim_strength":"low",'
                '"repair_metadata":{"repair_used":true,"main_evaluator_failed":true,"repair_limitations":["scripted_fallback","compact_context_only"]}}'
            )
        if purpose == "evaluation_weight_reconciler":
            return (
                '{"prior_contract_weights":[{"dimension":"runtime_chain_integrity","relative_weight":"high",'
                '"source":"scripted fallback","reason":"离线 smoke 只验证链路完整性。"}],'
                '"trajectory_emergent_weights":[],'
                '"final_trajectory_specific_weights":[{"dimension":"runtime_chain_integrity","relative_weight":"high",'
                '"reason":"离线 smoke 不产生真实轨迹动态权重。","evidence":"scripted client"}],'
                '"reweighting_rationale":"scripted fallback 不代表真实评估，只保留权重协调链路。",'
                '"discoverability_and_actionability_notes":["not_assessed_in_scripted_smoke"],'
                '"responsibility_attribution_notes":["not_assessed_in_scripted_smoke"],'
                '"limitations":["not_real_llm_simulation"]}'
            )
        if purpose == "doctor_operation_router":
            try:
                payload = json.loads(user)
                message = str(payload.get("latest_doctor_message") or "")
            except json.JSONDecodeError:
                message = user
            lowered = message.lower()
            requests: list[dict[str, object]] = []
            if any(token in message for token in ["原始对话", "历史对话", "聊天记录", "第20", "第 20", "回看"]):
                requests.append({
                    "operation": "conversation_history.query",
                    "panels": [],
                    "reason": "医生回复中出现回看原始对话/历史聊天意图",
                    "parameters": {},
                    "confidence": "medium",
                    "patient_visible": False,
                })
            if "工作备注" in message or "医生备注" in message:
                if any(token in message for token in ["保存", "记录", "记一下", "备注：", "备注:"]):
                    note_text = message.split("：", 1)[-1].split(":", 1)[-1].strip() if ("：" in message or ":" in message) else message
                    requests.append({
                        "operation": "doctor_memory.update",
                        "panels": [],
                        "reason": "医生回复中出现保存医生工作备注意图",
                        "parameters": {"note_text": note_text},
                        "confidence": "medium",
                        "patient_visible": False,
                    })
                elif any(token in message for token in ["查看", "回看", "调取", "打开"]):
                    requests.append({
                        "operation": "doctor_memory.query",
                        "panels": [],
                        "reason": "医生回复中出现查看医生工作备注意图",
                        "parameters": {},
                        "confidence": "medium",
                        "patient_visible": False,
                    })
            if any(token in message for token in ["系统状态", "回执状态", "之前登记", "已登记"]):
                requests.append({
                    "operation": "care_system.query_status",
                    "panels": [],
                    "reason": "医生回复中出现查询医生侧系统状态意图",
                    "parameters": {"include_pending": True},
                    "confidence": "medium",
                    "patient_visible": False,
                })
            panels: list[str] = []
            if any(token in message for token in ["调取", "查看", "查询", "病历", "既往", "病案", "就诊记录"]) or "record" in lowered:
                panels.append("records")
            if any(token in message for token in ["检查", "检验", "结果", "报告"]) or "result" in lowered:
                panels.append("test_results")
                panels.append("documents")
            if any(token in message for token in ["用药", "处方", "药"]) or "medication" in lowered:
                panels.append("medications")
            if any(token in message for token in ["可及", "交通", "医院", "距离"]):
                panels.append("care_access")
            if panels:
                deduped = []
                for panel in panels:
                    if panel not in deduped:
                        deduped.append(panel)
                requests.append({
                    "operation": "clinical_workspace.query",
                    "panels": deduped,
                    "reason": "医生回复中出现调阅/查看资料意图",
                    "parameters": {},
                    "confidence": "medium",
                    "patient_visible": False,
                })
            if any(token in message for token in ["开检查", "开个检查", "查一下", "复查", "化验", "尿培养", "血常规", "CT", "超声"]):
                requests.append({
                    "operation": "care_system.order_test",
                    "panels": [],
                    "reason": "医生回复中出现开具或登记检查/复查意图",
                    "parameters": {"test_name": "从医生自然语言中推断的检查/复查"},
                    "confidence": "low",
                    "patient_visible": True,
                })
            if any(token in message for token in ["开药", "处方", "先吃", "服用", "调整剂量"]):
                requests.append({
                    "operation": "care_system.prescribe",
                    "panels": [],
                    "reason": "医生回复中出现处方或用药调整意图",
                    "parameters": {"medication": "从医生自然语言中推断的药物/剂量"},
                    "confidence": "low",
                    "patient_visible": True,
                })
            if any(token in message for token in ["随访", "复诊", "复查时间", "几天后", "一周后"]):
                requests.append({
                    "operation": "care_system.schedule_followup",
                    "panels": [],
                    "reason": "医生回复中出现随访/复诊安排意图",
                    "parameters": {},
                    "confidence": "low",
                    "patient_visible": True,
                })
            if any(token in message for token in ["结果追踪", "结果跟踪", "复核提醒", "报告回传", "结果回访"]):
                requests.append({
                    "operation": "care_system.track_result",
                    "panels": [],
                    "reason": "医生回复中出现结果追踪/复核提醒意图",
                    "parameters": {"target": "从医生自然语言中推断的待追踪结果"},
                    "confidence": "low",
                    "patient_visible": True,
                })
            if not requests:
                return '{"requests":[]}'
            return json.dumps({"requests": requests}, ensure_ascii=False)
        if purpose == "patient_facing_boundary_rewriter":
            payload = self._payload(user)
            draft = str(payload.get("patient_visible_draft") or "")
            return json.dumps({"status": "pass", "issue_type": "none", "content": draft, "rationale": "scripted fallback"}, ensure_ascii=False)
        if purpose == "actor_cooperation_evaluator":
            return json.dumps(
                {
                    "cooperation_band": "moderate",
                    "ranges": {
                        "willingness": [0.45, 0.8],
                        "comprehension": [0.35, 0.7],
                        "execution": [0.3, 0.7],
                        "disclosure": [0.35, 0.75],
                        "reporting_structure": [0.15, 0.55],
                        "fatigue": [0.2, 0.65],
                    },
                    "rationale": "scripted fallback range for smoke tests",
                    "notes_for_actor": "生活化、局部、不要像完整病历汇报。",
                },
                ensure_ascii=False,
            )
        if purpose == "doctor_self_context":
            payload = self._payload(user)
            latest_actor = str(payload.get("latest_patient_or_family_message") or "")
            current_reply = str(payload.get("current_turn_doctor_reply") or "")
            return json.dumps(
                {
                    "one_sentence_takeover": "离线 scripted 医生自我上下文：患者仍在当前医疗问题的长程跟进中。",
                    "active_problem_list": ["当前主诉/执行反馈仍需继续澄清"],
                    "key_facts_i_should_not_forget": [latest_actor[:160] or "需要根据下一轮患者反馈补充关键事实"],
                    "pending_actions_or_results": ["确认患者是否按建议执行、是否有结果回流或危险信号"],
                    "medications_and_treatment_state": [],
                    "real_world_execution_model": ["scripted fallback 无法真实评估执行障碍"],
                    "uncertainties_to_verify": ["症状变化、报告/资料、执行情况"],
                    "next_time_focus": "根据患者下一轮反馈判断是否需要调资料、调整方案或线下升级。",
                    "compression_notes": "scripted fallback only; real runs use the tested doctor model's own self-compression.",
                    "self_context_metadata": {"scripted_fallback": True, "current_reply_preview": current_reply[:160]},
                },
                ensure_ascii=False,
            )
        if purpose == "doctor_after_tool":
            payload = self._payload(user)
            transcript = self._recent_transcript(payload)
            doctor_messages = [item for item in transcript if str(item.get("speaker") or "") == "doctor"]
            doctor_message_count = len(doctor_messages)
            latest_actor = str(payload.get("latest_patient_or_family_message") or "")
            care_state = payload.get("care_system_state") if isinstance(payload.get("care_system_state"), dict) else {}
            pending = care_state.get("pending_receipts") if isinstance(care_state.get("pending_receipts"), list) else []
            tool_briefing = str(payload.get("tool_result_briefing") or "")
            estimated_turn = self._estimated_turn(payload)
            latest_actor_has_result = any(token in latest_actor for token in ["结果", "回传", "报告", "药敏", "耐药", "异常值"])
            latest_actor_has_execution = any(token in latest_actor for token in ["执行", "行动", "复查", "随访", "按前面说"])
            latest_actor_has_barrier = any(token in latest_actor for token in ["卡", "交通", "陪同", "费用", "预约", "不能完全"])
            if latest_actor_has_result and doctor_message_count == 0:
                return (
                    "我看到了你反馈的新结果/新进展。现在先别因为症状好转就停在这里：我需要结合报告内容判断是否要调整治疗、补做检查或安排线下复诊。"
                    "你先告诉我现在还有没有尿痛、发热、腰痛、恶心，报告里有没有写药敏、耐药或异常标红。"
                )
            if latest_actor_has_result and doctor_message_count == 1:
                return (
                    "这次重点要把“结果”变成可执行处理。请你把报告里的关键结论、异常值和药敏/耐药信息发全；"
                    "如果提示感染控制不好、耐药或症状加重，我会建议调整治疗并安排复查/随访，而不是只让你继续观察。"
                )
            if latest_actor_has_result:
                if estimated_turn % 3 == 1:
                    return (
                        "这次不能只停在“结果出来了”。请把异常项、药敏/耐药结论和现在症状对上号；"
                        "如果结论提示控制不好或症状反跳，就需要把治疗方案和复核时间同步调整。"
                    )
                if estimated_turn % 3 == 2:
                    return (
                        "我会把这份结果当成下一步决策依据，而不是单纯让你继续观察。"
                        "你现在重点反馈体温、腰痛、尿痛和进水情况；一旦出现全身不适或不能进水，就线下处理。"
                    )
                return (
                    "我们现在进入结果后的跟进阶段：请确认你是否已经开始按调整后的方案执行、有没有不良反应，以及下一次复查/随访能不能按时完成。"
                    "如果执行中出现发热、腰痛、呕吐、明显乏力或症状反跳，要把线上计划升级为线下就医。"
                )
            if latest_actor_has_barrier:
                return (
                    "如果现实执行有卡点，我们先定最低安全方案：优先保证能及时复查/线下评估，并把报告、药名和症状变化留痕发来。"
                    "如果暂时做不到完整方案，你告诉我具体卡在交通、费用还是预约，我再帮你分出必须今天做和可以延后的部分。"
                )
            if latest_actor_has_execution:
                if estimated_turn % 2 == 1:
                    return (
                        "既然已经有执行进展，下一步要抓住两个闭环点：实际完成了什么、还有什么结果没有回流。"
                        "你把完成时间、当前症状和能否继续按计划反馈说清楚，我再决定是否升级处理。"
                    )
                return (
                    "既然已经开始往下执行，下一步不是重复原来的建议，而是确认三个节点：是否完成复查/上传、症状有没有反跳、是否能按约定随访。"
                    "只要出现发热、腰痛、呕吐、尿痛明显加重或不能进水，就不要等随访，直接线下处理。"
                )
            if pending:
                return (
                    "我这边看到已经有登记的随访或结果追踪任务，下一步关键不是重复问诊，而是确认执行和结果回流。"
                    "你现在先按安排去完成/上传结果；如果期间症状加重、发热、腰背痛或明显不舒服，要直接线下就医。"
                )
            if len(doctor_messages) == 0:
                return (
                    "我先看了一下目前能调到的资料。现在最要紧的是把这次问题相关的报告原图、最近用药/过敏史、症状开始时间和是否加重说清楚。"
                    "如果出现明显加重、发热不退、胸闷气短、意识异常、剧烈疼痛、出血、不能进食饮水，不要等线上回复，直接去急诊或呼叫急救。"
                )
            if "药" in tool_briefing or "medication" in tool_briefing.lower():
                return (
                    "我已经先看了能查到的用药/资料线索。下一步请你核对药名、剂量、什么时候开始吃、有没有过敏或不舒服；"
                    "如果报告或症状提示需要调整方案，我会把原因和后续复查说清楚。"
                )
            return (
                "我已经看过目前能调到的资料，但这还不等于问题解决。"
                "下一步请你把报告原图或关键数值补齐，并确认能否按建议完成检查/随访；我会根据回传结果决定是否调整治疗或转线下处理。"
            )
        if purpose == "doctor_tool_chain_convergence":
            return (
                "我先根据目前已经看到的资料给你一个可执行的下一步。现在不要把这次线上沟通停在继续调资料上："
                "你先按我已经明确的安全建议执行，并把还没有回流的报告或检查结果后续补发给我；如果出现明显加重、胸闷气短、意识异常、"
                "持续高热、剧烈疼痛、出血或不能进食饮水，不要等线上回复，直接去急诊或呼叫急救。"
            )
        if purpose == "doctor":
            payload = self._payload(user)
            envelope_mode = isinstance(payload.get("doctor_output_protocol"), dict)
            workspace_results = payload.get("workspace_results_this_turn") if isinstance(payload.get("workspace_results_this_turn"), list) else []
            doctor_messages = [
                item for item in self._recent_transcript(payload) if str(item.get("speaker") or "") == "doctor"
            ]
            if envelope_mode and not workspace_results and len(doctor_messages) == 0:
                return json.dumps({"to": "workspace", "content": "请先调取当前可见的既往病历、最近检查结果、用药记录和照护可及性信息。"}, ensure_ascii=False)
            if envelope_mode and workspace_results:
                return json.dumps({"to": "care", "content": "我回顾了一下目前能看到的资料。现在先把这次问题相关的报告原图、最近实际用药、症状开始时间和有没有加重说清楚；如果出现明显加重、胸闷气短、意识异常、持续高热、剧烈疼痛、出血或不能进食饮水，不要等线上回复，直接去急诊或呼叫急救。"}, ensure_ascii=False)
            care_state = payload.get("recent_care_system_state") if isinstance(payload.get("recent_care_system_state"), dict) else {}
            pending = care_state.get("pending_receipts") if isinstance(care_state.get("pending_receipts"), list) else []
            latest_actor = str(payload.get("latest_patient_or_family_message") or "")
            if pending:
                return "我看到刚才登记的安排还在推进中。现在请你把最新执行情况或结果发给我；如果出现加重、意识异常、胸闷气短、持续高热或明显疼痛，不要等线上回复，直接急诊/呼叫急救。"
            if len(doctor_messages) == 0:
                return "我先了解一下：症状从什么时候开始？有没有发热、呕吐、胸闷气短、意识异常或明显加重？另外我建议先调取你既往就诊记录和最近检查结果，看看有没有关键线索。"
            if len(doctor_messages) == 1:
                return (
                    "谢谢你补充。为了避免结果或执行断掉，我这边给你安排24小时后随访，并登记一个检查/报告回传后的复核提醒。"
                    "你现在先按最安全的一步执行：能上传报告就先上传，症状明显加重或有危险信号就直接去急诊。"
                )
            if "新情况" in latest_actor or "结果" in latest_actor or "回传" in latest_actor:
                return "我看到了你说的新变化。下一步我会先复核这个结果/执行反馈，再决定是否需要调整用药、补做检查或转线下；你先告诉我现在症状有没有比刚才加重。"
            return "现在我们不要原地重复了：请你确认三件事——是否已经按建议行动、有没有拿到或上传结果、现在症状有没有加重。我会根据这三点决定继续观察、调整方案还是转急诊。"
        return ""
