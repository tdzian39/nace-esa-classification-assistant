"""Read a batch input sheet that nobody cleaned up first.

What arrives in practice is an export from the monthly OKEČ-vs-NACE check, or a list someone
assembled by hand. The reader therefore assumes almost nothing:

* the header may sit below a title row, or be missing entirely;
* the IČO and the name may be in separate columns, or mixed in one;
* IČOs arrive as numbers with their leading zeros eaten by Excel (``177041``), padded with
  apostrophes, or spaced as ``"00 177 041"``;
* rows are blank, duplicated, or carry a stray note in a trailing column.

What it deliberately does **not** do is repair an IČO that fails its check digit. When an
IČO column holds a value, that value is what gets looked up even if it is malformed - the
row then comes back ``invalid_input`` with the reason. Silently falling back to the name
column would return data for a *different* company than the one the sheet names, which is
the one failure mode a reviewer would not catch.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from core.codebooks.normalize import normalize_cell
from core.codebooks.xlsx import fold_header
from core.identifiers.ico import is_valid_ico

LOGGER = logging.getLogger(__name__)

#: How far down the sheet a header row may hide under title rows.
HEADER_SCAN_ROWS: Final[int] = 20

#: Header texts that mark the IČO column, compared through :func:`fold_header`.
ICO_ALIASES: Final[tuple[str, ...]] = (
    "ico",
    "ič",
    "ičo",
    "ic",
    "ico subjektu",
    "ičo subjektu",
    "ico klienta",
    "ičo klienta",
    "identifikator",
    "identifikátor",
    "identifikacni cislo",
    "identifikační číslo",
    "reg. c.",
    "company id",
)

#: Header texts that mark the name column.
NAME_ALIASES: Final[tuple[str, ...]] = (
    "nazev",
    "název",
    "nazev subjektu",
    "název subjektu",
    "nazev klienta",
    "název klienta",
    "obchodni firma",
    "obchodní firma",
    "obchodni jmeno",
    "obchodní jméno",
    "firma",
    "klient",
    "subjekt",
    "protistrana",
    "name",
    "company",
    "company name",
)

#: A header row must contain at least one of these to be recognised as a header at all.
_ALL_ALIASES: Final[frozenset[str]] = frozenset(
    fold_header(alias) for alias in (*ICO_ALIASES, *NAME_ALIASES)
)


class BatchInputError(Exception):
    """The input workbook cannot be read or contains no usable identifier."""


@dataclass(frozen=True, slots=True)
class InputRow:
    """One data row of the input sheet.

    Attributes:
        row_number: 1-based Excel row number, so a problem can be reported by the number the
            user sees on screen.
        identifier: The text handed to the resolver - an IČO or a name.
        columns: Every non-empty input cell, keyed by column name, for echoing back.
        note: Why this row's identifier was chosen, when that was not obvious.
    """

    row_number: int
    identifier: str
    columns: Mapping[str, str]
    note: str | None = None


@dataclass(frozen=True, slots=True)
class BatchInput:
    """Everything read from the input workbook."""

    path: Path
    sheet: str
    columns: tuple[str, ...]
    rows: tuple[InputRow, ...]
    header_row: int | None
    ico_column: str | None
    name_column: str | None
    skipped_rows: int = 0
    notes: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.rows)

    def describe(self) -> str:
        """One line for the log and the CLI."""
        detected = ", ".join(
            part
            for part in (
                f"IČO column {self.ico_column!r}" if self.ico_column else "",
                f"name column {self.name_column!r}" if self.name_column else "",
            )
            if part
        )
        header = f"header row {self.header_row}" if self.header_row else "no header row"
        return (
            f"{self.path.name} [{self.sheet}]: {len(self.rows)} row(s), {header}"
            + (f", {detected}" if detected else "")
            + (f", {self.skipped_rows} blank row(s) skipped" if self.skipped_rows else "")
        )


def _match_column(header: Sequence[str], aliases: Sequence[str]) -> int | None:
    """Index of the first header cell matching one of ``aliases`` (exact folded match first).

    A prefix match is accepted as a fallback, so ``"IČO klienta (RES)"`` still resolves.
    """
    folded = [fold_header(cell) for cell in header]
    wanted = [fold_header(alias) for alias in aliases]
    for alias in wanted:
        if alias in folded:
            return folded.index(alias)
    for index, cell in enumerate(folded):
        if cell and any(cell.startswith(alias) for alias in wanted):
            return index
    return None


def _looks_like_header(values: Sequence[str]) -> bool:
    """True when a row contains a recognised column name."""
    return any(fold_header(value) in _ALL_ALIASES for value in values if value)


def _read_rows(path: Path, sheet: str | None) -> tuple[str, list[tuple[int, list[str]]]]:
    """Return the worksheet title and its non-blank rows as ``(row_number, cells)``.

    Values pass through :func:`~core.codebooks.normalize.normalize_cell`, so the Excel float
    artefact ``177041.0`` arrives as ``"177041"`` rather than as a string with a fraction.
    """
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except FileNotFoundError as exc:
        raise BatchInputError(f"input file not found: {path}") from exc
    except Exception as exc:  # openpyxl raises a family of errors for a bad workbook
        raise BatchInputError(f"cannot read {path} as an xlsx workbook: {exc}") from exc

    try:
        if sheet is not None:
            if sheet not in workbook.sheetnames:
                raise BatchInputError(
                    f"sheet {sheet!r} not found in {path.name}; available: "
                    f"{', '.join(workbook.sheetnames)}"
                )
            worksheet = workbook[sheet]
        else:
            worksheet = workbook.worksheets[0]

        rows: list[tuple[int, list[str]]] = []
        for row_number, raw in enumerate(worksheet.iter_rows(values_only=True), start=1):
            cells = [normalize_cell(value) for value in raw]
            while cells and not cells[-1]:
                cells.pop()
            # Blank rows are kept so they can be counted and reported; only the trailing run
            # is dropped, because a sheet whose content was deleted can carry hundreds.
            rows.append((row_number, cells))
        while rows and not rows[-1][1]:
            rows.pop()
        return worksheet.title, rows
    finally:
        workbook.close()


def _column_names(header: Sequence[str] | None, width: int) -> list[str]:
    """Column names: the header texts where present, spreadsheet letters otherwise."""
    names: list[str] = []
    for index in range(width):
        text = header[index] if header is not None and index < len(header) else ""
        names.append(text or f"column {get_column_letter(index + 1)}")
    # Excel tolerates duplicate headers; the output mapping cannot.
    seen: dict[str, int] = {}
    unique: list[str] = []
    for name in names:
        if name in seen:
            seen[name] += 1
            unique.append(f"{name} ({seen[name]})")
        else:
            seen[name] = 1
            unique.append(name)
    return unique


def _pick_identifier(
    values: Mapping[str, str],
    *,
    ico_column: str | None,
    name_column: str | None,
    ordered: Sequence[str],
) -> tuple[str, str | None]:
    """Choose the text to resolve for one row, and a note when the choice needs explaining.

    Priority: a non-empty IČO column (even when malformed - see the module docstring), then
    the name column, then the first non-empty cell for a sheet with no recognised header.
    """
    if ico_column is not None:
        ico_value = values.get(ico_column, "")
        if ico_value:
            if is_valid_ico(ico_value):
                return ico_value, None
            note = f"{ico_column!r} is not a valid IČO; looked up as given, not by name"
            return ico_value, note
    if name_column is not None:
        name_value = values.get(name_column, "")
        if name_value:
            note = (
                f"{ico_column!r} was empty, looked up by name" if ico_column is not None else None
            )
            return name_value, note
    for column in ordered:
        value = values.get(column, "")
        if value:
            return value, None
    return "", None


def read_batch_input(path: Path, *, sheet: str | None = None) -> BatchInput:
    """Read ``path`` into a :class:`BatchInput`.

    Raises:
        BatchInputError: the file is missing, is not an xlsx workbook, names a sheet that
            does not exist, or holds no usable row.
    """
    sheet_title, raw_rows = _read_rows(path, sheet)
    if not any(cells for _, cells in raw_rows):
        raise BatchInputError(f"{path.name} [{sheet_title}] contains no data")

    header_index: int | None = None
    for position, (_, cells) in enumerate(raw_rows[:HEADER_SCAN_ROWS]):
        if _looks_like_header(cells):
            header_index = position
            break

    notes: list[str] = []
    if header_index is None:
        header_cells: list[str] | None = None
        header_row: int | None = None
        data = raw_rows
        # No header is recognised, so row 1 cannot be assumed to be one. It is kept as data:
        # a stray junk row comes back as invalid_input and is visible, whereas discarding a
        # row that turned out to hold a real company would be silent data loss.
        notes.append(
            "no header row recognised; every row is treated as data and the first non-empty "
            "cell is used as the identifier"
        )
    else:
        header_row, header_cells = raw_rows[header_index][0], raw_rows[header_index][1]
        data = raw_rows[header_index + 1 :]

    width = max((len(cells) for _, cells in raw_rows), default=0)
    columns = _column_names(header_cells, width)

    ico_index = _match_column(header_cells, ICO_ALIASES) if header_cells else None
    name_index = _match_column(header_cells, NAME_ALIASES) if header_cells else None
    ico_column = columns[ico_index] if ico_index is not None else None
    name_column = columns[name_index] if name_index is not None else None

    rows: list[InputRow] = []
    skipped = 0
    for row_number, cells in data:
        values = {
            columns[index]: cells[index]
            for index in range(min(len(cells), len(columns)))
            if cells[index]
        }
        if not values:
            skipped += 1
            continue
        identifier, note = _pick_identifier(
            values, ico_column=ico_column, name_column=name_column, ordered=columns
        )
        if not identifier:
            skipped += 1
            continue
        rows.append(
            InputRow(row_number=row_number, identifier=identifier, columns=values, note=note)
        )

    if not rows:
        raise BatchInputError(f"{path.name} [{sheet_title}] contains no identifiers")

    result = BatchInput(
        path=path,
        sheet=sheet_title,
        columns=tuple(columns),
        rows=tuple(rows),
        header_row=header_row,
        ico_column=ico_column,
        name_column=name_column,
        skipped_rows=skipped,
        notes=tuple(notes),
    )
    LOGGER.info("%s", result.describe())
    return result
