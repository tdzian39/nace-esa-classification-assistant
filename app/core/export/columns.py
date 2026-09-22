"""The output row contract: one row per subject, shared by the xlsx export and the CLIs.

Defined once here so the batch sheet, the single-lookup CLI and (from step 4) the API can
never drift apart. Column names carry the prefixes required by CLAUDE.md:

* ``IN_`` - echoed back from the input file, so a reviewer can line the result up with the
  sheet they submitted;
* ``RES_`` - the statistical register half;
* ``OR_`` - the commercial register half;
* ``CTS_`` - reserved for build step 5 (the deterministic RES -> CTS ID mapping). No CTS
  column is emitted yet: a column that is always empty reads like a bug, not like a promise.

Values are kept in their natural Python types here (``date``, ``bool``, tuples) so the xlsx
writer can produce real date and boolean cells; :func:`json_row` renders the same row for a
JSON consumer.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Final

from core.sources.base import NACE_REV_2, NACE_REV_21, SubjectRecord

#: Columns describing the subject, in sheet order. ``IN_*`` columns are prepended by the
#: batch runner because their number depends on the input file.
SUBJECT_COLUMNS: Final[tuple[str, ...]] = (
    "status",
    "ico",
    "source",
    "timestamp",
    "retrieved_at",
    "snapshot_at",
    "codebook_version",
    "nace_mismatch",
    "RES_name",
    "RES_nace_rev2_main",
    "RES_nace_rev2_other",
    "RES_nace_rev21_main",
    "RES_nace_rev21_other",
    "RES_esa_sector",
    "RES_founded_on",
    "RES_source",
    "OR_obchodni_firma",
    "OR_predmet_podnikani",
    "OR_predmet_cinnosti",
    "OR_datum_vzniku",
    "OR_datum_zapisu",
    "OR_spisova_znacka",
    "OR_source",
    "notes",
)

#: Columns Excel must keep as text. An IČO (``00177041``) or a NACE code (``01110``) loses
#: its leading zeros the moment Excel decides the cell is a number - which is exactly the
#: corruption this tool exists to clean up, so it must not reintroduce it on the way out.
TEXT_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "IN_identifier",
        "ico",
        "RES_nace_rev2_main",
        "RES_nace_rev2_other",
        "RES_nace_rev21_main",
        "RES_nace_rev21_other",
        "RES_esa_sector",
        "OR_spisova_znacka",
    }
)

#: Columns holding a list of long texts; the writer wraps these and widens the column.
WRAPPED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "OR_predmet_podnikani",
        "OR_predmet_cinnosti",
        "notes",
        "NACE_candidates",
        "ESA_candidates",
    }
)

#: Human-readable header shown in the sheet, when it differs from the column key.
HEADER_LABELS: Final[Mapping[str, str]] = {
    "ico": "IČO",
    # Excel cannot store a timezone, so these are written as naive UTC; say so in the header.
    "timestamp": "timestamp (UTC)",
    "retrieved_at": "retrieved_at (UTC)",
    "snapshot_at": "snapshot_at (UTC)",
    "nace_mismatch": "nace_mismatch (Rev.2 vs Rev.2.1)",
    "RES_nace_rev21_main": "RES_nace_rev21_main (CZ-NACE 2025)",
    "RES_nace_rev21_other": "RES_nace_rev21_other (CZ-NACE 2025)",
}

#: Separator for a list of codes inside one cell.
CODE_SEPARATOR: Final[str] = "; "

#: Separator for a list of long texts; a newline reads far better in a wrapped Excel cell.
TEXT_SEPARATOR: Final[str] = "\n"


def header_label(column: str) -> str:
    """The header text for ``column`` (the key itself unless an explicit label exists)."""
    if column in HEADER_LABELS:
        return HEADER_LABELS[column]
    return SUGGESTION_HEADER_LABELS.get(column, column)


def record_row(record: SubjectRecord | None, *, status: str | None = None) -> dict[str, object]:
    """Build the subject columns of one output row.

    ``record`` may be ``None`` (nothing was found): every subject column is then empty except
    ``status``, so the sheet still has one row per input line.
    """
    row: dict[str, object] = dict.fromkeys(SUBJECT_COLUMNS)
    row["status"] = status
    if record is None:
        row["notes"] = ()
        return row

    res = record.res
    or_record = record.or_record
    row.update(
        {
            "ico": record.ico,
            "source": record.source_label,
            "timestamp": record.timestamp,
            "retrieved_at": record.retrieved_at,
            "snapshot_at": record.snapshot_at,
            "codebook_version": record.codebook_version,
            "nace_mismatch": record.nace_mismatch,
            "notes": record.notes,
        }
    )
    if res is not None:
        main_2 = res.main(NACE_REV_2)
        main_21 = res.main(NACE_REV_21)
        row.update(
            {
                "RES_name": res.name,
                "RES_nace_rev2_main": main_2.code if main_2 else None,
                "RES_nace_rev2_other": tuple(item.code for item in res.others(NACE_REV_2)),
                "RES_nace_rev21_main": main_21.code if main_21 else None,
                "RES_nace_rev21_other": tuple(item.code for item in res.others(NACE_REV_21)),
                "RES_esa_sector": res.esa_sector,
                "RES_founded_on": res.founded_on,
                "RES_source": res.provenance.source,
            }
        )
    if or_record is not None:
        row.update(
            {
                "OR_obchodni_firma": or_record.obchodni_firma,
                "OR_predmet_podnikani": or_record.predmet_podnikani,
                "OR_predmet_cinnosti": or_record.predmet_cinnosti,
                "OR_datum_vzniku": or_record.datum_vzniku,
                "OR_datum_zapisu": or_record.datum_zapisu,
                "OR_spisova_znacka": or_record.spisova_znacka,
                "OR_source": or_record.provenance.source,
            }
        )
    return row


def json_value(value: object) -> object:
    """Render one cell for JSON: dates as ISO 8601, tuples as lists, everything else as is."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    return value


def json_row(row: Mapping[str, object]) -> dict[str, object]:
    """Render a whole row for JSON, preserving column order."""
    return {key: json_value(value) for key, value in row.items()}


def cell_value(column: str, value: object) -> object:
    """Render one cell for a spreadsheet.

    Lists are joined - codes with ``"; "``, long texts with a newline - and everything else
    is handed to openpyxl unchanged so dates stay dates and booleans stay booleans.
    """
    if isinstance(value, tuple):
        separator = TEXT_SEPARATOR if column in WRAPPED_COLUMNS else CODE_SEPARATOR
        return separator.join(str(item) for item in value) or None
    return value


# ---------------------------------------------------------------------------------------
# Tool 1: one row per foreign issuer. Separate from SUBJECT_COLUMNS because the two tools
# answer different questions - Tool 2 reports what a register says, Tool 1 reports what was
# suggested and how sure it is - and forcing them into one row shape would serve neither.
# ---------------------------------------------------------------------------------------

#: Columns of a suggestion row, in sheet order.
SUGGESTION_COLUMNS: Final[tuple[str, ...]] = (
    "IN_isin",
    "IN_name",
    "issuer_name",
    "issuer_lei",
    "issuer_country",
    "description",
    "NACE_code",
    "NACE_cts_id",
    "NACE_label",
    "NACE_confidence",
    "NACE_justification",
    "NACE_alt1",
    "NACE_alt2",
    "NACE_candidates",
    "ESA_code",
    "ESA_cts_id",
    "ESA_label",
    "ESA_confidence",
    "ESA_justification",
    "ESA_alt1",
    "ESA_alt2",
    "ESA_candidates",
    "source",
    "retrieved_at",
    "codebook_version",
    "model",
    "prompt_version",
    "evidence_urls",
    "notes",
)

#: Suggestion columns Excel must keep as text, for the same leading-zero reason.
SUGGESTION_TEXT_COLUMNS: Final[frozenset[str]] = frozenset(
    {
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
    }
)

SUGGESTION_HEADER_LABELS: Final[Mapping[str, str]] = {
    "issuer_lei": "issuer_lei (GLEIF)",
    "issuer_country": "issuer_country (sídlo podle GLEIF)",
    "NACE_cts_id": "NACE_cts_id (do CTS)",
    "ESA_cts_id": "ESA_cts_id (do CTS)",
    "retrieved_at": "retrieved_at (UTC)",
}


def _alternative(suggestions: tuple[object, ...], index: int) -> str | None:
    """``"66 (CTS 514) – label"`` for the n-th runner-up, or ``None``."""
    if index >= len(suggestions):
        return None
    item = suggestions[index]
    return f"{item.code} (CTS {item.cts_id}) – {item.label}"  # type: ignore[attr-defined]


def suggestion_row(suggestion: object) -> dict[str, object]:
    """Build the output row for one :class:`~core.suggest.IssuerSuggestion`.

    An abstention leaves the code columns empty and puts the reason in ``notes``: a reviewer
    must be able to see that the tool declined, not find a blank row and guess why.
    """
    request = suggestion.request  # type: ignore[attr-defined]
    row: dict[str, object] = dict.fromkeys(SUGGESTION_COLUMNS)
    row.update(
        {
            "IN_isin": request.isin,
            "IN_name": request.name,
            "issuer_name": suggestion.issuer_name,  # type: ignore[attr-defined]
            "issuer_lei": suggestion.identity.lei,  # type: ignore[attr-defined]
            "issuer_country": suggestion.identity.country,  # type: ignore[attr-defined]
            "description": suggestion.description,  # type: ignore[attr-defined]
            "source": suggestion.source_label,  # type: ignore[attr-defined]
            "retrieved_at": suggestion.created_at,  # type: ignore[attr-defined]
            "codebook_version": suggestion.codebook_version,  # type: ignore[attr-defined]
            "notes": tuple(suggestion.all_notes),  # type: ignore[attr-defined]
            "evidence_urls": tuple(
                source.url
                for source in suggestion.evidence_sources  # type: ignore[attr-defined]
            ),
        }
    )
    for prefix, classification, candidates in (
        ("NACE", suggestion.nace, suggestion.nace_candidates),  # type: ignore[attr-defined]
        ("ESA", suggestion.esa, suggestion.esa_candidates),  # type: ignore[attr-defined]
    ):
        # The shortlist goes in the sheet whether or not a code was chosen. When the tool
        # abstained it is the entire result, and even when it chose, it shows what else was
        # on the table - which is what a reviewer needs to overrule it.
        row[f"{prefix}_candidates"] = tuple(
            f"{item.code} (CTS {item.cts_id}) – {item.label}" for item in candidates
        )
        row[f"{prefix}_alt1"] = _alternative(classification.suggestions, 1)
        row[f"{prefix}_alt2"] = _alternative(classification.suggestions, 2)
        row["model"] = classification.model or row["model"]
        row["prompt_version"] = classification.prompt_version or row["prompt_version"]
        top = classification.top
        if top is None:
            continue
        row.update(
            {
                f"{prefix}_code": top.code,
                f"{prefix}_cts_id": top.cts_id,
                f"{prefix}_label": top.label,
                f"{prefix}_confidence": top.confidence,
                f"{prefix}_justification": top.justification,
            }
        )
    return row
