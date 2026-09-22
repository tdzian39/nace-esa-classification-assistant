"""Tool 1 end to end: an ISIN, a name or a description in, two ranked suggestions out.

The pipeline the brief describes, in one place so the API, the UI and a CLI all get the same
behaviour:

    input -> evidence (web or typed) -> shortlist per codebook -> classifier -> suggestions

What it deliberately does **not** do is fail. Every step degrades: a malformed ISIN becomes a
note, an issuer the web cannot describe produces an abstention, an exhausted budget produces
an abstention. A reviewer always gets a row back, with the reason attached, because MO's
fallback is to research the issuer themselves - which they can only do if they can see that
the tool did not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.classify.candidates import DEFAULT_LIMIT, EsaCandidateFilter, NaceCandidateFilter
from core.classify.llm import LlmClassifier
from core.classify.models import ESA, NACE, CandidateSet, Classification
from core.codebooks.models import CodebookSet
from core.identifiers.isin import InvalidIsinError, normalize_isin
from core.sources.web import IssuerEvidence, WebEvidenceGatherer

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SuggestionRequest:
    """What the user asked for. At least one field must be filled."""

    isin: str | None = None
    name: str | None = None
    description: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any((self.isin or "").strip() for _ in (1,)) and not (
            (self.name or "").strip() or (self.description or "").strip()
        )

    def cleaned(self) -> tuple[SuggestionRequest, tuple[str, ...]]:
        """Trim the inputs and normalise the ISIN, collecting notes about what was wrong.

        A malformed ISIN is a note, not a rejection: the name or description may still be
        enough, and telling the user their ISIN looks wrong is more useful than refusing the
        whole request.
        """
        notes: list[str] = []
        isin = (self.isin or "").strip() or None
        name = " ".join((self.name or "").split()) or None
        description = (self.description or "").strip() or None

        if isin is not None:
            try:
                isin = normalize_isin(isin)
            except InvalidIsinError as exc:
                notes.append(f"ISIN {isin!r} nevypadá jako platný ISIN ({exc.reason})")
                if not (name or description):
                    notes.append("bez názvu nebo popisu nelze pokračovat")
        return SuggestionRequest(isin=isin, name=name, description=description), tuple(notes)


@dataclass(frozen=True, slots=True)
class IssuerSuggestion:
    """Everything one lookup produced, including why a half of it may be empty."""

    request: SuggestionRequest
    evidence: IssuerEvidence
    nace: Classification
    esa: Classification
    nace_candidates: CandidateSet
    esa_candidates: CandidateSet
    codebook_version: str | None = None
    created_at: datetime | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def issuer_name(self) -> str | None:
        """The name to show: what the user typed, else what the web resolved."""
        return self.request.name or self.evidence.issuer_name

    @property
    def description(self) -> str | None:
        return self.evidence.description

    @property
    def answered(self) -> bool:
        """True when at least one codebook produced a suggestion."""
        return bool(self.nace.suggestions or self.esa.suggestions)

    @property
    def all_notes(self) -> tuple[str, ...]:
        """Request notes, evidence notes and each abstention reason, deduplicated."""
        collected: list[str] = [*self.notes, *self.evidence.notes]
        for classification in (self.nace, self.esa):
            if classification.abstained and classification.abstain_reason:
                collected.append(f"{classification.kind}: {classification.abstain_reason}")
        seen: list[str] = []
        for note in collected:
            if note and note not in seen:
                seen.append(note)
        return tuple(seen)


class SuggestionService:
    """Runs the pipeline. One instance is shared by the API; it holds no per-request state."""

    def __init__(
        self,
        codebooks: CodebookSet,
        *,
        gatherer: WebEvidenceGatherer,
        classifier: LlmClassifier,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        self._codebooks = codebooks
        self._gatherer = gatherer
        self._classifier = classifier
        self._limit = limit
        self._nace = NaceCandidateFilter(codebooks)
        self._esa = EsaCandidateFilter(codebooks)

    @property
    def codebooks(self) -> CodebookSet:
        return self._codebooks

    def suggest(self, request: SuggestionRequest) -> IssuerSuggestion:
        """Run one lookup. Never raises for ordinary failures."""
        cleaned, notes = request.cleaned()

        evidence = self._gatherer.gather(
            name=cleaned.name, isin=cleaned.isin, description=cleaned.description
        )
        description = evidence.description or ""
        issuer_name = cleaned.name or evidence.issuer_name

        nace_candidates = self._nace.shortlist(description, limit=self._limit)
        esa_candidates = self._esa.shortlist(description, limit=self._limit)
        nace, esa = self._classifier.classify_both(
            nace_candidates, esa_candidates, issuer_name=issuer_name, description=description
        )

        return IssuerSuggestion(
            request=cleaned,
            evidence=evidence,
            nace=nace,
            esa=esa,
            nace_candidates=nace_candidates,
            esa_candidates=esa_candidates,
            codebook_version=self._codebooks.version.id,
            created_at=datetime.now(UTC),
            notes=notes,
        )


def build_service(
    settings: object = None,
    *,
    codebooks: CodebookSet | None = None,
) -> SuggestionService:
    """The standard service: real codebooks, the configured search provider and model."""
    from config.settings import Settings, get_settings
    from core.classify.llm import build_classifier
    from core.codebooks.loaders import load_and_check

    resolved: Settings = settings if isinstance(settings, Settings) else get_settings()
    if codebooks is None:
        codebooks, _ = load_and_check(resolved, strict=False)
    return SuggestionService(
        codebooks,
        gatherer=WebEvidenceGatherer(resolved),
        classifier=build_classifier(resolved, codebook_version=codebooks.version.id),
    )


__all__ = [
    "ESA",
    "NACE",
    "IssuerSuggestion",
    "SuggestionRequest",
    "SuggestionService",
    "build_service",
]
