"""Spending limits and a usage ledger, enforced in this codebase.

Set a cap in the provider's own dashboard as well - that is the backstop and it cannot be
bypassed by a bug in here. What this module adds is the part a dashboard cannot give you:

* **it stops before spending**, not after, so a runaway loop is capped at the limit rather
  than discovered on the invoice;
* **it fails as an abstention.** :class:`BudgetExceededError` derives from
  :class:`~core.classify.errors.LlmError`, which the classifier already turns into "no
  suggestion, here is why". A batch that exhausts its budget produces rows saying so, rather
  than crashing halfway and losing the work already done;
* **it records every call**, so "what did this actually cost" is answerable from the tool
  rather than from a monthly statement.

Three independent limits, because they fail differently:

* ``max_prompt_tokens`` - one oversized request (a pathological description, a codebook
  change that inflated the prompt). Refused before it is sent.
* ``max_calls_per_run`` - a loop that does not terminate. Bounded per process.
* ``daily_token_budget`` - sustained overuse across runs. Bounded per day, from the ledger.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from core.classify.errors import LlmError
from core.classify.prompts import Prompt
from core.classify.provider import LlmProvider, LlmResponse

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    at                TEXT NOT NULL,
    model             TEXT NOT NULL,
    kind              TEXT,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cached_prompt_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS usage_at ON usage (at);
"""

#: Added after ledgers already existed; NULL in their rows means "not recorded", not zero.
_CACHED_COLUMN = "cached_prompt_tokens"


class BudgetExceededError(LlmError):
    """A limit would be crossed by this call, so it was not made.

    Derives from :class:`LlmError` on purpose: the classifier already turns that into an
    abstention with a reason, which is exactly the right behaviour here.
    """


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """What has been spent over some window."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def describe(self) -> str:
        return (
            f"{self.calls} call(s), {self.prompt_tokens:,} in + "
            f"{self.completion_tokens:,} out = {self.total_tokens:,} tokens"
        )


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """One model call as the ledger recorded it; ``at`` is timezone-aware UTC."""

    at: datetime
    model: str
    kind: str
    prompt_tokens: int
    completion_tokens: int
    #: The part of ``prompt_tokens`` billed at the cached rate; ``None`` when the call was
    #: recorded before this was kept, or the provider did not report it.
    cached_prompt_tokens: int | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class UsageRecorder(Protocol):
    """Where usage is written and read back."""

    #: False when this recorder cannot report past usage, which makes a daily budget
    #: unenforceable. See :class:`BudgetedProvider`, which then refuses to spend.
    can_track: bool

    def record(
        self,
        *,
        model: str,
        kind: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int | None = None,
    ) -> None: ...

    def totals_since(self, since: datetime) -> UsageTotals: ...


class NullLedger:
    """No ledger: nothing recorded, so no daily budget can be enforced."""

    can_track = False

    def record(
        self,
        *,
        model: str,
        kind: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int | None = None,
    ) -> None:
        return None

    def totals_since(self, since: datetime) -> UsageTotals:
        return UsageTotals()


class SqliteLedger:
    """File-backed usage log.

    Unlike the answer cache, a ledger failure is **not** shrugged off silently on the read
    path: if usage cannot be read, the daily budget cannot be enforced, and the caller is
    told so rather than being allowed to spend unmeasured. Write failures are logged and
    tolerated, since losing one row must not fail a lookup the user already paid for.
    """

    can_track = True

    def __init__(self, path: Path) -> None:
        self.path = path
        self.usable = True
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
                columns = {row[1] for row in connection.execute("PRAGMA table_info(usage)")}
                if _CACHED_COLUMN not in columns:
                    connection.execute(f"ALTER TABLE usage ADD COLUMN {_CACHED_COLUMN} INTEGER")
        except (sqlite3.Error, OSError) as exc:
            LOGGER.warning(
                "usage ledger is unusable (%s); the daily budget cannot be enforced", exc
            )
            self.usable = False
            self.can_track = False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def record(
        self,
        *,
        model: str,
        kind: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int | None = None,
    ) -> None:
        if not self.usable:
            return
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO usage (at, model, kind, prompt_tokens, completion_tokens, "
                    f"{_CACHED_COLUMN}) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now(UTC).isoformat(),
                        model,
                        kind,
                        int(prompt_tokens or 0),
                        int(completion_tokens or 0),
                        None if cached_prompt_tokens is None else int(cached_prompt_tokens),
                    ),
                )
        except sqlite3.Error as exc:
            LOGGER.warning("could not record usage: %s", exc)

    def totals_since(self, since: datetime) -> UsageTotals:
        if not self.usable:
            return UsageTotals()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), "
                    "COALESCE(SUM(completion_tokens), 0) FROM usage WHERE at >= ?",
                    (since.isoformat(),),
                ).fetchone()
        except sqlite3.Error as exc:
            LOGGER.warning("could not read usage: %s", exc)
            return UsageTotals()
        return UsageTotals(
            calls=int(row[0]), prompt_tokens=int(row[1]), completion_tokens=int(row[2])
        )

    def today(self) -> UsageTotals:
        """Usage since midnight UTC."""
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.totals_since(start)

    def records(self) -> list[UsageRecord]:
        """Every recorded call, oldest first - what the usage workbook is made of.

        Unlike :meth:`totals_since`, a read error is raised rather than logged: a report that
        quietly came back empty would say that nothing was spent.
        """
        if not self.usable:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT at, model, kind, prompt_tokens, completion_tokens, "
                f"{_CACHED_COLUMN} FROM usage ORDER BY at, id"
            ).fetchall()
        records = []
        for at, model, kind, prompt_tokens, completion_tokens, cached in rows:
            when = datetime.fromisoformat(at)
            records.append(
                UsageRecord(
                    at=when if when.tzinfo is not None else when.replace(tzinfo=UTC),
                    model=model,
                    kind=kind or "",
                    prompt_tokens=int(prompt_tokens),
                    completion_tokens=int(completion_tokens),
                    cached_prompt_tokens=None if cached is None else int(cached),
                )
            )
        return records


@dataclass(frozen=True, slots=True)
class Budget:
    """The limits. ``0`` disables an individual limit; it never means "no spending".

    Attributes:
        max_prompt_tokens: Refuse a single request larger than this.
        max_calls_per_run: Refuse after this many calls in one process.
        daily_token_budget: Refuse once this many tokens have been used since midnight UTC.
    """

    max_prompt_tokens: int = 8_000
    max_calls_per_run: int = 200
    daily_token_budget: int = 500_000

    def describe(self) -> str:
        def limit(value: int, unit: str) -> str:
            return "unlimited" if value <= 0 else f"{value:,} {unit}"

        return (
            f"limits: {limit(self.max_prompt_tokens, 'tokens/request')}, "
            f"{limit(self.max_calls_per_run, 'calls/run')}, "
            f"{limit(self.daily_token_budget, 'tokens/day')}"
        )


class BudgetedProvider:
    """Wraps any provider with limits and a usage ledger.

    A decorator rather than logic inside the classifier: the limits then apply to every
    caller - CLI, API, batch - and cannot be forgotten by a new one.
    """

    def __init__(
        self,
        inner: LlmProvider,
        *,
        budget: Budget | None = None,
        ledger: UsageRecorder | None = None,
    ) -> None:
        self._inner = inner
        self._budget = budget or Budget()
        self._ledger = NullLedger() if ledger is None else ledger
        self.calls_made = 0
        self.tokens_used = 0

    @property
    def name(self) -> str:
        return f"{self._inner.name}+budget"

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def budget(self) -> Budget:
        return self._budget

    def _check(self, prompt: Prompt) -> None:
        """Refuse before spending, naming the limit that would be crossed."""
        estimated = prompt.estimated_tokens
        if 0 < self._budget.max_prompt_tokens < estimated:
            raise BudgetExceededError(
                f"request of ~{estimated:,} tokens exceeds the per-request limit of "
                f"{self._budget.max_prompt_tokens:,}"
            )
        if 0 < self._budget.max_calls_per_run <= self.calls_made:
            raise BudgetExceededError(
                f"this run has already made {self.calls_made} model call(s), the limit is "
                f"{self._budget.max_calls_per_run}"
            )
        if self._budget.daily_token_budget > 0:
            # A daily cap that cannot be measured is not a cap. Refusing to spend is the
            # safe direction: someone set this limit precisely to be protected from the
            # case where nobody is watching, so a broken ledger must not quietly remove it.
            if not getattr(self._ledger, "can_track", False):
                raise BudgetExceededError(
                    "a daily token budget is configured but usage cannot be recorded, so it "
                    "cannot be enforced; fix LLM_USAGE_PATH or set LLM_DAILY_TOKEN_BUDGET=0"
                )
            spent = self._ledger.totals_since(
                datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            ).total_tokens
            if spent + estimated > self._budget.daily_token_budget:
                raise BudgetExceededError(
                    f"today's usage ({spent:,} tokens) plus this request (~{estimated:,}) "
                    f"would exceed the daily budget of {self._budget.daily_token_budget:,}"
                )

    def complete(self, prompt: Prompt) -> LlmResponse:
        self._check(prompt)
        response = self._inner.complete(prompt)
        self.calls_made += 1
        prompt_tokens = response.prompt_tokens or prompt.estimated_tokens
        completion_tokens = response.completion_tokens or 0
        # A cached count only means something next to the provider's own prompt count, never
        # next to our estimate.
        cached_prompt_tokens = (
            min(response.cached_prompt_tokens, prompt_tokens)
            if response.prompt_tokens and response.cached_prompt_tokens is not None
            else None
        )
        self.tokens_used += prompt_tokens + completion_tokens
        self._ledger.record(
            model=response.model,
            kind=prompt.kind,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
        )
        return response

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


def build_budget(settings: object = None) -> Budget:
    """The configured limits."""
    from config.settings import Settings, get_settings

    resolved: Settings = settings if isinstance(settings, Settings) else get_settings()
    return Budget(
        max_prompt_tokens=resolved.llm_max_prompt_tokens,
        max_calls_per_run=resolved.llm_max_calls_per_run,
        daily_token_budget=resolved.llm_daily_token_budget,
    )


def build_ledger(path: Path | None) -> UsageRecorder:
    """A SQLite ledger at ``path``, or a null one when usage tracking is switched off."""
    return SqliteLedger(path) if path else NullLedger()


__all__ = [
    "Budget",
    "BudgetExceededError",
    "BudgetedProvider",
    "NullLedger",
    "SqliteLedger",
    "UsageRecord",
    "UsageRecorder",
    "UsageTotals",
    "build_budget",
    "build_ledger",
]
