"""The proposed code ("navrhovaný kód"): the model's pick, else a rule's, else nothing."""

from __future__ import annotations

from core.classify.candidates import HINT_SCORE, EsaCandidateFilter, NaceCandidateFilter
from core.classify.models import ESA, NACE, Candidate, CandidateSet, Classification, Suggestion
from core.classify.proposal import RULE_FLOOR, propose
from core.codebooks.models import CodebookSet
from tests.classify.conftest import CAPTIVE_EN


def _set(kind, *candidates: Candidate) -> CandidateSet:
    return CandidateSet(kind=kind, candidates=candidates, considered=87, filter_name="test")


RULED = _set(
    NACE,
    Candidate(NACE, "84", "527", "Veřejná správa a obrana", score=15.03, reasons=("register: x",)),
    Candidate(NACE, "64", "512", "Finanční činnosti", score=10.09, reasons=("keyword: banking",)),
    Candidate(NACE, "94", "535", "Činnosti organizací sdružujících osoby", score=0.08),
)
LEXICAL_ONLY = _set(
    NACE,
    Candidate(NACE, "64", "512", "Finanční činnosti", score=0.13, reasons=("text match 0.13",)),
    Candidate(NACE, "25", "473", "Výroba kovových konstrukcí", score=0.07),
)
ABSTAINED = Classification(kind=NACE, abstained=True, abstain_reason="no model configured")


class TestModelBasis:
    def test_the_model_pick_is_the_proposal(self) -> None:
        pick = Suggestion.from_candidate(
            RULED.candidates[1], confidence="medium", justification="Banka."
        )
        runner_up = Suggestion.from_candidate(
            RULED.candidates[0], confidence="low", justification="Stát.", rank=2
        )
        answered = Classification(kind=NACE, suggestions=(pick, runner_up))
        proposal = propose(answered, RULED)
        assert proposal is not None
        assert (proposal.basis, proposal.code, proposal.cts_id) == ("model", "64", "512")
        assert proposal.confidence == "medium"
        assert proposal.justification == "Banka."
        assert proposal.alternatives == (runner_up,)
        assert proposal.tie_note is None


class TestRulesBasis:
    def test_a_rule_placed_first_candidate_is_proposed_when_the_model_abstains(self) -> None:
        proposal = propose(ABSTAINED, RULED)
        assert proposal is not None
        assert (proposal.basis, proposal.code, proposal.cts_id) == ("rules", "84", "527")

    def test_a_rule_proposal_claims_no_confidence(self) -> None:
        """Only a model has a confidence; the rules must not borrow one."""
        proposal = propose(ABSTAINED, RULED)
        assert proposal is not None and proposal.confidence is None

    def test_a_rule_proposal_names_the_rules(self) -> None:
        proposal = propose(ABSTAINED, RULED)
        assert proposal is not None
        assert proposal.justification.startswith("Podle pravidel, bez modelu:")
        assert "register: x" in proposal.justification

    def test_the_rest_of_the_shortlist_are_the_alternatives(self) -> None:
        proposal = propose(ABSTAINED, RULED)
        assert proposal is not None
        assert tuple(item.code for item in proposal.alternatives) == ("64", "94")

    def test_text_similarity_alone_proposes_nothing(self) -> None:
        """A lexical-only first candidate is right about one time in four: no proposal."""
        assert propose(ABSTAINED, LEXICAL_ONLY) is None

    def test_an_empty_shortlist_proposes_nothing(self) -> None:
        assert propose(ABSTAINED, _set(NACE)) is None

    def test_the_threshold_is_a_rule_not_a_lucky_text_match(self) -> None:
        """Lexical scores stop at 1.0; the floor sits above that, at the smaller rule bonus."""
        assert 1.0 < RULE_FLOOR <= HINT_SCORE
        just_below = _set(NACE, Candidate(NACE, "64", "512", "x", score=RULE_FLOOR - 0.01))
        assert propose(ABSTAINED, just_below) is None


BANKS = _set(
    ESA,
    Candidate(ESA, "2002213", "635", "Banky pod zahraniční kontrolou", score=10.01),
    Candidate(ESA, "2002212", "634", "Banky soukromé národní", score=10.01),
    Candidate(ESA, "2002211", "633", "Banky veřejné", score=10.01),
    Candidate(ESA, "2001003", "613", "Nefinanční podniky soukromé pod zahraniční kontrolou"),
)


class TestTies:
    def test_control_variants_of_one_family_tie_and_are_named(self) -> None:
        """The rules pick the family; the control digit is MO's call, and the page says so."""
        proposal = propose(Classification(kind=ESA, abstained=True), BANKS)
        assert proposal is not None and proposal.basis == "rules"
        assert proposal.code == "2002213"
        assert proposal.tied == ("2002212", "2002211")
        assert proposal.tie_note == (
            "Stejné skóre mají i 2002212, 2002211 – pravidla mezi nimi nerozhodují, vyberte."
        )

    def test_rules_for_two_families_tying_propose_nothing(self, codebooks: CodebookSet) -> None:
        """The classic trap: "bank" and "captive" score alike for an English captive vehicle,
        and only the alphabet would have put the bank first."""
        candidates = EsaCandidateFilter(codebooks).shortlist(CAPTIVE_EN)
        top = candidates.candidates
        assert top[0].score == top[3].score  # banks and captives tie on this codebook
        assert propose(Classification(kind=ESA, abstained=True), candidates) is None

    def test_two_nace_divisions_tying_propose_nothing(self) -> None:
        """For NACE every code is its own family: a tie means no rule decided."""
        pair = _set(
            NACE,
            Candidate(NACE, "64", "512", "x", score=10.0, reasons=("keyword: a",)),
            Candidate(NACE, "70", "520", "y", score=10.0, reasons=("keyword: b",)),
        )
        assert propose(ABSTAINED, pair) is None

    def test_one_tied_code_reads_in_the_singular(self) -> None:
        pair = _set(
            ESA,
            Candidate(
                ESA,
                "2002703",
                "667",
                "Kaptivní finanční instituce a půjčovatelé peněz pod zahraniční kontrolou",
                score=10.0,
            ),
            Candidate(
                ESA,
                "2002702",
                "666",
                "Kaptivní finanční instituce a půjčovatelé peněz soukromé národní",
                score=10.0,
            ),
        )
        proposal = propose(Classification(kind=ESA, abstained=True), pair)
        assert proposal is not None
        assert proposal.tie_note == (
            "Stejné skóre má i 2002702 – pravidla mezi nimi nerozhodují, vyberte."
        )

    def test_a_clear_winner_has_no_tie_note(self, codebooks: CodebookSet) -> None:
        candidates = NaceCandidateFilter(codebooks).shortlist(CAPTIVE_EN)
        proposal = propose(ABSTAINED, candidates)
        assert proposal is not None
        assert proposal.code == "64"
        assert proposal.tie_note is None
