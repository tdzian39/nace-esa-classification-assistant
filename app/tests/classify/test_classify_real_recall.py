"""Pre-filter recall against the REAL codebooks. Skips when they are not on this machine.

This is the test that matters. Recall is the ceiling on everything downstream: a code the
filter never offers is one the classifier can never return, however good the model is.

The threshold is deliberately absolute. Both misses it caught while being written were real
defects, not tuning noise:

* an industrial issuer whose description merely said "bonds" and "finance" scored weakly
  against a dozen financial families, which consumed every slot and left "Nefinanční
  podniky" - the sector it belongs to - unoffered;
* a securitisation vehicle whose prospectus said "asset-backed notes" rather than
  "securitisation", so no keyword fired at all.

Note these are PROVISIONAL cases: this measures the filter's reach, not its accuracy. Once
MO has verified cases, `--golden` reports the two populations separately.
"""

from __future__ import annotations

import pytest

from config.settings import get_settings
from core.classify.candidates import DEFAULT_LIMIT, EsaCandidateFilter, NaceCandidateFilter
from core.classify.golden import ESA, NACE, load_golden, score_recall
from core.classify.hints import HINTS, REGISTER_RULES
from core.classify.models import Classification
from core.classify.nace_en import NACE_TITLES_EN
from core.classify.proposal import propose
from core.codebooks.errors import CodebookError
from core.codebooks.loaders import load_and_check
from core.codebooks.models import CodebookSet


def trap_cases():
    """The fictional trap cases: controlled texts that must always be recalled in full.

    The real issuers (roadmap E8) are measured, not asserted - their recall is a figure to
    improve (`python -m core.classify --golden`), not a gate a new case may break.
    """
    return tuple(case for case in load_golden() if not case.real)


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
    except CodebookError as exc:  # pragma: no cover - the codebook check covers this
        pytest.skip(f"real codebooks could not be loaded: {exc}")
    return codebooks


def test_every_expected_nace_code_is_offered(real_codebooks: CodebookSet) -> None:
    cases = trap_cases()
    chooser = NaceCandidateFilter(real_codebooks)
    shortlists = [
        (case, chooser.shortlist(case.description, limit=DEFAULT_LIMIT)) for case in cases
    ]
    report = score_recall(cases, shortlists, NACE)

    assert report.recall(verified_only=False) == 1.0, report.summary()


def test_every_expected_esa_code_is_offered(real_codebooks: CodebookSet) -> None:
    cases = trap_cases()
    chooser = EsaCandidateFilter(real_codebooks)
    shortlists = [
        (case, chooser.shortlist(case.description, limit=DEFAULT_LIMIT)) for case in cases
    ]
    report = score_recall(cases, shortlists, ESA)

    assert report.recall(verified_only=False) == 1.0, report.summary()


def test_the_correct_code_ranks_near_the_top(real_codebooks: CodebookSet) -> None:
    """Not a correctness requirement, but a shortlist whose answer sits at rank 11 of 12 is
    a filter about to start missing. Guards against silent drift."""
    cases = trap_cases()
    chooser = NaceCandidateFilter(real_codebooks)
    shortlists = [(case, chooser.shortlist(case.description)) for case in cases]
    mean_rank = score_recall(cases, shortlists, NACE).mean_rank(verified_only=False)

    assert mean_rank is not None and mean_rank <= 3.0


def test_the_expected_codes_exist_in_the_real_codebooks(real_codebooks: CodebookSet) -> None:
    """A golden case naming a code the codebook does not have would be unscoreable, and
    would look like a filter failure rather than a bad case."""
    for case in load_golden():
        if case.expected_nace:
            assert real_codebooks.cts_id_for_nace(case.expected_nace).cts_id
        if case.expected_esa:
            assert real_codebooks.cts_id_for_esa(case.expected_esa).cts_id


def test_captive_vehicle_is_not_classified_as_a_bank_by_the_filter_alone(
    real_codebooks: CodebookSet,
) -> None:
    """Both readings must be offered: the whole point of the ESA half is that a human or a
    model, not the keyword table, decides between 2002703 and 2002213."""
    case = next(c for c in load_golden() if c.id == "captive-funding-spv")
    codes = EsaCandidateFilter(real_codebooks).shortlist(case.description).codes

    assert "2002703" in codes
    assert "2002213" in codes


def test_the_rules_propose_no_bank_for_the_captive_trap(real_codebooks: CodebookSet) -> None:
    """Without a model, "bank" and "captive" tie for this description; proposing the first of
    them would propose the bank - the exact error the trap exists to catch."""
    case = next(c for c in load_golden() if c.id == "captive-funding-spv")
    candidates = EsaCandidateFilter(real_codebooks).shortlist(case.description)
    assert propose(Classification(kind=ESA, abstained=True), candidates) is None


def test_every_code_the_rules_name_exists_in_cts(real_codebooks: CodebookSet) -> None:
    """A hint naming a division or family CTS does not have would silently do nothing."""
    divisions = {division.code for division in real_codebooks.nace_divisions()}
    families = EsaCandidateFilter(real_codebooks).families
    for hint in HINTS:
        assert set(hint.nace) <= divisions, hint.note
        assert set(hint.esa_families) <= set(families), hint.note
    assert {rule.nace for rule in REGISTER_RULES} <= divisions


def test_the_english_titles_cover_exactly_the_cts_divisions(real_codebooks: CodebookSet) -> None:
    """CZ-NACE 2025: 87 divisions, no 45. A title for a missing division would be dead; a
    missing title would leave that division Czech-only."""
    divisions = {division.code for division in real_codebooks.nace_divisions()}
    assert set(NACE_TITLES_EN) == divisions
