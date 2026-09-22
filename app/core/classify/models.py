"""Types shared by the whole classification path: candidates, suggestions, results.

The shape encodes the rule that makes this safe: a suggestion can only ever name a code that
was on the candidate list handed to the classifier, and every candidate already carries the
CTS ID it resolves to. Nothing downstream has to look a code up again, and nothing can
return a code that CTS does not know.

The two kinds are deliberately one type rather than two parallel hierarchies: NACE and ESA
differ in how candidates are *chosen*, not in what a suggestion looks like.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

#: Which codebook a candidate or suggestion belongs to.
Kind = Literal["NACE", "ESA"]

NACE: Kind = "NACE"
ESA: Kind = "ESA"

#: How sure the classifier is. Deliberately coarse: a model's numeric probability is not
#: calibrated, and a reviewer reads three buckets far better than a spurious 0.83.
Confidence = Literal["high", "medium", "low"]

CONFIDENCE_ORDER: dict[Confidence, int] = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True, slots=True)
class Candidate:
    """One code offered to the classifier, with everything needed to judge and emit it.

    Attributes:
        kind: ``NACE`` or ``ESA``.
        code: The codebook code - a 2-digit NACE division, or a 7-digit BA0036 code.
        cts_id: The CTS ID this code maps to, resolved when the candidate was built.
        label: Short label, used for the narrowing stage (stage A).
        definitions: The full defining texts, used for the deciding stage (stage B). For
            NACE this is every ``NACE_STAT.Text`` of the division; for ESA the ``Popis``.
        score: Pre-filter score, higher is more plausible. Ordering only, not a probability.
        reasons: Why this candidate was shortlisted, e.g. ``("keyword: bank",)``. Kept so a
            surprising shortlist can be explained without re-running the filter.
    """

    kind: Kind
    code: str
    cts_id: str
    label: str
    definitions: tuple[str, ...] = ()
    score: float = 0.0
    reasons: tuple[str, ...] = ()

    @property
    def definition_text(self) -> str:
        """The definitions as one block, deduplicated, for a stage-B prompt."""
        seen: list[str] = []
        for text in self.definitions:
            if text and text not in seen:
                seen.append(text)
        return "\n".join(seen)


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """The shortlist handed to the classifier, plus how it was arrived at.

    Attributes:
        kind: Which codebook.
        candidates: The shortlist, best first.
        considered: How many codes were in scope before narrowing (87 NACE divisions, or
            the ESA leaves left after the residency rule).
        filter_name: Which filter produced this, for the audit trail.
    """

    kind: Kind
    candidates: tuple[Candidate, ...]
    considered: int
    filter_name: str

    def __len__(self) -> int:
        return len(self.candidates)

    def __iter__(self):
        return iter(self.candidates)

    @property
    def codes(self) -> tuple[str, ...]:
        """The codes on the list; the only values a suggestion may name."""
        return tuple(candidate.code for candidate in self.candidates)

    def by_code(self, code: str) -> Candidate | None:
        """The candidate with this code, or ``None`` when it was not offered."""
        return next((item for item in self.candidates if item.code == code), None)

    def contains(self, code: str) -> bool:
        """Whether ``code`` was offered. The gate every suggestion must pass."""
        return self.by_code(code) is not None

    def describe(self) -> str:
        """One line for the log."""
        return (
            f"{self.kind}: {len(self.candidates)} of {self.considered} candidate(s) "
            f"via {self.filter_name}"
        )


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One ranked answer: a code, why, and how sure.

    Only built from a :class:`Candidate`, so ``cts_id`` and ``label`` cannot disagree with
    the codebook and the code cannot be one the classifier invented.
    """

    kind: Kind
    code: str
    cts_id: str
    label: str
    confidence: Confidence
    justification: str
    rank: int = 1

    @classmethod
    def from_candidate(
        cls,
        candidate: Candidate,
        *,
        confidence: Confidence,
        justification: str,
        rank: int = 1,
    ) -> Suggestion:
        """Build a suggestion from an offered candidate."""
        return cls(
            kind=candidate.kind,
            code=candidate.code,
            cts_id=candidate.cts_id,
            label=candidate.label,
            confidence=confidence,
            justification=justification.strip(),
            rank=rank,
        )


@dataclass(frozen=True, slots=True)
class Classification:
    """The result for one codebook: up to three ranked suggestions, or none.

    An empty ``suggestions`` with ``abstained=True`` is a legitimate, useful answer: the
    evidence did not support a choice. A confident guess on no evidence is the failure mode
    that would cost MO their trust in the tool, so abstention is modelled explicitly rather
    than represented as a low-confidence guess.
    """

    kind: Kind
    suggestions: tuple[Suggestion, ...] = ()
    abstained: bool = False
    abstain_reason: str | None = None
    candidates_considered: int = 0
    rejected: tuple[str, ...] = field(default=())
    model: str | None = None
    prompt_version: str | None = None
    classified_at: datetime | None = None

    @property
    def top(self) -> Suggestion | None:
        """The first-ranked suggestion, or ``None`` when the classifier abstained."""
        return self.suggestions[0] if self.suggestions else None

    @property
    def alternatives(self) -> tuple[Suggestion, ...]:
        """The runners-up, which the UI shows under the top pick."""
        return self.suggestions[1:]
