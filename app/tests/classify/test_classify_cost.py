"""Cost controls, and the recall they must not buy.

Token spend and answer quality pull against each other, so both sides are asserted here.
The numbers come from measurements on the real codebooks; they are ceilings and floors, not
targets, and they exist so a later "small" change cannot quietly make the tool more
expensive or less accurate.
"""

from __future__ import annotations

import pytest

from config.settings import Settings, get_settings
from core.classify.candidates import DEFAULT_LIMIT, EsaCandidateFilter, NaceCandidateFilter
from core.classify.golden import ESA, NACE, load_golden, score_recall
from core.classify.prompts import MAX_DEFINITION_CHARS, build_prompt, select_definitions
from core.codebooks.errors import CodebookError
from core.codebooks.loaders import load_and_check
from core.codebooks.models import CodebookSet

#: The shortlist size below which ESA recall breaks. Measured: 100% at 8 and above, 80% at
#: 6 and 4 - ESA families come in three control variants, so a shorter list loses a whole
#: family rather than one code. DEFAULT_LIMIT deliberately sits above this, not on it.
ESA_RECALL_FLOOR = 8

#: Ceiling for one issuer, both codebooks, on the real codebooks. Measured ~3,400 with a
#: long description; the golden cases average lower.
MAX_TOKENS_PER_ISSUER = 4000


@pytest.fixture(scope="module")
def real_codebooks() -> CodebookSet:
    settings = get_settings()
    missing = [
        name
        for name in (
            settings.codebook_cts_ba0036_file,
            settings.codebook_ba0036_valid_file,
            settings.codebook_cts_okec_nace2_file,
            settings.codebook_nace_stat_file,
        )
        if not settings.codebook_path(name).is_file()
    ]
    if missing:
        pytest.skip(f"real codebooks not available: {missing}")
    try:
        codebooks, _ = load_and_check(settings, strict=False)
    except CodebookError as exc:  # pragma: no cover
        pytest.skip(f"real codebooks could not be loaded: {exc}")
    return codebooks


class TestDefinitionBudget:
    def test_a_giant_division_is_trimmed(self, real_codebooks: CodebookSet) -> None:
        """Oddíl 46 has 56 sub-activities, about 3,000 characters - one candidate costing
        more than the rest of the prompt together."""
        candidate = NaceCandidateFilter(real_codebooks).shortlist("velkoobchod").by_code("46")
        assert candidate is not None
        kept = select_definitions(candidate, "velkoobchod s potravinami")

        assert len(kept) < len(candidate.definitions)
        assert sum(len(text) for text in kept) <= MAX_DEFINITION_CHARS + len(kept[0])

    def test_an_ordinary_division_is_sent_whole(self, real_codebooks: CodebookSet) -> None:
        """The cost is in a handful of giants; trimming everything would lose signal for
        nothing."""
        candidates = NaceCandidateFilter(real_codebooks).shortlist("finanční zprostředkování")
        candidate = candidates.by_code("64")
        assert candidate is not None
        assert select_definitions(candidate, "x") == tuple(candidate.definitions)

    def test_the_decisive_definition_survives_an_english_description(
        self, real_codebooks: CodebookSet
    ) -> None:
        """The regression that made the first attempt at this worse than useless.

        A count-based cap ranked by overlap kept "the first six" when nothing scored - which
        is always, for an English description against a Czech codebook - and dropped
        "Činnosti účelových finančních společností", the one line that puts a captive funding
        vehicle in division 64.
        """
        description = (
            "Special purpose funding vehicle of a banking group. Issues bonds and on-lends "
            "the proceeds exclusively to its parent bank. No deposits, no banking licence."
        )
        candidate = NaceCandidateFilter(real_codebooks).shortlist(description).by_code("64")
        assert candidate is not None
        assert "Činnosti účelových finančních společností" in select_definitions(
            candidate, description
        )

    def test_the_first_definition_is_always_kept(self, real_codebooks: CodebookSet) -> None:
        """It is the division's own name - the canonical statement of what the code covers."""
        candidate = NaceCandidateFilter(real_codebooks).shortlist("velkoobchod").by_code("46")
        assert candidate is not None
        assert select_definitions(candidate, "x")[0] == candidate.definitions[0]

    def test_definitions_stay_in_codebook_order(self, real_codebooks: CodebookSet) -> None:
        """The model should read a definition, not a relevance ranking."""
        candidate = NaceCandidateFilter(real_codebooks).shortlist("velkoobchod").by_code("46")
        assert candidate is not None
        kept = select_definitions(candidate, "velkoobchod s léčivy")
        positions = [candidate.definitions.index(text) for text in kept]
        assert positions == sorted(positions)


class TestPromptCost:
    def test_one_issuer_stays_within_budget(self, real_codebooks: CodebookSet) -> None:
        description = (
            "Special purpose funding vehicle of the Nordkap banking group. Issues bonds and "
            "on-lends the proceeds exclusively to its parent bank. No deposits, no licence."
        )
        nace = NaceCandidateFilter(real_codebooks).shortlist(description)
        esa = EsaCandidateFilter(real_codebooks).shortlist(description)
        total = sum(
            build_prompt(
                candidates, issuer_name="Nordkap", description=description
            ).estimated_tokens
            for candidates in (nace, esa)
        )
        assert total <= MAX_TOKENS_PER_ISSUER, f"~{total} tokens per issuer"

    def test_every_golden_case_stays_within_budget(self, real_codebooks: CodebookSet) -> None:
        nace_filter = NaceCandidateFilter(real_codebooks)
        esa_filter = EsaCandidateFilter(real_codebooks)
        for case in load_golden():
            total = sum(
                build_prompt(
                    chooser.shortlist(case.description),
                    issuer_name=case.issuer,
                    description=case.description,
                ).estimated_tokens
                for chooser in (nace_filter, esa_filter)
            )
            assert total <= MAX_TOKENS_PER_ISSUER, f"{case.id}: ~{total} tokens"

    def test_output_is_capped(self) -> None:
        """Three picks with one sentence each; the cap stops a rambling model being billed."""
        assert 100 <= Settings().llm_max_output_tokens <= 2000


class TestRecallFloor:
    def test_the_default_shortlist_is_above_the_esa_cliff(self) -> None:
        """Measured: ESA recall is 100% at 8 and 80% at 6. Sitting the default ON the cliff
        would mean one new golden case could push it under."""
        assert DEFAULT_LIMIT > ESA_RECALL_FLOOR

    def test_recall_holds_at_the_default(self, real_codebooks: CodebookSet) -> None:
        cases = load_golden()
        for kind, chooser in (
            (NACE, NaceCandidateFilter(real_codebooks)),
            (ESA, EsaCandidateFilter(real_codebooks)),
        ):
            shortlists = [(case, chooser.shortlist(case.description)) for case in cases]
            report = score_recall(cases, shortlists, kind)
            assert report.recall(verified_only=False) == 1.0, report.summary()

    def test_trimming_definitions_did_not_cost_recall(self, real_codebooks: CodebookSet) -> None:
        """Recall is a property of the shortlist, not the prompt - but assert it after the
        cost work, because that is the change most likely to have broken it."""
        cases = load_golden()
        chooser = EsaCandidateFilter(real_codebooks)
        shortlists = [(case, chooser.shortlist(case.description)) for case in cases]
        assert score_recall(cases, shortlists, ESA).misses() == ()
