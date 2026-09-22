"""Tests of the startup consistency check and ``load_and_check``."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import pytest

from core.codebooks.consistency import (
    ConsistencyReport,
    Finding,
    assert_consistent,
    check_consistency,
)
from core.codebooks.errors import CodebookConsistencyError
from core.codebooks.loaders import load_and_check, load_codebooks
from core.codebooks.models import CodebookSet

from .conftest import FILE_NAMES, CodebookRows, MakeCodebookDir, Row, make_settings


def _report(
    tmp_path: Path,
    make_codebook_dir: MakeCodebookDir,
    **tables: Sequence[Row] | None,
) -> tuple[CodebookSet, ConsistencyReport]:
    codebooks = load_codebooks(make_settings(make_codebook_dir(tmp_path / "cb", **tables)))
    return codebooks, check_consistency(codebooks)


def _codes(report: ConsistencyReport) -> list[str]:
    return [f.code for f in report.findings]


def _find(report: ConsistencyReport, code: str) -> Finding:
    matches = [f for f in report.findings if f.code == code]
    assert len(matches) == 1, f"expected exactly one {code}, got {matches}"
    return matches[0]


def test_clean_fixture_is_ok_with_infos_only(
    codebooks: CodebookSet, defaults: CodebookRows
) -> None:
    report = check_consistency(codebooks)
    assert report.ok, report.summary()
    assert report.errors == () and report.warnings == ()
    assert sorted(_codes(report)) == ["I_CTS_ESA_NOT_VALID_LEAF", "I_EMITTABLE_IDS"]

    parents = _find(report, "I_CTS_ESA_NOT_VALID_LEAF")
    assert parents.severity == "info"
    assert parents.details["codes"] == list(defaults.parent_codes)
    assert parents.details["ids"] == ["1101", "1102", "1103", "1104", "1105"]
    assert "S.11" in parents.message

    emittable = _find(report, "I_EMITTABLE_IDS")
    assert emittable.details == {
        "version": codebooks.version.id,
        "esa_count": len(defaults.ba0036_valid),
        "nace_count": len(defaults.nace_divisions),
    }
    assert codebooks.version.id in emittable.message


def test_report_properties_and_summary() -> None:
    report = ConsistencyReport(
        (
            Finding("info", "I_X", "info line"),
            Finding("warning", "W_X", "warning line"),
            Finding("error", "E_X", "error line", {"k": 1}),
        )
    )
    assert not report.ok
    assert report.counts == {"error": 1, "warning": 1, "info": 1}
    assert [f.code for f in report.errors] == ["E_X"]
    assert [f.code for f in report.warnings] == ["W_X"]
    assert [f.code for f in report.infos] == ["I_X"]
    lines = report.summary().splitlines()
    assert lines[0] == "Codebook consistency FAILED: 1 error(s), 1 warning(s), 1 info"
    assert lines[1:] == [
        "[ERROR] E_X: error line",
        "[WARNING] W_X: warning line",
        "[INFO] I_X: info line",
    ]
    empty = ConsistencyReport(())
    assert empty.ok and empty.counts == {"error": 0, "warning": 0, "info": 0}
    assert empty.summary() == "Codebook consistency OK: 0 error(s), 0 warning(s), 0 info"


@pytest.mark.parametrize("name", ["cts_ba0036", "ba0036_valid", "cts_okec_nace2", "nace_stat"])
def test_empty_codebook_each(tmp_path: Path, make_codebook_dir: MakeCodebookDir, name: str) -> None:
    _, report = _report(tmp_path, make_codebook_dir, **{name: []})
    finding = _find(report, "E_EMPTY_CODEBOOK")
    assert finding.details["codebook"] == name
    assert FILE_NAMES[name] in finding.message
    assert not report.ok


def test_all_rows_unusable_counts_as_empty(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir
) -> None:
    _, report = _report(
        tmp_path, make_codebook_dir, cts_okec_nace2=[(2001, "621", "bad"), (2002, "A", "bad")]
    )
    assert _find(report, "E_EMPTY_CODEBOOK").details["codebook"] == "cts_okec_nace2"
    assert _find(report, "E_MALFORMED_ROW").details["codebook"] == "cts_okec_nace2"


def test_valid_leaf_without_cts_entry(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [row for row in defaults.cts_ba0036 if row[1] not in {"S.1314", "S.2111"}]
    _, report = _report(tmp_path, make_codebook_dir, cts_ba0036=rows)
    assert not report.ok
    finding = _find(report, "E_VALID_ESA_WITHOUT_CTS_ID")
    assert finding.details["codes"] == ["S.1314", "S.2111"]
    assert "S.1314" in finding.message and "S.2111" in finding.message
    emittable = _find(report, "I_EMITTABLE_IDS")
    assert emittable.details["esa_count"] == len(defaults.ba0036_valid) - 2
    assert "E_EMITTED_ID_MISSING" not in _codes(report)


def test_duplicate_cts_id(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [*defaults.cts_okec_nace2, (2001, "99", "duplicate id, new code")]
    _, report = _report(tmp_path, make_codebook_dir, cts_okec_nace2=rows)
    finding = _find(report, "E_DUPLICATE_CTS_ID")
    assert finding.details == {"codebook": "cts_okec_nace2", "ids": ["2001"]}
    assert not report.ok
    # the extra division 99 has no NACE_STAT labels -> warning as well
    assert _find(report, "W_CTS_NACE_NOT_IN_STAT").details["codes"] == ["99"]


def test_duplicate_cts_value(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [*defaults.cts_ba0036, (1999, "s11001", "same code, other spelling")]
    _, report = _report(tmp_path, make_codebook_dir, cts_ba0036=rows)
    finding = _find(report, "E_DUPLICATE_CTS_VALUE")
    assert finding.details == {"codebook": "cts_ba0036", "keys": ["11001"], "values": ["S.11001"]}
    assert "E_DUPLICATE_CTS_ID" not in _codes(report)
    assert not report.ok


def test_nace_stat_division_without_cts(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [
        *defaults.nace_stat,
        (
            "99",
            "Činnosti exteritoriálních organizací",
            "Činnosti exteritoriálních organizací a orgánů",
        ),
    ]
    _, report = _report(tmp_path, make_codebook_dir, nace_stat=rows)
    assert not report.ok
    assert _find(report, "E_NACE_STAT_WITHOUT_CTS_ID").details["codes"] == ["99"]
    assert _find(report, "I_EMITTABLE_IDS").details["nace_count"] == len(defaults.nace_divisions)


def test_cts_division_without_stat_is_warning_only(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [*defaults.cts_okec_nace2, (2099, "99", "Činnosti exteritoriálních organizací")]
    codebooks, report = _report(tmp_path, make_codebook_dir, cts_okec_nace2=rows)
    assert report.ok, report.summary()
    finding = _find(report, "W_CTS_NACE_NOT_IN_STAT")
    assert finding.severity == "warning"
    assert finding.details["codes"] == ["99"]
    assert codebooks.cts_id_for_nace("99.00").cts_id == "2099"


def test_malformed_value_row(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [*defaults.cts_ba0036, (1998, "S.1A", "typo")]
    _, report = _report(tmp_path, make_codebook_dir, cts_ba0036=rows)
    assert not report.ok
    finding = _find(report, "E_MALFORMED_ROW")
    assert finding.details["codebook"] == "cts_ba0036"
    rows_detail = finding.details["rows"]
    assert isinstance(rows_detail, list) and len(rows_detail) == 1
    assert rows_detail[0]["row"] == len(defaults.cts_ba0036) + 2
    assert rows_detail[0]["raw"]["VALUE"] == "S.1A"
    assert "S.1A" in rows_detail[0]["reason"]


def test_duplicate_valid_esa_is_warning(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [*defaults.ba0036_valid, ("S.14", "Domácnosti (znovu)", "")]
    codebooks, report = _report(tmp_path, make_codebook_dir, ba0036_valid=rows)
    assert report.ok
    assert _find(report, "W_DUPLICATE_VALID_ESA").details["codes"] == ["S.14"]
    assert codebooks.ba0036_valid.duplicate_keys == ("14",)
    assert len(codebooks.ba0036_valid.sectors) == len(defaults.ba0036_valid) + 1
    assert codebooks.ba0036_valid.by_key["14"].name == "Domácnosti"  # first row wins
    assert len(codebooks.esa_leaves()) == len(defaults.ba0036_valid)  # deduplicated
    assert "ba0036_valid=13 leaves" in codebooks.describe()


def test_missing_descriptions_are_warnings(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    cts = [
        (row[0], row[1], None) if row[1] in {"S.14", "S.15"} else row for row in defaults.cts_ba0036
    ]
    valid = [(row[0], "", row[2]) if row[0] == "S.121" else row for row in defaults.ba0036_valid]
    stat = [*defaults.nace_stat, ("99", None, None)]
    okec = [*defaults.cts_okec_nace2, (2099, "99", "")]
    _, report = _report(
        tmp_path,
        make_codebook_dir,
        cts_ba0036=cts,
        ba0036_valid=valid,
        nace_stat=stat,
        cts_okec_nace2=okec,
    )
    assert report.ok, report.summary()
    findings = {
        f.details["codebook"]: f for f in report.findings if f.code == "W_MISSING_DESCRIPTION"
    }
    assert findings["cts_ba0036"].details["codes"] == ["S.14", "S.15"]
    assert findings["ba0036_valid"].details["codes"] == ["S.121"]
    assert findings["cts_okec_nace2"].details["codes"] == ["99"]
    assert findings["nace_stat"].details["codes"] == ["99"]


def test_skipped_rows_are_warnings(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [*defaults.ba0036_valid, (None, "bez kódu", "řádek bez Kód"), ("", "také bez kódu", "")]
    _, report = _report(tmp_path, make_codebook_dir, ba0036_valid=rows)
    assert report.ok
    finding = _find(report, "W_SKIPPED_ROWS")
    assert finding.details == {"codebook": "ba0036_valid", "skipped_rows": 2}


def test_emitted_id_self_check_never_fires_on_consistent_data(codebooks: CodebookSet) -> None:
    report = check_consistency(codebooks)
    assert "E_EMITTED_ID_MISSING" not in _codes(report)
    emitted_esa = {codebooks.cts_id_for_esa(s.code).cts_id for s in codebooks.esa_leaves()}
    emitted_nace = {codebooks.cts_id_for_nace(d.code).cts_id for d in codebooks.nace_divisions()}
    assert emitted_esa <= set(codebooks.cts_ba0036.by_id)
    assert emitted_nace <= set(codebooks.cts_okec_nace2.by_id)
    assert len(emitted_esa) == _find(report, "I_EMITTABLE_IDS").details["esa_count"]


def test_assert_consistent(codebooks: CodebookSet) -> None:
    assert_consistent(check_consistency(codebooks))
    failing = ConsistencyReport((Finding("error", "E_X", "boom"),))
    with pytest.raises(CodebookConsistencyError) as info:
        assert_consistent(failing)
    assert info.value.report is failing
    assert info.value.codebooks is None
    assert "E_X: boom" in str(info.value)


def test_load_and_check_strict_raises_with_report_and_codebooks(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [row for row in defaults.cts_ba0036 if row[1] != "S.14"]
    directory = make_codebook_dir(tmp_path / "cb", cts_ba0036=rows)
    with pytest.raises(CodebookConsistencyError) as info:
        load_and_check(make_settings(directory))
    assert "E_VALID_ESA_WITHOUT_CTS_ID" in _codes(info.value.report)
    assert info.value.codebooks is not None
    assert info.value.codebooks.version.id.startswith("cb-")


def test_load_and_check_non_strict_returns_report_and_logs(
    tmp_path: Path,
    make_codebook_dir: MakeCodebookDir,
    defaults: CodebookRows,
    caplog: pytest.LogCaptureFixture,
) -> None:
    rows = [row for row in defaults.cts_ba0036 if row[1] != "S.14"]
    okec = [*defaults.cts_okec_nace2, (2099, "99", "no labels")]
    directory = make_codebook_dir(tmp_path / "cb", cts_ba0036=rows, cts_okec_nace2=okec)
    with caplog.at_level(logging.INFO, logger="core.codebooks"):
        codebooks, report = load_and_check(make_settings(directory), strict=False)
    assert isinstance(codebooks, CodebookSet)
    assert not report.ok
    by_level = {
        (r.levelno, r.getMessage().split(":")[0])
        for r in caplog.records
        if r.name == "core.codebooks.loaders"
    }
    assert (logging.ERROR, "E_VALID_ESA_WITHOUT_CTS_ID") in by_level
    assert (logging.WARNING, "W_CTS_NACE_NOT_IN_STAT") in by_level
    assert (logging.INFO, "I_EMITTABLE_IDS") in by_level
    assert any(codebooks.version.id in r.getMessage() for r in caplog.records)


def test_load_and_check_clean_strict(codebook_dir: Path) -> None:
    codebooks, report = load_and_check(make_settings(codebook_dir), strict=True)
    assert report.ok
    assert codebooks.cts_id_for_esa("S.11001").cts_id == "1001"


def test_load_and_check_accepts_codebook_dir_override(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir
) -> None:
    directory = make_codebook_dir(tmp_path / "elsewhere")
    codebooks, report = load_and_check(make_settings(tmp_path / "missing"), codebook_dir=directory)
    assert report.ok
    assert codebooks.version.files[0].path.parent == directory


# --- review regressions -------------------------------------------------------------------


def test_consistency_error_pickles_round_trip(codebooks: CodebookSet) -> None:
    import pickle

    report = ConsistencyReport((Finding("error", "E_X", "boom", {"k": 1}),))
    error = CodebookConsistencyError(report, codebooks=codebooks)
    restored = pickle.loads(pickle.dumps(error))
    assert isinstance(restored, CodebookConsistencyError)
    assert restored.report == report
    assert restored.codebooks is not None
    assert restored.codebooks.version.id == codebooks.version.id
    assert "E_X: boom" in str(restored)
    plain = pickle.loads(pickle.dumps(CodebookConsistencyError(report)))
    assert plain.codebooks is None and plain.report == report


def test_rows_behind_a_duplicate_id_are_still_reported(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [*defaults.cts_ba0036, (1101, "S.128", "")]  # 1101 already used by S.11
    codebooks, report = _report(tmp_path, make_codebook_dir, cts_ba0036=rows)
    assert _find(report, "E_DUPLICATE_CTS_ID").details["ids"] == ["1101"]
    assert "128" in codebooks.cts_ba0036.by_key
    parents = _find(report, "I_CTS_ESA_NOT_VALID_LEAF")
    assert parents.details["codes"] == [*defaults.parent_codes, "S.128"]
    missing = {
        f.details["codebook"]: f for f in report.findings if f.code == "W_MISSING_DESCRIPTION"
    }
    assert missing["cts_ba0036"].details["codes"] == ["S.128"]


def test_emitted_id_missing_fires_when_by_id_disagrees_with_by_key(codebooks: CodebookSet) -> None:
    """The spec self-check must be able to fire: drop one emittable ID from each by_id index."""
    esa_book, nace_book = codebooks.cts_ba0036, codebooks.cts_okec_nace2
    object.__setattr__(esa_book, "by_id", {k: v for k, v in esa_book.by_id.items() if k != "1001"})
    object.__setattr__(
        nace_book, "by_id", {k: v for k, v in nace_book.by_id.items() if k != "2004"}
    )
    report = check_consistency(codebooks)
    finding = _find(report, "E_EMITTED_ID_MISSING")
    assert finding.severity == "error"
    assert finding.details == {"esa_ids": ["1001"], "nace_ids": ["2004"]}
    assert "1001" in finding.message and "2004" in finding.message
    assert not report.ok


def test_emitted_id_missing_fires_for_one_codebook_alone(codebooks: CodebookSet) -> None:
    book = codebooks.cts_ba0036
    object.__setattr__(book, "by_id", {k: v for k, v in book.by_id.items() if k != "1001"})
    finding = _find(check_consistency(codebooks), "E_EMITTED_ID_MISSING")
    assert finding.details == {"esa_ids": ["1001"], "nace_ids": []}
