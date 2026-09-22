"""The result workbook: cell types, number formats, widths and the Run sheet.

Every test reads the written file back with openpyxl, so what is asserted is what Excel
would actually show. Rows are plain mappings keyed by ``SUGGESTION_COLUMNS``; the last test
writes a real suggestion row end to end.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook

from core.export.columns import SUGGESTION_COLUMNS, WRAPPED_COLUMNS, header_label, suggestion_row
from core.export.xlsx import MAX_CELL_LENGTH, RUN_SHEET, SUBJECTS_SHEET, write_workbook
from tests.export.conftest import ISIN, LEGAL_NAME, LEI, suggestion

#: 14:00 in Prague summer time is 12:00 UTC - what the sheet must show.
PRAGUE_2PM = datetime(2026, 9, 22, 14, 0, tzinfo=timezone(timedelta(hours=2)))
NOON_UTC_NAIVE = datetime(2026, 9, 22, 12, 0)

ROW: dict[str, object] = {
    "IN_isin": ISIN,
    "issuer_name": LEGAL_NAME,
    "issuer_lei": LEI,
    "NACE_code": "01",
    "NACE_cts_id": "0455",
    "NACE_alt1": "10 (CTS 0464) – Výroba potravinářských výrobků",
    "NACE_candidates": (
        "01 (CTS 0455) – Rostlinná a živočišná výroba, myslivost",
        "10 (CTS 0464) – Výroba potravinářských výrobků",
    ),
    "ESA_code": "2002703",
    "ESA_cts_id": "0613",
    "source": "GLEIF+WEB",
    "retrieved_at": PRAGUE_2PM,
    "codebook_version": "cb-0123456789abcdef",
    "evidence_urls": ("https://a.example/1", "https://a.example/2"),
    "notes": ("první poznámka", "druhá poznámka"),
}


def write(tmp_path: Path, columns, rows, **kwargs) -> Path:
    return write_workbook(tmp_path / "out.xlsx", columns, rows, **kwargs)


def cell(tmp_path: Path, column: str, row: dict[str, object] = ROW):
    """The written cell of ``column`` in the first data row."""
    sheet = load_workbook(write(tmp_path, SUGGESTION_COLUMNS, [row]))[SUBJECTS_SHEET]
    return sheet.cell(row=2, column=SUGGESTION_COLUMNS.index(column) + 1)


class TestStructure:
    def test_header_and_one_row_per_issuer(self, tmp_path: Path) -> None:
        rows = [ROW, {"IN_name": "Jiný emitent"}]
        sheet = load_workbook(write(tmp_path, SUGGESTION_COLUMNS, rows))[SUBJECTS_SHEET]

        assert sheet.max_row == 3  # header + two issuers
        assert [c.value for c in sheet[1]] == [header_label(c) for c in SUGGESTION_COLUMNS]

    def test_header_is_frozen_and_filterable(self, tmp_path: Path) -> None:
        sheet = load_workbook(write(tmp_path, SUGGESTION_COLUMNS, [ROW]))[SUBJECTS_SHEET]
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref is not None

    def test_missing_keys_become_empty_cells(self, tmp_path: Path) -> None:
        sheet = load_workbook(write(tmp_path, ("a", "b"), [{"a": 1}]))[SUBJECTS_SHEET]
        assert sheet.cell(row=2, column=1).value == 1
        assert sheet.cell(row=2, column=2).value is None

    def test_no_run_sheet_unless_metadata_is_given(self, tmp_path: Path) -> None:
        book = load_workbook(write(tmp_path, SUGGESTION_COLUMNS, [ROW]))
        assert book.sheetnames == [SUBJECTS_SHEET]


class TestCellTypes:
    @pytest.mark.parametrize(
        "column",
        [
            "IN_isin",
            "issuer_lei",
            "NACE_code",
            "NACE_cts_id",
            "NACE_alt1",
            "ESA_code",
            "ESA_cts_id",
        ],
    )
    def test_codes_stay_text_exactly_as_given(self, tmp_path: Path, column: str) -> None:
        """A division ``01`` or a CTS ID ``0455`` that Excel turns into a number is a wrong code."""
        written = cell(tmp_path, column)
        assert written.value == ROW[column]
        assert isinstance(written.value, str)
        assert written.number_format == "@"

    def test_prose_is_not_forced_to_text(self, tmp_path: Path) -> None:
        assert cell(tmp_path, "issuer_name").number_format == "General"

    def test_datetimes_are_written_as_naive_utc(self, tmp_path: Path) -> None:
        """Excel has no timezone; writing the Prague wall clock would shift the time by two hours."""
        written = cell(tmp_path, "retrieved_at")
        assert written.value == NOON_UTC_NAIVE
        assert written.number_format == "yyyy-mm-dd hh:mm:ss"
        assert header_label("retrieved_at") == "retrieved_at (UTC)"

    def test_a_naive_datetime_is_written_unchanged(self, tmp_path: Path) -> None:
        path = write(tmp_path, ("at",), [{"at": datetime(2020, 1, 2, 3, 4)}])
        assert load_workbook(path)[SUBJECTS_SHEET].cell(row=2, column=1).value == datetime(
            2020, 1, 2, 3, 4
        )

    def test_dates_are_real_dates(self, tmp_path: Path) -> None:
        path = write(tmp_path, ("day",), [{"day": date(2020, 1, 2)}])
        written = load_workbook(path)[SUBJECTS_SHEET].cell(row=2, column=1)
        assert written.value == datetime(2020, 1, 2)
        assert written.number_format == "yyyy-mm-dd"

    def test_booleans_stay_boolean(self, tmp_path: Path) -> None:
        path = write(tmp_path, ("flag",), [{"flag": False}])
        assert load_workbook(path)[SUBJECTS_SHEET].cell(row=2, column=1).value is False

    def test_none_is_an_empty_cell_not_false(self, tmp_path: Path) -> None:
        """None means "unknown"; writing FALSE would assert something nobody knows."""
        path = write(tmp_path, ("flag",), [{"flag": None}])
        assert load_workbook(path)[SUBJECTS_SHEET].cell(row=2, column=1).value is None


class TestListsAndWidths:
    def test_codes_are_separated_by_semicolons(self, tmp_path: Path) -> None:
        assert cell(tmp_path, "evidence_urls").value == "https://a.example/1; https://a.example/2"

    def test_long_texts_are_separated_by_newlines(self, tmp_path: Path) -> None:
        assert cell(tmp_path, "notes").value == "první poznámka\ndruhá poznámka"
        assert cell(tmp_path, "NACE_candidates").value == "\n".join(ROW["NACE_candidates"])  # type: ignore[arg-type]

    @pytest.mark.parametrize("column", sorted(WRAPPED_COLUMNS))
    def test_wrapped_columns_wrap_and_are_wide(self, tmp_path: Path, column: str) -> None:
        sheet = load_workbook(write(tmp_path, SUGGESTION_COLUMNS, [ROW]))[SUBJECTS_SHEET]
        index = SUGGESTION_COLUMNS.index(column) + 1
        assert sheet.cell(row=2, column=index).alignment.wrap_text is True
        letter = sheet.cell(row=1, column=index).column_letter
        assert sheet.column_dimensions[letter].width == 60

    def test_other_columns_are_measured_within_bounds(self, tmp_path: Path) -> None:
        """Header or longest value plus two, never below 10 nor above 40 characters."""
        rows = [{"isin": ISIN, "a": 1, "description": "x" * 200}]
        sheet = load_workbook(write(tmp_path, ("isin", "a", "description"), rows))[SUBJECTS_SHEET]
        assert sheet.column_dimensions["A"].width == len(ISIN) + 2
        assert sheet.column_dimensions["B"].width == 10
        assert sheet.column_dimensions["C"].width == 40

    def test_an_empty_list_is_an_empty_cell(self, tmp_path: Path) -> None:
        sheet = load_workbook(write(tmp_path, ("a",), [{"a": ()}]))[SUBJECTS_SHEET]
        assert sheet.cell(row=2, column=1).value is None


class TestHostileText:
    def test_illegal_control_characters_are_stripped(self, tmp_path: Path) -> None:
        """openpyxl refuses these outright; a scraped web text must not crash the export."""
        path = write(tmp_path, ("a",), [{"a": "pěstuje\x0bobilí"}])
        assert load_workbook(path)[SUBJECTS_SHEET].cell(row=2, column=1).value == "pěstujeobilí"

    def test_over_long_text_is_truncated_not_rejected(self, tmp_path: Path) -> None:
        path = write(tmp_path, ("a",), [{"a": "x" * (MAX_CELL_LENGTH + 500)}])
        value = load_workbook(path)[SUBJECTS_SHEET].cell(row=2, column=1).value

        assert len(value) <= MAX_CELL_LENGTH
        assert value.endswith("[…]")


class TestRunSheet:
    def pairs(self, tmp_path: Path, metadata: dict[str, object]) -> dict[object, object]:
        sheet = load_workbook(write(tmp_path, SUGGESTION_COLUMNS, [], run_metadata=metadata))[
            RUN_SHEET
        ]
        assert (sheet.cell(row=1, column=1).value, sheet.cell(row=1, column=2).value) == (
            "key",
            "value",
        )
        return {
            sheet.cell(row=r, column=1).value: sheet.cell(row=r, column=2).value
            for r in range(2, sheet.max_row + 1)
        }

    def test_metadata_is_written_as_key_value_pairs(self, tmp_path: Path) -> None:
        pairs = self.pairs(
            tmp_path,
            {
                "nástroj": "ESA a NACE našeptávač",
                "uživatel": "tester",
                "zdroje": ("GLEIF", "OPENFIGI", "WEB"),
                "verze číselníku": "cb-0123456789abcdef",
            },
        )
        assert pairs["nástroj"] == "ESA a NACE našeptávač"
        assert pairs["zdroje"] == "GLEIF, OPENFIGI, WEB"
        assert pairs["verze číselníku"] == "cb-0123456789abcdef"

    def test_an_empty_list_is_an_empty_value(self, tmp_path: Path) -> None:
        assert self.pairs(tmp_path, {"zdroje": ()})["zdroje"] is None

    def test_numbers_stay_numbers(self, tmp_path: Path) -> None:
        """A count on the Run sheet must stay a number Excel can sum, not become text."""
        pairs = self.pairs(tmp_path, {"počet řádků": 3, "podíl": 0.5})
        assert pairs["počet řádků"] == 3
        assert pairs["podíl"] == 0.5

    def test_datetimes_survive(self, tmp_path: Path) -> None:
        pairs = self.pairs(tmp_path, {"vytvořeno (UTC)": NOON_UTC_NAIVE})
        assert pairs["vytvořeno (UTC)"] == NOON_UTC_NAIVE

    def test_aware_datetimes_are_converted_to_utc(self, tmp_path: Path) -> None:
        assert self.pairs(tmp_path, {"vytvořeno (UTC)": PRAGUE_2PM})["vytvořeno (UTC)"] == (
            NOON_UTC_NAIVE
        )

    def test_control_characters_are_stripped_here_too(self, tmp_path: Path) -> None:
        assert self.pairs(tmp_path, {"uživatel": "test\x0ber"})["uživatel"] == "tester"


def test_parent_directories_are_created(tmp_path: Path) -> None:
    path = write_workbook(tmp_path / "new" / "dir" / "out.xlsx", ("a",), [{"a": 1}])
    assert path.is_file()


def test_a_real_suggestion_row_round_trips(tmp_path: Path) -> None:
    """The contract and the writer together: what the download of one lookup contains."""
    sheet = load_workbook(write(tmp_path, SUGGESTION_COLUMNS, [suggestion_row(suggestion())]))[
        SUBJECTS_SHEET
    ]
    values = {
        column: sheet.cell(row=2, column=index).value
        for index, column in enumerate(SUGGESTION_COLUMNS, start=1)
    }
    assert values["NACE_code"] == "01"
    assert values["NACE_cts_id"] == "0455"
    assert values["ESA_cts_id"] == "0613"
    assert values["issuer_lei"] == LEI
    assert values["retrieved_at"] == NOON_UTC_NAIVE
    assert values["NACE_candidates"].count("\n") == 2
