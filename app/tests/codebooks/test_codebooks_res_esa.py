"""The RES sector -> BA0036 table: every row, the codes it may name, and its startup check.

The table is data a reviewer reads row by row, so these tests restate the rules of the brief
(2 Oct 2026) against it: the direct rows follow ``1`` + RES code without its first digit +
``00``; S.122 splits into banks, cooperatives and other deposit-takers; S.125 into four
subtypes; some RES sectors have no resident code. Nothing here names a CTS ID.
"""

from __future__ import annotations

import re

import pytest

from config.settings import get_settings
from core.codebooks.consistency import check_consistency
from core.codebooks.errors import CodebookError
from core.codebooks.loaders import load_and_check
from core.codebooks.res_esa import (
    DEPOSIT_TAKERS,
    DIRECT,
    NO_COUNTERPART,
    OTHER_INTERMEDIARIES,
    SECTOR_NAMES_CS,
    missing_targets,
    targets,
)
from tests.classify.conftest import ESA_ROWS, RESIDENT_ESA_ROWS, build_codebooks


@pytest.mark.parametrize(("res", "ba0036"), sorted(DIRECT.items()))
def test_a_direct_row_is_one_plus_the_code_without_its_first_digit_plus_00(
    res: str, ba0036: str
) -> None:
    assert ba0036 == "1" + res[1:] + "00"


@pytest.mark.parametrize("control", ["1", "2", "3"])
def test_the_deposit_takers_split_by_institution_type(control: str) -> None:
    row = DEPOSIT_TAKERS["1220" + control]
    assert row["bank"] == f"1221{control}00"
    if control == "1":
        # ČNB's code for a public "other deposit-taker"; BA0036 has no 1224100 and no public
        # cooperative.
        assert row["other"] == "1222100"
        assert "cooperative" not in row
    else:
        assert row["cooperative"] == f"1222{control}00"
        assert row["other"] == f"1224{control}00"


@pytest.mark.parametrize("control", ["1", "2", "3"])
def test_the_other_intermediaries_are_1250_control_subtype_0(control: str) -> None:
    row = OTHER_INTERMEDIARIES["1250" + control]
    assert row == {
        "securitisation": f"1250{control}10",
        "dealer": f"1250{control}20",
        "lender": f"1250{control}30",
        "specialised": f"1250{control}40",
    }


def test_the_rows_without_a_resident_code() -> None:
    assert {"0", "13120", "21110", "21120", "21210", "21220", "22000"} == NO_COUNTERPART


def test_every_res_sector_is_in_exactly_one_part_of_the_table() -> None:
    parts = (set(DIRECT), set(DEPOSIT_TAKERS), set(OTHER_INTERMEDIARIES), set(NO_COUNTERPART))
    assert sum(len(part) for part in parts) == len(set().union(*parts))
    assert set().union(*parts) == set(SECTOR_NAMES_CS)


def test_the_table_names_ba0036_resident_codes_only() -> None:
    """Seven digits, first digit 1 - and nothing that could be a CTS ID (the repo is public)."""
    codes = targets()
    assert all(re.fullmatch(r"1\d{6}", code) for code in codes)
    assert len(codes) == 53
    assert "1224100" not in codes and "1500100" not in codes
    assert "1222100" in codes


def test_the_targets_are_the_resident_block_of_the_test_codebook() -> None:
    assert set(targets()) == {code for code, _, _ in RESIDENT_ESA_ROWS}


class TestStartupCheck:
    def test_a_codebook_with_the_whole_resident_block_has_no_finding(self) -> None:
        codebooks = build_codebooks((*ESA_ROWS[:-2], *RESIDENT_ESA_ROWS))
        assert missing_targets(codebooks) == ()
        report = check_consistency(codebooks)
        assert "W_RES_ESA_TARGET_MISSING" not in {f.code for f in report.findings}

    def test_a_target_the_codebook_lacks_is_a_warning_naming_it(self) -> None:
        rows = [row for row in RESIDENT_ESA_ROWS if row[0] != "1250330"]
        codebooks = build_codebooks((*ESA_ROWS[:-2], *rows))
        assert missing_targets(codebooks) == ("1250330",)
        (finding,) = [
            f for f in check_consistency(codebooks).findings if f.code == "W_RES_ESA_TARGET_MISSING"
        ]
        assert finding.severity == "warning"
        assert finding.details["codes"] == ["1250330"]

    def test_a_missing_target_does_not_stop_the_tool(self) -> None:
        """A warning, not an error: foreign issuers must keep working."""
        codebooks = build_codebooks(ESA_ROWS)
        assert check_consistency(codebooks).ok


@pytest.fixture(scope="module")
def real_codebooks():
    try:
        codebooks, _ = load_and_check(get_settings(), strict=False)
    except (CodebookError, OSError) as exc:
        pytest.skip(f"real codebooks not available: {exc}")
    return codebooks


def test_every_target_is_emittable_with_the_real_codebook(real_codebooks) -> None:
    assert missing_targets(real_codebooks) == ()


def test_the_targets_are_exactly_the_real_resident_leaves(real_codebooks) -> None:
    resident = {sector.key for sector in real_codebooks.esa_leaves() if sector.key.startswith("1")}
    assert set(targets()) == resident
