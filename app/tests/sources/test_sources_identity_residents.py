"""The identity step for a Czech issuer: residency by the register's seat, then RES by IČO.

The real GLEIF and ARES adapters run over mock transports (trimmed live payloads of 2 Oct
2026, ``conftest.py``); nothing touches the network.
"""

from __future__ import annotations

from config.settings import Settings
from core.sources.ares import AresSource
from core.sources.gleif import GleifSource
from core.sources.identity import IssuerIdentifier
from tests.sources.conftest import (
    GLEIF_CEZ,
    GLEIF_CZ_FUND,
    GLEIF_DEUTSCHE_BANK,
    GLEIF_KB,
    GLEIF_MF,
    RES_CEZ,
    RES_KB,
    RES_MF,
    ares_client,
    gleif_client,
    res_payload,
)

CEZ_ISIN = "CZ0005112300"
KB_ISIN = "CZ0008019106"
GOV_BOND = "CZ0001004469"
DB_ISIN = "DE0005140008"
MF_LEI = "3157007EFDLQABN47912"


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "gleif_min_interval_seconds": 0.0,
        "gleif_max_attempts": 1,
        "gleif_fetch_parents": False,
        "openfigi_enabled": False,
        "ares_enabled": True,
        "ares_min_interval_seconds": 0.0,
        "ares_max_attempts": 1,
        "firds_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def identifier(
    *,
    res: dict | None = None,
    ares_calls: list[str] | None = None,
    firds: object = None,
    **overrides: object,
) -> IssuerIdentifier:
    resolved = settings(**overrides)
    gleif = gleif_client(
        by_isin={CEZ_ISIN: GLEIF_CEZ, KB_ISIN: GLEIF_KB, DB_ISIN: GLEIF_DEUTSCHE_BANK},
        by_ico={"45317054": [GLEIF_KB], "45274649": [GLEIF_CEZ], "00006947": [GLEIF_MF]},
        records={MF_LEI: GLEIF_MF},
    )
    answers = {"45274649": RES_CEZ, "45317054": RES_KB, "00006947": RES_MF} if res is None else res
    return IssuerIdentifier(
        resolved,
        gleif=GleifSource(resolved, client=gleif),
        ares=AresSource(resolved, client=ares_client(answers, calls=ares_calls)),
        firds=firds,  # type: ignore[arg-type]
    )


class TestResidency:
    def test_a_czech_seat_makes_the_issuer_resident_and_res_is_asked(self) -> None:
        identity = identifier().identify(CEZ_ISIN)
        assert identity.resident
        assert identity.ico == "45274649"
        assert identity.res_record is not None and identity.res_record.sector == "11001"
        assert identity.sources == ("GLEIF", "RES")

    def test_the_res_line_joins_the_fact_sheet_and_the_page_joins_the_evidence(self) -> None:
        identity = identifier().identify(CEZ_ISIN)
        assert identity.facts()[-1].startswith("RES (ČSÚ, přes ARES, stav k 2026-09-04)")
        assert "[RES]" in identity.fact_sheet()
        assert identity.evidence[-1].url == "https://ares.gov.cz/ekonomicke-subjekty?ico=45274649"

    def test_a_foreign_issuer_is_never_sent_to_res(self) -> None:
        calls: list[str] = []
        identity = identifier(ares_calls=calls).identify(DB_ISIN)
        assert not identity.resident and identity.res_record is None
        assert calls == []

    def test_the_seat_decides_not_the_isin_prefix(self) -> None:
        """A foreign issuer under a CZ ISIN stays foreign; a Czech one under XS is resident."""
        gleif = gleif_client(
            by_isin={"CZ0000000005": GLEIF_DEUTSCHE_BANK, "XS0000000009": GLEIF_CEZ}
        )
        resolved = settings()
        ids = IssuerIdentifier(
            resolved,
            gleif=GleifSource(resolved, client=gleif),
            ares=AresSource(resolved, client=ares_client({"45274649": RES_CEZ})),
        )
        assert not ids.identify("CZ0000000005").resident
        assert ids.identify("XS0000000009").resident


class TestTypedIco:
    def test_an_ico_finds_the_lei_and_the_res_record(self) -> None:
        identity = identifier().identify(None, ico="45317054")
        assert identity.resident and identity.typed_ico == "45317054"
        assert identity.lei == "IYKCAVNFR8QGF00HV840"
        assert (
            identity.res_record is not None and identity.res_record.name == "Komerční banka, a.s."
        )
        assert identity.sources == ("GLEIF", "RES")

    def test_an_ico_gleif_does_not_know_is_still_resident(self) -> None:
        """The Prague branch of ING Bank N.V. has no LEI; RES still answers by the IČO."""
        ing = {
            "49279866": res_payload(
                "49279866", "ING Bank N.V.", legal_form="421", sector="12203", nace="64190"
            )
        }
        identity = identifier(res=ing).identify(None, ico="49279866")
        assert identity.resident and identity.lei is None
        assert identity.res_record is not None and identity.res_record.legal_form == "421"
        assert "GLEIF nevede k IČO 49279866 žádný záznam LEI" in identity.notes

    def test_with_gleif_off_the_ico_goes_to_res_alone(self) -> None:
        identity = identifier(gleif_enabled=False).identify(None, ico="45274649")
        assert identity.resident and identity.sources == ("RES",)

    def test_an_isin_wins_over_a_typed_ico_of_another_czech_issuer(self) -> None:
        calls: list[str] = []
        identity = identifier(ares_calls=calls).identify(CEZ_ISIN, ico="45317054")
        assert identity.ico == "45274649"
        assert calls and calls[0].endswith("/45274649")
        assert any("zadané IČO 45317054 se liší" in note for note in identity.notes)

    def test_a_typed_ico_does_not_make_a_foreign_isin_resident(self) -> None:
        identity = identifier().identify(DB_ISIN, ico="45317054")
        assert not identity.resident and identity.res_record is None
        assert any("k emitentovi ISIN nepatří (sídlo podle GLEIF DE)" in n for n in identity.notes)


class TestRegisterSaysNo:
    def test_a_404_is_an_answer_said_as_such(self) -> None:
        identity = identifier(res={}).identify(CEZ_ISIN)
        assert identity.resident and identity.res_record is None
        assert "RES" in identity.sources, "RES answered: it holds no such IČO"
        assert any(
            note.startswith("RES (ARES) nemá záznam s IČO 45274649") for note in identity.notes
        )

    def test_an_outage_is_never_said_to_be_a_404(self) -> None:
        identity = identifier(res={"45274649": 503}).identify(CEZ_ISIN)
        assert identity.res_record is None
        assert "RES" not in identity.sources
        assert any(note.startswith("RES: zdroj se nepodařilo dotázat") for note in identity.notes)
        assert not any("nemá záznam" in note for note in identity.notes)

    def test_a_czech_fund_without_an_ico_is_resident_but_res_cannot_be_asked(self) -> None:
        calls: list[str] = []
        gleif = gleif_client(by_isin={"CZ0008474780": GLEIF_CZ_FUND})
        resolved = settings()
        identity = IssuerIdentifier(
            resolved,
            gleif=GleifSource(resolved, client=gleif),
            ares=AresSource(resolved, client=ares_client({}, calls=calls)),
        ).identify("CZ0008474780")
        assert identity.resident and identity.ico is None
        assert calls == []
        assert any(note.startswith("GLEIF u emitenta neuvádí IČO") for note in identity.notes)

    def test_ares_switched_off_is_a_note(self) -> None:
        calls: list[str] = []
        identity = identifier(ares_enabled=False, ares_calls=calls).identify(CEZ_ISIN)
        assert identity.resident and identity.res_record is None and calls == []
        assert any("ARES je vypnutý" in note for note in identity.notes)


def test_a_government_bond_reaches_the_ministry_through_firds() -> None:
    """GLEIF maps no Czech government bond; FIRDS names the Ministry of Finance's LEI."""
    identity = identifier(firds_enabled=True, firds=lambda isin: MF_LEI).identify(GOV_BOND)
    assert identity.sources == ("GLEIF", "FIRDS", "RES")
    assert identity.lei == MF_LEI and identity.resident
    assert identity.res_record is not None and identity.res_record.sector == "13110"
    assert identity.res_record.nace == "84110"
