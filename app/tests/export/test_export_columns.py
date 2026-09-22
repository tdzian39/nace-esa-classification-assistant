"""The output row contract shared by the batch sheet and the single-lookup CLI."""

from __future__ import annotations

from datetime import date, datetime

from core.export.columns import (
    SUBJECT_COLUMNS,
    TEXT_COLUMNS,
    cell_value,
    header_label,
    json_row,
    record_row,
)
from tests.batch.conftest import record


class TestRecordRow:
    def test_every_declared_column_is_present(self) -> None:
        assert set(record_row(record())) == set(SUBJECT_COLUMNS)

    def test_column_order_is_stable(self) -> None:
        """The sheet's column order must not depend on which halves were found."""
        assert tuple(record_row(record())) == SUBJECT_COLUMNS
        assert tuple(record_row(None, status="not_found")) == SUBJECT_COLUMNS

    def test_no_cts_columns_are_emitted_yet(self) -> None:
        """CTS_ is reserved for step 5; an always-empty column would read like a bug."""
        assert not any(column.startswith("CTS_") for column in SUBJECT_COLUMNS)

    def test_attribution_fields_are_carried(self) -> None:
        row = record_row(record(), status="found")
        assert row["source"] == "ARES_LIVE"
        assert row["timestamp"] is not None
        assert row["codebook_version"] == "cb-0123456789abcdef"

    def test_both_revisions_are_split_into_main_and_other(self) -> None:
        row = record_row(record())
        assert row["RES_nace_rev2_main"] == "64190"
        assert row["RES_nace_rev2_other"] == ("66190",)
        assert row["RES_nace_rev21_main"] == "64190"

    def test_mismatch_is_reported(self) -> None:
        assert record_row(record(mismatch=True))["nace_mismatch"] is True
        assert record_row(record())["nace_mismatch"] is False

    def test_native_types_are_preserved_for_the_spreadsheet(self) -> None:
        row = record_row(record())
        assert isinstance(row["RES_founded_on"], date)
        assert isinstance(row["timestamp"], datetime)
        assert isinstance(row["OR_predmet_podnikani"], tuple)

    def test_a_missing_record_yields_an_empty_row_with_a_status(self) -> None:
        row = record_row(None, status="not_found")
        assert row["status"] == "not_found"
        assert row["ico"] is None
        assert row["RES_name"] is None


class TestRendering:
    def test_json_renders_dates_and_lists(self) -> None:
        row = json_row(record_row(record()))
        assert row["RES_founded_on"] == "1993-06-25"
        assert row["OR_predmet_podnikani"] == ["bankovní obchody", "pronájem nemovitostí"]

    def test_json_is_serializable(self) -> None:
        import json

        assert json.loads(json.dumps(json_row(record_row(record()))))["ico"]

    def test_codes_join_with_semicolons_and_prose_with_newlines(self) -> None:
        assert cell_value("RES_nace_rev2_other", ("64190", "66190")) == "64190; 66190"
        assert cell_value("OR_predmet_podnikani", ("a", "b")) == "a\nb"

    def test_scalars_pass_through_untouched(self) -> None:
        assert cell_value("nace_mismatch", True) is True
        assert cell_value("RES_founded_on", date(1993, 6, 25)) == date(1993, 6, 25)


def test_identifier_columns_are_declared_as_text() -> None:
    """Anything whose leading zeros matter must be in TEXT_COLUMNS."""
    for column in ("ico", "RES_nace_rev2_main", "RES_nace_rev21_main"):
        assert column in TEXT_COLUMNS


def test_header_labels_explain_the_less_obvious_columns() -> None:
    assert header_label("ico") == "IČO"
    assert "CZ-NACE 2025" in header_label("RES_nace_rev21_main")
    assert header_label("RES_name") == "RES_name"
