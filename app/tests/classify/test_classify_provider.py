"""The OpenAI adapter and the cache. No key, no network: MockTransport and tmp files."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from config.settings import Settings
from core.classify.cache import NullCache, SqliteCache, build_cache, cache_key
from core.classify.errors import (
    LlmNotConfiguredError,
    LlmResponseError,
    LlmUnavailableError,
)
from core.classify.models import NACE, Classification, Suggestion
from core.classify.prompts import build_prompt
from core.classify.provider import (
    NullLlmProvider,
    OpenAiProvider,
    StubLlmProvider,
    build_provider,
)
from tests.classify.test_classify_llm import ranked_set

ANSWER = json.dumps({"sufficient_evidence": True, "picks": []})


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "llm_enabled": True,
        "llm_provider": "openai",
        "llm_model": "test-model",
        "llm_api_key": "sk-test",
        "llm_max_attempts": 1,
        "llm_cache_path": None,
    }
    base.update(overrides)
    return Settings(**base)


def prompt():
    return build_prompt(ranked_set(), issuer_name="Nordkap", description="a captive lender")


def chat_response(content: str = ANSWER, **extra: object) -> dict:
    return {
        "model": "test-model",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        **extra,
    }


def provider_with(handler, **overrides) -> OpenAiProvider:
    return OpenAiProvider(
        settings(**overrides),
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test/v1"),
        sleep=lambda _: None,
    )


class TestRequestShape:
    def test_the_schema_is_sent_in_strict_mode(self) -> None:
        """Strict mode is what makes the code enum binding rather than advisory."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=chat_response())

        provider_with(handler).complete(prompt())
        fmt = seen["response_format"]
        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["strict"] is True
        assert fmt["json_schema"]["schema"]["additionalProperties"] is False

    def test_temperature_zero_is_sent_for_reproducibility(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=chat_response())

        provider_with(handler).complete(prompt())
        assert seen["temperature"] == 0.0
        assert seen["model"] == "test-model"

    def test_the_default_effort_none_is_sent_and_keeps_temperature(self) -> None:
        """The default model is a reasoning model; 'none' is the one effort that allows
        temperature 0 and spends no output tokens on reasoning."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=chat_response())

        provider_with(handler).complete(prompt())
        assert seen["reasoning_effort"] == "none"
        assert seen["temperature"] == 0.0
        assert seen["max_completion_tokens"] == 700

    def test_a_real_effort_drops_temperature(self) -> None:
        """OpenAI rejects temperature on a reasoning model unless the effort is 'none'."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=chat_response())

        provider_with(handler, llm_reasoning_effort="low").complete(prompt())
        assert seen["reasoning_effort"] == "low"
        assert "temperature" not in seen

    @pytest.mark.parametrize("blank", ["", "  "])
    def test_an_empty_effort_is_not_sent(self, blank: str) -> None:
        """gpt-4o-mini and many gateways do not know the parameter at all."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=chat_response())

        provider_with(handler, llm_reasoning_effort=blank).complete(prompt())
        assert "reasoning_effort" not in seen
        assert seen["temperature"] == 0.0

    def test_the_default_model_is_a_current_one(self) -> None:
        """The gpt-4o-mini placeholder is gone; checked against OpenAI's model list 2026-09-23."""
        defaults = Settings(_env_file=None)
        assert defaults.llm_model == "gpt-5.6-luna"
        assert defaults.llm_reasoning_effort == "none"

    def test_the_key_travels_in_the_authorization_header(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization", "")
            seen["url"] = str(request.url)
            return httpx.Response(200, json=chat_response())

        OpenAiProvider(
            settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(handler),
                base_url="https://api.test/v1",
                headers={"Authorization": "Bearer sk-test"},
            ),
        ).complete(prompt())
        assert seen["auth"].startswith("Bearer ")
        assert "sk-test" not in seen["url"]

    def test_both_messages_are_sent(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=chat_response())

        provider_with(handler).complete(prompt())
        roles = [message["role"] for message in seen["messages"]]
        assert roles == ["system", "user"]


class TestResponseReading:
    def test_content_and_model_are_returned(self) -> None:
        response = provider_with(lambda r: httpx.Response(200, json=chat_response())).complete(
            prompt()
        )
        assert response.content == ANSWER
        assert response.model == "test-model"

    @pytest.mark.parametrize(
        ("usage", "expected"),
        [
            ({"prompt_tokens": 1200, "completion_tokens": 90}, (1200, 90)),
            ({"input_tokens": 1200, "output_tokens": 90}, (1200, 90)),
        ],
    )
    def test_token_counts_are_read_under_either_naming(
        self, usage: dict, expected: tuple[int, int]
    ) -> None:
        """Chat Completions and the Responses API name these differently; accept both rather
        than silently reporting no cost."""
        response = provider_with(
            lambda r: httpx.Response(200, json=chat_response(usage=usage))
        ).complete(prompt())
        assert (response.prompt_tokens, response.completion_tokens) == expected
        assert response.total_tokens == sum(expected)

    @pytest.mark.parametrize(
        ("usage", "cached"),
        [
            (
                {"prompt_tokens": 3000, "prompt_tokens_details": {"cached_tokens": 2048}},
                2048,
            ),
            ({"input_tokens": 3000, "input_tokens_details": {"cached_tokens": 1024}}, 1024),
            ({"prompt_tokens": 3000, "prompt_tokens_details": {"cached_tokens": 0}}, 0),
            ({"prompt_tokens": 3000}, None),  # not reported is not "none cached"
            ({"prompt_tokens": 3000, "prompt_tokens_details": {"cached_tokens": 9999}}, 3000),
            ({"prompt_tokens_details": {"cached_tokens": 10}}, None),  # nothing to be part of
        ],
    )
    def test_cached_input_is_read_under_either_naming(
        self, usage: dict, cached: int | None
    ) -> None:
        """The cached part is billed at a tenth; it is clamped to the prompt count so a
        malformed answer cannot price a call below what it cost."""
        response = provider_with(
            lambda r: httpx.Response(200, json=chat_response(usage=usage))
        ).complete(prompt())
        assert response.cached_prompt_tokens == cached

    def test_missing_usage_is_none_not_zero(self) -> None:
        response = provider_with(lambda r: httpx.Response(200, json=chat_response())).complete(
            prompt()
        )
        assert response.total_tokens is None

    @pytest.mark.parametrize(
        "payload",
        [{}, {"choices": []}, {"choices": [{"message": {"content": "  "}}]}],
    )
    def test_an_unusable_body_is_a_response_error(self, payload: dict) -> None:
        with pytest.raises(LlmResponseError):
            provider_with(lambda r: httpx.Response(200, json=payload)).complete(prompt())

    def test_a_refusal_is_reported_clearly(self) -> None:
        payload = {"choices": [{"message": {"refusal": "I cannot help with that"}}]}
        with pytest.raises(LlmResponseError, match="refused"):
            provider_with(lambda r: httpx.Response(200, json=payload)).complete(prompt())

    def test_non_json_body(self) -> None:
        with pytest.raises(LlmResponseError, match="non-JSON"):
            provider_with(lambda r: httpx.Response(200, text="<html>gateway</html>")).complete(
                prompt()
            )


class TestFailureHandling:
    def test_a_client_error_is_a_response_error_and_is_not_retried(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(400, text="bad schema")

        with pytest.raises(LlmResponseError, match="400"):
            provider_with(handler, llm_max_attempts=3).complete(prompt())
        assert attempts["n"] == 1

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_are_retried_then_reported(self, status: int) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(status)

        with pytest.raises(LlmUnavailableError):
            provider_with(handler, llm_max_attempts=2).complete(prompt())
        assert attempts["n"] == 2

    def test_a_retry_can_succeed(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=chat_response())

        assert provider_with(handler, llm_max_attempts=3).complete(prompt()).content == ANSWER

    def test_a_timeout_is_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        with pytest.raises(LlmUnavailableError, match="timed out"):
            provider_with(handler).complete(prompt())

    def test_no_key_configured_is_not_configured(self) -> None:
        with pytest.raises(LlmNotConfiguredError):
            OpenAiProvider(settings(llm_api_key=None)).complete(prompt())


class TestBuildProvider:
    def test_openai_when_a_key_is_present(self) -> None:
        assert build_provider(settings()).name == "openai"

    def test_null_when_no_key(self) -> None:
        assert build_provider(settings(llm_api_key=None)).name == "none"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_null_when_the_key_is_blank(self, blank: str) -> None:
        """A blank LLM_API_KEY (how Vercel spells "unset") is no key: it used to build an
        OpenAI provider that sent an illegal `Authorization: Bearer ` header on every call."""
        assert settings(llm_api_key=blank).llm_api_key is None
        assert build_provider(settings(llm_api_key=blank)).name == "none"

    def test_null_when_disabled(self) -> None:
        assert build_provider(settings(llm_enabled=False)).name == "none"

    def test_unknown_provider_falls_back_to_null_rather_than_crashing(self) -> None:
        assert build_provider(settings(llm_provider="mystery")).name == "none"

    def test_the_null_provider_raises_not_configured(self) -> None:
        with pytest.raises(LlmNotConfiguredError):
            NullLlmProvider().complete(prompt())


class TestStub:
    def test_responses_can_be_keyed_by_kind(self) -> None:
        stub = StubLlmProvider({NACE: ANSWER})
        assert stub.complete(prompt()).content == ANSWER

    def test_calls_are_recorded(self) -> None:
        stub = StubLlmProvider(ANSWER)
        stub.complete(prompt())
        assert len(stub.calls) == 1


class TestCacheKey:
    def base(self, **overrides: object) -> str:
        args: dict = {
            "kind": NACE,
            "issuer_name": "Nordkap Funding B.V.",
            "description": "a captive lender",
            "codebook_version": "cb-1",
            "model": "m1",
            "prompt_version": "v1",
        }
        args.update(overrides)
        return cache_key(**args)

    def test_same_inputs_same_key(self) -> None:
        assert self.base() == self.base()

    def test_name_is_normalised(self) -> None:
        assert self.base(issuer_name="  NORDKAP   funding b.v. ") == self.base()

    @pytest.mark.parametrize(
        "change",
        [
            {"codebook_version": "cb-2"},
            {"model": "m2"},
            {"prompt_version": "v2"},
            {"description": "something else"},
            {"kind": "ESA"},
        ],
    )
    def test_anything_that_changes_the_answer_changes_the_key(self, change: dict) -> None:
        """A codebook update especially: caching on the name alone would keep serving IDs
        derived from the previous codebook."""
        assert self.base(**change) != self.base()


class TestSqliteCache:
    def suggestion(self) -> Suggestion:
        return Suggestion(
            kind=NACE,
            code="64",
            cts_id="512",
            label="Finanční činnosti",
            confidence="high",
            justification="Financuje vlastní skupinu.",
        )

    def classification(self) -> Classification:
        return Classification(
            kind=NACE,
            suggestions=(self.suggestion(),),
            candidates_considered=12,
            model="m1",
            prompt_version="v1",
            classified_at=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
        )

    def test_round_trip(self, tmp_path: Path) -> None:
        cache = SqliteCache(tmp_path / "c.sqlite3")
        cache.put("k", self.classification(), issuer_name="Nordkap")
        restored = cache.get("k")

        assert restored is not None
        assert restored.top.code == "64"
        assert restored.top.cts_id == "512"
        assert restored.top.justification == "Financuje vlastní skupinu."
        assert restored.classified_at == datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    def test_a_miss_returns_none(self, tmp_path: Path) -> None:
        assert SqliteCache(tmp_path / "c.sqlite3").get("absent") is None

    def test_an_abstention_is_cached_too(self, tmp_path: Path) -> None:
        cache = SqliteCache(tmp_path / "c.sqlite3")
        cache.put(
            "k",
            Classification(kind=NACE, abstained=True, abstain_reason="no evidence"),
            issuer_name=None,
        )
        restored = cache.get("k")
        assert restored is not None and restored.abstained

    def test_it_survives_a_restart(self, tmp_path: Path) -> None:
        path = tmp_path / "c.sqlite3"
        SqliteCache(path).put("k", self.classification(), issuer_name="N")
        assert SqliteCache(path).get("k") is not None

    def test_an_unreadable_row_is_ignored_rather_than_raising(self, tmp_path: Path) -> None:
        import sqlite3

        path = tmp_path / "c.sqlite3"
        cache = SqliteCache(path)
        cache.put("k", self.classification(), issuer_name="N")
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE classifications SET payload = ? WHERE key = ?", ("{", "k"))
        assert cache.get("k") is None

    def test_an_unusable_path_degrades_to_no_cache(self, tmp_path: Path) -> None:
        """A cache is an optimisation; a broken one must not fail a lookup."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        cache = SqliteCache(blocker / "nested" / "c.sqlite3")
        cache.put("k", self.classification(), issuer_name="N")
        assert cache.get("k") is None

    def test_build_cache_honours_the_off_switch(self, tmp_path: Path) -> None:
        assert isinstance(build_cache(None), NullCache)
        assert isinstance(build_cache(tmp_path / "c.sqlite3"), SqliteCache)


class TestCacheUse:
    def test_a_second_identical_lookup_does_not_call_the_model(self, tmp_path: Path) -> None:
        """The whole point: the same issuer is never paid for twice."""
        from core.classify.llm import LlmClassifier

        provider = StubLlmProvider(
            json.dumps(
                {
                    "sufficient_evidence": True,
                    "picks": [{"code": "64", "confidence": "high", "justification": "x"}],
                }
            )
        )
        classifier = LlmClassifier(
            provider, cache=SqliteCache(tmp_path / "c.sqlite3"), codebook_version="cb-1"
        )
        first = classifier.classify(ranked_set(), issuer_name="N", description="a captive lender")
        second = classifier.classify(ranked_set(), issuer_name="N", description="a captive lender")

        assert len(provider.calls) == 1
        assert first.top.code == second.top.code == "64"

    def test_a_different_codebook_version_is_a_different_answer(self, tmp_path: Path) -> None:
        from core.classify.llm import LlmClassifier

        content = json.dumps(
            {
                "sufficient_evidence": True,
                "picks": [{"code": "64", "confidence": "high", "justification": "x"}],
            }
        )
        cache = SqliteCache(tmp_path / "c.sqlite3")
        provider = StubLlmProvider(content)
        LlmClassifier(provider, cache=cache, codebook_version="cb-1").classify(
            ranked_set(), issuer_name="N", description="d"
        )
        LlmClassifier(provider, cache=cache, codebook_version="cb-2").classify(
            ranked_set(), issuer_name="N", description="d"
        )
        assert len(provider.calls) == 2


def test_an_empty_cache_is_still_used(tmp_path: Path) -> None:
    """Regression: SqliteCache defines __len__, so an empty one is falsy. Built with
    `cache or NullCache()` it was silently discarded, disabling caching until the cache had
    something in it - which it never would."""
    from core.classify.llm import LlmClassifier

    cache = SqliteCache(tmp_path / "c.sqlite3")
    assert len(cache) == 0 and not cache, "the trap needs an empty, falsy cache"

    classifier = LlmClassifier(StubLlmProvider(ANSWER), cache=cache, codebook_version="cb-1")
    classifier.classify(ranked_set(), issuer_name="N", description="d")
    assert len(cache) == 1
