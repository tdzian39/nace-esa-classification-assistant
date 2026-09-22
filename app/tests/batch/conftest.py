"""Fixtures for the batch tests: builders for messy input workbooks and a scripted resolver.

The sheets built here reproduce what actually turns up: leading zeros eaten by Excel, title
rows above the header, accented and inconsistent header spellings, a single column holding
both names and IČOs, and blank rows in the middle.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from core.sources.base import (
    NACE_REV_2,
    NACE_REV_21,
    NaceAssignment,
    OrRecord,
    Provenance,
    ResRecord,
    SubjectCandidate,
    SubjectRecord,
)
from core.sources.resolver import LookupResult, LookupStatus, SubjectResolver

RETRIEVED = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
SNAPSHOT = datetime(2026, 8, 31, tzinfo=UTC)

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


def record(
    ico: str = RAIFFEISENBANK,
    *,
    source: str = "ARES_LIVE",
    name: str = "Raiffeisenbank a.s.",
    mismatch: bool = False,
) -> SubjectRecord:
    """A fully populated subject, optionally with differing NACE revisions."""
    provenance = Provenance(source=source, retrieved_at=RETRIEVED, snapshot_at=SNAPSHOT)
    return SubjectRecord(
        ico=ico,
        res=ResRecord(
            ico=ico,
            provenance=provenance,
            name=name,
            nace=(
                NaceAssignment("64190", NACE_REV_2, is_main=True),
                NaceAssignment("66190", NACE_REV_2),
                NaceAssignment("29200" if mismatch else "64190", NACE_REV_21, is_main=True),
            ),
            esa_sector="S.12203",
            founded_on=date(1993, 6, 25),
        ),
        or_record=OrRecord(
            ico=ico,
            provenance=provenance,
            obchodni_firma=name,
            predmet_podnikani=("bankovní obchody", "pronájem nemovitostí"),
            predmet_cinnosti=("správa vlastního majetku",),
            datum_zapisu=date(1993, 6, 25),
            spisova_znacka="B 2051/MSPH",
        ),
        codebook_version="cb-0123456789abcdef",
    )


class ScriptedSource:
    """A source answering from a ``{ico: SubjectRecord | None}`` script."""

    source = "ARES_LIVE"

    def __init__(
        self,
        by_ico: dict[str, SubjectRecord | None] | None = None,
        *,
        candidates: tuple[SubjectCandidate, ...] = (),
        default: SubjectRecord | None = None,
    ) -> None:
        self._by_ico = by_ico or {}
        self._candidates = candidates
        self._default = default
        self.ico_calls: list[str] = []
        self.name_calls: list[str] = []
        self.closed = False

    def fetch_by_ico(self, ico: str) -> SubjectRecord | None:
        self.ico_calls.append(ico)
        if ico in self._by_ico:
            return self._by_ico[ico]
        return self._default

    def search_by_name(self, name: str, *, limit: int = 10) -> tuple[SubjectCandidate, ...]:
        self.name_calls.append(name)
        return self._candidates

    def close(self) -> None:
        self.closed = True


def make_resolver(source: ScriptedSource) -> SubjectResolver:
    return SubjectResolver([source], codebook_version="cb-0123456789abcdef", user="tester")


def lookup(
    query: str, status: LookupStatus, *, subject: SubjectRecord | None = None, **kwargs: Any
) -> LookupResult:
    """Build a :class:`LookupResult` directly, for tests that do not need a resolver."""
    return LookupResult(query=query, status=status, resolved_at=RETRIEVED, record=subject, **kwargs)
