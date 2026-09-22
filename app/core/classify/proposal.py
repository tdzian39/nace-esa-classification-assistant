"""The proposed code per codebook - the brief's "navrhovaný kód" - and what it rests on.

The brief asks for a suggested NACE and ESA code with its CTS ID. With a model configured
that is the model's first pick. Without one the tool used to stop at the shortlist and say
the choice was MO's, even when a rule had already decided: a GLEIF category, a keyword such
as "bank". Now the pre-filter's first candidate is proposed too, but only when a rule put it
first (a keyword or a register category, see :mod:`core.classify.hints`). Nothing is
proposed when only text similarity ranks the list - measured on the golden set, a
lexical-only first candidate is right about one time in four - nor when rules for two
different codes tie: for a captive funding vehicle described in English, "bank" and
"captive" score exactly alike and only the alphabet would put the bank first. The panel then
says the choice is MO's, as before.

A rule-based proposal is marked as such everywhere it appears: it has no confidence (only
the model has one), its justification names the rules, and candidates with the same score
are listed, because a family's control variants always tie and the rules cannot choose
between veřejné, soukromé národní and pod zahraniční kontrolou.

Measured on the 36 real golden issuers (22-23 Sept 2026, provisional): NACE proposed for 35,
30 of them the expected division; ESA proposed for 26, 21 in the expected family and 14 with
the expected control digit too (the rest turn on Q7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.classify.candidates import HINT_SCORE, REGISTER_SCORE
from core.classify.hints import split_control
from core.classify.models import (
    ESA,
    Candidate,
    CandidateSet,
    Classification,
    Confidence,
    Kind,
    Suggestion,
)

#: What a proposal rests on: the model's pick, or the pre-filter's rules.
Basis = Literal["model", "rules"]

#: A lexical score is at most 1.0, so a candidate scoring this much was put there by a rule.
RULE_FLOOR: float = min(HINT_SCORE, REGISTER_SCORE)


@dataclass(frozen=True, slots=True)
class Proposal:
    """The code proposed for one codebook, with its CTS ID and the runners-up.

    Attributes:
        kind: ``NACE`` or ``ESA``.
        basis: ``"model"`` or ``"rules"``.
        top: The proposed code - a model :class:`Suggestion` or a pre-filter :class:`Candidate`.
        alternatives: The model's runners-up, or the rest of the shortlist.
        tied: Codes on the shortlist with exactly the proposal's score (rules only).
    """

    kind: Kind
    basis: Basis
    top: Suggestion | Candidate
    alternatives: tuple[Suggestion | Candidate, ...] = ()
    tied: tuple[str, ...] = ()

    @property
    def code(self) -> str:
        return self.top.code

    @property
    def cts_id(self) -> str:
        return self.top.cts_id

    @property
    def label(self) -> str:
        return self.top.label

    @property
    def confidence(self) -> Confidence | None:
        """The model's confidence; ``None`` for the rules, which have none to give."""
        return self.top.confidence if isinstance(self.top, Suggestion) else None

    @property
    def justification(self) -> str:
        """The model's sentence, or the rules that put the code first."""
        if isinstance(self.top, Suggestion):
            return self.top.justification
        return "Podle pravidel, bez modelu: " + "; ".join(self.top.reasons) + "."

    @property
    def tie_note(self) -> str | None:
        """Czech sentence naming the equally scored codes, or ``None`` when nothing ties."""
        if not self.tied:
            return None
        verb = "má" if len(self.tied) == 1 else "mají"
        return (
            f"Stejné skóre {verb} i {', '.join(self.tied)} – pravidla mezi nimi nerozhodují, "
            "vyberte."
        )


def propose(classification: Classification, candidates: CandidateSet) -> Proposal | None:
    """The model's first pick; else the shortlist's first candidate if a rule put it there.

    Returns ``None`` when neither holds - no model answer and nothing but text similarity
    behind the ranking - which the page shows as "the choice is yours".
    """
    if classification.top is not None:
        return Proposal(
            kind=classification.kind,
            basis="model",
            top=classification.top,
            alternatives=classification.alternatives,
        )
    ranked = candidates.candidates
    if not ranked or ranked[0].score < RULE_FLOOR:
        return None
    first, rest = ranked[0], ranked[1:]
    tied = tuple(item for item in rest if item.score == first.score)
    if any(_family(item) != _family(first) for item in tied):
        # Two rules pointed at different codes equally hard - "bank" and "captive" for a
        # captive funding vehicle, the classic trap - and only the alphabet ordered them.
        return None
    return Proposal(
        kind=candidates.kind,
        basis="rules",
        top=first,
        alternatives=rest,
        tied=tuple(item.code for item in tied),
    )


def _family(candidate: Candidate) -> str:
    """What a tie may stay within: an ESA family, whose control variants always tie; else the code."""
    return split_control(candidate.label)[0] if candidate.kind == ESA else candidate.code


__all__ = ["Basis", "Proposal", "propose"]
