"""Tool 1 end to end: an ISIN, a name or a description in, two ranked suggestions out.

The pipeline the brief describes, in one place so the API, the UI and a CLI all get the same
behaviour:

    input -> identity (GLEIF, OpenFIGI) -> evidence (web or typed)
          -> shortlist per codebook -> classifier -> suggestions

The identity step is what makes an ISIN a useful input on its own. GLEIF turns it into the
issuer's LEI record - legal name, country, legal form, entity category, parents - and
OpenFIGI into the instrument's type and market sector. The legal name becomes the web search
query and the name on the page; the facts go to the pre-filter and the model next to the
web description, so a bank is a bank because the register says so, not because the model
recognised the name.

What the pipeline deliberately does **not** do is fail. Every step degrades: a malformed ISIN
becomes a note, a register that cannot be asked becomes a note, an issuer the web cannot
describe produces an abstention, an exhausted budget produces an abstention. A reviewer
always gets a row back, with the reason attached, because MO's fallback is to research the
issuer themselves - which they can only do if they can see that the tool did not.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.classify.candidates import DEFAULT_LIMIT, EsaCandidateFilter, NaceCandidateFilter
from core.classify.llm import LlmClassifier
from core.classify.models import ESA, NACE, CandidateSet, Classification
from core.classify.proposal import Proposal, propose
from core.codebooks.models import CodebookSet
from core.identifiers.isin import InvalidIsinError, normalize_isin
from core.sources.identity import NO_IDENTITY, IssuerIdentifier, IssuerIdentity
from core.sources.web import EvidenceSource, IssuerEvidence, WebEvidenceGatherer

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
        whole request. It is also never sent to a register - only a valid ISIN is looked up.
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
                isin = None
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
    identity: IssuerIdentity = NO_IDENTITY

    @property
    def issuer_name(self) -> str | None:
        """The name to show: what the user typed, else the register's legal name, else the web's."""
        return self.request.name or self.identity.legal_name or self.evidence.issuer_name

    @property
    def description(self) -> str | None:
        return self.evidence.description

    @property
    def classifier_text(self) -> str:
        """What the pre-filter and the model read: the description, then the register facts."""
        return "\n\n".join(
            part for part in (self.evidence.description, self.identity.fact_sheet()) if part
        )

    @property
    def evidence_sources(self) -> tuple[EvidenceSource, ...]:
        """Register pages first, then the web hits - the "Podklady" list."""
        return (*self.identity.evidence, *self.evidence.sources)

    @property
    def sources(self) -> tuple[str, ...]:
        """Registers that answered, then ``WEB``: the audit trail and the row's ``source``."""
        return (*self.identity.sources, "WEB")

    @property
    def source_label(self) -> str:
        """``"GLEIF+OPENFIGI+WEB"``, or plain ``"WEB"`` for a lookup without an ISIN."""
        return "+".join(self.sources)

    @property
    def answered(self) -> bool:
        """True when the model produced a suggestion for at least one codebook."""
        return bool(self.nace.suggestions or self.esa.suggestions)

    @property
    def nace_proposal(self) -> Proposal | None:
        """The proposed NACE code ("navrhovaný kód"): the model's pick, else a rule's."""
        return propose(self.nace, self.nace_candidates)

    @property
    def esa_proposal(self) -> Proposal | None:
        """The proposed ESA code ("navrhovaný kód"): the model's pick, else a rule's."""
        return propose(self.esa, self.esa_candidates)

    @property
    def all_notes(self) -> tuple[str, ...]:
        """Request notes, register notes, evidence notes and each abstention reason, deduplicated."""
        collected: list[str] = [*self.notes, *self.identity.notes, *self.evidence.notes]
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
        identifier: IssuerIdentifier | None = None,
        limit: int = DEFAULT_LIMIT,
        deadline_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._codebooks = codebooks
        self._gatherer = gatherer
        self._classifier = classifier
        self._identifier = identifier
        self._limit = limit
        self._deadline_seconds = max(0.0, deadline_seconds)
        self._clock = clock
        self._nace = NaceCandidateFilter(codebooks)
        self._esa = EsaCandidateFilter(codebooks)

    @property
    def codebooks(self) -> CodebookSet:
        return self._codebooks

    def suggest(self, request: SuggestionRequest) -> IssuerSuggestion:
        """Run one lookup. Never raises for ordinary failures.

        With ``deadline_seconds`` set (``LOOKUP_DEADLINE_SECONDS``), the model calls must be
        over that long after this started: whatever the registers took is subtracted.
        """
        started = self._clock()
        deadline = started + self._deadline_seconds if self._deadline_seconds else None
        cleaned, notes = request.cleaned()

        identity = (
            self._identifier.identify(cleaned.isin) if self._identifier is not None else NO_IDENTITY
        )
        # The register's legal name is the best possible search query; what the user typed
        # still wins as the name shown, because it is what they will recognise. The LEI lets
        # the gatherer find the issuer's Wikipedia article by identifier, not by name.
        evidence = self._gatherer.gather(
            name=cleaned.name or identity.legal_name,
            isin=cleaned.isin,
            description=cleaned.description,
            lei=identity.lei,
            deadline=deadline,
        )
        issuer_name = cleaned.name or identity.legal_name or evidence.issuer_name
        text = "\n\n".join(part for part in (evidence.description, identity.fact_sheet()) if part)

        nace_candidates = self._nace.shortlist(text, limit=self._limit)
        esa_candidates = self._esa.shortlist(text, limit=self._limit)
        nace, esa = self._classifier.classify_both(
            nace_candidates,
            esa_candidates,
            issuer_name=issuer_name,
            description=text,
            deadline=deadline,
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
            identity=identity,
        )


def build_service(
    settings: object = None,
    *,
    codebooks: CodebookSet | None = None,
) -> SuggestionService:
    """The standard service: real codebooks, the configured registers, search provider and model."""
    from config.settings import Settings, get_settings
    from core.classify.llm import build_classifier
    from core.codebooks.loaders import load_and_check
    from core.sources.identity import build_identifier

    resolved: Settings = settings if isinstance(settings, Settings) else get_settings()
    if codebooks is None:
        codebooks, _ = load_and_check(resolved, strict=False)
    return SuggestionService(
        codebooks,
        gatherer=WebEvidenceGatherer(resolved),
        classifier=build_classifier(resolved, codebook_version=codebooks.version.id),
        identifier=build_identifier(resolved),
        deadline_seconds=resolved.lookup_deadline_seconds,
    )


__all__ = [
    "ESA",
    "NACE",
    "IssuerSuggestion",
    "SuggestionRequest",
    "SuggestionService",
    "build_service",
]
