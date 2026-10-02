"""The pipeline for a Czech issuer: RES settles the codes, the model is asked only what RES
cannot answer, and the page says which it was.

The identity is faked (each register's answer is pinned in the tests above this one); the
codebook is the synthetic one with the whole resident block.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest

from config.settings import Settings
from core.classify.explain import decision_cs
from core.classify.llm import LlmClassifier
from core.classify.models import ESA, NACE
from core.classify.provider import NullLlmProvider, StubLlmProvider
from core.export.columns import EXPORT_COLUMNS, suggestion_row
from core.sources.ares import ResRecord
from core.sources.base import Provenance
from core.sources.ecb import EcbEntry
from core.sources.gleif import LeiRecord
from core.sources.identity import NO_IDENTITY, IssuerIdentity
from core.sources.web import StaticSearchProvider, WebEvidenceGatherer
from core.suggest import SuggestionRequest, SuggestionService, _input_warnings
from tests.classify.conftest import build_resident_codebooks

NOW = datetime(2026, 10, 2, tzinfo=UTC)
CEZ_ISIN = "CZ0005112300"
KB_ISIN = "CZ0008019106"


def lei_record(lei: str, name: str, ico: str | None, *, country: str = "CZ") -> LeiRecord:
    return LeiRecord(
        lei=lei,
        legal_name=name,
        other_names=(),
        jurisdiction=country,
        legal_address_country=country,
        headquarters_country=country,
        category="GENERAL",
        sub_category=None,
        legal_form_id="6CQN",
        legal_form_text=None,
        entity_status="ACTIVE",
        registration_status="ISSUED",
        provenance=Provenance(source="GLEIF", retrieved_at=NOW),
        registered_as=ico,
        registered_at="RA000163" if ico else None,
    )


def res(ico: str, name: str, *, sector: str | None, nace: str | None) -> ResRecord:
    return ResRecord(
        ico=ico,
        name=name,
        legal_form="121",
        sector=sector,
        nace=nace,
        nace_2008=nace,
        updated_on=date(2026, 9, 4),
        provenance=Provenance(source="RES", retrieved_at=NOW),
    )


CEZ = IssuerIdentity(
    isin=CEZ_ISIN,
    lei_record=lei_record("529900S5R9YHJHYKKG94", "ČEZ, a. s.", "45274649"),
    sources=("GLEIF", "RES"),
    res_record=res("45274649", "ČEZ, a. s.", sector="11001", nace="35110"),
)
KB = IssuerIdentity(
    isin=KB_ISIN,
    lei_record=lei_record("IYKCAVNFR8QGF00HV840", "Komerční banka, a.s.", "45317054"),
    sources=("GLEIF", "RES"),
    res_record=res("45317054", "Komerční banka, a.s.", sector="12203", nace="64190"),
)
KB_ON_THE_MFI_LIST = IssuerIdentity(
    isin=KB_ISIN,
    lei_record=KB.lei_record,
    sources=("GLEIF", "RES"),
    res_record=KB.res_record,
    ecb_entry=EcbEntry(
        lei="IYKCAVNFR8QGF00HV840",
        list_name="MFI",
        code="ECB_MFI:CREDIT_INSTITUTION",
        name="Komerční banka, a.s.",
        country="CZ",
        subtype=None,
        head_lei=None,
        as_of="2026-10-02",
    ),
)


class Identifier:
    """Answers every lookup with one identity and remembers what it was asked."""

    def __init__(self, identity: IssuerIdentity) -> None:
        self.identity = identity
        self.asked: list[tuple[str | None, str | None, str | None]] = []

    def identify(
        self, isin: str | None, *, name: str | None = None, ico: str | None = None
    ) -> IssuerIdentity:
        self.asked.append((isin, name, ico))
        return self.identity


def answer(code: str) -> str:
    return json.dumps(
        {
            "sufficient_evidence": True,
            "picks": [{"code": code, "confidence": "high", "justification": "Podle registru."}],
        }
    )


def service(
    identity: IssuerIdentity, provider: object = None
) -> tuple[SuggestionService, Identifier, object]:
    settings = Settings(
        web_min_interval_seconds=0.0, llm_cache_path=None, llm_api_key=None, wikimedia_enabled=False
    )
    gatherer = WebEvidenceGatherer(
        settings,
        provider=StaticSearchProvider({}),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
        sleep=lambda _: None,
    )
    stub = (
        provider
        if provider is not None
        else StubLlmProvider({"NACE": answer("64"), "ESA": answer("1221300")})
    )
    fake = Identifier(identity)
    return (
        SuggestionService(
            build_resident_codebooks(),
            gatherer=gatherer,
            classifier=LlmClassifier(stub),  # type: ignore[arg-type]
            identifier=fake,  # type: ignore[arg-type]
        ),
        fake,
        stub,
    )


class TestResSettlesBoth:
    def test_no_model_call_is_made(self) -> None:
        svc, _, stub = service(CEZ)
        suggestion = svc.suggest(SuggestionRequest(isin=CEZ_ISIN))
        assert stub.calls == []  # type: ignore[attr-defined]
        assert suggestion.settled_by_res == (NACE, ESA) and suggestion.settled
        assert not suggestion.answered

    def test_the_codes_are_res_s_and_say_so(self) -> None:
        suggestion = service(CEZ)[0].suggest(SuggestionRequest(isin=CEZ_ISIN))
        nace, esa = suggestion.nace_proposal, suggestion.esa_proposal
        assert nace is not None and nace.code == "35" and nace.basis == "rules"
        assert esa is not None and esa.code == "1100100" and esa.basis == "rules"
        decided = decision_cs(esa, suggestion.esa, suggestion.esa_candidates)
        assert (
            decided
            == "převzato z RES (ARES), stav k 2026-09-04 – kód určuje registr, model se neptal."
        )
        assert esa.justification.startswith(
            "Podle registru, bez modelu – RES (ARES): institucionální"
        )

    def test_the_esa_shortlist_is_the_resident_block(self) -> None:
        suggestion = service(CEZ)[0].suggest(SuggestionRequest(isin=CEZ_ISIN))
        assert suggestion.esa_candidates.codes[0] == "1100100"
        assert all(code.startswith("1") for code in suggestion.esa_candidates.codes)

    def test_the_name_is_res_s_and_no_resident_warning_is_shown(self) -> None:
        suggestion = service(CEZ)[0].suggest(SuggestionRequest(isin=CEZ_ISIN, name="CEZ"))
        assert suggestion.issuer_name == "ČEZ, a. s."
        assert suggestion.warnings == ()
        assert suggestion.source_label == "GLEIF+RES+WEB"

    def test_the_six_export_columns_carry_res_s_codes(self) -> None:
        row = suggestion_row(service(CEZ)[0].suggest(SuggestionRequest(isin=CEZ_ISIN)))
        exported = {column: row[column] for column in EXPORT_COLUMNS}
        assert exported["issuer_name"] == "ČEZ, a. s."
        assert exported["NACE_code"] == "35" and exported["ESA_code"] == "1100100"
        assert exported["NACE_cts_id"] and exported["ESA_cts_id"]


class TestTie:
    def test_only_the_tied_codes_go_to_the_model(self) -> None:
        """KB without the ECB lists: bank or other deposit-taker, both under foreign control."""
        svc, _, stub = service(KB)
        suggestion = svc.suggest(SuggestionRequest(isin=KB_ISIN))
        calls = stub.calls  # type: ignore[attr-defined]
        assert [prompt.kind for prompt in calls] == ["ESA"], "NACE is RES's"
        assert calls[0].candidate_codes == ("1221300", "1224300")
        proposal = suggestion.esa_proposal
        assert proposal is not None and proposal.basis == "model" and proposal.code == "1221300"

    def test_without_a_model_the_tie_is_left_to_mo_and_explained(self) -> None:
        suggestion = service(KB, NullLlmProvider())[0].suggest(SuggestionRequest(isin=KB_ISIN))
        assert suggestion.esa_proposal is None
        assert suggestion.esa_candidates.codes == ("1221300", "1224300")
        decided = decision_cs(None, suggestion.esa, suggestion.esa_candidates)
        assert decided.startswith("nerozhodnuto – RES (ARES) určuje sektor i typ kontroly")
        assert any("vyberte jeden z kódů 1221300, 1224300" in note for note in suggestion.all_notes)
        row = suggestion_row(suggestion)
        assert row["ESA_code"] is None and row["NACE_code"] == "64"

    def test_the_ecb_mfi_list_decides_it_and_no_model_is_asked(self) -> None:
        svc, _, stub = service(KB_ON_THE_MFI_LIST)
        suggestion = svc.suggest(SuggestionRequest(isin=KB_ISIN))
        assert stub.calls == []  # type: ignore[attr-defined]
        assert suggestion.esa_proposal is not None and suggestion.esa_proposal.code == "1221300"


def test_an_axis_res_cannot_settle_still_goes_to_the_model() -> None:
    no_nace = IssuerIdentity(
        isin=CEZ_ISIN,
        lei_record=CEZ.lei_record,
        res_record=res("45274649", "ČEZ, a. s.", sector="11001", nace=None),
    )
    svc, _, stub = service(no_nace)
    suggestion = svc.suggest(SuggestionRequest(isin=CEZ_ISIN, description="Výroba elektřiny."))
    assert [prompt.kind for prompt in stub.calls] == ["NACE"]  # type: ignore[attr-defined]
    assert suggestion.settled_by_res == (ESA,)
    assert (
        "NACE: RES neuvádí převažující činnost – kód vybírá klasifikátor." in suggestion.all_notes
    )


class TestResidentWithoutRes:
    def test_the_warning_says_res_could_not_give_the_codes(self) -> None:
        no_res = IssuerIdentity(isin=CEZ_ISIN, lei_record=CEZ.lei_record, notes=("RES: zdroj …",))
        svc, _, stub = service(no_res)
        suggestion = svc.suggest(SuggestionRequest(isin=CEZ_ISIN, description="Energetika."))
        (warning,) = suggestion.warnings
        assert warning.startswith("Emitent je rezident ČR (sídlo podle GLEIF), ale kódy z RES")
        assert all(code.startswith("1") for code in suggestion.esa_candidates.codes)
        assert [prompt.kind for prompt in stub.calls] == ["NACE", "ESA"]  # type: ignore[attr-defined]

    def test_a_typed_ico_with_no_record_is_said_to_be_the_reason(self) -> None:
        typed = IssuerIdentity(typed_ico="49279866")
        (warning,) = _input_warnings(SuggestionRequest(ico="49279866"), typed)
        assert "(zadané IČO)" in warning


def test_a_cz_isin_no_register_could_place_keeps_a_reworded_warning() -> None:
    (warning,) = _input_warnings(SuggestionRequest(isin=CEZ_ISIN), NO_IDENTITY)
    assert warning.startswith("ISIN má kód země CZ, ale registry emitenta nedohledaly")
    assert "Českého emitenta zadejte jeho IČO." in warning


def test_a_foreign_seat_under_a_cz_isin_gets_no_resident_warning() -> None:
    foreign = IssuerIdentity(
        isin="CZ0000000005", lei_record=lei_record("7LTWFZYICNSX8D621K86", "DB", None, country="DE")
    )
    assert _input_warnings(SuggestionRequest(isin="CZ0000000005"), foreign) == ()


class TestIcoInput:
    @pytest.mark.parametrize(
        ("typed", "ico"),
        [("45274649", "45274649"), ("452 74 649", "45274649"), ("6947", "00006947")],
    )
    def test_digits_in_the_name_field_are_an_ico(self, typed: str, ico: str) -> None:
        cleaned, notes = SuggestionRequest(name=typed).cleaned()
        assert cleaned.ico == ico and cleaned.name is None
        assert notes == ()

    def test_a_failed_checksum_is_a_note_not_a_refusal(self) -> None:
        cleaned, notes = SuggestionRequest(name="12345678").cleaned()
        assert cleaned.ico == "12345678"
        assert notes == (
            "IČO 12345678 nemá platnou kontrolní číslici (modulo 11) – v registrech ho hledám "
            "přesto, ověřte ho",
        )

    @pytest.mark.parametrize("typed", ["123456789", "ČEZ", "45274649 a.s."])
    def test_anything_else_stays_a_name(self, typed: str) -> None:
        cleaned, _ = SuggestionRequest(name=typed).cleaned()
        assert cleaned.ico is None and cleaned.name == typed

    def test_the_api_field_is_cleaned_alike(self) -> None:
        assert SuggestionRequest(ico=" 45 274 649 ").cleaned()[0].ico == "45274649"
        cleaned, notes = SuggestionRequest(ico="CZ45274649").cleaned()
        assert cleaned.ico is None and notes == ("„CZ45274649“ nevypadá jako IČO (1 až 8 číslic)",)

    def test_an_ico_alone_is_a_request(self) -> None:
        assert not SuggestionRequest(ico="45274649").is_empty

    def test_the_identifier_gets_the_ico_and_the_export_echoes_it(self) -> None:
        svc, fake, _ = service(CEZ)
        suggestion = svc.suggest(SuggestionRequest(name="45274649"))
        assert fake.asked == [(None, None, "45274649")]
        assert suggestion.typed_name_apart is None
        assert suggestion_row(suggestion)["IN_name"] == "45274649"
