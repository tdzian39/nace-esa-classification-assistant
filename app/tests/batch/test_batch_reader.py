"""Reading input sheets nobody cleaned up first."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.batch.reader import BatchInputError, read_batch_input
from tests.batch.conftest import BAD_CHECKSUM, RAIFFEISENBANK, SKODA, write_sheet


class TestHeaderDetection:
    def test_clean_sheet(self, clean_input: Path) -> None:
        batch = read_batch_input(clean_input)
        assert batch.header_row == 1
        assert batch.ico_column == "IČO"
        assert batch.name_column == "Název klienta"
        assert [row.identifier for row in batch.rows] == [RAIFFEISENBANK, SKODA]

    def test_title_rows_above_the_header_are_skipped(self, messy_input: Path) -> None:
        batch = read_batch_input(messy_input)
        assert batch.header_row == 2

    def test_header_spelling_is_matched_loosely(self, tmp_path: Path) -> None:
        """`ico`, `IČO`, `  Ico  ` and `NAZEV` are the same columns."""
        path = write_sheet(
            tmp_path / "spelling.xlsx", [("  Ico  ", "NAZEV"), (RAIFFEISENBANK, "Raiffeisenbank")]
        )
        batch = read_batch_input(path)
        assert batch.ico_column is not None and batch.name_column is not None

    def test_header_with_a_suffix_still_matches(self, tmp_path: Path) -> None:
        path = write_sheet(
            tmp_path / "suffix.xlsx",
            [("IČO klienta (RES)", "Obchodní firma"), (RAIFFEISENBANK, "Raiffeisenbank")],
        )
        batch = read_batch_input(path)
        assert batch.ico_column == "IČO klienta (RES)"
        assert batch.name_column == "Obchodní firma"

    def test_no_header_falls_back_to_the_first_cell(self, tmp_path: Path) -> None:
        path = write_sheet(tmp_path / "bare.xlsx", [(RAIFFEISENBANK,), (SKODA,)])
        batch = read_batch_input(path)

        assert batch.header_row is None
        assert [row.identifier for row in batch.rows] == [RAIFFEISENBANK, SKODA]
        assert any("no header row" in note for note in batch.notes)

    def test_duplicate_headers_are_disambiguated(self, tmp_path: Path) -> None:
        path = write_sheet(
            tmp_path / "dupes.xlsx", [("IČO", "Poznámka", "Poznámka"), (RAIFFEISENBANK, "a", "b")]
        )
        batch = read_batch_input(path)
        assert len(set(batch.columns)) == len(batch.columns)


class TestMessyValues:
    def test_lost_leading_zeros_are_restored(self, messy_input: Path) -> None:
        """Excel stores 00177041 as the number 177041; the row must still find Škoda Auto."""
        batch = read_batch_input(messy_input)
        assert batch.rows[0].identifier == "177041"  # passed on verbatim...
        from core.identifiers.ico import normalize_ico

        assert normalize_ico(batch.rows[0].identifier) == SKODA  # ...and normalises correctly

    def test_spaces_inside_an_ico_are_tolerated(self, messy_input: Path) -> None:
        batch = read_batch_input(messy_input)
        assert batch.rows[1].identifier.strip() == "49 240 901"

    def test_float_artefacts_do_not_survive(self, tmp_path: Path) -> None:
        """A cell typed as 177041.0 must not arrive as "177041.0"."""
        path = write_sheet(tmp_path / "float.xlsx", [("IČO",), (177041.0,)])
        batch = read_batch_input(path)
        assert batch.rows[0].identifier == "177041"

    def test_blank_rows_are_skipped_and_counted(self, messy_input: Path) -> None:
        batch = read_batch_input(messy_input)
        assert batch.skipped_rows == 1
        assert all(row.identifier for row in batch.rows)

    def test_row_numbers_are_the_ones_shown_in_excel(self, messy_input: Path) -> None:
        batch = read_batch_input(messy_input)
        assert [row.row_number for row in batch.rows] == [3, 4, 6]

    def test_input_columns_are_kept_for_echoing(self, messy_input: Path) -> None:
        batch = read_batch_input(messy_input)
        assert batch.rows[0].columns["Poznámka"] == "flag A"


class TestIdentifierChoice:
    def test_ico_column_wins_over_the_name(self, clean_input: Path) -> None:
        batch = read_batch_input(clean_input)
        assert batch.rows[0].identifier == RAIFFEISENBANK

    def test_empty_ico_falls_back_to_the_name(self, messy_input: Path) -> None:
        row = read_batch_input(messy_input).rows[2]
        assert row.identifier == "Neznámá firma s.r.o."
        assert row.note is not None and "looked up by name" in row.note

    def test_a_bad_ico_is_not_silently_replaced_by_the_name(self, tmp_path: Path) -> None:
        """The safety case: a wrong IČO must surface, not quietly return another company."""
        path = write_sheet(
            tmp_path / "bad.xlsx",
            [("IČO", "Název"), (BAD_CHECKSUM, "Raiffeisenbank a.s.")],
        )
        row = read_batch_input(path).rows[0]

        assert row.identifier == BAD_CHECKSUM
        assert row.identifier != "Raiffeisenbank a.s."
        assert row.note is not None and "not a valid IČO" in row.note

    def test_mixed_single_column_passes_each_cell_through(self, tmp_path: Path) -> None:
        """One column holding both kinds: classification is the resolver's job, not the reader's."""
        path = write_sheet(
            tmp_path / "mixed.xlsx",
            [("Identifikátor",), (RAIFFEISENBANK,), ("Škoda Auto a.s.",), (177041,)],
        )
        batch = read_batch_input(path)
        assert [row.identifier for row in batch.rows] == [
            RAIFFEISENBANK,
            "Škoda Auto a.s.",
            "177041",
        ]

    def test_unrecognised_header_is_kept_as_data_not_discarded(self, tmp_path: Path) -> None:
        """With no recognisable header, row 1 might be data. Junk surfaces; data is never lost."""
        path = write_sheet(
            tmp_path / "unknown.xlsx", [("Sloupec X", "Sloupec Y"), (RAIFFEISENBANK, "ignored")]
        )
        batch = read_batch_input(path)

        assert batch.header_row is None
        assert [row.identifier for row in batch.rows] == ["Sloupec X", RAIFFEISENBANK]


class TestSheetSelection:
    def test_named_sheet_is_read(self, tmp_path: Path) -> None:
        path = write_sheet(
            tmp_path / "multi.xlsx",
            [("IČO",), (RAIFFEISENBANK,)],
            title="Pokyny",
            extra={"Data": [("IČO",), (SKODA,)]},
        )
        batch = read_batch_input(path, sheet="Data")
        assert batch.sheet == "Data"
        assert batch.rows[0].identifier == SKODA

    def test_first_sheet_is_the_default(self, tmp_path: Path) -> None:
        path = write_sheet(
            tmp_path / "multi.xlsx",
            [("IČO",), (RAIFFEISENBANK,)],
            title="Prvni",
            extra={"Druhy": [("IČO",), (SKODA,)]},
        )
        assert read_batch_input(path).sheet == "Prvni"

    def test_a_missing_sheet_names_the_available_ones(self, clean_input: Path) -> None:
        with pytest.raises(BatchInputError, match="available"):
            read_batch_input(clean_input, sheet="Neexistuje")


class TestFailures:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(BatchInputError, match="not found"):
            read_batch_input(tmp_path / "nothing.xlsx")

    def test_not_a_workbook(self, tmp_path: Path) -> None:
        path = tmp_path / "notes.xlsx"
        path.write_text("this is not a workbook", encoding="utf-8")
        with pytest.raises(BatchInputError, match="xlsx"):
            read_batch_input(path)

    def test_empty_sheet(self, tmp_path: Path) -> None:
        with pytest.raises(BatchInputError, match="no data"):
            read_batch_input(write_sheet(tmp_path / "empty.xlsx", []))

    def test_header_only_sheet(self, tmp_path: Path) -> None:
        with pytest.raises(BatchInputError, match="no identifiers"):
            read_batch_input(write_sheet(tmp_path / "headeronly.xlsx", [("IČO", "Název")]))


def test_describe_names_the_detected_columns(messy_input: Path) -> None:
    description = read_batch_input(messy_input).describe()
    assert "3 row(s)" in description
    assert "header row 2" in description
    assert "1 blank row(s) skipped" in description
