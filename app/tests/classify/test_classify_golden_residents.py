"""The resident golden cases (2 Oct 2026): RES's codes through the replayed pipeline, model off.

Each case runs the real pipeline - GLEIF by ISIN or IČO, the FIRDS fallback, RES through ARES,
the ECB memberships - replayed from ``tests/golden/residents_registers.json``. None of this
says the brief's codes are right: like the foreign cases, they are provisional until checked.
"""

from __future__ import annotations

import json
import re

import pytest

from config.settings import APP_ROOT, get_settings
from core.classify.golden import GoldenError
from core.classify.golden_residents import (
    RecordedEcb,
    ResidentCase,
    load_residents,
    replaying_service,
    score_residents,
)
from core.codebooks.errors import CodebookError
from core.codebooks.loaders import load_and_check
from core.codebooks.res_esa import targets
from tests.classify.conftest import build_resident_codebooks

CASES = load_residents()


def ids(cases: tuple[ResidentCase, ...]) -> list[str]:
    return [case.id for case in cases]


def test_the_fifteen_subjects_of_the_brief_are_there() -> None:
    assert len(CASES) == 15
    assert all(case.id.startswith("res-") for case in CASES)
    assert all(case.verified_by is None for case in CASES), "provisional until checked"


def test_three_are_looked_up_by_isin_and_the_rest_by_ico() -> None:
    by_isin = {case.id for case in CASES if case.isin}
    assert by_isin == {"res-cez", "res-komercni-banka", "res-ministerstvo-financi"}
    assert all(re.fullmatch(r"\d{8}", case.ico) for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=ids(CASES))
def test_the_expected_codes_are_ones_the_table_can_give(case: ResidentCase) -> None:
    assert case.expected_esa in targets()
    assert re.fullmatch(r"\d{2}", case.expected_nace)
    if case.tie:
        assert case.expected_esa in case.tie and set(case.tie) <= set(targets())


@pytest.fixture(scope="module")
def replayed() -> dict[str, object]:
    service = replaying_service(build_resident_codebooks())
    return {case.id: service.suggest(case.request()) for case in CASES}


@pytest.mark.parametrize("case", CASES, ids=ids(CASES))
class TestEachCase:
    def test_the_recording_replays_what_res_said(self, case: ResidentCase, replayed) -> None:
        identity = replayed[case.id].identity
        assert identity.resident and identity.ico == case.ico
        res = identity.res_record
        assert res is not None
        assert (res.legal_form, res.sector, res.nace) == (
            case.legal_form,
            case.res_sector,
            case.res_nace,
        )

    def test_the_proposal_is_the_brief_s_with_the_model_off(
        self, case: ResidentCase, replayed
    ) -> None:
        suggestion = replayed[case.id]
        nace = suggestion.nace_proposal
        assert nace is not None and nace.code == case.expected_nace and nace.basis == "rules"
        esa = suggestion.esa_proposal
        if case.tie:
            assert esa is None
            assert set(suggestion.esa_candidates.codes) == set(case.tie)
        else:
            assert esa is not None and esa.code == case.expected_esa and esa.basis == "rules"

    def test_the_name_shown_is_res_s(self, case: ResidentCase, replayed) -> None:
        assert replayed[case.id].issuer_name == case.subject


def test_the_scorer_counts_every_case_as_the_brief() -> None:
    results = score_residents(build_resident_codebooks())
    assert len(results) == 15
    assert [r.case.id for r in results if not (r.nace_ok and r.esa_ok)] == []


def test_the_ceb_disagreement_is_noted() -> None:
    """RES: central government; the ECB MFI list: a credit institution. RES wins, MO checks."""
    service = replaying_service(build_resident_codebooks())
    (case,) = [case for case in CASES if case.id == "res-ceb"]
    notes = service.suggest(case.request()).all_notes
    assert any("[ECB_MFI:CREDIT_INSTITUTION]" in note and "13110" in note for note in notes)


def test_the_recording_is_dated_and_holds_the_ecb_memberships() -> None:
    document = json.loads(
        (APP_ROOT / "tests" / "golden" / "residents_registers.json").read_text(encoding="utf-8")
    )
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", document["captured_on"])
    assert document["ecb"]["entries"], "captured with the ECB lists"


def test_an_unrecorded_ecb_lei_is_an_error_not_an_absence() -> None:
    with pytest.raises(GoldenError):
        RecordedEcb({"as_of": "2026-10-02", "entries": {}}).find("529900S5R9YHJHYKKG94")


@pytest.fixture(scope="module")
def real_codebooks():
    try:
        codebooks, _ = load_and_check(get_settings(), strict=False)
    except (CodebookError, OSError) as exc:
        pytest.skip(f"real codebooks not available: {exc}")
    return codebooks


def test_with_the_real_codebooks_every_case_is_the_brief_s_too(real_codebooks) -> None:
    results = score_residents(real_codebooks)
    assert [r.describe() for r in results if not (r.nace_ok and r.esa_ok)] == []
