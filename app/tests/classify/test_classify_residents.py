"""What RES settles for a Czech issuer: every row of the sector table, the S.122 and S.125
branches, the ties, the NACE fallbacks, and the note when two registers disagree.

The codebook is the synthetic one with the whole resident block (``conftest.py``); the RES,
GLEIF and ECB answers are built directly, so each rule is seen alone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from core.classify.residents import NO_RULES, resident_rules
from core.codebooks.models import CodebookSet
from core.codebooks.res_esa import DEPOSIT_TAKERS, DIRECT, NO_COUNTERPART, OTHER_INTERMEDIARIES
from core.sources.ares import ResRecord
from core.sources.base import Provenance
from core.sources.ecb import EcbEntry
from core.sources.gleif import LeiRecord
from core.sources.identity import IssuerIdentity
from tests.classify.conftest import build_resident_codebooks

NOW = datetime(2026, 10, 2, tzinfo=UTC)
LEI = "IYKCAVNFR8QGF00HV840"
AS_OF = ", as of 2026-09-04"


def codebooks(drop: str | None = None) -> CodebookSet:
    return build_resident_codebooks(drop)


BOOKS = codebooks()


def lei_record(lei: str = LEI) -> LeiRecord:
    return LeiRecord(
        lei=lei,
        legal_name="Subjekt, a.s.",
        other_names=(),
        jurisdiction="CZ",
        legal_address_country="CZ",
        headquarters_country="CZ",
        category="GENERAL",
        sub_category=None,
        legal_form_id="6CQN",
        legal_form_text=None,
        entity_status="ACTIVE",
        registration_status="ISSUED",
        provenance=Provenance(source="GLEIF", retrieved_at=NOW),
        registered_as="12345679",
        registered_at="RA000163",
    )


def ecb(code: str, list_name: str = "MFI") -> EcbEntry:
    return EcbEntry(
        lei=LEI,
        list_name=list_name,
        code=code,
        name="Subjekt, a.s.",
        country="CZ",
        subtype=None,
        head_lei=None,
        as_of="2026-10-02",
    )


def identity(
    *,
    sector: str | None = None,
    nace: str | None = None,
    nace_2008: str | None = None,
    legal_form: str | None = "121",
    with_lei: bool = True,
    ecb_entry: EcbEntry | None = None,
    ecb_as_of: str | None = None,
) -> IssuerIdentity:
    res = ResRecord(
        ico="12345679",
        name="Subjekt, a.s.",
        legal_form=legal_form,
        sector=sector,
        nace=nace,
        nace_2008=nace_2008,
        updated_on=date(2026, 9, 4),
        provenance=Provenance(source="RES", retrieved_at=NOW),
    )
    return IssuerIdentity(
        lei_record=lei_record() if with_lei else None,
        res_record=res,
        ecb_entry=ecb_entry,
        ecb_as_of=ecb_as_of,
        typed_ico=None if with_lei else "12345679",
    )


def esa_notes(rules) -> list[str]:
    return [note for note in rules.notes if note.startswith("ESA")]


def test_no_res_record_settles_nothing() -> None:
    assert resident_rules(IssuerIdentity(lei_record=lei_record()), BOOKS) is NO_RULES


class TestDirectRows:
    @pytest.mark.parametrize(("sector", "code"), sorted(DIRECT.items()))
    def test_the_sector_alone_settles_the_code(self, sector: str, code: str) -> None:
        rules = resident_rules(identity(sector=sector), BOOKS)
        assert dict(rules.esa) == {code: (f"register: RES sector {sector}{AS_OF}",)}
        assert rules.esa_settled and not rules.esa_tied
        assert esa_notes(rules) == []


class TestDepositTakers:
    @pytest.mark.parametrize("sector", ["12202", "12203"])
    def test_legal_form_205_is_a_savings_and_credit_cooperative(self, sector: str) -> None:
        rules = resident_rules(identity(sector=sector, legal_form="205"), BOOKS)
        cooperative = DEPOSIT_TAKERS[sector]["cooperative"]
        assert dict(rules.esa) == {
            cooperative: (f"register: RES sector {sector}{AS_OF}", "register: RES legal form 205")
        }

    def test_a_public_cooperative_has_no_code_so_bank_and_other_tie(self) -> None:
        rules = resident_rules(identity(sector="12201", legal_form="205"), BOOKS)
        assert set(rules.esa) == {"1221100", "1222100"}
        assert "BA0036 nemá veřejné spořitelní a úvěrní družstvo" in esa_notes(rules)[0]

    @pytest.mark.parametrize("sector", sorted(DEPOSIT_TAKERS))
    def test_a_credit_institution_on_the_ecb_mfi_list_is_a_bank(self, sector: str) -> None:
        entry = ecb("ECB_MFI:CREDIT_INSTITUTION")
        rules = resident_rules(identity(sector=sector, ecb_entry=entry), BOOKS)
        assert list(rules.esa) == [DEPOSIT_TAKERS[sector]["bank"]]
        assert rules.esa[DEPOSIT_TAKERS[sector]["bank"]][1] == (
            "register: ECB MFI list: credit institution"
        )

    def test_an_other_institution_on_the_mfi_list_is_another_deposit_taker(self) -> None:
        entry = ecb("ECB_MFI:OTHER_INSTITUTION")
        rules = resident_rules(identity(sector="12202", ecb_entry=entry), BOOKS)
        assert list(rules.esa) == ["1224200"]

    @pytest.mark.parametrize(("sector", "other"), [("12201", "1222100"), ("12203", "1224300")])
    def test_absent_from_the_mfi_list_is_another_deposit_taker(
        self, sector: str, other: str
    ) -> None:
        """Read for the LEI and not a credit institution: 1224c00, or 1222100 when public."""
        rules = resident_rules(identity(sector=sector, ecb_as_of="2026-10-02"), BOOKS)
        assert dict(rules.esa) == {
            other: (
                f"register: RES sector {sector}{AS_OF}",
                "register: not a credit institution on the ECB MFI list",
            )
        }

    def test_on_another_ecb_list_is_not_a_bank(self) -> None:
        entry = ecb("ECB_IF", list_name="IF")
        rules = resident_rules(identity(sector="12203", ecb_entry=entry), BOOKS)
        assert list(rules.esa) == ["1224300"]

    def test_without_a_lei_bank_and_other_deposit_taker_tie(self) -> None:
        """A branch of a foreign bank (pravniForma 421) has no LEI: the MFI list cannot say."""
        rules = resident_rules(identity(sector="12203", legal_form="421", with_lei=False), BOOKS)
        assert rules.esa_tied and set(rules.esa) == {"1221300", "1224300"}
        assert "emitent LEI nemá" in esa_notes(rules)[0]
        assert "vyberte jeden z kódů 1221300, 1224300" in esa_notes(rules)[0]

    def test_without_the_ecb_lists_an_as_ties_at_the_known_control_digit(self) -> None:
        rules = resident_rules(identity(sector="12202"), BOOKS)
        assert set(rules.esa) == {"1221200", "1224200"}, "never the cooperative: not a 205"
        assert "není načtený" in esa_notes(rules)[0]

    def test_tied_codes_carry_only_res_reasons(self) -> None:
        rules = resident_rules(identity(sector="12203"), BOOKS)
        assert all(
            reasons == (f"register: RES sector 12203{AS_OF}",) for reasons in rules.esa.values()
        )


class TestOtherIntermediaries:
    @pytest.mark.parametrize("sector", sorted(OTHER_INTERMEDIARIES))
    def test_a_member_of_the_ecb_fvc_list_is_securitisation(self, sector: str) -> None:
        entry = ecb("ECB_FVC", list_name="FVC")
        rules = resident_rules(identity(sector=sector, nace="64910", ecb_entry=entry), BOOKS)
        assert list(rules.esa) == [OTHER_INTERMEDIARIES[sector]["securitisation"]]

    @pytest.mark.parametrize(
        ("sector", "nace", "code"),
        [
            ("12503", "64910", "1250330"),
            ("12502", "64929", "1250230"),
            ("12501", "6492", "1250130"),
        ],
    )
    def test_a_lending_nace_class_is_a_lender(self, sector: str, nace: str, code: str) -> None:
        rules = resident_rules(identity(sector=sector, nace=nace), BOOKS)
        assert dict(rules.esa) == {
            code: (
                f"register: RES sector {sector}{AS_OF}",
                f"register: RES CZ-NACE {nace}: lending",
            )
        }

    def test_the_2008_code_serves_when_2025_is_missing(self) -> None:
        rules = resident_rules(identity(sector="12502", nace_2008="64921"), BOOKS)
        assert list(rules.esa) == ["1250230"]

    def test_otherwise_the_four_subtypes_tie(self) -> None:
        rules = resident_rules(identity(sector="12502", nace="64991"), BOOKS)
        assert set(rules.esa) == {"1250210", "1250220", "1250230", "1250240"}
        assert "ne jejich podtyp" in esa_notes(rules)[0]


class TestNoResidentCode:
    @pytest.mark.parametrize("sector", sorted(NO_COUNTERPART))
    def test_the_sector_has_no_resident_code_and_the_classifier_decides(self, sector: str) -> None:
        rules = resident_rules(identity(sector=sector, nace="84110"), BOOKS)
        assert dict(rules.esa) == {}
        assert f"sektor {sector}" in esa_notes(rules)[0]
        assert "nemá v BA0036 rezidentský kód" in esa_notes(rules)[0]

    def test_an_unknown_sector_is_treated_alike(self) -> None:
        rules = resident_rules(identity(sector="99999"), BOOKS)
        assert dict(rules.esa) == {} and "sektor 99999 z RES" in esa_notes(rules)[0]

    def test_no_sector_at_all_is_a_note(self) -> None:
        rules = resident_rules(identity(nace="35110"), BOOKS)
        assert dict(rules.esa) == {} and "neuvádí institucionální sektor" in esa_notes(rules)[0]

    def test_a_target_the_codebook_lacks_is_dropped_and_said(self) -> None:
        rules = resident_rules(identity(sector="12503", nace="64910"), codebooks(drop="1250330"))
        assert dict(rules.esa) == {}
        assert "kód BA0036 1250330 z převodu RES v číselníku CTS není" in esa_notes(rules)[0]


class TestNace:
    @pytest.mark.parametrize(("code", "division"), [("35110", "35"), ("651", "65"), ("84", "84")])
    def test_the_prevailing_code_settles_its_division(self, code: str, division: str) -> None:
        rules = resident_rules(identity(sector="11001", nace=code), BOOKS)
        assert dict(rules.nace) == {division: (f"register: RES CZ-NACE 2025 {code}{AS_OF}",)}
        assert rules.nace_settled
        assert not [note for note in rules.notes if note.startswith("NACE")]

    def test_the_full_code_stays_in_the_reason(self) -> None:
        """Truncation happens only in cts_id_for_nace (hard rule); the data keeps 35110."""
        rules = resident_rules(identity(sector="11001", nace="35110"), BOOKS)
        assert "35110" in rules.nace["35"][0]

    def test_without_a_2025_code_the_2008_one_is_taken_with_a_note(self) -> None:
        rules = resident_rules(identity(sector="11002", nace_2008="70100"), BOOKS)
        assert dict(rules.nace) == {"70": (f"register: RES CZ-NACE 2008 70100{AS_OF}",)}
        assert any("převzat oddíl kódu CZ-NACE 2008 70100" in note for note in rules.notes)

    def test_division_45_of_2008_has_no_2025_counterpart_and_no_rule(self) -> None:
        rules = resident_rules(identity(sector="11002", nace_2008="45200"), BOOKS)
        assert dict(rules.nace) == {}
        assert any("CZ-NACE 2008 45200" in note for note in rules.notes)

    def test_no_prevailing_code_at_all_leaves_it_to_the_classifier(self) -> None:
        rules = resident_rules(identity(sector="11002"), BOOKS)
        assert dict(rules.nace) == {} and not rules.nace_settled
        assert "NACE: RES neuvádí převažující činnost – kód vybírá klasifikátor." in rules.notes

    def test_a_code_without_a_division_is_no_rule(self) -> None:
        rules = resident_rules(identity(sector="11002", nace="00"), BOOKS)
        assert dict(rules.nace) == {}


class TestRegistersDisagree:
    def test_a_credit_institution_res_files_as_central_government(self) -> None:
        """Česká exportní banka: on the ECB MFI list, 13110 in RES (2 Oct 2026)."""
        entry = ecb("ECB_MFI:CREDIT_INSTITUTION")
        rules = resident_rules(identity(sector="13110", nace="64190", ecb_entry=entry), BOOKS)
        assert list(rules.esa) == ["1311000"], "RES wins"
        (note,) = esa_notes(rules)
        assert "jako úvěrovou instituci [ECB_MFI:CREDIT_INSTITUTION]" in note
        assert "do sektoru 13110" in note and "rozpor registrů ověřte" in note

    def test_an_insurer_res_files_as_central_government(self) -> None:
        """EGAP: on the ECB list of insurers, 13110 in RES."""
        entry = ecb("ECB_IC", list_name="IC")
        rules = resident_rules(identity(sector="13110", nace="65120", ecb_entry=entry), BOOKS)
        assert "jako pojišťovnu [ECB_IC]" in esa_notes(rules)[0]

    def test_agreeing_registers_say_nothing(self) -> None:
        entry = ecb("ECB_MFI:CREDIT_INSTITUTION")
        rules = resident_rules(identity(sector="12203", nace="64190", ecb_entry=entry), BOOKS)
        assert esa_notes(rules) == []
