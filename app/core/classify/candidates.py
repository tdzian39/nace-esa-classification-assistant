"""The candidate pre-filter: narrow a codebook to the ten or so codes worth deciding between.

Why this exists at all is a cost *and* a correctness argument.

**Cost**, measured on the real codebooks: all 87 NACE short labels are ~895 tokens, but the
full defining texts of all 87 divisions are ~10,400. Narrowing to a dozen divisions and
sending only *their* full texts costs ~1,440 - about a 4.5x saving on the part of the prompt
that actually decides the answer.

**Correctness**: the classifier is handed a list and may only choose from it. Every code it
can return has therefore already been looked up in CTS and already has an ID. A hallucinated
code is not something to detect after the fact; it is unrepresentable.

This stage optimises for **recall**, not precision - the right answer must survive into the
shortlist. Deciding between the survivors is the classifier's job, and separating the two
concerns is what makes each measurable on the golden set.

Two mechanisms, deliberately independent:

* :func:`~core.classify.hints.matching_hints` - a small keyword table, Czech and English,
  that forces obvious codes on regardless of scoring;
* lexical overlap against the codebook text, which works well when MO pastes a Czech
  description and poorly across languages (see :mod:`core.classify.text`).

A model-based narrowing stage will join them as the primary path; it slots in behind the
same :class:`CandidateFilter` interface and needs no change here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Final, Protocol

from core.classify.hints import EsaFamily, build_families, hinted_esa_families, hinted_nace
from core.classify.models import ESA, NACE, Candidate, CandidateSet, Kind
from core.classify.text import inverse_document_frequency, overlap_score, stems
from core.codebooks.errors import UnknownCodeError
from core.codebooks.models import CodebookSet

LOGGER = logging.getLogger(__name__)

#: Default shortlist size. Large enough that the right answer is rarely squeezed out, small
#: enough that the stage-B prompt stays around 1,500 tokens.
DEFAULT_LIMIT: Final[int] = 12

#: Score added for a keyword hit. Comfortably above any lexical score, so a hinted code is
#: never pushed off the shortlist by prose that happens to overlap a lot.
HINT_SCORE: Final[float] = 10.0

#: ESA keys in the rest-of-world block start with this. Foreign issuers are non-residents,
#: so Tool 1 restricts to it; the resident block stays reachable for completeness.
ROW_PREFIX: Final[str] = "2"

#: ESA families always offered, however the description scores.
#:
#: Without this an ordinary foreign corporate - a carmaker, a retailer - matches no keyword
#: and no Czech text, and the shortlist comes back empty, so the one sector it almost
#: certainly belongs to is never put in front of the classifier. "Nefinanční podniky" is
#: ESA's residual category for exactly this: an entity that is not a financial institution,
#: a government body or a household. Offering it costs three slots and removes the worst
#: failure mode the filter has.
BASELINE_ESA_FAMILIES: Final[tuple[str, ...]] = ("nefinancni podniky",)

#: Score for a baseline family: below any real match, above nothing.
BASELINE_SCORE: Final[float] = 0.0


class CandidateFilter(Protocol):
    """What the classifier needs from any way of producing a shortlist."""

    name: str

    def shortlist(self, description: str, *, limit: int = DEFAULT_LIMIT) -> CandidateSet:
        """Return the codes worth deciding between for this description."""
        ...


def _scored(
    entries: Sequence[tuple[str, str, tuple[str, ...]]],
    description: str,
    hints: dict[str, str],
) -> list[tuple[float, tuple[str, ...], str]]:
    """Score ``(code, label, texts)`` entries against the description.

    Returns ``(score, reasons, code)`` per entry. IDF is computed over this codebook's own
    texts, so stems that appear everywhere in it (``financ`` across section K) stop being
    evidence and the discriminating ones (``pojist``, ``leasin``) count for more.
    """
    documents = [stems(" ".join((label, *texts))) for _, label, texts in entries]
    weights = inverse_document_frequency(documents)
    query = stems(description)

    scored: list[tuple[float, tuple[str, ...], str]] = []
    for (code, _label, _texts), document in zip(entries, documents, strict=True):
        score = overlap_score(query, document, weights)
        reasons: list[str] = []
        if score > 0:
            reasons.append(f"text match {score:.2f}")
        if code in hints:
            score += HINT_SCORE
            reasons.insert(0, hints[code])
        if score > 0:
            scored.append((score, tuple(reasons), code))
    scored.sort(key=lambda item: (-item[0], item[2]))
    return scored


class NaceCandidateFilter:
    """Shortlist NACE divisions for a described activity.

    Scores each division against the *whole* of its NACE_STAT text - every sub-activity
    label, not just the short one. That is what lets a captive funding vehicle reach
    division 64 through its sub-item "Činnosti účelových finančních společností", which the
    short label alone ("Finanční činnosti, kromě pojišťování...") would never have matched.
    """

    name = "nace-lexical+hints"

    def __init__(self, codebooks: CodebookSet) -> None:
        self._codebooks = codebooks
        self._entries: list[tuple[str, str, tuple[str, ...]]] = []
        for division in codebooks.nace_divisions():
            labels = division.labels
            if not labels:
                continue
            self._entries.append((division.code, division.short_text or labels[0], labels))

    def shortlist(self, description: str, *, limit: int = DEFAULT_LIMIT) -> CandidateSet:
        hints = hinted_nace(description)
        ranked = _scored(self._entries, description, hints)
        texts = {code: labels for code, _label, labels in self._entries}
        labels = {code: label for code, label, _ in self._entries}

        candidates: list[Candidate] = []
        for score, reasons, code in ranked[:limit]:
            candidate = _build(
                self._codebooks, NACE, code, labels[code], texts[code], score, reasons
            )
            if candidate is not None:
                candidates.append(candidate)
        return CandidateSet(
            kind=NACE,
            candidates=tuple(candidates),
            considered=len(self._entries),
            filter_name=self.name,
        )


class EsaCandidateFilter:
    """Shortlist ESA sectors, family first and control variants together.

    The BA0036 grid is entity family x control type, and the two axes are settled by
    different evidence: the family by what the entity is, the control by who owns it. So
    the filter picks *families* and offers every control variant of each, leaving the
    classifier one question it can actually answer from an ownership sentence rather than
    56 flat options.

    ``resident=False`` (the default, and Tool 1's case) restricts to the rest-of-world
    block: a foreign issuer is a non-resident, and offering it a resident code would be
    wrong before the classifier even looks.
    """

    name = "esa-family+hints"

    def __init__(self, codebooks: CodebookSet, *, resident: bool = False) -> None:
        self._codebooks = codebooks
        self._resident = resident
        prefix = "1" if resident else ROW_PREFIX
        self._sectors = {
            sector.key: sector for sector in codebooks.esa_leaves() if sector.key.startswith(prefix)
        }
        self._families: dict[str, EsaFamily] = build_families(
            (key, sector.name) for key, sector in self._sectors.items()
        )

    @property
    def families(self) -> dict[str, EsaFamily]:
        """The derived family grid, exposed for tests and diagnostics."""
        return self._families

    def shortlist(self, description: str, *, limit: int = DEFAULT_LIMIT) -> CandidateSet:
        hints = hinted_esa_families(description)
        entries = [
            (
                family.key,
                family.display,
                tuple(
                    text
                    for key in family.codes
                    for text in (self._sectors[key].name, self._sectors[key].description)
                    if text
                ),
            )
            for family in self._families.values()
        ]
        ranked = _scored(entries, description, hints)

        # Reserve slots for the residual families before filling from the ranking. Appending
        # them afterwards is not enough: an industrial issuer whose description merely
        # mentions "bonds" and "finance" scores weakly against a dozen financial families,
        # and those weak matches would consume every slot before the one sector the issuer
        # actually belongs to got a look. Measured on the golden set, that was the difference
        # between missing "Nefinanční podniky" and offering it first.
        present = {family_key for _, _, family_key in ranked}
        missing = [
            key for key in BASELINE_ESA_FAMILIES if key in self._families and key not in present
        ]
        reserved: list[str] = []
        for key in missing:
            for code in self._families[key].codes:
                if len(reserved) >= max(0, limit // 2):
                    break
                reserved.append(code)

        candidates: list[Candidate] = []
        budget = max(0, limit - len(reserved))
        for score, reasons, family_key in ranked:
            family = self._families[family_key]
            for code in family.codes:
                if len(candidates) >= budget:
                    break
                candidates.append(
                    self._candidate(code, score, (*reasons, f"family: {family.display}"))
                )
            if len(candidates) >= budget:
                break

        for code in reserved:
            if len(candidates) >= limit:
                break
            candidates.append(self._candidate(code, BASELINE_SCORE, ("baseline: residual sector",)))

        return CandidateSet(
            kind=ESA,
            candidates=tuple(item for item in candidates if item is not None),
            considered=len(self._sectors),
            filter_name=self.name + ("" if self._resident else " (rest-of-world)"),
        )

    def _candidate(self, code: str, score: float, reasons: tuple[str, ...]) -> Candidate | None:
        sector = self._sectors[code]
        return _build(
            self._codebooks,
            ESA,
            code,
            sector.name,
            (sector.description,) if sector.description else (),
            score,
            reasons,
        )


def _build(
    codebooks: CodebookSet,
    kind: Kind,
    code: str,
    label: str,
    definitions: tuple[str, ...],
    score: float,
    reasons: tuple[str, ...],
) -> Candidate | None:
    """Resolve the CTS ID and build a candidate, or drop the code with a warning.

    Resolving here is what guarantees every offered code is emittable: a code the CTS
    codebook cannot map is never put in front of the classifier, so it can never come back
    as an answer.
    """
    try:
        entry = codebooks.cts_id_for_nace(code) if kind == NACE else codebooks.cts_id_for_esa(code)
    except UnknownCodeError as exc:
        LOGGER.warning(
            "%s %s has no usable CTS ID, not offered as a candidate: %s", kind, code, exc
        )
        return None
    return Candidate(
        kind=kind,
        code=code,
        cts_id=entry.cts_id,
        label=label or entry.description,
        definitions=definitions,
        score=round(score, 4),
        reasons=reasons,
    )


def shortlist_both(
    codebooks: CodebookSet,
    description: str,
    *,
    limit: int = DEFAULT_LIMIT,
    resident: bool = False,
) -> tuple[CandidateSet, CandidateSet]:
    """Convenience: ``(nace_candidates, esa_candidates)`` for one description."""
    return (
        NaceCandidateFilter(codebooks).shortlist(description, limit=limit),
        EsaCandidateFilter(codebooks, resident=resident).shortlist(description, limit=limit),
    )
