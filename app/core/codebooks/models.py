"""Immutable in-memory representation of the four codebooks and their version.

Everything here is a frozen dataclass: the codebooks are loaded once at startup, checked, and
then shared read-only across requests. Derived indexes (``by_id``, ``by_key`` ...) are built
in ``__post_init__`` so a codebook is fully usable the moment it exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from core.codebooks.errors import (
    InvalidEsaCodeError,
    MalformedCodeError,
    UnknownEsaCodeError,
    UnknownNaceCodeError,
)
from core.codebooks.normalize import format_esa_code, nace_to_division, normalize_esa_key

CtsKind = Literal["BA0036", "OKEC_NACE2"]


@dataclass(frozen=True, slots=True)
class CodebookFile:
    """Provenance of one loaded codebook file.

    Attributes:
        name: Logical name, one of ``cts_ba0036``, ``ba0036_valid``, ``cts_okec_nace2``,
            ``nace_stat``.
        path: Where the file was read from.
        sha256: Hex digest of the file content (the version is derived from it).
        size_bytes: File size.
        modified_at: File modification time as a timezone-aware UTC datetime.
        row_count: Number of non-blank data rows read from the sheet (before any skipping
            or malformed-row handling by the loader).
    """

    name: str
    path: Path
    sha256: str
    size_bytes: int
    modified_at: datetime
    row_count: int


@dataclass(frozen=True, slots=True)
class CodebookVersion:
    """Content-derived version of a whole codebook set.

    ``id`` depends only on the file contents (and the optional label), never on paths or
    modification times, so two machines holding the same files report the same version.
    """

    id: str
    label: str | None
    loaded_at: datetime
    files: tuple[CodebookFile, ...]


@dataclass(frozen=True, slots=True)
class MalformedRow:
    """A data row whose code could not be normalized; recorded, never silently dropped."""

    row_number: int
    raw: dict[str, str]
    reason: str


@dataclass(frozen=True, slots=True)
class CtsEntry:
    """One row of a CTS codebook.

    Attributes:
        cts_id: The opaque CTS identifier, exactly as written in the file.
        value: The code text as in the file (stripped), e.g. ``"S.11001"`` or ``"62"``.
        key: Canonical lookup key: the ESA key (``"11001"``) or the 2-digit division.
        description: The DESCRIPTION column (may be empty).
    """

    cts_id: str
    value: str
    key: str
    description: str


@dataclass(frozen=True, slots=True)
class CtsCodebook:
    """A CTS codebook (BA0036 = ESA sectors, OKEC_NACE2 = NACE divisions) with its indexes.

    ``by_id`` and ``by_key`` keep the FIRST occurrence; every later duplicate is recorded in
    ``duplicate_ids`` / ``duplicate_keys`` (each value once) so the consistency check can
    report it. Nothing is dropped silently.
    """

    kind: CtsKind
    entries: tuple[CtsEntry, ...]
    file: CodebookFile
    malformed: tuple[MalformedRow, ...] = ()
    skipped_rows: int = 0
    by_id: dict[str, CtsEntry] = field(init=False, repr=False, compare=False)
    by_key: dict[str, CtsEntry] = field(init=False, repr=False, compare=False)
    duplicate_ids: tuple[str, ...] = field(init=False, repr=False, compare=False)
    duplicate_keys: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_id: dict[str, CtsEntry] = {}
        by_key: dict[str, CtsEntry] = {}
        duplicate_ids: list[str] = []
        duplicate_keys: list[str] = []
        for entry in self.entries:
            if entry.cts_id in by_id:
                if entry.cts_id not in duplicate_ids:
                    duplicate_ids.append(entry.cts_id)
            else:
                by_id[entry.cts_id] = entry
            if entry.key in by_key:
                if entry.key not in duplicate_keys:
                    duplicate_keys.append(entry.key)
            else:
                by_key[entry.key] = entry
        object.__setattr__(self, "by_id", by_id)
        object.__setattr__(self, "by_key", by_key)
        object.__setattr__(self, "duplicate_ids", tuple(duplicate_ids))
        object.__setattr__(self, "duplicate_keys", tuple(duplicate_keys))

    def __len__(self) -> int:
        return len(self.entries)


@dataclass(frozen=True, slots=True)
class EsaSector:
    """One valid ESA 2010 leaf sector from BA0036_2024_jen_validni.

    Attributes:
        code: Canonical display form, e.g. ``"S.11001"``.
        key: Canonical lookup key, e.g. ``"11001"``.
        name: The ``Název`` column.
        description: The ``Popis`` column.
    """

    code: str
    key: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class EsaValidList:
    """The list of ESA leaf codes that may be emitted; ``by_key`` keeps the first occurrence."""

    sectors: tuple[EsaSector, ...]
    file: CodebookFile
    malformed: tuple[MalformedRow, ...] = ()
    skipped_rows: int = 0
    by_key: dict[str, EsaSector] = field(init=False, repr=False, compare=False)
    duplicate_keys: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_key: dict[str, EsaSector] = {}
        duplicates: list[str] = []
        for sector in self.sectors:
            if sector.key in by_key:
                if sector.key not in duplicates:
                    duplicates.append(sector.key)
            else:
                by_key[sector.key] = sector
        object.__setattr__(self, "by_key", by_key)
        object.__setattr__(self, "duplicate_keys", tuple(duplicates))

    def __len__(self) -> int:
        return len(self.sectors)


@dataclass(frozen=True, slots=True)
class NaceStatRow:
    """One row of NACE_STAT: a 2-digit code with its short and long label."""

    code: str
    short_text: str
    text: str


@dataclass(frozen=True, slots=True)
class NaceDivision:
    """All NACE_STAT rows of one 2-digit division, in file order."""

    code: str
    rows: tuple[NaceStatRow, ...]

    @property
    def short_text(self) -> str:
        """The first non-empty ``Zkrtext`` (``""`` when none)."""
        return next((row.short_text for row in self.rows if row.short_text), "")

    @property
    def texts(self) -> tuple[str, ...]:
        """Distinct non-empty ``Text`` values in file order."""
        return tuple(dict.fromkeys(row.text for row in self.rows if row.text))

    @property
    def labels(self) -> tuple[str, ...]:
        """``short_text`` followed by ``texts``, deduplicated, empty strings dropped."""
        return tuple(dict.fromkeys(label for label in (self.short_text, *self.texts) if label))


@dataclass(frozen=True, slots=True)
class NaceStat:
    """NACE_STAT grouped by 2-digit division."""

    divisions: dict[str, NaceDivision]
    file: CodebookFile
    malformed: tuple[MalformedRow, ...] = ()
    skipped_rows: int = 0

    @property
    def codes(self) -> tuple[str, ...]:
        """Sorted division codes."""
        return tuple(sorted(self.divisions))

    def __len__(self) -> int:
        return len(self.divisions)


@dataclass(frozen=True, slots=True)
class CodebookSet:
    """The four loaded codebooks plus their version: the only object that emits CTS IDs.

    Exactly two methods may ever produce a CTS ID: :meth:`cts_id_for_esa` and
    :meth:`cts_id_for_nace`. Both refuse anything that is not a valid, emittable code.
    """

    cts_ba0036: CtsCodebook
    ba0036_valid: EsaValidList
    cts_okec_nace2: CtsCodebook
    nace_stat: NaceStat
    version: CodebookVersion

    def is_valid_esa(self, code: object) -> bool:
        """True when ``code`` (in any spelling) is a valid ESA leaf; False for malformed input."""
        try:
            key = normalize_esa_key(code)
        except MalformedCodeError:
            return False
        return key in self.ba0036_valid.by_key

    def cts_id_for_esa(self, code: object) -> CtsEntry:
        """Return the CTS_BA0036 entry for an ESA sector code in any spelling.

        Raises:
            MalformedCodeError: ``code`` is not an ESA code at all.
            UnknownEsaCodeError: no CTS_BA0036 entry exists (whether or not the code is a
                valid leaf; the consistency check flags a valid leaf without CTS entry as an
                error at startup, but the lookup must still fail safely).
            InvalidEsaCodeError: CTS knows the code but it is not a valid leaf (a parent such
                as ``S.11``); parent codes must never be emitted.
        """
        key = normalize_esa_key(code)
        display = format_esa_code(key)
        entry = self.cts_ba0036.by_key.get(key)
        is_valid = key in self.ba0036_valid.by_key
        if entry is None:
            if is_valid:
                raise UnknownEsaCodeError(
                    f"ESA code {display} is a valid leaf but has no entry in CTS_BA0036 "
                    f"(codebook version {self.version.id})"
                )
            raise UnknownEsaCodeError(f"ESA code {display} is not present in CTS_BA0036")
        if not is_valid:
            raise InvalidEsaCodeError(
                f"ESA code {display} (CTS ID {entry.cts_id}) is not a valid leaf sector; "
                "parent codes must not be emitted"
            )
        return entry

    def cts_id_for_nace(self, nace: object) -> CtsEntry:
        """Return the CTS_OKEC_NACE2 entry for a full NACE code in any spelling.

        The division is computed here via :func:`~core.codebooks.normalize.nace_to_division`,
        the only truncation point of the application.

        Raises:
            MalformedCodeError: ``nace`` is not a NACE code.
            UnknownNaceCodeError: the division has no CTS_OKEC_NACE2 entry.
        """
        division = nace_to_division(nace)
        entry = self.cts_okec_nace2.by_key.get(division)
        if entry is None:
            raise UnknownNaceCodeError(
                f"NACE division {division} (from {nace!r}) is not present in CTS_OKEC_NACE2"
            )
        return entry

    def esa_leaves(self) -> tuple[EsaSector, ...]:
        """Valid ESA leaf sectors, one per code, in file order (candidate pre-filter input)."""
        return tuple(self.ba0036_valid.by_key.values())

    def nace_divisions(self) -> tuple[NaceDivision, ...]:
        """NACE_STAT divisions sorted by code."""
        return tuple(self.nace_stat.divisions[code] for code in self.nace_stat.codes)

    def describe(self) -> str:
        """One-line summary for logs."""
        stat_rows = sum(len(division.rows) for division in self.nace_stat.divisions.values())
        return (
            f"codebooks {self.version.id}: "
            f"cts_ba0036={len(self.cts_ba0036.by_id)} entries, "
            f"ba0036_valid={len(self.ba0036_valid.by_key)} leaves, "
            f"cts_okec_nace2={len(self.cts_okec_nace2.by_id)} entries, "
            f"nace_stat={len(self.nace_stat.divisions)} divisions ({stat_rows} rows)"
        )
