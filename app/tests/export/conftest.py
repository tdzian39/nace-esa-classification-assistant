"""Builders for the export tests: one real Tool 1 suggestion, assembled without the pipeline.

The suggestion is made of the real types (``IssuerSuggestion``, ``Classification``,
``CandidateSet``, ``IssuerIdentity``, ``LeiRecord``) rather than run through
``SuggestionService``, so every value the row contract reads is pinned here. The issuer is
fictional; the shape is what the pipeline produces for an ISIN lookup that GLEIF answered
and OpenFIGI did not.

The CTS IDs carry a leading zero on purpose. Real CTS IDs are opaque text (the NACE ones are
455-542 today), and a leading zero here is what makes a test fail if the export ever turns
an ID into a number.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.classify.models import ESA, NACE, Candidate, CandidateSet, Classification, Suggestion
from core.sources.base import Provenance
from core.sources.gleif import LeiRecord
from core.sources.identity import NO_IDENTITY, IssuerIdentity
from core.sources.web import EvidenceSource, IssuerEvidence
from core.suggest import IssuerSuggestion, SuggestionRequest

ISIN = "NL0012345672"
LEI = "529900NORDKAPAGRAR42"
LEGAL_NAME = "Nordkap Agrar N.V."
DESCRIPTION = "Nordkap Agrar N.V. pěstuje obilí a chová skot na farmách v Nizozemsku."
WEB_URL = "https://nordkap-agrar.example/o-nas"
CREATED = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
CODEBOOK_VERSION = "cb-0123456789abcdef"
MODEL = "stub-model"
PROMPT_VERSION = "test-1"
ABSTAIN_REASON = "model není nastaven"

NACE_CANDIDATES = CandidateSet(
    kind=NACE,
    candidates=(
        Candidate(NACE, "01", "0455", "Rostlinná a živočišná výroba, myslivost"),
        Candidate(NACE, "10", "0464", "Výroba potravinářských výrobků"),
        Candidate(NACE, "46", "0500", "Velkoobchod, kromě motorových vozidel"),
    ),
    considered=87,
    filter_name="test",
)
ESA_CANDIDATES = CandidateSet(
    kind=ESA,
    candidates=(
        Candidate(ESA, "2001003", "0613", "Nefinanční podniky pod zahraniční kontrolou"),
        Candidate(ESA, "2001002", "0612", "Nefinanční podniky soukromé národní"),
        Candidate(ESA, "2001001", "0611", "Nefinanční podniky veřejné"),
    ),
    considered=56,
    filter_name="test",
)

LEI_RECORD = LeiRecord(
    lei=LEI,
    legal_name=LEGAL_NAME,
    other_names=(),
    jurisdiction="NL",
    legal_address_country="NL",
    headquarters_country="NL",
    category="GENERAL",
    sub_category=None,
    legal_form_id="B5PM",
    legal_form_text=None,
    entity_status="ACTIVE",
    registration_status="ISSUED",
    provenance=Provenance(source="GLEIF", retrieved_at=CREATED),
)

IDENTITY = IssuerIdentity(
    isin=ISIN,
    lei_record=LEI_RECORD,
    sources=("GLEIF",),
    evidence=(EvidenceSource(url=LEI_RECORD.url, title=f"GLEIF – záznam LEI {LEI}"),),
    notes=("OpenFIGI tento ISIN nezná",),
)


def _classification(candidates: CandidateSet, *, abstain: bool) -> Classification:
    """Three ranked picks straight from the shortlist, or an abstention with its reason."""
    if abstain:
        return Classification(
            kind=candidates.kind,
            abstained=True,
            abstain_reason=ABSTAIN_REASON,
            candidates_considered=len(candidates),
        )
    confidences = ("high", "medium", "low")
    return Classification(
        kind=candidates.kind,
        suggestions=tuple(
            Suggestion.from_candidate(
                candidate,
                confidence=confidence,  # type: ignore[arg-type]
                justification=f"Zdůvodnění pro {candidate.code}.",
                rank=rank,
            )
            for rank, (candidate, confidence) in enumerate(
                zip(candidates.candidates, confidences, strict=True), start=1
            )
        ),
        candidates_considered=len(candidates),
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        classified_at=CREATED,
    )


def suggestion(
    *, isin: str | None = ISIN, name: str | None = None, abstain: bool = False
) -> IssuerSuggestion:
    """A finished suggestion; ``isin=None`` makes it a name-only lookup with no register facts."""
    evidence = IssuerEvidence(
        query=name or LEGAL_NAME,
        issuer_name=name or LEGAL_NAME,
        description=DESCRIPTION,
        sources=(EvidenceSource(url=WEB_URL, title=LEGAL_NAME, fetched=True),),
        provenance=Provenance(source="WEB", retrieved_at=CREATED, detail="web:search"),
    )
    return IssuerSuggestion(
        request=SuggestionRequest(isin=isin, name=name),
        evidence=evidence,
        nace=_classification(NACE_CANDIDATES, abstain=abstain),
        esa=_classification(ESA_CANDIDATES, abstain=abstain),
        nace_candidates=NACE_CANDIDATES,
        esa_candidates=ESA_CANDIDATES,
        codebook_version=CODEBOOK_VERSION,
        created_at=CREATED,
        identity=IDENTITY if isin else NO_IDENTITY,
    )
