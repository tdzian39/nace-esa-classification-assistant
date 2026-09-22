"""Shared fixtures for the codebook tests: synthetic xlsx codebooks with realistic content.

The real bank files are not available in the repository, so every test builds its own
workbook(s) with :func:`write_xlsx` / :func:`make_codebook_dir`. The defaults describe a small
but faithful slice of the real codebooks (Czech ESA 2010 leaf names, parent codes in CTS,
several NACE_STAT rows per division).
"""

from __future__ import annotations

import copy
import io
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from openpyxl import Workbook

from config.settings import Settings
from core.codebooks.loaders import load_codebooks
from core.codebooks.models import CodebookSet

Row = Sequence[object]

# Default file names (identical to the ``Settings`` defaults; fixed here so a developer's
# ``app/.env`` can never change what the tests write).
FILE_NAMES: dict[str, str] = {
    "cts_ba0036": "CTS_BA0036_NEW.xlsx",
    "ba0036_valid": "BA0036_2024_jen_validni.xlsx",
    "cts_okec_nace2": "CTS_OKEC_NACE2.xlsx",
    "nace_stat": "NACE_STAT.xlsx",
}

HEADERS: dict[str, tuple[str, ...]] = {
    "cts_ba0036": ("ID", "VALUE", "DESCRIPTION"),
    "ba0036_valid": ("Kód", "Název", "Popis"),
    "cts_okec_nace2": ("ID", "VALUE", "DESCRIPTION"),
    "nace_stat": ("NACE", "Zkrtext", "Text"),
}

# (Kód, Název, Popis) - the emittable ESA 2010 leaves.
VALID_ESA_ROWS: list[Row] = [
    ("S.11001", "Veřejné nefinanční podniky", "Nefinanční podniky pod veřejnou kontrolou"),
    (
        "S.11002",
        "Národní soukromé nefinanční podniky",
        "Nefinanční podniky pod národní soukromou kontrolou",
    ),
    (
        "S.11003",
        "Nefinanční podniky pod zahraniční kontrolou",
        "Nefinanční podniky kontrolované nerezidenty",
    ),
    ("S.121", "Centrální banka", "Česká národní banka"),
    (
        "S.12201",
        "Instituce přijímající vklady kromě centrální banky - veřejné",
        "Banky pod veřejnou kontrolou",
    ),
    (
        "S.12202",
        "Instituce přijímající vklady kromě centrální banky - národní soukromé",
        "Banky pod národní soukromou kontrolou",
    ),
    (
        "S.12203",
        "Instituce přijímající vklady kromě centrální banky - pod zahraniční kontrolou",
        "Banky kontrolované nerezidenty",
    ),
    ("S.1311", "Ústřední vládní instituce", "Ministerstva a ústřední orgány"),
    ("S.1313", "Místní vládní instituce", "Obce a kraje"),
    ("S.1314", "Fondy sociálního zabezpečení", "Zdravotní pojišťovny"),
    ("S.14", "Domácnosti", "Fyzické osoby a skupiny fyzických osob"),
    ("S.15", "Neziskové instituce sloužící domácnostem", "Spolky, nadace, církve"),
    ("S.2111", "Členské státy EU", "Nerezidenti z členských států Evropské unie"),
]

# (ID, VALUE, DESCRIPTION) - CTS knows every leaf PLUS parent codes that must never be emitted.
PARENT_ESA_CODES: tuple[str, ...] = ("S.11", "S.12", "S.122", "S.13", "S.2")
CTS_BA0036_ROWS: list[Row] = [
    (1001, "S.11001", "Veřejné nefinanční podniky"),
    (1002, "S.11002", "Národní soukromé nefinanční podniky"),
    (1003, "S.11003", "Nefinanční podniky pod zahraniční kontrolou"),
    (1004, "S.121", "Centrální banka"),
    (1005, "S.12201", "Instituce přijímající vklady - veřejné"),
    (1006, "S.12202", "Instituce přijímající vklady - národní soukromé"),
    (1007, "S.12203", "Instituce přijímající vklady - pod zahraniční kontrolou"),
    (1008, "S.1311", "Ústřední vládní instituce"),
    (1009, "S.1313", "Místní vládní instituce"),
    (1010, "S.1314", "Fondy sociálního zabezpečení"),
    (1011, "S.14", "Domácnosti"),
    (1012, "S.15", "Neziskové instituce sloužící domácnostem"),
    (1013, "S.2111", "Členské státy EU"),
    (1101, "S.11", "Nefinanční podniky"),
    (1102, "S.12", "Finanční instituce"),
    (1103, "S.122", "Instituce přijímající vklady kromě centrální banky"),
    (1104, "S.13", "Vládní instituce"),
    (1105, "S.2", "Nerezidenti"),
]

# (NACE, Zkrtext, Text) - several rows per 2-digit division, as in the real file.
NACE_STAT_ROWS: list[Row] = [
    ("01", "Rostlinná a živočišná výroba, myslivost", "Pěstování plodin jiných než trvalých"),
    ("01", "Rostlinná a živočišná výroba, myslivost", "Živočišná výroba"),
    ("10", "Výroba potravinářských výrobků", "Zpracování a konzervování masa"),
    ("10", "Výroba potravinářských výrobků", "Výroba pekařských výrobků"),
    ("41", "Výstavba budov", "Developerská činnost"),
    ("41", "Výstavba budov", "Výstavba bytových a nebytových budov"),
    ("62", "Programování a poradenství", "Činnosti v oblasti informačních technologií"),
    ("62", "Programování a poradenství", "Poradenství v oblasti informačních technologií"),
    ("64", "Finanční zprostředkování", "Peněžní zprostředkování"),
    ("64", "Finanční zprostředkování", "Činnosti holdingových společností"),
    ("64", "Finanční zprostředkování", "Ostatní finanční zprostředkování"),
    (
        "66",
        "Ostatní finanční činnosti",
        "Pomocné činnosti související s finančním zprostředkováním",
    ),
    ("66", "Ostatní finanční činnosti", "Správa fondů"),
    ("84", "Veřejná správa a obrana", "Veřejná správa a hospodářská a sociální politika"),
    ("84", "Veřejná správa a obrana", "Činnosti pro společnost jako celek"),
]
NACE_DIVISIONS: tuple[str, ...] = ("01", "10", "41", "62", "64", "66", "84")

# (ID, VALUE, DESCRIPTION)
CTS_OKEC_NACE2_ROWS: list[Row] = [
    (2001, "01", "Rostlinná a živočišná výroba, myslivost a související činnosti"),
    (2002, "10", "Výroba potravinářských výrobků"),
    (2003, "41", "Výstavba budov"),
    (2004, "62", "Činnosti v oblasti informačních technologií"),
    (2005, "64", "Finanční zprostředkování, kromě pojišťovnictví a penzijního financování"),
    (2006, "66", "Ostatní finanční činnosti"),
    (2007, "84", "Veřejná správa a obrana; povinné sociální zabezpečení"),
]


@dataclass
class CodebookRows:
    """Mutable copy of the default rows so a test can tweak one table."""

    cts_ba0036: list[Row] = field(default_factory=lambda: copy.deepcopy(CTS_BA0036_ROWS))
    ba0036_valid: list[Row] = field(default_factory=lambda: copy.deepcopy(VALID_ESA_ROWS))
    cts_okec_nace2: list[Row] = field(default_factory=lambda: copy.deepcopy(CTS_OKEC_NACE2_ROWS))
    nace_stat: list[Row] = field(default_factory=lambda: copy.deepcopy(NACE_STAT_ROWS))
    headers: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(HEADERS))
    file_names: dict[str, str] = field(default_factory=lambda: dict(FILE_NAMES))
    parent_codes: tuple[str, ...] = PARENT_ESA_CODES
    nace_divisions: tuple[str, ...] = NACE_DIVISIONS


def write_xlsx(
    path: Path,
    headers: Sequence[object],
    rows: Sequence[Row],
    *,
    sheet_title: str = "Sheet1",
    title_rows: Sequence[Row] = (),
) -> Path:
    """Write ``title_rows``, then ``headers``, then ``rows`` into a single-sheet workbook."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title
    for title in title_rows:
        sheet.append(list(title))
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def make_codebook_dir(
    directory: Path,
    *,
    cts_ba0036: Sequence[Row] | None = None,
    ba0036_valid: Sequence[Row] | None = None,
    cts_okec_nace2: Sequence[Row] | None = None,
    nace_stat: Sequence[Row] | None = None,
) -> Path:
    """Write all four codebooks (defaults unless overridden) under the default names."""
    tables = {
        "cts_ba0036": CTS_BA0036_ROWS if cts_ba0036 is None else cts_ba0036,
        "ba0036_valid": VALID_ESA_ROWS if ba0036_valid is None else ba0036_valid,
        "cts_okec_nace2": CTS_OKEC_NACE2_ROWS if cts_okec_nace2 is None else cts_okec_nace2,
        "nace_stat": NACE_STAT_ROWS if nace_stat is None else nace_stat,
    }
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        write_xlsx(directory / FILE_NAMES[name], HEADERS[name], rows)
    return directory


def make_settings(directory: Path, *, label: str | None = None) -> Settings:
    """Settings pointing at ``directory`` with the default file names; env and .env ignored."""
    return Settings(
        _env_file=None,
        codebook_dir=directory,
        codebook_cts_ba0036_file=FILE_NAMES["cts_ba0036"],
        codebook_ba0036_valid_file=FILE_NAMES["ba0036_valid"],
        codebook_cts_okec_nace2_file=FILE_NAMES["cts_okec_nace2"],
        codebook_nace_stat_file=FILE_NAMES["nace_stat"],
        codebook_version_label=label,
    )


MakeCodebookDir = Callable[..., Path]


@pytest.fixture
def defaults() -> CodebookRows:
    """Fresh, mutable copy of the default codebook rows."""
    return CodebookRows()


@pytest.fixture(name="make_codebook_dir")
def make_codebook_dir_fixture() -> MakeCodebookDir:
    """Factory: ``make_codebook_dir(directory, *, cts_ba0036=..., ...) -> Path``."""
    return make_codebook_dir


@pytest.fixture(name="make_settings")
def make_settings_fixture() -> Callable[..., Settings]:
    """Factory: ``make_settings(directory, *, label=None) -> Settings``."""
    return make_settings


@pytest.fixture
def codebook_dir(tmp_path: Path) -> Path:
    """A directory with the four default codebooks."""
    return make_codebook_dir(tmp_path / "codebooks")


@pytest.fixture
def codebooks(codebook_dir: Path) -> CodebookSet:
    """The default codebooks, loaded."""
    return load_codebooks(make_settings(codebook_dir))


def corrupt_sheet_xml(path: Path, *, truncate: bool) -> None:
    """Keep the zip container valid but truncate (or garble) the first worksheet's XML."""
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(path) as source,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data[: len(data) // 2] if truncate else data + b"<<garbage"
            target.writestr(item, data)
    path.write_bytes(buffer.getvalue())
