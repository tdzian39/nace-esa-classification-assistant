"""The pipeline with the identity step: what an ISIN alone now produces, and what it must not.

The registers are faked here so each behaviour can be pinned precisely; the API tests run
the same pipeline over the real adapters and mock transports.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from config.settings import Settings
from core.classify.llm import LlmClassifier
from core.classify.provider import NullLlmProvider, StubLlmProvider
from core.export.columns import SUGGESTION_COLUMNS, SUGGESTION_TEXT_COLUMNS, suggestion_row
from core.sources.base import Provenance
from core.sources.gleif import LeiRecord, ParentEntity
from core.sources.identity import NO_IDENTITY, IssuerIdentity
from core.sources.web import EvidenceSource, SearchHit, StaticSearchProvider, WebEvidenceGatherer
from core.suggest import SuggestionRequest, SuggestionService
from tests.classify.conftest import build_codebooks

ISIN = "FR0129895324"
LEI = "5299006ZHG3IXU0PNJ56"
LEGAL_NAME = "BMW Finance N.V."
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

PAGE = (
    "<html><head><meta name='description' content='BMW Finance N.V. is the financing company "
    "of the BMW Group.'></head><body><p>It issues bonds and lends the proceeds to group "
    "companies. It holds no banking licence.</p></body></html>"
)

BMW_RECORD = LeiRecord(
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
    provenance=Provenance(source="GLEIF", retrieved_at=NOW),
    direct_parent=ParentEntity(
        lei="YEH5ZCD6E441RHVHD759",
        legal_name="Bayerische Motoren Werke Aktiengesellschaft",
        country="DE",
    ),
    ultimate_parent=ParentEntity(
        lei="YEH5ZCD6E441RHVHD759",
        legal_name="Bayerische Motoren Werke Aktiengesellschaft",
        country="DE",
    ),
)

BMW_IDENTITY = IssuerIdentity(
    isin=ISIN,
    lei_record=BMW_RECORD,
    sources=("GLEIF", "OPENFIGI"),
    evidence=(EvidenceSource(url=BMW_RECORD.url, title=f"GLEIF – záznam LEI {LEI}", fetched=True),),
    notes=("OpenFIGI tento ISIN nezná",),
)


class FakeIdentifier:
    def __init__(self, identity: IssuerIdentity) -> None:
        self.identity = identity
        self.asked: list[str | None] = []

    def identify(self, isin: str | None) -> IssuerIdentity:
        self.asked.append(isin)
        return self.identity if isin else NO_IDENTITY


def answer(code: str) -> str:
    return json.dumps(
        {
            "sufficient_evidence": True,
            "picks": [{"code": code, "confidence": "high", "justification": "Financuje skupinu."}],
        }
    )


def service(
    identity: IssuerIdentity = BMW_IDENTITY,
    *,
    provider: StubLlmProvider | None = None,
    hits: dict[str, tuple[SearchHit, ...]] | None = None,
    identifier: FakeIdentifier | None = None,
) -> tuple[SuggestionService, FakeIdentifier, StubLlmProvider]:
    settings = Settings(web_min_interval_seconds=0.0, llm_cache_path=None, llm_api_key=None)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
        )
    )
    gatherer = WebEvidenceGatherer(
        settings, provider=StaticSearchProvider(hits or {}), client=client, sleep=lambda _: None
    )
    stub = provider or StubLlmProvider({"NACE": answer("64"), "ESA": answer("2002703")})
    fake = identifier or FakeIdentifier(identity)
    return (
        SuggestionService(
            build_codebooks(),
            gatherer=gatherer,
            classifier=LlmClassifier(stub),
            identifier=fake,  # type: ignore[arg-type]
        ),
        fake,
        stub,
    )


class TestIsinAlone:
    def test_the_register_names_the_issuer(self) -> None:
        svc, _, _ = service()
        suggestion = svc.suggest(SuggestionRequest(isin=ISIN))
        assert suggestion.issuer_name == LEGAL_NAME
        assert suggestion.identity.lei == LEI

    def test_the_facts_reach_the_model(self) -> None:
        svc, _, stub = service()
        svc.suggest(SuggestionRequest(isin=ISIN))
        assert stub.calls, "the classifier must have been asked"
        prompt = stub.calls[0].user
        assert f"GLEIF (LEI {LEI})" in prompt
        assert "Bayerische Motoren Werke" in prompt
        assert "jiné zemi (DE)" in prompt

    def test_the_facts_shortlist_the_captive_family_through_the_parent_and_the_name(self) -> None:
        """No web description at all: the shortlist has to come from the register facts."""
        svc, _, _ = service(provider=StubLlmProvider({}))
        suggestion = svc.suggest(SuggestionRequest(isin=ISIN))
        assert suggestion.description is None
        assert suggestion.classifier_text.startswith("GLEIF (LEI")
        # 'BMW Finance N.V.' fires the finance-vehicle name hint: division 64 and the
        # captive family (2002703 here) are on the list even with no description at all.
        assert "64" in suggestion.nace_candidates.codes
        assert "2002703" in suggestion.esa_candidates.codes

    def test_the_register_page_leads_the_evidence_and_the_sources_are_named(self) -> None:
        svc, _, _ = service()
        suggestion = svc.suggest(SuggestionRequest(isin=ISIN))
        assert suggestion.evidence_sources[0].url == BMW_RECORD.url
        assert suggestion.sources == ("GLEIF", "OPENFIGI", "WEB")
        assert suggestion.source_label == "GLEIF+OPENFIGI+WEB"

    def test_register_notes_surface(self) -> None:
        svc, _, _ = service()
        suggestion = svc.suggest(SuggestionRequest(isin=ISIN))
        assert "OpenFIGI tento ISIN nezná" in suggestion.all_notes

    def test_the_legal_name_becomes_the_search_query(self) -> None:
        """The web is searched for the issuer, not for a twelve-character code."""
        hits = {LEGAL_NAME: (SearchHit(url="https://example.com/bmw", title=LEGAL_NAME),)}
        svc, _, stub = service(hits=hits)
        suggestion = svc.suggest(SuggestionRequest(isin=ISIN))
        assert suggestion.description is not None and "financing company" in suggestion.description
        assert suggestion.evidence.query == LEGAL_NAME
        prompt = stub.calls[0].user
        assert "financing company" in prompt and "GLEIF (LEI" in prompt

    def test_a_malformed_isin_is_never_looked_up(self) -> None:
        svc, fake, _ = service()
        suggestion = svc.suggest(SuggestionRequest(isin="NOTANISIN", name="BMW Finance"))
        assert fake.asked == [None]
        assert suggestion.identity is NO_IDENTITY
        assert any("ISIN" in note for note in suggestion.all_notes)


class TestPrecedence:
    def test_a_typed_name_wins_over_the_register(self) -> None:
        svc, _, _ = service()
        suggestion = svc.suggest(SuggestionRequest(isin=ISIN, name="BMW Finance NV"))
        assert suggestion.issuer_name == "BMW Finance NV"
        assert suggestion.identity.legal_name == LEGAL_NAME

    def test_a_typed_description_is_kept_and_the_facts_are_appended(self) -> None:
        svc, _, stub = service()
        suggestion = svc.suggest(
            SuggestionRequest(isin=ISIN, description="Captive finance vehicle of the BMW group.")
        )
        assert suggestion.description == "Captive finance vehicle of the BMW group."
        assert suggestion.classifier_text.startswith("Captive finance vehicle")
        assert "GLEIF (LEI" in stub.calls[0].user

    def test_a_name_lookup_never_asks_the_registers(self) -> None:
        svc, fake, _ = service()
        suggestion = svc.suggest(SuggestionRequest(name="Nordkap Funding B.V."))
        assert fake.asked == [None]
        assert suggestion.identity is NO_IDENTITY
        assert suggestion.source_label == "WEB"

    def test_a_service_without_an_identifier_behaves_as_before(self) -> None:
        settings = Settings(web_min_interval_seconds=0.0, llm_cache_path=None, llm_api_key=None)
        svc = SuggestionService(
            build_codebooks(),
            gatherer=WebEvidenceGatherer(
                settings, provider=StaticSearchProvider(()), sleep=lambda _: None
            ),
            classifier=LlmClassifier(NullLlmProvider()),
        )
        suggestion = svc.suggest(SuggestionRequest(isin=ISIN))
        assert suggestion.identity is NO_IDENTITY
        assert suggestion.issuer_name is None
        assert suggestion.source_label == "WEB"


class TestRow:
    def test_the_row_carries_the_lei_the_country_and_the_sources(self) -> None:
        svc, _, _ = service()
        row = suggestion_row(svc.suggest(SuggestionRequest(isin=ISIN)))
        assert row["issuer_lei"] == LEI
        assert row["issuer_country"] == "NL"
        assert row["source"] == "GLEIF+OPENFIGI+WEB"
        assert row["evidence_urls"][0] == BMW_RECORD.url
        assert tuple(row) == SUGGESTION_COLUMNS

    def test_the_lei_is_a_text_column(self) -> None:
        assert "issuer_lei" in SUGGESTION_TEXT_COLUMNS

    def test_a_name_lookup_leaves_the_register_columns_empty(self) -> None:
        svc, _, _ = service()
        row = suggestion_row(svc.suggest(SuggestionRequest(name="Nordkap Funding B.V.")))
        assert row["issuer_lei"] is None and row["issuer_country"] is None
        assert row["source"] == "WEB"
