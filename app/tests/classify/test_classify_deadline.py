"""The lookup deadline: a model call that could outlive Vercel's maxDuration is never started.

One lookup is the registers (about 5 s, ~46 s in their worst case) plus two model calls in
a row. Vercel ends the function at 60 s, so the classifier starts a call only when the
worst case of that call - every attempt timing out - still ends before the deadline. A
clock is injected; nothing sleeps.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from config.settings import Settings
from core.classify.cache import SqliteCache
from core.classify.llm import LlmClassifier, build_classifier, can_start_a_call, warn_if_unstartable
from core.classify.models import ESA
from core.classify.provider import (
    CONNECT_TIMEOUT_SECONDS,
    NullLlmProvider,
    OpenAiProvider,
    StubLlmProvider,
    worst_case_call_seconds,
)
from tests.classify.test_classify_llm import ranked_set

ANSWER = json.dumps(
    {
        "sufficient_evidence": True,
        "picks": [{"code": "64", "confidence": "high", "justification": "Banka."}],
    }
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class SlowStub(StubLlmProvider):
    """A stub whose every call takes ``seconds`` on the fake clock."""

    def __init__(self, clock: FakeClock, seconds: float) -> None:
        super().__init__(ANSWER)
        self._clock = clock
        self._seconds = seconds

    def complete(self, prompt):
        self._clock.now += self._seconds
        return super().complete(prompt)


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


class TestWorstCase:
    @pytest.mark.parametrize(
        ("timeout", "attempts", "expected"),
        [
            (15.0, 1, 20.0),  # connect 5 + answer 15
            (15.0, 2, 42.0),  # two attempts of 20 and a 2 s pause
            (60.0, 3, 201.0),  # what the defaults USED to be - 3 x 65 + 2 + 4, unstartable
            (3.0, 1, 6.0),  # a timeout below 5 s caps the connect wait too
        ],
    )
    def test_the_worst_case_counts_every_attempt_and_pause(
        self, timeout: float, attempts: int, expected: float
    ) -> None:
        worst = worst_case_call_seconds(
            settings(llm_timeout_seconds=timeout, llm_max_attempts=attempts)
        )
        assert worst == pytest.approx(expected)

    def test_the_client_waits_at_most_five_seconds_for_a_connection(self) -> None:
        provider = OpenAiProvider(
            settings(llm_api_key="sk-test", llm_timeout_seconds=15.0), sleep=lambda _: None
        )
        try:
            timeout = provider._ensure_client().timeout
            assert timeout.connect == CONNECT_TIMEOUT_SECONDS
            assert timeout.read == 15.0
        finally:
            provider.close()

    def test_the_vercel_values_fit_twice_into_the_deadline_after_typical_registers(self) -> None:
        """The runbook's LLM_TIMEOUT_SECONDS=15 and LLM_MAX_ATTEMPTS=1: 5 s of registers and
        two worst-case calls end at 45 s, before the 50 s deadline and Vercel's 60 s."""
        worst = worst_case_call_seconds(settings(llm_timeout_seconds=15, llm_max_attempts=1))
        assert 5 + 2 * worst <= settings().lookup_deadline_seconds < 60

    def test_the_shipped_defaults_can_actually_start_a_call(self) -> None:
        """The regression this file exists for.

        The defaults were 60 s x 3 attempts = a 201 s worst case, checked against a 50 s
        deadline, so no model call was ever started: every lookup abstained with "no time left
        for the model" and fell back to the rules. It looked exactly like a working
        deterministic run, and the shipped .env.example reproduced it. Whatever the defaults
        become, TWO of them plus the registers must fit inside the deadline.
        """
        defaults = settings()
        worst = worst_case_call_seconds(defaults)
        assert can_start_a_call(defaults, worst), (
            f"the shipped defaults cannot start a call: 2 x {worst:.0f} s against a "
            f"{defaults.lookup_deadline_seconds:.0f} s deadline"
        )

    def test_no_deadline_means_anything_can_start(self) -> None:
        """Off Vercel there is no maxDuration, so a generous timeout is allowed again."""
        assert can_start_a_call(
            settings(lookup_deadline_seconds=0, llm_timeout_seconds=600, llm_max_attempts=5),
            worst_case_call_seconds(settings(llm_timeout_seconds=600, llm_max_attempts=5)),
        )

    def test_an_unstartable_configuration_is_warned_about_not_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A log line is the only warning anyone gets before the silent abstentions start."""
        unstartable = settings(llm_timeout_seconds=60, llm_max_attempts=3)
        with caplog.at_level(logging.WARNING, logger="core.classify.llm"):
            warn_if_unstartable(unstartable, worst_case_call_seconds(unstartable))
        assert "will NEVER be called" in caplog.text
        assert "LLM_TIMEOUT_SECONDS" in caplog.text


class TestClassifierDeadline:
    def test_a_call_that_could_run_past_the_deadline_is_not_started(self) -> None:
        clock = FakeClock()
        stub = StubLlmProvider(ANSWER)
        classifier = LlmClassifier(stub, call_seconds=20.0, clock=clock)
        result = classifier.classify(
            ranked_set(), issuer_name="A", description="a bank", deadline=clock.now + 10
        )
        assert result.abstained
        assert "no time left for the model" in (result.abstain_reason or "")
        assert "LOOKUP_DEADLINE_SECONDS" in (result.abstain_reason or "")
        assert stub.calls == [], "the call must not be started"

    def test_a_call_that_fits_is_made(self) -> None:
        clock = FakeClock()
        classifier = LlmClassifier(StubLlmProvider(ANSWER), call_seconds=20.0, clock=clock)
        result = classifier.classify(
            ranked_set(), issuer_name="A", description="a bank", deadline=clock.now + 30
        )
        assert result.top is not None and result.top.code == "64"

    def test_the_second_call_sees_what_the_first_one_took(self) -> None:
        """NACE starts at 0 s and ends at 15 s; ESA could then run to 35 s, past 30."""
        clock = FakeClock()
        classifier = LlmClassifier(SlowStub(clock, 15.0), call_seconds=20.0, clock=clock)
        esa = ranked_set().__class__(
            kind=ESA,
            candidates=ranked_set().candidates,
            considered=56,
            filter_name="handmade",
        )
        nace_result, esa_result = classifier.classify_both(
            ranked_set(), esa, issuer_name="A", description="a bank", deadline=clock.now + 30
        )
        assert nace_result.top is not None
        assert esa_result.abstained and "no time left" in (esa_result.abstain_reason or "")

    def test_no_deadline_means_no_limit(self) -> None:
        classifier = LlmClassifier(StubLlmProvider(ANSWER), call_seconds=500.0, clock=FakeClock())
        result = classifier.classify(ranked_set(), issuer_name="A", description="a bank")
        assert result.top is not None

    def test_a_cached_answer_is_served_even_with_no_time_left(self, tmp_path: Path) -> None:
        """A cached answer costs no time, so the deadline does not apply to it."""
        clock = FakeClock()
        cache = SqliteCache(tmp_path / "cache.sqlite3")
        stub = StubLlmProvider(ANSWER)
        classifier = LlmClassifier(stub, cache=cache, call_seconds=20.0, clock=clock)
        first = classifier.classify(ranked_set(), issuer_name="A", description="a bank")
        again = classifier.classify(
            ranked_set(), issuer_name="A", description="a bank", deadline=clock.now - 1
        )
        assert first.top is not None and again.top is not None
        assert len(stub.calls) == 1

    def test_the_null_provider_keeps_its_own_reason(self) -> None:
        """Deterministic mode must still say "no model configured", not "no time left"."""
        classifier = build_classifier(settings(llm_enabled=False))
        result = classifier.classify(
            ranked_set(), issuer_name="A", description="a bank", deadline=0.0
        )
        assert result.abstained
        assert "no model configured" in (result.abstain_reason or "")
        assert isinstance(classifier.provider._inner, NullLlmProvider)  # type: ignore[attr-defined]
