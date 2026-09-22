"""Keyword hints and the derived ESA family grid."""

from __future__ import annotations

import pytest

from core.classify.hints import (
    HINTS,
    build_families,
    hinted_esa_families,
    hinted_nace,
    matching_hints,
    split_control,
)
from tests.classify.conftest import ESA_ROWS


class TestSplitControl:
    @pytest.mark.parametrize(
        ("name", "family", "control"),
        [
            ("Banky veřejné", "banky", "public"),
            ("Banky soukromé národní", "banky", "private_national"),
            ("Banky pod zahraniční kontrolou", "banky", "foreign_controlled"),
            (
                "Specializované finanční instituce - veřejné",
                "specializovane financni instituce",
                "public",
            ),
        ],
    )
    def test_control_suffix_is_stripped(self, name: str, family: str, control: str) -> None:
        assert split_control(name) == (family, control)

    def test_soukrome_pod_zahranicni_is_one_control_phrase(self) -> None:
        """The bug this guards: "soukromé pod zahraniční kontrolou" split the family in two,
        stranding the commonest sector for a foreign corporate in a family of its own."""
        family, control = split_control("Nefinanční podniky soukromé pod zahraniční kontrolou")
        assert family == "nefinancni podniky"
        assert control == "foreign_controlled"

    def test_a_sector_with_no_control_axis_keeps_its_name(self) -> None:
        assert split_control("Centrální banka") == ("centralni banka", "unknown")

    def test_mezinarodni_rozvojove_banky_is_not_split(self) -> None:
        family, control = split_control("Mezinárodní rozvojové banky")
        assert family == "mezinarodni rozvojove banky"
        assert control == "unknown"


class TestFamilies:
    def test_control_variants_group_together(self) -> None:
        rest = [(c, n) for c, n, _ in ESA_ROWS if c.startswith("2")]
        assert set(build_families(rest)["banky"].codes) == {"2002211", "2002212", "2002213"}

    def test_a_mixed_residency_list_keeps_only_one_code_per_slot(self) -> None:
        """Documents the contract: both blocks spell the families identically, so callers
        must pass one block at a time. EsaCandidateFilter filters by prefix first."""
        mixed = build_families((code, name) for code, name, _ in ESA_ROWS)
        foreign_controlled_banks = [c for c in mixed["banky"].codes if c.endswith("13")]
        assert len(foreign_controlled_banks) == 1

    def test_non_financial_family_keeps_all_three_variants(self) -> None:
        rest = [(c, n) for c, n, _ in ESA_ROWS if c.startswith("2")]
        families = build_families(rest)
        assert set(families["nefinancni podniky"].codes) == {"2001001", "2001002", "2001003"}

    def test_foreign_control_is_offered_first(self) -> None:
        """Tool 1 classifies foreign issuers, so that variant leads when the list is cut."""
        rest = [(c, n) for c, n, _ in ESA_ROWS if c.startswith("2")]
        families = build_families(rest)
        assert families["banky"].codes[0] == "2002213"

    def test_display_name_is_kept_for_prompts(self) -> None:
        families = build_families((code, name) for code, name, _ in ESA_ROWS)
        assert families["banky"].display.startswith("Banky")


class TestHintTable:
    @pytest.mark.parametrize(
        ("text", "division"),
        [
            ("a commercial bank in Italy", "64"),
            ("italská komerční banka", "64"),
            ("non-life insurance undertaking", "65"),
            ("pojišťovna a zajišťovna", "65"),
            ("manufacturer of motor vehicles", "29"),
            ("výroba motorových vozidel", "29"),
            ("chain of supermarkets", "47"),
        ],
    )
    def test_triggers_fire_in_both_languages(self, text: str, division: str) -> None:
        assert division in hinted_nace(text)

    def test_captive_vocabulary_reaches_the_captive_family(self) -> None:
        families = hinted_esa_families("special purpose funding vehicle for the group")
        assert "kaptivni financni instituce a pujcovatele penez" in families

    def test_asset_backed_reaches_securitisation(self) -> None:
        """Prospectuses say "asset-backed notes", not "securitisation"."""
        families = hinted_esa_families("issues rated asset-backed notes secured on receivables")
        assert "ucelove financni instituce pro sekuritizaci aktiv" in families

    def test_reason_is_recorded(self) -> None:
        assert hinted_nace("a commercial bank")["64"].startswith("keyword:")

    def test_unrelated_text_fires_nothing(self) -> None:
        assert hinted_nace("a quiet afternoon by the lake") == {}
        assert hinted_esa_families("a quiet afternoon by the lake") == {}

    def test_every_hint_has_triggers_and_an_effect(self) -> None:
        for hint in HINTS:
            assert hint.triggers, "a hint with no trigger can never fire"
            assert hint.nace or hint.esa_families, f"hint {hint.note!r} does nothing"
            assert hint.note, "a hint needs a note; it becomes the candidate's reason"

    @pytest.mark.parametrize(
        "name",
        [
            "BMW Finance N.V.",
            "Volkswagen International Finance N.V.",
            "Deutsche Telekom International Finance B.V.",
            "Toyota Motor Credit Corporation",
            "Nordkap Funding B.V.",
        ],
    )
    def test_a_finance_vehicle_name_reaches_the_captive_family(self, name: str) -> None:
        """GLEIF files these as plain GENERAL entities; with no description, the name is
        the only clue that they finance a group rather than run one. Real names, checked
        against GLEIF on 2026-09-22."""
        sheet = f"GLEIF (LEI X): {name}. Země sídla: NL. Kategorie subjektu podle GLEIF: běžná právnická osoba [GENERAL]."
        assert "kaptivni financni instituce a pujcovatele penez" in hinted_esa_families(sheet)
        assert "64" in hinted_nace(sheet)

    def test_a_bare_finance_word_does_not_fire_the_vehicle_hint(self) -> None:
        """'finance' alone is half of section K; only the vehicle spelling counts."""
        assert not any(
            hint.note == "finance-vehicle name"
            for hint in matching_hints("Ministry of Finance of the Republic of Austria")
        )

    def test_matching_hints_can_return_several(self) -> None:
        hits = matching_hints("bank and insurance group")
        assert len(hits) >= 2
