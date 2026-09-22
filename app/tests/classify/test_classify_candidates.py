"""The pre-filter: what reaches the shortlist, what cannot, and why."""

from __future__ import annotations

import pytest

from core.classify.candidates import (
    BASELINE_ESA_FAMILIES,
    EsaCandidateFilter,
    NaceCandidateFilter,
    shortlist_both,
)
from core.classify.models import ESA, NACE
from core.codebooks.models import CodebookSet
from tests.classify.conftest import (
    CAPTIVE_CS,
    CAPTIVE_EN,
    CARMAKER_EN,
    NACE_ROWS,
    build_codebooks,
)


class TestNaceShortlist:
    def test_captive_vehicle_reaches_division_64(self, codebooks: CodebookSet) -> None:
        result = NaceCandidateFilter(codebooks).shortlist(CAPTIVE_EN)
        assert result.candidates[0].code == "64"

    def test_sub_activity_text_is_what_makes_64_reachable(self, codebooks: CodebookSet) -> None:
        """The short label never mentions účelové společnosti; the NACE_STAT rows do."""
        candidate = NaceCandidateFilter(codebooks).shortlist(CAPTIVE_CS).by_code("64")
        assert candidate is not None
        assert any("účelových finančních" in text for text in candidate.definitions)

    def test_carmaker_reaches_division_29(self, codebooks: CodebookSet) -> None:
        assert NaceCandidateFilter(codebooks).shortlist(CARMAKER_EN).candidates[0].code == "29"

    def test_every_candidate_carries_a_cts_id(self, codebooks: CodebookSet) -> None:
        for candidate in NaceCandidateFilter(codebooks).shortlist(CAPTIVE_EN):
            assert candidate.cts_id
            assert candidate.kind == NACE

    def test_limit_is_respected(self, codebooks: CodebookSet) -> None:
        assert len(NaceCandidateFilter(codebooks).shortlist(CAPTIVE_EN, limit=2)) <= 2

    def test_considered_counts_the_whole_codebook(self, codebooks: CodebookSet) -> None:
        result = NaceCandidateFilter(codebooks).shortlist(CAPTIVE_EN)
        assert result.considered == len(codebooks.nace_divisions())

    def test_reasons_explain_the_shortlist(self, codebooks: CodebookSet) -> None:
        candidate = NaceCandidateFilter(codebooks).shortlist(CAPTIVE_EN).by_code("64")
        assert candidate is not None and candidate.reasons

    def test_a_meaningless_description_yields_nothing_rather_than_noise(
        self, codebooks: CodebookSet
    ) -> None:
        """An empty NACE shortlist is honest: it makes the classifier abstain."""
        assert len(NaceCandidateFilter(codebooks).shortlist("")) == 0


#: The fixture codebook plus the two public-sector divisions the register rules name.
PUBLIC_NACE_ROWS = (
    *NACE_ROWS,
    ("84", "Veřejná správa a obrana", ("Veřejná správa a obrana; povinné sociální zabezpečení",)),
    (
        "99",
        "Činnosti exteritoriálních organizací",
        ("Činnosti exteritoriálních organizací a orgánů",),
    ),
)


class TestRegisterPrecedence:
    def test_an_international_organisation_leads_with_99_although_its_name_says_bank(
        self,
    ) -> None:
        """GLEIF files the EIB as INTERNATIONAL_ORGANIZATION; "bank" in its name must not win."""
        codebooks = build_codebooks(nace_rows=PUBLIC_NACE_ROWS)
        text = (
            "European Investment Bank, the EU's lending institution, owned by the member "
            "states.\n\nGLEIF (LEI X): European Investment Bank. Kategorie subjektu podle GLEIF: "
            "mezinárodní organizace (supranational international organisation) "
            "[INTERNATIONAL_ORGANIZATION]."
        )
        result = NaceCandidateFilter(codebooks).shortlist(text)
        assert result.codes[:2] == ("99", "64")
        assert result.candidates[0].reasons[0].startswith("register:")

    def test_a_government_leads_with_84_although_it_mentions_bank_loans(self) -> None:
        codebooks = build_codebooks(nace_rows=PUBLIC_NACE_ROWS)
        text = (
            "Ville de Paris borrows through bank loans and an EMTN programme.\n\n"
            "Kategorie subjektu podle GLEIF: vládní instituce (government) "
            "[RESIDENT_GOVERNMENT_ENTITY], místní samospráva (local government, municipality)."
        )
        assert NaceCandidateFilter(codebooks).shortlist(text).codes[0] == "84"

    def test_without_the_register_a_government_word_still_offers_84(self) -> None:
        """A typed description has no fact sheet; the keyword alone must still offer 84."""
        codebooks = build_codebooks(nace_rows=PUBLIC_NACE_ROWS)
        assert "84" in NaceCandidateFilter(codebooks).shortlist("sovereign borrower").codes


class TestEnglishTitles:
    def test_an_english_description_reaches_a_division_no_keyword_names(self) -> None:
        """No hint mentions crops; only division 01's English title does."""
        rows = (*NACE_ROWS, ("01", "Rostlinná a živočišná výroba", ("Pěstování obilovin",)))
        codebooks = build_codebooks(nace_rows=rows)
        result = NaceCandidateFilter(codebooks).shortlist("grows crops and raises animals")
        assert result.codes[:1] == ("01",)

    def test_the_english_title_is_scored_but_never_becomes_part_of_the_candidate(
        self, codebooks: CodebookSet
    ) -> None:
        """MO sees the Czech codebook; the prompt gets the codebook's own texts."""
        candidate = NaceCandidateFilter(codebooks).shortlist(CARMAKER_EN).by_code("29")
        assert candidate is not None
        assert not any("motor vehicles, trailers" in text for text in candidate.definitions)
        assert "Manufacture" not in candidate.label


class TestEsaShortlist:
    def test_bank_family_is_offered_with_its_control_variants(self, codebooks: CodebookSet) -> None:
        result = EsaCandidateFilter(codebooks).shortlist("an Italian commercial bank")
        codes = set(result.codes)
        assert {"2002211", "2002212", "2002213"} <= codes

    def test_captive_vehicle_reaches_the_captive_family(self, codebooks: CodebookSet) -> None:
        assert "2002703" in EsaCandidateFilter(codebooks).shortlist(CAPTIVE_EN).codes

    def test_rest_of_world_only_by_default(self, codebooks: CodebookSet) -> None:
        """A foreign issuer is a non-resident; a resident code would be wrong before the
        classifier even looks at it."""
        codes = EsaCandidateFilter(codebooks).shortlist("a bank").codes
        assert codes and all(code.startswith("2") for code in codes)

    def test_resident_mode_offers_the_resident_block(self, codebooks: CodebookSet) -> None:
        codes = EsaCandidateFilter(codebooks, resident=True).shortlist("a bank").codes
        assert codes and all(code.startswith("1") for code in codes)

    def test_every_candidate_carries_a_cts_id(self, codebooks: CodebookSet) -> None:
        for candidate in EsaCandidateFilter(codebooks).shortlist(CAPTIVE_EN):
            assert candidate.cts_id
            assert candidate.kind == ESA


class TestEsaBaseline:
    def test_an_industrial_issuer_still_gets_the_residual_sector(
        self, codebooks: CodebookSet
    ) -> None:
        """The regression that cost 20% recall: financial words like "bonds" and "finance"
        scored weakly against a dozen financial families and consumed every slot, leaving
        "Nefinanční podniky" - the sector the issuer actually belongs to - unoffered."""
        assert "2001003" in EsaCandidateFilter(codebooks).shortlist(CARMAKER_EN).codes

    def test_baseline_survives_a_small_limit(self, codebooks: CodebookSet) -> None:
        assert "2001003" in EsaCandidateFilter(codebooks).shortlist(CARMAKER_EN, limit=4).codes

    def test_an_empty_description_still_offers_the_residual_sector(
        self, codebooks: CodebookSet
    ) -> None:
        assert "2001003" in EsaCandidateFilter(codebooks).shortlist("").codes

    def test_a_strong_match_still_leads(self, codebooks: CodebookSet) -> None:
        """The baseline must not displace a real answer, only backstop its absence."""
        result = EsaCandidateFilter(codebooks).shortlist(
            "an Italian commercial bank licensed as a bank"
        )
        assert result.candidates[0].code.startswith("20022")

    def test_baseline_families_exist_in_the_codebook(self, codebooks: CodebookSet) -> None:
        families = EsaCandidateFilter(codebooks).families
        for key in BASELINE_ESA_FAMILIES:
            assert key in families, f"baseline family {key!r} is not in the codebook"


class TestUnmappableCodes:
    def test_a_division_without_a_cts_id_is_never_offered(self) -> None:
        """A code the CTS codebook cannot map must not reach the classifier, or it could
        come back as an answer that nobody can type into CTS."""
        codebooks = build_codebooks(
            nace_rows=(("64", "Finanční činnosti", ("Peněžní zprostředkování",)),)
        )
        # Strip the CTS entry for 64 while leaving its NACE_STAT labels in place.
        stripped = build_codebooks(
            nace_rows=(
                ("64", "Finanční činnosti", ("Peněžní zprostředkování",)),
                ("29", "Výroba motorových vozidel", ("Výroba motorových vozidel",)),
            )
        )
        broken = CodebookSet(
            cts_ba0036=stripped.cts_ba0036,
            ba0036_valid=stripped.ba0036_valid,
            cts_okec_nace2=codebooks.cts_okec_nace2.__class__(
                kind="OKEC_NACE2",
                entries=tuple(e for e in stripped.cts_okec_nace2.entries if e.key != "64"),
                file=stripped.cts_okec_nace2.file,
            ),
            nace_stat=stripped.nace_stat,
            version=stripped.version,
        )
        result = NaceCandidateFilter(broken).shortlist("peněžní zprostředkování a úvěry")
        assert "64" not in result.codes


class TestCandidateSet:
    def test_contains_is_the_gate_a_suggestion_must_pass(self, codebooks: CodebookSet) -> None:
        result = NaceCandidateFilter(codebooks).shortlist(CAPTIVE_EN)
        assert result.contains("64")
        assert not result.contains("99")

    def test_definition_text_deduplicates(self, codebooks: CodebookSet) -> None:
        candidate = NaceCandidateFilter(codebooks).shortlist(CAPTIVE_CS).by_code("64")
        assert candidate is not None
        lines = candidate.definition_text.splitlines()
        assert len(lines) == len(set(lines))

    def test_describe_is_one_line(self, codebooks: CodebookSet) -> None:
        assert "of" in NaceCandidateFilter(codebooks).shortlist(CAPTIVE_EN).describe()


def test_shortlist_both_returns_both_kinds(codebooks: CodebookSet) -> None:
    nace, esa = shortlist_both(codebooks, CAPTIVE_EN)
    assert nace.kind == NACE and esa.kind == ESA


@pytest.mark.parametrize("limit", [1, 3, 12, 50])
def test_limit_is_never_exceeded(codebooks: CodebookSet, limit: int) -> None:
    nace, esa = shortlist_both(codebooks, CAPTIVE_EN, limit=limit)
    assert len(nace) <= limit and len(esa) <= limit
