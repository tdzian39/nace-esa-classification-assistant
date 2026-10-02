"""Tool 1 end to end: an ISIN, a name or a description in, two ranked suggestions out.

The pipeline the brief describes, in one place so the API, the UI and a CLI all get the same
behaviour:

    input -> identity (GLEIF, OpenFIGI; RES for a Czech issuer)
          -> evidence (typed, Wikipedia, the model's web search)
          -> shortlist per codebook -> classifier -> suggestions

The identity step is what makes an ISIN a useful input on its own. GLEIF turns it into the
issuer's LEI record - legal name, country, legal form, entity category, parents - and
OpenFIGI into the instrument's type and market sector. The legal name becomes the web search
query and the name on the page; the facts go to the pre-filter and the model next to the
web description, so a bank is a bank because the register says so, not because the model
recognised the name. The web is looked at on every lookup (30 Sept 2026): Wikipedia by LEI
or name, then the model's own web search (:mod:`core.sources.llm_web`) when a model is on.

A Czech (resident) issuer - GLEIF's seat CZ, or an IČO typed into the name field - takes its
codes from RES (2 Oct 2026, :mod:`core.classify.residents`): RES's prevailing activity settles
NACE, its institutional sector the BA0036 resident code, and an axis RES settles is not sent to
the model at all. A RES tie (the institution type RES does not record) goes to the model with
only the tied codes; the ESA shortlist of a resident comes from the resident block.

What the pipeline deliberately does **not** do is fail. Every step degrades: a malformed ISIN
becomes a note, a register that cannot be asked becomes a note, an issuer the web cannot
describe produces an abstention, an exhausted budget produces an abstention. A reviewer
always gets a row back, with the reason attached, because MO's fallback is to research the
issuer themselves - which they can only do if they can see that the tool did not.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from core.classify.candidates import DEFAULT_LIMIT, EsaCandidateFilter, NaceCandidateFilter
from core.classify.llm import LlmClassifier
from core.classify.models import ESA, NACE, CandidateSet, Classification, Kind
from core.classify.proposal import Proposal, propose
from core.classify.residents import NO_RULES, resident_rules
from core.codebooks.models import CodebookSet
from core.identifiers.ico import ico_checksum_ok
from core.identifiers.isin import InvalidIsinError, normalize_isin
from core.sources.identity import NO_IDENTITY, IssuerIdentifier, IssuerIdentity
from core.sources.llm_web import LlmWebSearch
from core.sources.names import fold
from core.sources.web import EvidenceSource, IssuerEvidence, WebEvidenceGatherer

LOGGER = logging.getLogger(__name__)

#: A name field holding only digits (spaces allowed), 1 to 8 of them, is an IČO (2 Oct 2026).
_ICO_INPUT: Final[re.Pattern[str]] = re.compile(r"[0-9 ]+")
_ICO_DIGITS: Final[re.Pattern[str]] = re.compile(r"[0-9]{1,8}")


@dataclass(frozen=True, slots=True)
class SuggestionRequest:
    """What the user asked for. At least one field must be filled.

    ``ico`` is a Czech issuer's IČO. The page has no field of its own for it: digits typed
    into the name field are one (:meth:`cleaned`); the JSON API may also send it as ``ico``.
    """

    isin: str | None = None
    name: str | None = None
    description: str | None = None
    ico: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any((self.isin or "").strip() for _ in (1,)) and not (
            (self.name or "").strip()
            or (self.description or "").strip()
            or (self.ico or "").strip()
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
        typed_ico = " ".join((self.ico or "").split()) or None
        if typed_ico is None and name is not None and _is_ico_input(name):
            # Digits in the name field are an IČO: a Czech issuer, answered from RES.
            typed_ico, name = name, None
        ico = _clean_ico(typed_ico, notes) if typed_ico is not None else None

        if isin is not None:
            try:
                isin = normalize_isin(isin)
            except InvalidIsinError as exc:
                notes.append(f"ISIN {isin!r} nevypadá jako platný ISIN ({exc.reason})")
                if not (name or description or ico):
                    notes.append("bez názvu, IČO nebo popisu nelze pokračovat")
                isin = None
        return (
            SuggestionRequest(isin=isin, name=name, description=description, ico=ico),
            tuple(notes),
        )


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
    warnings: tuple[str, ...] = field(default=())
    rule_notes: tuple[str, ...] = field(default=())
    settled_by_res: tuple[Kind, ...] = field(default=())

    @property
    def issuer_name(self) -> str | None:
        """The name to show and export ("Jméno emitenta"): the one found, not the one typed.

        See :func:`issuer_name_of`. What was typed is shown beside it when it differs
        (:attr:`typed_name_apart`).
        """
        return issuer_name_of(self.request, self.identity, self.evidence)

    @property
    def typed_name_apart(self) -> str | None:
        """The typed name, when the name shown is a different one; the page shows both."""
        typed = (self.request.name or "").strip()
        return typed if typed and fold(typed) != fold(self.issuer_name or "") else None

    @property
    def description(self) -> str | None:
        return self.evidence.description

    @property
    def resident(self) -> bool:
        """A Czech issuer (GLEIF's seat CZ, or an IČO typed): its codes are RES's."""
        return self.identity.resident

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
    def settled(self) -> bool:
        """True when a code came from somewhere: the model, or RES for a resident issuer."""
        return self.answered or bool(self.settled_by_res)

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
        collected: list[str] = [
            *self.warnings,
            *self.notes,
            *self.identity.notes,
            *self.rule_notes,
            *self.evidence.notes,
        ]
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
        web_search: LlmWebSearch | None = None,
    ) -> None:
        self._codebooks = codebooks
        self._gatherer = gatherer
        self._classifier = classifier
        self._identifier = identifier
        self._web_search = web_search
        self._limit = limit
        self._deadline_seconds = max(0.0, deadline_seconds)
        self._clock = clock
        self._nace = NaceCandidateFilter(codebooks)
        self._esa = EsaCandidateFilter(codebooks)
        self._esa_resident = EsaCandidateFilter(codebooks, resident=True)

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
            self._identifier.identify(cleaned.isin, name=cleaned.name, ico=cleaned.ico)
            if self._identifier is not None
            else NO_IDENTITY
        )
        # The LEI lets the gatherer find the issuer's Wikipedia article by identifier; without
        # one, what the user typed, else the register's name, is searched on Wikipedia.
        evidence = self._gatherer.gather(
            name=cleaned.name or identity.legal_name,
            isin=cleaned.isin,
            description=cleaned.description,
            lei=identity.lei,
            deadline=deadline,
        )
        if self._web_search is not None:
            # "Use the LLM for each" (Jakub, 30 Sept 2026): the model searches the web too,
            # whatever was typed or found on Wikipedia, and its finding follows theirs.
            evidence = self._web_search.enrich(
                evidence,
                name=(
                    identity.lei_record.legal_name
                    if identity.lei_record
                    else cleaned.name or identity.legal_name
                ),
                isin=cleaned.isin,
                lei=identity.lei,
                country=identity.country,
                instrument=identity.instrument.name if identity.instrument else None,
                typed=cleaned.description,
                deadline=deadline,
            )
        issuer_name = issuer_name_of(cleaned, identity, evidence)
        text = "\n\n".join(part for part in (evidence.description, identity.fact_sheet()) if part)

        # A resident's codes come from RES; an axis it settles is not asked of the model.
        rules = resident_rules(identity, self._codebooks) if identity.resident else NO_RULES
        nace_candidates = self._nace.shortlist(text, limit=self._limit, settled=rules.nace)
        if not identity.resident:
            esa_candidates = self._esa.shortlist(text, limit=self._limit)
        elif rules.esa_tied:
            esa_candidates = self._esa_resident.only(rules.esa)
        else:
            esa_candidates = self._esa_resident.shortlist(
                text, limit=self._limit, settled=rules.esa
            )
        nace = (
            _not_asked(NACE, nace_candidates)
            if rules.nace_settled
            else self._classifier.classify(
                nace_candidates, issuer_name=issuer_name, description=text, deadline=deadline
            )
        )
        esa = (
            _not_asked(ESA, esa_candidates)
            if rules.esa_settled
            else self._classifier.classify(
                esa_candidates, issuer_name=issuer_name, description=text, deadline=deadline
            )
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
            warnings=_input_warnings(cleaned, identity),
            rule_notes=rules.notes,
            settled_by_res=tuple(
                kind
                for kind, settled in ((NACE, rules.nace_settled), (ESA, rules.esa_settled))
                if settled
            ),
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
    from core.sources.llm_web import build_web_search

    resolved: Settings = settings if isinstance(settings, Settings) else get_settings()
    if codebooks is None:
        codebooks, _ = load_and_check(resolved, strict=False)
    classifier = build_classifier(resolved, codebook_version=codebooks.version.id)
    return SuggestionService(
        codebooks,
        gatherer=WebEvidenceGatherer(resolved),
        classifier=classifier,
        identifier=build_identifier(resolved),
        deadline_seconds=resolved.lookup_deadline_seconds,
        web_search=build_web_search(resolved, classifier),
    )


def issuer_name_of(
    request: SuggestionRequest, identity: IssuerIdentity, evidence: IssuerEvidence
) -> str | None:
    """The issuer's name as found, not as typed (Jakub, 30 Sept 2026: "find the issuer's name").

    For a resident issuer RES's ``obchodniJmeno`` (2 Oct 2026); then GLEIF's legal name; then
    the official name the model's web search found; then what the user typed; then OpenFIGI's
    market name and a web page's title. Until 30 Sept 2026 the typed name came first, so
    "adidas" was exported where GLEIF says "adidas AG".
    """
    record = identity.lei_record
    res = identity.res_record
    return (
        (res.name if res is not None else None)
        or (record.legal_name if record is not None else None)
        or evidence.found_name
        or request.name
        or identity.legal_name
        or evidence.issuer_name
    )


def _input_warnings(request: SuggestionRequest, identity: IssuerIdentity) -> tuple[str, ...]:
    """Residency that could not be settled, and a typed name that does not fit the ISIN."""
    from core.sources.names import names_agree

    record = identity.lei_record
    resident = _residency_warnings(request, identity)
    if not (request.isin and request.name and identity.legal_name):
        return tuple(resident)
    known = (
        identity.legal_name,
        *(record.other_names if record else ()),
        identity.instrument.name if identity.instrument else None,
        identity.res_record.name if identity.res_record else None,
    )
    if names_agree(request.name, *known):
        return tuple(resident)
    return (
        *resident,
        f"Zadaný název „{request.name}“ neodpovídá emitentovi ISIN {request.isin} "
        f"(podle registru „{identity.legal_name}“). Zkontrolujte ISIN i název – výsledek "
        "vychází z ISIN.",
    )


def _residency_warnings(request: SuggestionRequest, identity: IssuerIdentity) -> list[str]:
    """A resident whose codes RES could not give, or a CZ ISIN no register could place.

    None when RES answered: then the codes are RES's and the page says so on each code.
    """
    if identity.resident:
        if identity.res_record is not None:
            return []
        why = "zadané IČO" if identity.lei_record is None else "sídlo podle GLEIF"
        return [
            f"Emitent je rezident ČR ({why}), ale kódy z RES převzít nešlo – důvod je v "
            "poznámkách. Návrh vychází z rezidentských kódů ESA; ověřte ho."
        ]
    if identity.lei_record is None and (request.isin or "").startswith("CZ"):
        return [
            "ISIN má kód země CZ, ale registry emitenta nedohledaly, takže nelze ověřit, zda je "
            "rezident ČR – návrh počítá s nerezidentem. Českého emitenta zadejte jeho IČO."
        ]
    return []


def _is_ico_input(name: str) -> bool:
    """Digits and spaces only, 1 to 8 digits: an IČO typed into the name field."""
    return bool(_ICO_INPUT.fullmatch(name)) and bool(_ICO_DIGITS.fullmatch(name.replace(" ", "")))


def _clean_ico(typed: str, notes: list[str]) -> str | None:
    """The 8-digit IČO, zero-padded; a failed mod-11 check is a note, not a refusal.

    Some historic IČOs fail the check, so the register is asked anyway.
    """
    digits = typed.replace(" ", "")
    if not _ICO_DIGITS.fullmatch(digits):
        notes.append(f"„{typed}“ nevypadá jako IČO (1 až 8 číslic)")
        return None
    ico = digits.zfill(8)
    if not ico_checksum_ok(ico):
        notes.append(
            f"IČO {ico} nemá platnou kontrolní číslici (modulo 11) – v registrech ho hledám "
            "přesto, ověřte ho"
        )
    return ico


def _not_asked(kind: Kind, candidates: CandidateSet) -> Classification:
    """An axis RES settled: no model call was made, so no suggestion and no reason to state."""
    return Classification(
        kind=kind,
        abstained=True,
        candidates_considered=len(candidates),
        classified_at=datetime.now(UTC),
    )


__all__ = [
    "ESA",
    "NACE",
    "IssuerSuggestion",
    "SuggestionRequest",
    "SuggestionService",
    "build_service",
    "issuer_name_of",
]
