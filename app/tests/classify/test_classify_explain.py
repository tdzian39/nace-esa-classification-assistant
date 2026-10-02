"""How a code was decided, in Czech: the "Jak rozhodl" line and the rules' reasons."""

from __future__ import annotations

import pytest

from core.classify.explain import _NOTES_CS, _REGISTERS_CS, decision_cs, reason_cs, reasons_cs
from core.classify.hints import ESA_REGISTER_RULES, HINTS, REGISTER_RULES
from core.classify.models import NACE, Candidate, CandidateSet, Classification, Suggestion
from core.classify.proposal import propose

SHORTLIST = CandidateSet(
    kind=NACE,
    candidates=(
        Candidate(NACE, "84", "527", "Veřejná správa", score=15.0, reasons=("register: x",)),
        Candidate(
            NACE, "64", "512", "Finanční činnosti", score=10.0, reasons=("keyword: banking",)
        ),
    ),
    considered=87,
    filter_name="test",
)


class TestReasons:
    @pytest.mark.parametrize(
        ("reason", "czech"),
        [
            ("keyword: banking", "klíčové slovo: bankovnictví"),
            ("register: ECB MFI list: credit institution", "seznam MFI ECB: úvěrová instituce"),
            ("register: GLEIF: international organisation", "GLEIF: mezinárodní organizace"),
            ("text match 0.09", "shoda textu 0,09"),
            ("family: Banky", "skupina: Banky"),
            ("baseline: residual sector", "zbytkový sektor (nefinanční podniky)"),
        ],
    )
    def test_each_kind_of_reason_reads_in_czech(self, reason: str, czech: str) -> None:
        assert reason_cs(reason) == czech

    def test_an_unknown_note_is_shown_as_it_is_never_dropped(self) -> None:
        assert reason_cs("keyword: something new") == "klíčové slovo: something new"
        assert reason_cs("register: a new list") == "registr: a new list"
        assert reason_cs("anything else") == "anything else"

    def test_every_hint_and_register_rule_has_its_czech_wording(self) -> None:
        """A new hint or register rule has to bring its Czech note along."""
        assert {hint.note for hint in HINTS} <= set(_NOTES_CS)
        registers = {rule.note for rule in REGISTER_RULES} | {n for _, _, n in ESA_REGISTER_RULES}
        assert registers <= set(_REGISTERS_CS)

    def test_reasons_are_listed_with_semicolons(self) -> None:
        assert reasons_cs(("keyword: banking", "text match 0.20")) == (
            "klíčové slovo: bankovnictví; shoda textu 0,20"
        )


class TestDecision:
    def test_a_model_pick_names_the_model_and_the_shortlist(self) -> None:
        pick = Suggestion.from_candidate(
            SHORTLIST.candidates[0], confidence="high", justification="x"
        )
        answered = Classification(kind=NACE, suggestions=(pick,), model="gpt-test")
        text = decision_cs(propose(answered, SHORTLIST), answered, SHORTLIST)
        assert text.startswith("model gpt-test – vybral z nabídky 2 kódů zúženého číselníku")

    def test_a_register_that_overrules_the_model_says_so(self) -> None:
        pick = Suggestion.from_candidate(
            SHORTLIST.candidates[1], confidence="high", justification="x"
        )
        answered = Classification(kind=NACE, suggestions=(pick,), model="gpt-test")
        text = decision_cs(propose(answered, SHORTLIST), answered, SHORTLIST)
        assert text.startswith("registr – určuje jediný kód")
        assert "(ten navrhl 64)" in text

    def test_the_rules_say_why_the_model_did_not_decide(self) -> None:
        declined = Classification(
            kind=NACE, abstained=True, abstain_reason="model usoudil, že podklady nestačí"
        )
        text = decision_cs(propose(declined, SHORTLIST), declined, SHORTLIST)
        assert text == "pravidla, protože model kód nevybral (model usoudil, že podklady nestačí)."

    def test_nothing_decided_gives_the_reason(self) -> None:
        declined = Classification(kind=NACE, abstained=True, abstain_reason="chybí popis činnosti")
        assert decision_cs(None, declined, SHORTLIST) == "nerozhodnuto – chybí popis činnosti."


class TestResReasons:
    """The reasons of a Czech issuer's RES codes (2 Oct 2026) carry codes and RES's date."""

    @pytest.mark.parametrize(
        ("reason", "czech"),
        [
            (
                "register: RES sector 12203, as of 2023-06-29",
                "RES (ARES): institucionální sektor 12203, stav k 2023-06-29",
            ),
            (
                "register: RES CZ-NACE 2025 35110, as of 2026-09-04",
                "RES (ARES): převažující činnost CZ-NACE 2025 35110, stav k 2026-09-04",
            ),
            (
                "register: RES CZ-NACE 2008 64190",
                "RES (ARES): převažující činnost CZ-NACE 2008 64190",
            ),
            (
                "register: RES CZ-NACE 64910: lending",
                "RES: převažující činnost CZ-NACE 64910 – finanční leasing nebo jiné poskytování "
                "úvěrů (64.91, 64.92)",
            ),
            (
                "register: RES legal form 205",
                "RES: právní forma 205 družstvo – spořitelní a úvěrní družstvo",
            ),
            (
                "register: not a credit institution on the ECB MFI list",
                "na seznamu MFI ECB není úvěrovou institucí",
            ),
        ],
    )
    def test_each_is_said_in_czech(self, reason: str, czech: str) -> None:
        assert reason_cs(reason) == czech

    def test_a_res_code_is_said_to_be_taken_from_res_with_its_date(self) -> None:
        from core.classify.models import ESA

        settled = CandidateSet(
            kind=ESA,
            candidates=(
                Candidate(
                    ESA,
                    "1100100",
                    "584",
                    "Nefinanční podniky veřejné",
                    score=100.0,
                    reasons=("register: RES sector 11001, as of 2026-09-04",),
                ),
            ),
            considered=53,
            filter_name="test",
        )
        not_asked = Classification(kind=ESA, abstained=True)
        decided = decision_cs(propose(not_asked, settled), not_asked, settled)
        assert (
            decided
            == "převzato z RES (ARES), stav k 2026-09-04 – kód určuje registr, model se neptal."
        )

    def test_a_res_tie_asks_mo_to_pick_and_keeps_the_model_s_reason(self) -> None:
        from core.classify.models import ESA

        tied = CandidateSet(
            kind=ESA,
            candidates=tuple(
                Candidate(
                    ESA, code, cts, label, score=100.0, reasons=("register: RES sector 12203",)
                )
                for code, cts, label in (
                    ("1221300", "590", "Banky pod zahraniční kontrolou"),
                    (
                        "1224300",
                        "595",
                        "Jiné instituce přijímající vklady pod zahraniční kontrolou",
                    ),
                )
            ),
            considered=53,
            filter_name="res-register",
        )
        declined = Classification(
            kind=ESA,
            abstained=True,
            abstain_reason="model usoudil, že podklady k rozhodnutí nestačí",
        )
        assert propose(declined, tied) is None, "two families tie: the rules propose nothing"
        assert decision_cs(None, declined, tied) == (
            "nerozhodnuto – RES (ARES) určuje sektor i typ kontroly, ne druh instituce; vyberte "
            "jeden z nabízených kódů (model usoudil, že podklady k rozhodnutí nestačí)."
        )
