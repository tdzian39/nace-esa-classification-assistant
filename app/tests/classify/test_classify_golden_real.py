"""The real issuers of the golden set (roadmap E8): each case keeps the promises it makes.

A provisional case is only useful if a checker can overturn it quickly: the ISIN must be the
issuer's, the codes must exist in the codebook the bank uses, and the argument and its sources
must be there to read. These tests pin that, and that the recorded register answers replay the
issuer the case names. None of them says the codes are right - nobody at the bank has checked.
"""

from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from config.settings import APP_ROOT, Settings
from core.classify.golden import GoldenCase, load_golden
from core.classify.golden_fixtures import load_answers, replaying_identifier

CASES = load_golden()
REAL = [case for case in CASES if case.real]
BA0036_NONRESIDENT = {
    item["code"]
    for item in json.loads(
        (APP_ROOT / "tests" / "golden" / "ba0036_v044_nonresident.json").read_text(encoding="utf-8")
    )["items"]
}
CATEGORIES = {"bank", "insurer", "corp", "vehicle", "gov", "supra", "fund", "fvc", "agency"}


def ids(cases: list[GoldenCase]) -> list[str]:
    return [case.id for case in cases]


def test_there_are_about_thirty_real_issuers_of_every_kind() -> None:
    assert len(REAL) >= 30
    kinds = Counter(case.category for case in REAL)
    assert {"bank", "corp", "fund", "gov", "supra", "vehicle"} <= set(kinds)


def test_no_real_case_is_marked_verified() -> None:
    """Codes worked out from public sources stay provisional until checked against CTS (Q8)."""
    assert all(case.verified_by is None and case.verified_on is None for case in REAL)


def test_the_fictional_trap_cases_are_still_there() -> None:
    fictional = [case for case in CASES if not case.real]
    assert len(fictional) == 10
    assert all(case.source == "fictional" for case in fictional)


def test_the_ba0036_reference_has_the_56_non_resident_leaves_cts_holds() -> None:
    assert len(BA0036_NONRESIDENT) == 56
    assert {"2002703", "2002213", "2001003", "2009031", "2003110"} <= BA0036_NONRESIDENT


@pytest.mark.parametrize("case", REAL, ids=ids(REAL))
class TestEachRealCase:
    def test_the_esa_code_exists_in_ba0036(self, case: GoldenCase) -> None:
        assert case.expected_esa in BA0036_NONRESIDENT

    def test_the_nace_code_is_a_division(self, case: GoldenCase) -> None:
        assert re.fullmatch(r"\d{2}", case.expected_nace or "")

    def test_the_identifiers_are_well_formed(self, case: GoldenCase) -> None:
        assert case.isin and len(case.isin) == 12
        assert case.lei and re.fullmatch(r"[0-9A-Z]{18}[0-9]{2}", case.lei)
        assert case.country and len(case.country) == 2

    def test_the_argument_and_its_sources_are_there(self, case: GoldenCase) -> None:
        assert case.nace_reasoning and case.esa_reasoning
        assert case.evidence and all(url.startswith("https://") for url in case.evidence)
        assert case.confidence in {"high", "medium", "low"}
        assert case.category in CATEGORIES
        assert case.id.startswith(f"{case.category}-")

    def test_the_description_is_short_enough_to_paste(self, case: GoldenCase) -> None:
        assert 15 <= len(case.description.split()) <= 62


@pytest.fixture(scope="module")
def identities() -> dict[str, object]:
    """Every real case's identity, replayed once from the recording."""
    identifier = replaying_identifier(Settings(_env_file=None), load_answers())
    return {case.id: identifier.identify(case.isin) for case in REAL}


class TestRecordedRegisterAnswers:
    def test_every_real_isin_was_recorded(self, identities: dict[str, object]) -> None:
        """A replay that misses a request raises, so building the identities is the check."""
        assert len(identities) == len(REAL)

    def test_the_replayed_lei_is_the_one_the_case_names(
        self, identities: dict[str, object]
    ) -> None:
        """GLEIF maps most ISINs; where it has none the LEI is absent, never a different one."""
        for case in REAL:
            lei = identities[case.id].lei  # type: ignore[attr-defined]
            assert lei in (None, case.lei), case.id

    def test_gleif_resolves_most_of_them_and_openfigi_all(
        self, identities: dict[str, object]
    ) -> None:
        with_lei = [case for case in REAL if identities[case.id].lei]  # type: ignore[attr-defined]
        assert len(with_lei) >= 20
        assert all(identities[case.id].instrument for case in REAL)  # type: ignore[attr-defined]

    def test_the_recording_is_dated(self) -> None:
        document = json.loads(
            (APP_ROOT / "tests" / "golden" / "identity.json").read_text(encoding="utf-8")
        )
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", document["captured_on"])
