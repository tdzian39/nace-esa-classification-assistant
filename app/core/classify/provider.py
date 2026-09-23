"""Model providers. The adapter boundary, so the classifier never knows whose API it is.

Three implementations:

* :class:`StubLlmProvider` - canned answers. The whole classifier is testable through it,
  which is why the model call could be built and verified before any key existed.
* :class:`OpenAiProvider` - Chat Completions with a strict JSON schema, over ``httpx``.
  The official SDK is deliberately not a dependency: one HTTP call against a documented
  shape is less to keep up to date than a client library, and ``llm_base_url`` then points
  at any OpenAI-compatible endpoint.
* :class:`NullLlmProvider` - configured off; makes the classifier abstain rather than fail.

``llm_provider`` picks one. CLAUDE.md requires the provider and model to be configurable and
the cheapest one to be the default, measured against ``/tests/golden`` before changing.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from config.settings import Settings
from core.classify.errors import (
    LlmNotConfiguredError,
    LlmResponseError,
    LlmUnavailableError,
)
from core.classify.prompts import Prompt

LOGGER = logging.getLogger(__name__)

#: Longest wait for a TCP/TLS connection, whatever LLM_TIMEOUT_SECONDS says: a model that
#: is slow to answer is normal, an endpoint that is slow to accept a connection is down.
CONNECT_TIMEOUT_SECONDS = 5.0


def _backoff_seconds(attempt: int) -> float:
    """Pause after failed attempt ``attempt`` (1-based) before the next one."""
    return min(2.0 * attempt, 10.0)


def worst_case_call_seconds(settings: Settings) -> float:
    """The longest one model call can take, every attempt timing out.

    Each attempt waits at most the connect timeout plus LLM_TIMEOUT_SECONDS for the answer,
    and the attempts are separated by the backoff. The classifier compares this with the
    time a lookup has left, so a call that could outlive Vercel's maxDuration is never
    started (``LOOKUP_DEADLINE_SECONDS``).
    """
    attempts = max(1, settings.llm_max_attempts)
    timeout = settings.llm_timeout_seconds
    per_attempt = min(CONNECT_TIMEOUT_SECONDS, timeout) + timeout
    return attempts * per_attempt + sum(_backoff_seconds(a) for a in range(1, attempts))


@dataclass(frozen=True, slots=True)
class LlmResponse:
    """What a provider returns: the raw JSON text, and what it cost."""

    content: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    def parsed(self) -> dict[str, Any]:
        """The content as an object.

        Raises:
            LlmResponseError: the body is not JSON, or is not an object.
        """
        try:
            payload = json.loads(self.content)
        except (TypeError, ValueError) as exc:
            raise LlmResponseError(f"model did not return JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise LlmResponseError("model returned JSON that is not an object")
        return dict(payload)


class LlmProvider(Protocol):
    """What the classifier needs from a model."""

    name: str
    model: str

    def complete(self, prompt: Prompt) -> LlmResponse:
        """Send ``prompt`` and return the structured answer."""
        ...


class NullLlmProvider:
    """No model configured. The classifier abstains instead of guessing."""

    name = "none"
    model = "none"

    def complete(self, prompt: Prompt) -> LlmResponse:
        raise LlmNotConfiguredError(
            "no LLM configured (set LLM_API_KEY in app/.env, or LLM_ENABLED=false)"
        )


class StubLlmProvider:
    """Canned answers, keyed by codebook kind or returned in order.

    Lets every branch of the classifier - a good answer, an abstention, an invented code, a
    malformed body - be tested without a key or a network.
    """

    name = "stub"

    def __init__(
        self,
        responses: Mapping[str, str] | Sequence[str] | str,
        *,
        model: str = "stub-model",
        prompt_tokens: int | None = 1000,
        completion_tokens: int | None = 80,
    ) -> None:
        self.model = model
        self._responses = responses
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self.calls: list[Prompt] = []

    def complete(self, prompt: Prompt) -> LlmResponse:
        self.calls.append(prompt)
        if isinstance(self._responses, str):
            content = self._responses
        elif isinstance(self._responses, Mapping):
            content = self._responses.get(
                prompt.kind, '{"sufficient_evidence": false, "picks": []}'
            )
        else:
            index = min(len(self.calls) - 1, len(self._responses) - 1)
            content = self._responses[index]
        return LlmResponse(
            content=content,
            model=self.model,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )


def _usage(payload: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Read token counts, tolerating both namings.

    Chat Completions reports ``prompt_tokens``/``completion_tokens``; the Responses API and
    parts of the docs use ``input_tokens``/``output_tokens``. Accept either rather than
    silently reporting no cost.
    """
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None, None

    def pick(*names: str) -> int | None:
        for name in names:
            value = usage.get(name)
            if isinstance(value, int):
                return value
        return None

    return pick("prompt_tokens", "input_tokens"), pick("completion_tokens", "output_tokens")


class OpenAiProvider:
    """Chat Completions with a strict JSON schema, spoken over plain HTTP.

    Request shape verified against the structured-outputs guide on 2026-09-22 and again on
    2026-09-23 (developers.openai.com): ``response_format = {"type": "json_schema",
    "json_schema": {"name", "strict", "schema"}}``, every object ``additionalProperties:
    false`` with every property required, ``enum`` and ``description`` supported, and array
    ``maxItems`` supported (except on fine-tuned models) - which
    :func:`~core.classify.prompts.response_schema` produces.

    ``reasoning_effort`` (``LLM_REASONING_EFFORT``) is sent when set. OpenAI's reasoning
    models (the GPT-5.x families, including the default ``gpt-5.6-luna``) default to
    ``medium`` and reject ``temperature`` unless the effort is ``none`` (latest-model guide,
    2026-09-23), so ``temperature`` goes out only with no effort or ``none``. Leave the
    effort empty for a model or gateway that does not know the parameter.
    """

    name = "openai"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self.model = settings.llm_model
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            if self._settings.llm_api_key is None:
                raise LlmNotConfiguredError("LLM_API_KEY is not set; put it in app/.env")
            timeout = self._settings.llm_timeout_seconds
            self._client = httpx.Client(
                base_url=self._settings.llm_base_url.rstrip("/"),
                timeout=httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT_SECONDS, timeout)),
                headers={
                    "Authorization": f"Bearer {self._settings.llm_api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def _body(self, prompt: Prompt) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            # A ceiling on output: three picks with one Czech sentence each is a few hundred
            # tokens. Without it a model that starts rambling is billed for the rambling.
            "max_completion_tokens": self._settings.llm_max_output_tokens,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{prompt.kind.lower()}_suggestion",
                    "strict": True,
                    "schema": prompt.schema,
                },
            },
        }
        effort = self._settings.llm_reasoning_effort
        if effort:
            body["reasoning_effort"] = effort
        if not effort or effort == "none":
            body["temperature"] = self._settings.llm_temperature
        return body

    def complete(self, prompt: Prompt) -> LlmResponse:
        client = self._ensure_client()
        attempts = max(1, self._settings.llm_max_attempts)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = client.post("/chat/completions", json=self._body(prompt))
            except httpx.TimeoutException as exc:
                last_error = LlmUnavailableError(f"model call timed out: {exc}")
            except httpx.HTTPError as exc:
                last_error = LlmUnavailableError(f"model is unreachable: {exc}")
            else:
                if response.status_code in (429,) or response.status_code >= 500:
                    # Rate limiting and 5xx are transient; a 4xx is a defect in the request
                    # and would fail identically however often it is repeated.
                    last_error = LlmUnavailableError(f"model returned HTTP {response.status_code}")
                elif response.status_code >= 400:
                    raise LlmResponseError(
                        f"model rejected the request with HTTP {response.status_code}: "
                        f"{response.text[:300]}"
                    )
                else:
                    return self._read(response)
            if attempt < attempts:
                LOGGER.debug("model attempt %d/%d failed, retrying", attempt, attempts)
                self._sleep(_backoff_seconds(attempt))

        raise last_error or LlmUnavailableError("model could not be reached")

    def _read(self, response: httpx.Response) -> LlmResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise LlmResponseError(f"model returned a non-JSON body: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise LlmResponseError("model returned a body that is not an object")

        choices = payload.get("choices")
        if not isinstance(choices, Sequence) or not choices:
            raise LlmResponseError("model response has no choices")
        message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str) or not content.strip():
            # A refusal is returned in its own field rather than as content.
            refusal = message.get("refusal") if isinstance(message, Mapping) else None
            if isinstance(refusal, str) and refusal.strip():
                raise LlmResponseError(f"model refused to answer: {refusal[:200]}")
            raise LlmResponseError("model response has no content")

        prompt_tokens, completion_tokens = _usage(payload)
        return LlmResponse(
            content=content,
            model=str(payload.get("model") or self.model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


def build_provider(settings: Settings, *, client: httpx.Client | None = None) -> LlmProvider:
    """The provider named by ``llm_provider``, or a null one when nothing is configured."""
    if not settings.llm_enabled:
        return NullLlmProvider()
    provider = (settings.llm_provider or "").strip().lower()
    if provider in {"", "none", "null", "stub"}:
        return NullLlmProvider()
    if provider == "openai":
        if settings.llm_api_key is None and client is None:
            return NullLlmProvider()
        return OpenAiProvider(settings, client=client)
    LOGGER.warning("unknown LLM_PROVIDER %r; the classifier will abstain", settings.llm_provider)
    return NullLlmProvider()
