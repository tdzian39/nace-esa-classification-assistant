"""Loaders for the four xlsx codebooks and the startup entry point.

The xlsx files are the source today; another source can replace them later. The loaders
are therefore split in two layers: ``_read_rows`` fetches ``(row_number, row)`` pairs
from xlsx, and the ``_build_*`` functions turn such pairs into models. A loader for
another source only has to replace the first layer.

Rows with an empty ID / code cell are skipped (counted in ``skipped_rows`` and logged at
WARNING); rows whose code cannot be normalized are recorded as ``MalformedRow`` instead of
raising, so one bad row never hides the rest and the consistency check can report them all.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

from config.settings import Settings, get_settings
from core.codebooks.consistency import ConsistencyReport, check_consistency
from core.codebooks.errors import CodebookConsistencyError, MalformedCodeError
from core.codebooks.models import (
    CodebookFile,
    CodebookSet,
    CtsCodebook,
    CtsEntry,
    CtsKind,
    EsaSector,
    EsaValidList,
    MalformedRow,
    NaceDivision,
    NaceStat,
    NaceStatRow,
)
from core.codebooks.normalize import (
    format_esa_code,
    normalize_cts_id,
    normalize_esa_key,
    normalize_nace_division,
)
from core.codebooks.versioning import build_version, fingerprint_file
from core.codebooks.xlsx import read_table

logger = logging.getLogger(__name__)

NumberedRows = Iterable[tuple[int, Mapping[str, str]]]

CTS_COLUMNS: Mapping[str, Sequence[str]] = {
    # ".ID" is the header the real CTS export writes (verified 2026-09-22).
    "ID": ("ID", ".ID", "CTS_ID"),
    "VALUE": ("VALUE", "HODNOTA"),
    "DESCRIPTION": ("DESCRIPTION", "POPIS"),
}
VALID_ESA_COLUMNS: Mapping[str, Sequence[str]] = {
    "Kód": ("Kód", "Kod", "Code"),
    "Název": ("Název", "Nazev", "Name"),
    "Popis": ("Popis", "Description"),
}
NACE_STAT_COLUMNS: Mapping[str, Sequence[str]] = {
    "NACE": ("NACE", "Kód NACE", "NACE2"),
    "Zkrtext": ("Zkrtext", "Zkrácený text"),
    "Text": ("Text",),
}


def _read_rows(
    path: Path, name: str, columns: Mapping[str, Sequence[str]]
) -> tuple[list[tuple[int, dict[str, str]]], CodebookFile]:
    """xlsx layer: read the sheet and fingerprint the file."""
    table = read_table(path, columns)
    file = fingerprint_file(table.path, name, row_count=len(table.rows))
    logger.debug(
        "%s: read %d data rows from %s (sheet %r, header row %d, columns %s)",
        name,
        len(table.rows),
        path,
        table.sheet,
        table.header_row,
        table.columns,
    )
    return list(table.iter_numbered()), file


def _skip(file: CodebookFile, row_number: int, column: str) -> None:
    logger.warning("%s row %d skipped: empty %s cell", file.path.name, row_number, column)


def _build_cts(
    kind: CtsKind,
    rows: NumberedRows,
    file: CodebookFile,
    normalize_key: Callable[[object], str],
) -> CtsCodebook:
    entries: list[CtsEntry] = []
    malformed: list[MalformedRow] = []
    skipped = 0
    for row_number, row in rows:
        raw_id, raw_value = row.get("ID", ""), row.get("VALUE", "")
        if not raw_id or not raw_value:
            _skip(file, row_number, "ID" if not raw_id else "VALUE")
            skipped += 1
            continue
        try:
            entries.append(
                CtsEntry(
                    cts_id=normalize_cts_id(raw_id),
                    value=raw_value,
                    key=normalize_key(raw_value),
                    description=row.get("DESCRIPTION", ""),
                )
            )
        except MalformedCodeError as exc:
            malformed.append(MalformedRow(row_number, dict(row), str(exc)))
            logger.warning("%s row %d malformed: %s", file.path.name, row_number, exc)
    return CtsCodebook(
        kind=kind,
        entries=tuple(entries),
        file=file,
        malformed=tuple(malformed),
        skipped_rows=skipped,
    )


def _build_valid_list(rows: NumberedRows, file: CodebookFile) -> EsaValidList:
    sectors: list[EsaSector] = []
    malformed: list[MalformedRow] = []
    skipped = 0
    for row_number, row in rows:
        raw_code = row.get("Kód", "")
        if not raw_code:
            _skip(file, row_number, "Kód")
            skipped += 1
            continue
        try:
            key = normalize_esa_key(raw_code)
        except MalformedCodeError as exc:
            malformed.append(MalformedRow(row_number, dict(row), str(exc)))
            logger.warning("%s row %d malformed: %s", file.path.name, row_number, exc)
            continue
        sectors.append(
            EsaSector(
                code=format_esa_code(key),
                key=key,
                name=row.get("Název", ""),
                description=row.get("Popis", ""),
            )
        )
    return EsaValidList(
        sectors=tuple(sectors), file=file, malformed=tuple(malformed), skipped_rows=skipped
    )


def _build_nace_stat(rows: NumberedRows, file: CodebookFile) -> NaceStat:
    grouped: dict[str, list[NaceStatRow]] = {}
    malformed: list[MalformedRow] = []
    skipped = 0
    for row_number, row in rows:
        raw_code = row.get("NACE", "")
        if not raw_code:
            _skip(file, row_number, "NACE")
            skipped += 1
            continue
        try:
            code = normalize_nace_division(raw_code)
        except MalformedCodeError as exc:
            malformed.append(MalformedRow(row_number, dict(row), str(exc)))
            logger.warning("%s row %d malformed: %s", file.path.name, row_number, exc)
            continue
        grouped.setdefault(code, []).append(
            NaceStatRow(code=code, short_text=row.get("Zkrtext", ""), text=row.get("Text", ""))
        )
    divisions = {code: NaceDivision(code, tuple(items)) for code, items in grouped.items()}
    return NaceStat(
        divisions=divisions, file=file, malformed=tuple(malformed), skipped_rows=skipped
    )


def load_cts_ba0036(path: Path) -> CtsCodebook:
    """Load CTS_BA0036 (ID, VALUE = ESA code, DESCRIPTION)."""
    rows, file = _read_rows(path, "cts_ba0036", CTS_COLUMNS)
    return _build_cts("BA0036", rows, file, normalize_esa_key)


def load_ba0036_valid(path: Path) -> EsaValidList:
    """Load BA0036_2024_jen_validni (Kód, Název, Popis): the emittable ESA leaves."""
    rows, file = _read_rows(path, "ba0036_valid", VALID_ESA_COLUMNS)
    return _build_valid_list(rows, file)


def load_cts_okec_nace2(path: Path) -> CtsCodebook:
    """Load CTS_OKEC_NACE2 (ID, VALUE = 2-digit NACE division, DESCRIPTION)."""
    rows, file = _read_rows(path, "cts_okec_nace2", CTS_COLUMNS)
    return _build_cts("OKEC_NACE2", rows, file, normalize_nace_division)


def load_nace_stat(path: Path) -> NaceStat:
    """Load NACE_STAT (NACE, Zkrtext, Text), grouping the rows of each division in file order."""
    rows, file = _read_rows(path, "nace_stat", NACE_STAT_COLUMNS)
    return _build_nace_stat(rows, file)


def load_codebooks(
    settings: Settings | None = None, *, codebook_dir: Path | None = None
) -> CodebookSet:
    """Load all four codebooks and compute their version (no consistency check).

    File names and the version label come from ``settings`` (default: ``get_settings()``);
    ``codebook_dir`` overrides ``settings.codebook_dir``.
    """
    settings = settings if settings is not None else get_settings()
    directory = Path(codebook_dir) if codebook_dir is not None else settings.codebook_dir
    logger.info("loading codebooks from %s", directory)
    cts_ba0036 = load_cts_ba0036(directory / settings.codebook_cts_ba0036_file)
    ba0036_valid = load_ba0036_valid(directory / settings.codebook_ba0036_valid_file)
    cts_okec_nace2 = load_cts_okec_nace2(directory / settings.codebook_cts_okec_nace2_file)
    nace_stat = load_nace_stat(directory / settings.codebook_nace_stat_file)
    version = build_version(
        (cts_ba0036.file, ba0036_valid.file, cts_okec_nace2.file, nace_stat.file),
        label=settings.codebook_version_label,
    )
    return CodebookSet(
        cts_ba0036=cts_ba0036,
        ba0036_valid=ba0036_valid,
        cts_okec_nace2=cts_okec_nace2,
        nace_stat=nace_stat,
        version=version,
    )


def load_and_check(
    settings: Settings | None = None,
    *,
    codebook_dir: Path | None = None,
    strict: bool = True,
) -> tuple[CodebookSet, ConsistencyReport]:
    """The startup entry point: load, check, log the report and enforce it.

    Errors are logged at ERROR, warnings at WARNING, infos at INFO. When ``strict`` is true
    and the report has errors a :class:`CodebookConsistencyError` (with ``.report`` and
    ``.codebooks``) is raised; otherwise both the codebooks and the report are returned.
    """
    codebooks = load_codebooks(settings, codebook_dir=codebook_dir)
    report = check_consistency(codebooks)
    for finding in report.errors:
        logger.error("%s: %s", finding.code, finding.message)
    for finding in report.warnings:
        logger.warning("%s: %s", finding.code, finding.message)
    for finding in report.infos:
        logger.info("%s: %s", finding.code, finding.message)
    logger.info(codebooks.describe())
    if strict and not report.ok:
        raise CodebookConsistencyError(report, codebooks=codebooks)
    return codebooks, report
