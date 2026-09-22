"""The golden set: loading, validation, recall scoring, and the verified/provisional split."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.classify.candidates import EsaCandidateFilter, NaceCandidateFilter
from core.classify.golden import (
    GOLDEN_PATH,
    NACE,
    GoldenCase,
    GoldenError,
    counts,
    load_golden,
    score_recall,
)
from core.codebooks.models import CodebookSet


def write_cases(path: Path, cases: list[dict]) -> Path:
    path.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
    return path


BASE_CASE = {
    "id": "one",
    "issuer": "Test B.V.",
    "description": "a commercial bank",
    "expected_nace": "64",
    "expected_esa": "2002213",
}


class TestLoading:
    def test_reads_the_shipped_file(self) -> None:
        cases = load_golden()
        assert cases
        assert all(case.description for case in cases)

    def test_shipped_cases_are_all_marked_provisional(self) -> None:
        """If this ever fails, someone has signed cases off - update the docs with the number."""
        cases = load_golden()
        assert all(not case.verified for case in cases), (
            "a case is now verified; the golden set can start reporting real accuracy"
        )

    def test_shipped_cases_cover_both_codebooks(self) -> None:
        stats = counts(load_golden())
        assert stats["with_nace"] >= 5 and stats["with_esa"] >= 5

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(GoldenError, match="not found"):
            load_golden(tmp_path / "nope.json")

    def test_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "cases.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(GoldenError, match="valid JSON"):
            load_golden(path)

    def test_case_without_an_id_is_rejected(self, tmp_path: Path) -> None:
        path = write_cases(tmp_path / "c.json", [{"description": "x"}])
        with pytest.raises(GoldenError, match="no id"):
            load_golden(path)

    def test_case_without_a_description_is_rejected(self, tmp_path: Path) -> None:
        """A case with nothing to classify would silently score as a pass."""
        path = write_cases(tmp_path / "c.json", [{"id": "a", "description": "  "}])
        with pytest.raises(GoldenError, match="no description"):
            load_golden(path)

    def test_duplicate_ids_are_rejected(self, tmp_path: Path) -> None:
        path = write_cases(tmp_path / "c.json", [BASE_CASE, dict(BASE_CASE)])
        with pytest.raises(GoldenError, match="duplicate"):
            load_golden(path)

    def test_verified_flag_follows_verified_by(self, tmp_path: Path) -> None:
        path = write_cases(
            tmp_path / "c.json",
            [BASE_CASE, {**BASE_CASE, "id": "two", "verified_by": "MO analyst"}],
        )
        cases = {case.id: case for case in load_golden(path)}
        assert not cases["one"].verified
        assert cases["two"].verified


class TestRecallScoring:
    def _score(self, codebooks: CodebookSet, cases: tuple[GoldenCase, ...], kind: str):
        chooser = NaceCandidateFilter(codebooks) if kind == NACE else EsaCandidateFilter(codebooks)
        shortlists = [(case, chooser.shortlist(case.description)) for case in cases]
        return score_recall(cases, shortlists, kind)

    def test_a_found_code_is_reported_with_its_rank(self, codebooks: CodebookSet) -> None:
        cases = (
            GoldenCase(id="bank", issuer="X", description="a commercial bank", expected_nace="64"),
        )
        report = self._score(codebooks, cases, NACE)
        assert report.results[0].found
        assert report.results[0].rank == 1

    def test_a_missing_code_is_a_miss(self, codebooks: CodebookSet) -> None:
        cases = (
            GoldenCase(id="odd", issuer="X", description="a commercial bank", expected_nace="47"),
        )
        report = self._score(codebooks, cases, NACE)
        assert report.misses()
        assert report.recall(verified_only=False) == 0.0

    def test_cases_without_an_expected_code_are_skipped(self, codebooks: CodebookSet) -> None:
        cases = (GoldenCase(id="none", issuer="X", description="a bank"),)
        assert self._score(codebooks, cases, NACE).results == ()

    def test_verified_and_provisional_are_scored_apart(self, codebooks: CodebookSet) -> None:
        """Quoting provisional cases as accuracy would manufacture confidence."""
        cases = (
            GoldenCase(
                id="ok",
                issuer="X",
                description="a commercial bank",
                expected_nace="64",
                verified_by="MO",
            ),
            GoldenCase(id="bad", issuer="X", description="a commercial bank", expected_nace="47"),
        )
        report = self._score(codebooks, cases, NACE)
        assert report.recall(verified_only=True) == 1.0
        assert report.recall(verified_only=False) == 0.5

    def test_recall_is_none_when_nothing_is_verified(self, codebooks: CodebookSet) -> None:
        cases = (GoldenCase(id="a", issuer="X", description="a bank", expected_nace="64"),)
        assert self._score(codebooks, cases, NACE).recall(verified_only=True) is None

    def test_summary_says_so_rather_than_printing_a_number(self, codebooks: CodebookSet) -> None:
        cases = (GoldenCase(id="a", issuer="X", description="a bank", expected_nace="64"),)
        assert "no verified case" in self._score(codebooks, cases, NACE).summary()

    def test_mean_rank_ignores_misses(self, codebooks: CodebookSet) -> None:
        cases = (
            GoldenCase(id="hit", issuer="X", description="a commercial bank", expected_nace="64"),
            GoldenCase(id="miss", issuer="X", description="a commercial bank", expected_nace="47"),
        )
        report = self._score(codebooks, cases, NACE)
        assert report.mean_rank(verified_only=False) == 1.0


def test_golden_path_points_into_the_tests_tree() -> None:
    assert GOLDEN_PATH.name == "cases.json"
    assert GOLDEN_PATH.parent.name == "golden"


def test_every_shipped_case_is_reachable_by_the_filter(codebooks: CodebookSet) -> None:
    """Smoke test against the synthetic codebook: the machinery runs on every shipped case.

    Real recall is measured against the real codebooks in test_classify_real_recall.py; the
    synthetic set does not contain every division a case expects.
    """
    nace_filter = NaceCandidateFilter(codebooks)
    esa_filter = EsaCandidateFilter(codebooks)
    for case in load_golden():
        assert nace_filter.shortlist(case.description) is not None
        assert esa_filter.shortlist(case.description) is not None
