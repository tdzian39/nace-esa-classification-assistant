"""The Tool 1 row contract shared by the xlsx download and the JSON endpoint."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from core.classify.models import NACE, Candidate, CandidateSet
from core.export.columns import (
    SUGGESTION_COLUMNS,
    SUGGESTION_TEXT_COLUMNS,
    WRAPPED_COLUMNS,
    cell_value,
    header_label,
    json_row,
    json_value,
    suggestion_row,
)
from tests.export.conftest import (
    ABSTAIN_REASON,
    CODEBOOK_VERSION,
    CREATED,
    DESCRIPTION,
    ISIN,
    LEGAL_NAME,
    LEI,
    LEI_RECORD,
    MODEL,
    PROMPT_VERSION,
    WEB_URL,
    suggestion,
)


class TestSuggestionRow:
    @pytest.mark.parametrize(
        "kwargs",
        [{}, {"abstain": True}, {"isin": None, "name": "Nordkap"}],
        ids=["answered", "abstained", "name-only"],
    )
    def test_column_order_is_the_declared_contract(self, kwargs: dict[str, object]) -> None:
        """The sheet's column order must not depend on what the lookup found."""
        assert tuple(suggestion_row(suggestion(**kwargs))) == SUGGESTION_COLUMNS  # type: ignore[arg-type]

    def test_the_request_is_echoed_back(self) -> None:
        row = suggestion_row(suggestion(name="Nordkap"))
        assert row["IN_isin"] == ISIN
        assert row["IN_name"] == "Nordkap"

    def test_the_register_identifies_the_issuer(self) -> None:
        row = suggestion_row(suggestion())
        assert row["issuer_name"] == LEGAL_NAME
        assert row["issuer_lei"] == LEI
        assert row["issuer_country"] == "NL"
        assert row["description"] == DESCRIPTION

    def test_attribution_fields_are_carried(self) -> None:
        row = suggestion_row(suggestion())
        assert row["source"] == "GLEIF+WEB"
        assert row["retrieved_at"] == CREATED
        assert row["codebook_version"] == CODEBOOK_VERSION
        assert row["model"] == MODEL
        assert row["prompt_version"] == PROMPT_VERSION

    def test_the_top_pick_keeps_codes_and_cts_ids_verbatim(self) -> None:
        """A division ``01`` or a CTS ID ``0455`` must reach the row exactly as the codebook has it."""
        row = suggestion_row(suggestion())
        assert row["NACE_code"] == "01"
        assert row["NACE_cts_id"] == "0455"
        assert row["NACE_label"] == "Rostlinná a živočišná výroba, myslivost"
        assert row["NACE_confidence"] == "high"
        assert row["NACE_justification"] == "Zdůvodnění pro 01."
        assert row["ESA_code"] == "2001003"
        assert row["ESA_cts_id"] == "0613"

    def test_runners_up_name_their_cts_id(self) -> None:
        row = suggestion_row(suggestion())
        assert row["NACE_alt1"] == "10 (CTS 0464) – Výroba potravinářských výrobků"
        assert row["NACE_alt2"] == "46 (CTS 0500) – Velkoobchod, kromě motorových vozidel"
        assert row["ESA_alt1"] == "2001002 (CTS 0612) – Nefinanční podniky soukromé národní"

    def test_the_shortlist_is_in_the_row_even_when_a_code_was_chosen(self) -> None:
        """It shows the reviewer what else was on the table - what they need to overrule it."""
        row = suggestion_row(suggestion())
        assert (
            row["NACE_candidates"][0] == "01 (CTS 0455) – Rostlinná a živočišná výroba, myslivost"
        )
        assert len(row["NACE_candidates"]) == 3
        assert len(row["ESA_candidates"]) == 3

    def test_an_abstention_leaves_the_codes_empty_and_says_why(self) -> None:
        """A blank row with no reason would leave the reviewer guessing."""
        row = suggestion_row(suggestion(abstain=True))
        for column in ("NACE_code", "NACE_cts_id", "NACE_alt1", "ESA_code", "ESA_cts_id"):
            assert row[column] is None
        assert len(row["NACE_candidates"]) == 3  # the shortlist is then the whole result
        assert f"NACE: {ABSTAIN_REASON}" in row["notes"]
        assert f"ESA: {ABSTAIN_REASON}" in row["notes"]

    def test_a_rule_based_proposal_fills_the_codes_without_a_confidence(self) -> None:
        """The model abstained, but a rule put a code first: the row carries it, marked."""
        ruled = CandidateSet(
            kind=NACE,
            candidates=(
                Candidate(
                    NACE,
                    "01",
                    "0455",
                    "Rostlinná a živočišná výroba, myslivost",
                    score=10.2,
                    reasons=("keyword: agriculture", "text match 0.20"),
                ),
                Candidate(NACE, "10", "0464", "Výroba potravinářských výrobků", score=0.1),
            ),
            considered=87,
            filter_name="test",
        )
        row = suggestion_row(replace(suggestion(abstain=True), nace_candidates=ruled))
        assert row["NACE_code"] == "01"
        assert row["NACE_cts_id"] == "0455"
        assert row["NACE_confidence"] is None  # only a model has one
        assert row["NACE_justification"] == (
            "Podle pravidel, bez modelu: keyword: agriculture; text match 0.20."
        )
        assert row["NACE_alt1"] == "10 (CTS 0464) – Výroba potravinářských výrobků"
        assert row["NACE_alt2"] is None
        assert f"NACE: {ABSTAIN_REASON}" in row["notes"]  # why the rules had to decide
        assert row["ESA_code"] is None  # no rule stood behind that shortlist

    def test_a_name_lookup_leaves_the_register_columns_empty(self) -> None:
        row = suggestion_row(suggestion(isin=None, name="Nordkap"))
        assert row["IN_isin"] is None
        assert row["issuer_lei"] is None and row["issuer_country"] is None
        assert row["source"] == "WEB"

    def test_register_pages_are_cited_before_web_hits(self) -> None:
        assert suggestion_row(suggestion())["evidence_urls"] == (LEI_RECORD.url, WEB_URL)

    def test_register_notes_reach_the_row(self) -> None:
        assert "OpenFIGI tento ISIN nezná" in suggestion_row(suggestion())["notes"]

    def test_native_types_are_preserved_for_the_spreadsheet(self) -> None:
        row = suggestion_row(suggestion())
        assert isinstance(row["retrieved_at"], datetime)
        for column in ("NACE_candidates", "ESA_candidates", "evidence_urls", "notes"):
            assert isinstance(row[column], tuple)


class TestJson:
    def test_json_renders_datetimes_and_lists(self) -> None:
        row = json_row(suggestion_row(suggestion()))
        assert row["retrieved_at"] == "2026-09-22T12:00:00+00:00"
        assert row["evidence_urls"] == [LEI_RECORD.url, WEB_URL]
        assert isinstance(row["NACE_candidates"], list)

    def test_json_keeps_the_column_order(self) -> None:
        assert tuple(json_row(suggestion_row(suggestion()))) == SUGGESTION_COLUMNS

    def test_json_is_serializable_with_the_cts_id_intact(self) -> None:
        text = json.dumps(json_row(suggestion_row(suggestion())))
        assert json.loads(text)["NACE_cts_id"] == "0455"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (date(1993, 6, 25), "1993-06-25"),
            (datetime(2026, 9, 22, 12, 0, tzinfo=UTC), "2026-09-22T12:00:00+00:00"),
            (("a", ("b", date(2020, 1, 2))), ["a", ["b", "2020-01-02"]]),
            (None, None),
            (True, True),
            ("01", "01"),
        ],
        ids=["date", "datetime", "nested-tuple", "none", "bool", "text"],
    )
    def test_each_cell_type_renders_as_plain_json(self, value: object, expected: object) -> None:
        assert json_value(value) == expected


class TestCellValue:
    def test_codes_join_with_semicolons_and_prose_with_newlines(self) -> None:
        assert cell_value("evidence_urls", ("https://a.test", "https://b.test")) == (
            "https://a.test; https://b.test"
        )
        assert cell_value("NACE_candidates", ("a", "b")) == "a\nb"
        assert cell_value("notes", ("a", "b")) == "a\nb"

    def test_an_empty_list_is_an_empty_cell(self) -> None:
        assert cell_value("notes", ()) is None

    def test_scalars_pass_through_untouched(self) -> None:
        assert cell_value("anything", True) is True
        assert cell_value("anything", date(1993, 6, 25)) == date(1993, 6, 25)
        assert cell_value("NACE_code", "01") == "01"


class TestDeclarations:
    def test_code_and_identifier_columns_are_declared_as_text(self) -> None:
        """Anything whose leading zeros or exact spelling matter must be in the text set."""
        for column in (
            "IN_isin",
            "issuer_lei",
            "NACE_code",
            "NACE_cts_id",
            "NACE_alt1",
            "NACE_alt2",
            "ESA_code",
            "ESA_cts_id",
            "ESA_alt1",
            "ESA_alt2",
        ):
            assert column in SUGGESTION_TEXT_COLUMNS

    def test_every_formatting_rule_names_a_column_the_row_has(self) -> None:
        """A text or wrap rule for a column that no longer exists is dead configuration."""
        assert set(SUGGESTION_TEXT_COLUMNS) <= set(SUGGESTION_COLUMNS)
        assert set(WRAPPED_COLUMNS) <= set(SUGGESTION_COLUMNS)

    def test_header_labels_explain_the_less_obvious_columns(self) -> None:
        assert header_label("NACE_cts_id") == "NACE_cts_id (do CTS)"
        assert header_label("ESA_cts_id") == "ESA_cts_id (do CTS)"
        assert header_label("issuer_lei") == "issuer_lei (GLEIF)"
        assert "GLEIF" in header_label("issuer_country")
        assert header_label("retrieved_at") == "retrieved_at (UTC)"

    def test_header_label_falls_back_to_the_column_key(self) -> None:
        assert header_label("NACE_code") == "NACE_code"
        assert header_label("not_a_column") == "not_a_column"

    def test_header_labels_are_unique(self) -> None:
        labels = [header_label(column) for column in SUGGESTION_COLUMNS]
        assert len(set(labels)) == len(labels)
