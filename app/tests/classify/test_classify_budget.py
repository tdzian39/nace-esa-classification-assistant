"""Spending limits: they must stop the call, not report it afterwards.

The behaviour that matters is that an exhausted budget produces an *abstention* - a row
saying why - rather than an exception that loses a half-finished batch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.classify.budget import (
    Budget,
    BudgetedProvider,
    BudgetExceededError,
    NullLedger,
    SqliteLedger,
    UsageTotals,
    build_ledger,
)
from core.classify.llm import LlmClassifier
from core.classify.prompts import build_prompt
from core.classify.provider import StubLlmProvider
from tests.classify.test_classify_llm import ranked_set

ANSWER = json.dumps(
    {
        "sufficient_evidence": True,
        "picks": [{"code": "64", "confidence": "high", "justification": "x"}],
    }
)


def prompt(description: str = "a captive lender"):
    return build_prompt(ranked_set(), issuer_name="Nordkap", description=description)


def guarded(**limits) -> BudgetedProvider:
    """A budgeted provider with the daily cap off unless the test is about the daily cap.

    The daily cap fails closed without a ledger - by design - so a test of the per-request
    or per-run limit has to disable it, or it would be testing the wrong thing.
    """
    limits.setdefault("daily_token_budget", 0)
    return BudgetedProvider(StubLlmProvider(ANSWER), budget=Budget(**limits))


class TestPerRequestLimit:
    def test_an_oversized_request_is_refused_before_it_is_sent(self) -> None:
        inner = StubLlmProvider(ANSWER)
        provider = BudgetedProvider(
            inner, budget=Budget(max_prompt_tokens=10, daily_token_budget=0)
        )
        with pytest.raises(BudgetExceededError, match="per-request limit"):
            provider.complete(prompt())
        assert inner.calls == [], "the request must not reach the provider"

    def test_a_normal_request_passes(self) -> None:
        assert guarded(max_prompt_tokens=8000).complete(prompt()).content == ANSWER

    def test_zero_disables_the_limit(self) -> None:
        assert guarded(max_prompt_tokens=0).complete(prompt()).content == ANSWER


class TestPerRunLimit:
    def test_a_runaway_loop_is_bounded(self) -> None:
        inner = StubLlmProvider(ANSWER)
        provider = BudgetedProvider(inner, budget=Budget(max_calls_per_run=3, daily_token_budget=0))
        for _ in range(3):
            provider.complete(prompt())
        with pytest.raises(BudgetExceededError, match="limit is 3"):
            provider.complete(prompt())
        assert len(inner.calls) == 3

    def test_calls_are_counted(self) -> None:
        provider = guarded(max_calls_per_run=5)
        provider.complete(prompt())
        provider.complete(prompt())
        assert provider.calls_made == 2

    def test_zero_disables_the_limit(self) -> None:
        provider = guarded(max_calls_per_run=0)
        for _ in range(5):
            provider.complete(prompt())
        assert provider.calls_made == 5


class TestDailyBudget:
    def test_spending_stops_once_the_day_is_used_up(self, tmp_path: Path) -> None:
        ledger = SqliteLedger(tmp_path / "usage.sqlite3")
        ledger.record(model="m", kind="NACE", prompt_tokens=9_900, completion_tokens=50)
        inner = StubLlmProvider(ANSWER)
        provider = BudgetedProvider(inner, budget=Budget(daily_token_budget=10_000), ledger=ledger)
        with pytest.raises(BudgetExceededError, match="daily budget"):
            provider.complete(prompt())
        assert inner.calls == []

    def test_spending_continues_while_there_is_room(self, tmp_path: Path) -> None:
        ledger = SqliteLedger(tmp_path / "usage.sqlite3")
        provider = BudgetedProvider(
            StubLlmProvider(ANSWER), budget=Budget(daily_token_budget=1_000_000), ledger=ledger
        )
        assert provider.complete(prompt()).content == ANSWER

    def test_yesterdays_usage_does_not_count(self, tmp_path: Path) -> None:
        """The budget is per day; otherwise it would become a lifetime cap."""
        import sqlite3

        path = tmp_path / "usage.sqlite3"
        ledger = SqliteLedger(path)
        ledger.record(model="m", kind="NACE", prompt_tokens=50_000, completion_tokens=0)
        yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE usage SET at = ?", (yesterday,))

        provider = BudgetedProvider(
            StubLlmProvider(ANSWER), budget=Budget(daily_token_budget=10_000), ledger=ledger
        )
        assert provider.complete(prompt()).content == ANSWER

    def test_an_unenforceable_daily_budget_fails_closed(self) -> None:
        """A cap that cannot be measured is not a cap.

        Whoever set a daily budget did so to be protected when nobody is watching, so a
        missing or broken ledger must refuse to spend rather than quietly remove the limit.
        """
        inner = StubLlmProvider(ANSWER)
        provider = BudgetedProvider(
            inner, budget=Budget(daily_token_budget=100_000), ledger=NullLedger()
        )
        with pytest.raises(BudgetExceededError, match="cannot be enforced"):
            provider.complete(prompt())
        assert inner.calls == []

    def test_no_daily_budget_and_no_ledger_is_fine(self) -> None:
        """Explicitly switching the cap off is a decision, not an accident."""
        provider = BudgetedProvider(
            StubLlmProvider(ANSWER), budget=Budget(daily_token_budget=0), ledger=NullLedger()
        )
        assert provider.complete(prompt()).content == ANSWER


class TestLedger:
    def test_usage_is_recorded(self, tmp_path: Path) -> None:
        ledger = SqliteLedger(tmp_path / "usage.sqlite3")
        BudgetedProvider(StubLlmProvider(ANSWER), ledger=ledger).complete(prompt())
        totals = ledger.today()

        assert totals.calls == 1
        assert totals.prompt_tokens > 0
        assert totals.total_tokens == totals.prompt_tokens + totals.completion_tokens

    def test_totals_accumulate(self, tmp_path: Path) -> None:
        ledger = SqliteLedger(tmp_path / "usage.sqlite3")
        provider = BudgetedProvider(StubLlmProvider(ANSWER), ledger=ledger)
        provider.complete(prompt())
        provider.complete(prompt())
        assert ledger.today().calls == 2

    def test_reported_tokens_are_preferred_over_the_estimate(self, tmp_path: Path) -> None:
        """The provider's own count is the truth; the estimate is only a fallback."""
        ledger = SqliteLedger(tmp_path / "usage.sqlite3")
        inner = StubLlmProvider(ANSWER, prompt_tokens=1234, completion_tokens=56)
        BudgetedProvider(inner, ledger=ledger).complete(prompt())
        totals = ledger.today()
        assert (totals.prompt_tokens, totals.completion_tokens) == (1234, 56)

    def test_an_unusable_ledger_says_so(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        ledger = SqliteLedger(blocker / "nested" / "usage.sqlite3")
        assert not ledger.usable
        assert ledger.today() == UsageTotals()

    def test_build_ledger_honours_the_off_switch(self, tmp_path: Path) -> None:
        assert isinstance(build_ledger(None), NullLedger)
        assert isinstance(build_ledger(tmp_path / "u.sqlite3"), SqliteLedger)


class TestClassifierBehaviour:
    def test_an_exhausted_budget_abstains_rather_than_crashing(self) -> None:
        """The property that matters for a batch: rows say why, and the run finishes."""
        provider = BudgetedProvider(
            StubLlmProvider(ANSWER), budget=Budget(max_calls_per_run=1, daily_token_budget=0)
        )
        classifier = LlmClassifier(provider)

        first = classifier.classify(ranked_set(), issuer_name="A", description="a captive lender")
        second = classifier.classify(ranked_set(), issuer_name="B", description="a captive lender")

        assert first.top is not None and first.top.code == "64"
        assert second.abstained
        assert "limit" in second.abstain_reason

    def test_the_reason_names_the_limit_that_was_hit(self) -> None:
        provider = BudgetedProvider(
            StubLlmProvider(ANSWER), budget=Budget(max_prompt_tokens=10, daily_token_budget=0)
        )
        result = LlmClassifier(provider).classify(
            ranked_set(), issuer_name="A", description="a captive lender"
        )
        assert result.abstained
        assert "per-request" in result.abstain_reason


def test_limits_are_described_readably() -> None:
    assert "unlimited" in Budget(max_calls_per_run=0).describe()
    assert "500,000 tokens/day" in Budget().describe()


def test_the_wrapper_is_transparent() -> None:
    provider = BudgetedProvider(StubLlmProvider(ANSWER, model="m1"))
    assert provider.model == "m1"
    assert "budget" in provider.name
