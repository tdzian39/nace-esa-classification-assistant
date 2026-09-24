"""The developer page: the cost ledger and MO's complaints, filtered, summed and priced.

Pure functions over what the stores return - :class:`~core.classify.budget.UsageRecord`
rows from the ledger and :class:`~core.reports.ErrorReport` items from the report store -
so the page, the workbook downloads and the tests share one view of the numbers. Prices
come from :mod:`core.classify.usage_report` (``PRICES``); a model without a price shows an
empty cost, never a guess, exactly as the workbook does.

Who may see it is decided in ``api.main``: the page sits behind its own password
(``ADMIN_PASSWORD_HASH``), on top of the app's sign-in when that is on.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

from core.classify.budget import UsageRecord
from core.classify.usage_report import CallCost, cost_of
from core.reports import ErrorReport

#: Rows the page lists at most; the workbook download has them all.
MAX_ROWS: int = 500


@dataclass(frozen=True, slots=True)
class LedgerFilter:
    """What the page's filter form says; empty means "everything"."""

    user: str = ""
    model: str = ""
    kind: str = ""
    since: date | None = None
    until: date | None = None

    def matches(self, record: UsageRecord) -> bool:
        if self.user and record.user != self.user:
            return False
        if self.model and record.model != self.model:
            return False
        if self.kind and record.kind != self.kind:
            return False
        day = record.at.astimezone(UTC).date()
        if self.since and day < self.since:
            return False
        return not (self.until and day > self.until)

    @property
    def active(self) -> bool:
        return any((self.user, self.model, self.kind, self.since, self.until))


@dataclass(slots=True)
class Bucket:
    """One line of a summary: a key and its totals."""

    key: str
    calls: int = 0
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    priced_calls: int = 0
    inexact_calls: int = 0

    def add(self, item: CallCost) -> None:
        record = item.record
        self.calls += 1
        self.prompt_tokens += record.prompt_tokens
        self.cached_prompt_tokens += record.cached_prompt_tokens or 0
        self.completion_tokens += record.completion_tokens
        if item.cost is not None:
            self.cost += item.cost
            self.priced_calls += 1
        if not item.exact:
            self.inexact_calls += 1

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_per_call(self) -> float | None:
        return self.cost / self.priced_calls if self.priced_calls else None


@dataclass(slots=True)
class LedgerView:
    """The page's numbers for one filter."""

    total: Bucket
    by_user: list[Bucket]
    by_model: list[Bucket]
    by_kind: list[Bucket]
    by_day: list[Bucket]
    calls: list[CallCost]
    users: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    matched: int = 0
    unpriced_models: list[str] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return self.matched > len(self.calls)


def _buckets(items: Iterable[CallCost], key) -> list[Bucket]:
    grouped: dict[str, Bucket] = {}
    for item in items:
        name = key(item)
        bucket = grouped.get(name)
        if bucket is None:
            bucket = grouped[name] = Bucket(name)
        bucket.add(item)
    return sorted(grouped.values(), key=lambda bucket: (-bucket.cost, -bucket.calls, bucket.key))


def ledger_view(
    records: Sequence[UsageRecord], where: LedgerFilter | None = None, *, limit: int = MAX_ROWS
) -> LedgerView:
    """Price and sum the ledger, newest call first in ``calls``."""
    where = where or LedgerFilter()
    everything = sorted(records, key=lambda record: record.at)
    users = sorted({record.user for record in everything})
    models = sorted({record.model for record in everything})
    kinds = sorted({record.kind for record in everything if record.kind})
    matching = [cost_of(record) for record in everything if where.matches(record)]
    total = Bucket("celkem")
    unpriced: set[str] = set()
    for item in matching:
        total.add(item)
        if item.cost is None:
            unpriced.add(item.record.model)
    by_day = sorted(
        _buckets(matching, lambda item: f"{item.record.at.astimezone(UTC):%Y-%m-%d}"),
        key=lambda bucket: bucket.key,
        reverse=True,
    )
    return LedgerView(
        total=total,
        by_user=_buckets(matching, lambda item: item.record.user),
        by_model=_buckets(matching, lambda item: item.record.model),
        by_kind=_buckets(matching, lambda item: item.record.kind or "-"),
        by_day=by_day,
        calls=list(reversed(matching))[:limit],
        users=users,
        models=models,
        kinds=kinds,
        matched=len(matching),
        unpriced_models=sorted(unpriced),
    )


@dataclass(frozen=True, slots=True)
class ReportFilter:
    user: str = ""
    since: date | None = None
    until: date | None = None
    text: str = ""

    def matches(self, report: ErrorReport) -> bool:
        if self.user and report.user != self.user:
            return False
        day = report.created_at.astimezone(UTC).date()
        if self.since and day < self.since:
            return False
        if self.until and day > self.until:
            return False
        if self.text:
            needle = self.text.casefold()
            haystack = " ".join(
                str(part or "")
                for part in (
                    report.note,
                    report.identifier,
                    report.result.get("issuer_name"),
                    report.request.get("name"),
                    report.request.get("description"),
                )
            ).casefold()
            return needle in haystack
        return True

    @property
    def active(self) -> bool:
        return any((self.user, self.since, self.until, self.text))


@dataclass(slots=True)
class ReportsView:
    reports: list[ErrorReport]
    users: list[str]
    matched: int
    total: int
    by_user: list[tuple[str, int]] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return self.matched > len(self.reports)


def reports_view(
    reports: Iterable[ErrorReport], where: ReportFilter | None = None, *, limit: int = MAX_ROWS
) -> ReportsView:
    """The complaints, newest first."""
    where = where or ReportFilter()
    everything = sorted(reports, key=lambda report: report.created_at, reverse=True)
    matching = [report for report in everything if where.matches(report)]
    counts: dict[str, int] = defaultdict(int)
    for report in matching:
        counts[report.user] += 1
    return ReportsView(
        reports=matching[:limit],
        users=sorted({report.user for report in everything}),
        matched=len(matching),
        total=len(everything),
        by_user=sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])),
    )


def parse_day(text: str | None) -> date | None:
    """``2026-09-24`` -> a date; anything else, including blank, is ``None``."""
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


__all__ = [
    "MAX_ROWS",
    "Bucket",
    "LedgerFilter",
    "LedgerView",
    "ReportFilter",
    "ReportsView",
    "day_start",
    "ledger_view",
    "parse_day",
    "reports_view",
]
