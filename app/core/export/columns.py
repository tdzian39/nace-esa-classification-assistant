"""The Tool 1 row contract: one row per foreign issuer, for the xlsx download and the JSON API.

Defined once here so the xlsx download (``GET /suggest.xlsx``) and the ``row`` object of
``POST /api/suggest`` can never drift apart. Neither builds its own row - keep it that way.
Columns of a row, in sheet order (:data:`SUGGESTION_COLUMNS`):

* ``IN_*`` - echoed back from the request (ISIN, name), so a reviewer can line the result up
  with what was asked;
* ``issuer_*`` and ``description`` - who the issuer is: the name shown, the LEI and country
  from GLEIF, and the activity description the classifier read;
* ``NACE_*`` / ``ESA_*`` - per codebook: the top pick with its CTS ID, label, confidence and
  justification, two alternatives, and the whole shortlist (``*_candidates``), which is the
  entire result when the classifier abstained;
* ``source``, ``retrieved_at``, ``codebook_version``, ``model``, ``prompt_version``,
  ``evidence_urls``, ``notes`` - what every output row must carry to be attributable.

Values are kept in their natural Python types here (``datetime``, tuples) so the xlsx writer
can produce real date cells; :func:`json_row` renders the same row for a JSON consumer.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Final

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

#: Columns Excel must keep as text. A NACE division such as ``01``, or a CTS ID stored with
#: leading zeros, loses them the moment Excel decides the cell is a number - and a code
#: that MO copies into CTS must be exactly what the codebook says.
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

#: Columns holding a list of long texts; the writer wraps these and widens the column.
WRAPPED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "notes",
        "NACE_candidates",
        "ESA_candidates",
    }
)

#: Human-readable header shown in the sheet, when it differs from the column key.
SUGGESTION_HEADER_LABELS: Final[Mapping[str, str]] = {
    "issuer_lei": "issuer_lei (GLEIF)",
    "issuer_country": "issuer_country (sídlo podle GLEIF)",
    "NACE_cts_id": "NACE_cts_id (do CTS)",
    "ESA_cts_id": "ESA_cts_id (do CTS)",
    # Excel cannot store a timezone, so this is written as naive UTC; say so in the header.
    "retrieved_at": "retrieved_at (UTC)",
}

#: Separator for a list of codes inside one cell.
CODE_SEPARATOR: Final[str] = "; "

#: Separator for a list of long texts; a newline reads far better in a wrapped Excel cell.
TEXT_SEPARATOR: Final[str] = "\n"


def header_label(column: str) -> str:
    """The header text for ``column`` (the key itself unless an explicit label exists)."""
    return SUGGESTION_HEADER_LABELS.get(column, column)


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
