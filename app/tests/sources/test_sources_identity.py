"""The identity step: GLEIF then OpenFIGI, fail-soft, with the reasons a reviewer needs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from config.settings import Settings
from core.sources.base import Provenance, SourceResponseError, SourceUnavailableError
from core.sources.gleif import GleifSource, LeiRecord
from core.sources.identity import NO_IDENTITY, IssuerIdentifier, IssuerIdentity
from core.sources.openfigi import FigiInstrument, OpenFigiSource
from tests.sources.conftest import (
    FIGI_DEUTSCHE_BANK,
    FIGI_FUND,
    FIGI_NOT_FOUND,
    GLEIF_DEUTSCHE_BANK,
    figi_client,
    gleif_client,
)

DB_ISIN = "DE0005140008"
DB_LEI = "7LTWFZYICNSX8D621K86"


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "gleif_min_interval_seconds": 0.0,
        "gleif_max_attempts": 1,
        "gleif_fetch_parents": False,
        "openfigi_min_interval_seconds": 0.0,
        "openfigi_max_attempts": 1,
    }
    base.update(overrides)
    return Settings(**base)


class FakeGleif:
    """A GLEIF client scripted per ISIN: a record, ``None``, or an exception to raise."""

    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.asked: list[str] = []
        self.closed = False

    def find_by_isin(self, isin: str) -> LeiRecord | None:
        self.asked.append(isin)
        outcome = self.outcomes.get(isin)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]

    def close(self) -> None:
        self.closed = True


class FakeFigi:
    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.asked: list[str] = []
        self.closed = False

    def map_isin(self, isin: str) -> FigiInstrument | None:
        self.asked.append(isin)
        outcome = self.outcomes.get(isin)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]

    def close(self) -> None:
        self.closed = True


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

DB_RECORD = LeiRecord(
    lei=DB_LEI,
    legal_name="DEUTSCHE BANK AKTIENGESELLSCHAFT",
    other_names=(),
    jurisdiction="DE",
    legal_address_country="DE",
    headquarters_country="DE",
    category="GENERAL",
    sub_category=None,
    legal_form_id="6QQB",
    legal_form_text=None,
    entity_status="ACTIVE",
    registration_status="ISSUED",
    provenance=Provenance(source="GLEIF", retrieved_at=NOW),
)
DB_INSTRUMENT = FigiInstrument(
    isin=DB_ISIN,
    figi="BBG000BBZTH2",
    name="DEUTSCHE BANK AG-REGISTERED",
    ticker="DBK",
    security_type="Common Stock",
    security_type2="Common Stock",
    market_sector="Equity",
    exchange_code="GR",
    listings=289,
    provenance=Provenance(source="OPENFIGI", retrieved_at=NOW),
)


def identifier(
    gleif: dict[str, object] | None = None,
    figi: dict[str, object] | None = None,
    **overrides: object,
) -> tuple[IssuerIdentifier, FakeGleif, FakeFigi]:
    fake_gleif = FakeGleif(gleif or {})
    fake_figi = FakeFigi(figi or {})
    return (
        IssuerIdentifier(settings(**overrides), gleif=fake_gleif, openfigi=fake_figi),  # type: ignore[arg-type]
        fake_gleif,
        fake_figi,
    )


class TestBothRegisters:
    def test_gleif_names_the_issuer_and_both_are_cited(self) -> None:
        ident, _, _ = identifier({DB_ISIN: DB_RECORD}, {DB_ISIN: DB_INSTRUMENT})
        identity = ident.identify(DB_ISIN)
        assert identity.found
        assert identity.legal_name == "DEUTSCHE BANK AKTIENGESELLSCHAFT"
        assert identity.lei == DB_LEI
        assert identity.country == "DE"
        assert identity.sources == ("GLEIF", "OPENFIGI")
        assert [source.title for source in identity.evidence] == [
            f"GLEIF – záznam LEI {DB_LEI}",
            f"OpenFIGI – nástroj {DB_ISIN}",
        ]
        assert identity.notes == ()

    def test_the_fact_sheet_puts_gleif_first(self) -> None:
        ident, _, _ = identifier({DB_ISIN: DB_RECORD}, {DB_ISIN: DB_INSTRUMENT})
        sheet = ident.identify(DB_ISIN).fact_sheet()
        assert sheet.index("GLEIF (LEI") < sheet.index("OpenFIGI (ISIN")

    def test_openfigi_names_the_issuer_when_gleif_has_no_mapping(self) -> None:
        """The ETF case: GLEIF's ISIN coverage is not complete, so the fallback matters."""
        ident, _, _ = identifier({}, {DB_ISIN: DB_INSTRUMENT})
        identity = ident.identify(DB_ISIN)
        assert identity.legal_name == "DEUTSCHE BANK AG-REGISTERED"
        assert identity.lei is None
        assert identity.sources == ("GLEIF", "OPENFIGI")
        assert "GLEIF nemá k tomuto ISIN přiřazen LEI emitenta" in identity.notes

    def test_nothing_found_is_not_an_error(self) -> None:
        ident, _, _ = identifier({}, {})
        identity = ident.identify(DB_ISIN)
        assert not identity.found
        assert identity.legal_name is None
        assert identity.fact_sheet() == ""
        assert len(identity.notes) == 2


class TestFailSoft:
    def test_an_outage_is_a_note_and_the_other_register_still_answers(self) -> None:
        ident, _, _ = identifier(
            {DB_ISIN: SourceUnavailableError("GLEIF returned HTTP 503")},
            {DB_ISIN: DB_INSTRUMENT},
        )
        identity = ident.identify(DB_ISIN)
        assert identity.legal_name == "DEUTSCHE BANK AG-REGISTERED"
        assert identity.sources == ("OPENFIGI",)
        assert any("GLEIF: zdroj se nepodařilo dotázat" in note for note in identity.notes)

    def test_a_bad_answer_is_a_note_too(self) -> None:
        ident, _, _ = identifier(
            {DB_ISIN: DB_RECORD}, {DB_ISIN: SourceResponseError("OpenFIGI returned HTTP 400")}
        )
        identity = ident.identify(DB_ISIN)
        assert identity.lei == DB_LEI
        assert identity.sources == ("GLEIF",)
        assert any("OPENFIGI: zdroj se nepodařilo dotázat" in note for note in identity.notes)

    def test_an_unexpected_exception_is_not_swallowed(self) -> None:
        """Only source errors are fail-soft; a programming error must surface in tests."""
        ident, _, _ = identifier({DB_ISIN: KeyError("bug")}, {})
        with pytest.raises(KeyError):
            ident.identify(DB_ISIN)


class TestSwitches:
    def test_no_isin_means_no_identity_and_no_calls(self) -> None:
        ident, gleif, figi = identifier({DB_ISIN: DB_RECORD}, {DB_ISIN: DB_INSTRUMENT})
        assert ident.identify(None) is NO_IDENTITY
        assert ident.identify("") is NO_IDENTITY
        assert gleif.asked == [] and figi.asked == []

    def test_a_disabled_register_is_skipped_silently(self) -> None:
        ident, gleif, figi = identifier(
            {DB_ISIN: DB_RECORD}, {DB_ISIN: DB_INSTRUMENT}, openfigi_enabled=False
        )
        identity = ident.identify(DB_ISIN)
        assert identity.sources == ("GLEIF",)
        assert figi.asked == [] and gleif.asked == [DB_ISIN]
        assert identity.notes == ()

    def test_both_disabled_says_so(self) -> None:
        ident, gleif, figi = identifier(
            {DB_ISIN: DB_RECORD}, {}, gleif_enabled=False, openfigi_enabled=False
        )
        identity = ident.identify(DB_ISIN)
        assert not ident.enabled
        assert not identity.found
        assert identity.isin == DB_ISIN
        assert any("vypnuto" in note for note in identity.notes)
        assert gleif.asked == [] and figi.asked == []

    def test_close_closes_both(self) -> None:
        ident, gleif, figi = identifier()
        ident.close()
        assert gleif.closed and figi.closed


class TestWithRealClients:
    """The identifier over the real adapters and mock transports, end to end."""

    def test_deutsche_bank(self) -> None:
        config = settings()
        ident = IssuerIdentifier(
            config,
            gleif=GleifSource(
                config,
                client=gleif_client(by_isin={DB_ISIN: GLEIF_DEUTSCHE_BANK}),
                sleep=lambda _: None,
            ),
            openfigi=OpenFigiSource(
                config, client=figi_client(FIGI_DEUTSCHE_BANK), sleep=lambda _: None
            ),
        )
        identity = ident.identify(DB_ISIN)
        assert identity.lei == DB_LEI
        assert identity.instrument is not None and identity.instrument.market_sector == "Equity"
        assert identity.sources == ("GLEIF", "OPENFIGI")

    def test_the_etf_only_openfigi_knows(self) -> None:
        config = settings()
        ident = IssuerIdentifier(
            config,
            gleif=GleifSource(config, client=gleif_client(), sleep=lambda _: None),
            openfigi=OpenFigiSource(config, client=figi_client(FIGI_FUND), sleep=lambda _: None),
        )
        identity = ident.identify("IE00B4L5Y983")
        assert identity.lei is None
        assert identity.legal_name == "ISHARES CORE MSCI WORLD"

    def test_an_isin_nobody_knows(self) -> None:
        config = settings()
        ident = IssuerIdentifier(
            config,
            gleif=GleifSource(config, client=gleif_client(), sleep=lambda _: None),
            openfigi=OpenFigiSource(
                config, client=figi_client(FIGI_NOT_FOUND), sleep=lambda _: None
            ),
        )
        identity = ident.identify("FR0129895324")
        assert not identity.found
        assert identity.sources == ("GLEIF", "OPENFIGI")


def test_identity_defaults_are_empty() -> None:
    identity = IssuerIdentity()
    assert identity.legal_name is None and identity.lei is None and identity.country is None
    assert identity.facts() == () and identity.evidence == () and identity.sources == ()
