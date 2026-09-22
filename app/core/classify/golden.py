"""The golden set: verified issuers with their correct codes, and how to score against it.

This is what turns "the classifier seems good" into a number, and it is the only honest
basis for choosing a cheaper model. Two metrics, one per stage:

* **recall@k** - was the correct code anywhere on the shortlist? This grades the pre-filter.
  A code the filter never offered is a code the classifier cannot possibly return, so a
  recall miss is an upper bound on everything downstream. This is measurable today, with
  no API key.
* **top-1 / top-3 accuracy** - graded once the classifier exists. Top-3 is the number that
  matters for a suggester: MO picks from three, so the tool has done its job if the right
  answer is among them.

A case is only *verified* when a human at the bank has confirmed both codes. Unverified
cases are kept (they are still useful for exercising the machinery and spotting crashes)
but are reported separately and must never be quoted as accuracy. A golden set that
silently mixes the two is worse than no golden set, because it manufactures confidence.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from config.settings import APP_ROOT
from core.classify.models import ESA, NACE, CandidateSet, Kind

#: Where the cases live.
GOLDEN_PATH: Final[Path] = APP_ROOT / "tests" / "golden" / "cases.json"


class GoldenError(Exception):
    """The golden file is missing, malformed, or contains a case that cannot be scored."""


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One verified (or provisional) issuer and its correct codes.

    Attributes:
        id: Stable slug, used in failure messages.
        issuer: The issuer name, as MO would type it.
        description: The activity description the classifier reasons over.
        expected_nace: Correct 2-digit NACE division, or ``None`` if not being graded.
        expected_esa: Correct BA0036 code, or ``None``.
        verified_by: Who at the bank confirmed it. ``None`` means provisional.
        verified_on: ISO date of that confirmation.
        note: Why this case is interesting - usually the trap it sets.
        source: Where the description came from.
    """

    id: str
    issuer: str
    description: str
    expected_nace: str | None = None
    expected_esa: str | None = None
    verified_by: str | None = None
    verified_on: str | None = None
    note: str = ""
    source: str = ""

    @property
    def verified(self) -> bool:
        """Whether a human at the bank has signed this case off."""
        return bool(self.verified_by)

    def expected(self, kind: Kind) -> str | None:
        return self.expected_nace if kind == NACE else self.expected_esa


def load_golden(path: Path | None = None) -> tuple[GoldenCase, ...]:
    """Load the golden cases.

    Raises:
        GoldenError: the file is missing, is not JSON, or a case lacks an id/description.
    """
    target = path or GOLDEN_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GoldenError(f"golden file not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise GoldenError(f"{target.name} is not valid JSON: {exc}") from exc

    entries = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise GoldenError(f"{target.name} must hold a list under 'cases'")

    cases: list[GoldenCase] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise GoldenError(f"{target.name} case {index} is not an object")
        case_id = str(entry.get("id") or "").strip()
        if not case_id:
            raise GoldenError(f"{target.name} case {index} has no id")
        if case_id in seen:
            raise GoldenError(f"{target.name} has a duplicate case id: {case_id}")
        seen.add(case_id)
        if not str(entry.get("description") or "").strip():
            raise GoldenError(f"golden case {case_id} has no description to classify")
        cases.append(
            GoldenCase(
                id=case_id,
                issuer=str(entry.get("issuer") or "").strip(),
                description=str(entry["description"]).strip(),
                expected_nace=_optional(entry.get("expected_nace")),
                expected_esa=_optional(entry.get("expected_esa")),
                verified_by=_optional(entry.get("verified_by")),
                verified_on=_optional(entry.get("verified_on")),
                note=str(entry.get("note") or "").strip(),
                source=str(entry.get("source") or "").strip(),
            )
        )
    return tuple(cases)


def _optional(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


@dataclass(frozen=True, slots=True)
class RecallResult:
    """Whether one case's expected code survived the pre-filter."""

    case_id: str
    kind: Kind
    expected: str
    found: bool
    rank: int | None
    offered: int
    verified: bool

    def describe(self) -> str:
        where = f"rank {self.rank}" if self.found else "NOT on the shortlist"
        return f"{self.case_id} [{self.kind}] expected {self.expected}: {where} of {self.offered}"


@dataclass(frozen=True, slots=True)
class RecallReport:
    """Recall of a pre-filter over the golden set, verified cases kept apart."""

    results: tuple[RecallResult, ...] = field(default=())

    def _subset(self, *, verified_only: bool) -> tuple[RecallResult, ...]:
        return tuple(r for r in self.results if r.verified or not verified_only)

    def recall(self, *, verified_only: bool = True) -> float | None:
        """Share of expected codes that made the shortlist. ``None`` when nothing to score."""
        subset = self._subset(verified_only=verified_only)
        if not subset:
            return None
        return sum(1 for r in subset if r.found) / len(subset)

    def misses(self, *, verified_only: bool = False) -> tuple[RecallResult, ...]:
        """The cases whose correct code was never offered - the ones worth fixing."""
        return tuple(r for r in self._subset(verified_only=verified_only) if not r.found)

    def mean_rank(self, *, verified_only: bool = True) -> float | None:
        """Average position of the correct code among those that were found."""
        ranks = [r.rank for r in self._subset(verified_only=verified_only) if r.rank is not None]
        return sum(ranks) / len(ranks) if ranks else None

    def summary(self) -> str:
        """One block for the console."""
        verified = self.recall(verified_only=True)
        overall = self.recall(verified_only=False)
        lines = [
            "recall (verified cases) : "
            + ("n/a - no verified case yet" if verified is None else f"{verified:.0%}"),
            "recall (all cases)      : "
            + ("n/a" if overall is None else f"{overall:.0%} of {len(self.results)}"),
        ]
        rank = self.mean_rank(verified_only=False)
        if rank is not None:
            lines.append(f"mean rank of correct code: {rank:.1f}")
        for miss in self.misses():
            lines.append(f"  MISS {miss.describe()}")
        return "\n".join(lines)


def score_recall(
    cases: Iterable[GoldenCase],
    shortlists: Sequence[tuple[GoldenCase, CandidateSet]],
    kind: Kind,
) -> RecallReport:
    """Score pre-computed shortlists against the expected codes of ``kind``."""
    known = {case.id: case for case in cases}
    results: list[RecallResult] = []
    for case, candidate_set in shortlists:
        expected = known.get(case.id, case).expected(kind)
        if expected is None:
            continue
        codes = candidate_set.codes
        found = expected in codes
        results.append(
            RecallResult(
                case_id=case.id,
                kind=kind,
                expected=expected,
                found=found,
                rank=(codes.index(expected) + 1) if found else None,
                offered=len(codes),
                verified=case.verified,
            )
        )
    return RecallReport(results=tuple(results))


def counts(cases: Sequence[GoldenCase]) -> dict[str, int]:
    """Case counts for a status line."""
    return {
        "cases": len(cases),
        "verified": sum(1 for case in cases if case.verified),
        "with_nace": sum(1 for case in cases if case.expected_nace),
        "with_esa": sum(1 for case in cases if case.expected_esa),
    }


__all__ = [
    "GOLDEN_PATH",
    "ESA",
    "NACE",
    "GoldenCase",
    "GoldenError",
    "RecallReport",
    "RecallResult",
    "counts",
    "load_golden",
    "score_recall",
]
