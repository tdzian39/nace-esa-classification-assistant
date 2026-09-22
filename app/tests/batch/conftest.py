"""Fixtures for the batch reader tests: builders for messy input workbooks.

The sheets built here reproduce what actually turns up: leading zeros eaten by Excel, title
rows above the header, accented and inconsistent header spellings, a single column holding
both names and IČOs, and blank rows in the middle.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

#: Real IČOs with correct check digits, so the reader's validity test is exercised honestly.
RAIFFEISENBANK = "49240901"
SKODA = "00177041"
#: Correct length, wrong check digit.
BAD_CHECKSUM = "11111111"


def write_sheet(
    path: Path,
    rows: Sequence[Sequence[Any]],
    *,
    title: str = "List1",
    extra: dict[str, Sequence[Sequence[Any]]] | None = None,
) -> Path:
    """Write ``rows`` verbatim to ``path``; values keep their Python type (int stays int)."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in rows:
        sheet.append(list(row))
    for name, other_rows in (extra or {}).items():
        other = workbook.create_sheet(name)
        for row in other_rows:
            other.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


@pytest.fixture
def clean_input(tmp_path: Path) -> Path:
    """A tidy two-column sheet: header, IČO as text, name."""
    return write_sheet(
        tmp_path / "clean.xlsx",
        [
            ("IČO", "Název klienta"),
            (RAIFFEISENBANK, "Raiffeisenbank a.s."),
            (SKODA, "Škoda Auto a.s."),
        ],
    )


@pytest.fixture
def messy_input(tmp_path: Path) -> Path:
    """Everything that goes wrong at once, in one sheet.

    Row 1 is a title, row 2 the header, then: an IČO Excel turned into a number (leading
    zeros gone), an IČO spaced out by hand, a blank row, and a row with only a name.
    """
    return write_sheet(
        tmp_path / "messy.xlsx",
        [
            ("Kontrola OKEČ vs NACE – září 2026", None, None),
            ("  ico  ", "Nazev", "Poznámka"),
            (177041, "Škoda Auto a.s.", "flag A"),
            ("  49 240 901 ", "Raiffeisenbank a.s.", None),
            (None, None, None),
            (None, "Neznámá firma s.r.o.", "no ičo"),
        ],
    )
