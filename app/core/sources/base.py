"""Source-independent record model and the interface every data source implements.

One subject (a Czech legal entity) is described by two independent halves:

* :class:`ResRecord` - the statistical register view (RES): name, NACE codes in both
  revisions, ESA 2010 institutional sector, founding date.
* :class:`OrRecord` - the commercial register view (OR / VR): obchodní firma, předmět
  podnikání, předmět činnosti, datum vzniku a zápisu.

They are kept apart on purpose: Tool 2 prints them side by side so a human can see where
the two registers disagree, and they may come from different sources (RES from DWS, OR from
ARES) in the same row.

Hard rules encoded here:

* every record carries its :class:`Provenance` (source, retrieval time, snapshot time) and
  the subject carries the codebook version, so an output row is always attributable;
* NACE is always a *list* of :class:`NaceAssignment` (main + others), never a single field,
  and both revisions live in the same list distinguished by ``revision``;
* full NACE codes are stored verbatim - nothing here truncates to a division. That happens
  only in :meth:`core.codebooks.CodebookSet.cts_id_for_nace`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable

#: Where a piece of data came from. ``WEB`` is reserved for build step 6 (foreign issuers).
Source = Literal["DWS", "ARES_LIVE", "WEB"]

#: NACE revision. ``"2"`` is NACE Rev. 2 (CZ-NACE 2008); ``"2.1"`` is NACE Rev. 2.1,
#: marketed in CZ as "CZ-NACE 2025".
NaceRevision = Literal["2", "2.1"]

NACE_REV_2: NaceRevision = "2"
NACE_REV_21: NaceRevision = "2.1"

#: Everything that is not a digit; used only to compare two spellings of the same code.
_NON_DIGITS_RE = re.compile(r"[^0-9]")


class SourceError(Exception):
    """Base class for every failure raised by a data source."""


class SourceUnavailableError(SourceError):
    """The source cannot be reached or is not configured (missing DSN, driver, network).

    Distinct from "the subject is not there": an unavailable source means the answer is
    unknown, so the caller must fall back rather than report a not-found.
    """


class SourceResponseError(SourceError):
    """The source answered, but the answer could not be understood (bad status, bad JSON)."""


class SourceQueryError(SourceError):
    """A query was rejected by the adapter itself (e.g. a non-read-only statement)."""


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where one half of a record came from and how fresh it is.

    Attributes:
        source: ``DWS``, ``ARES_LIVE`` or ``WEB``.
        retrieved_at: When this process obtained the data (timezone-aware UTC).
        snapshot_at: When the underlying data was last updated at the source - a DWS
            snapshot date or the ARES ``datumAktualizace``. ``None`` when the source does
            not say.
        detail: Free-form origin marker for debugging, e.g. ``"ares:res"`` or a DWS view name.
    """

    source: Source
    retrieved_at: datetime
    snapshot_at: datetime | None = None
    detail: str | None = None

    @property
    def timestamp(self) -> datetime:
        """The timestamp an output row must carry: the snapshot time, else the retrieval time."""
        return self.snapshot_at or self.retrieved_at


@dataclass(frozen=True, slots=True)
class NaceAssignment:
    """One NACE code assigned to a subject, in one revision.

    Attributes:
        code: The code exactly as the source spells it (``"64190"``, ``"64.19"``). Never
            truncated here; leading zeros are significant and are preserved.
        revision: :data:`NACE_REV_2` or :data:`NACE_REV_21`.
        is_main: True for the prevailing ("převažující") activity.
        label: Human-readable text when the source supplies one.
    """

    code: str
    revision: NaceRevision
    is_main: bool = False
    label: str | None = None

    @property
    def digits(self) -> str:
        """Digits only, for comparing two spellings of the same code (``"64.19"`` -> ``"6419"``)."""
        return _NON_DIGITS_RE.sub("", self.code)


def _sort_nace(codes: Iterable[NaceAssignment]) -> tuple[NaceAssignment, ...]:
    """Main activities first, then by code; duplicates (same revision + digits) dropped."""
    seen: set[tuple[str, str]] = set()
    unique: list[NaceAssignment] = []
    for item in codes:
        key = (item.revision, item.digits)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(sorted(unique, key=lambda item: (item.revision, not item.is_main, item.code)))


@dataclass(frozen=True, slots=True)
class ResRecord:
    """The RES (statistical register) half of a subject.

    ``nace`` holds every assignment of both revisions; use :meth:`main` and :meth:`others`
    to read one revision. ``esa_sector`` is the canonical display form (``"S.12203"``).
    """

    ico: str
    provenance: Provenance
    name: str | None = None
    nace: tuple[NaceAssignment, ...] = ()
    esa_sector: str | None = None
    founded_on: date | None = None
    legal_form: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "nace", _sort_nace(self.nace))

    def main(self, revision: NaceRevision) -> NaceAssignment | None:
        """The prevailing NACE assignment of ``revision``, or ``None`` when unknown."""
        for item in self.nace:
            if item.revision == revision and item.is_main:
                return item
        return None

    def others(self, revision: NaceRevision) -> tuple[NaceAssignment, ...]:
        """The non-prevailing NACE assignments of ``revision``, in code order."""
        return tuple(item for item in self.nace if item.revision == revision and not item.is_main)

    def codes(self, revision: NaceRevision) -> tuple[str, ...]:
        """Every NACE code of ``revision``, main first."""
        return tuple(item.code for item in self.nace if item.revision == revision)

    @property
    def nace_mismatch(self) -> bool | None:
        """Whether the main Rev. 2 and main Rev. 2.1 codes differ.

        Compared on digits only, so ``"64.19"`` and ``"6419"`` count as equal. Returns
        ``None`` - not ``False`` - when either revision is missing, because "we cannot tell"
        is not the same answer as "they agree", and a reviewer must see the difference.
        """
        main_2 = self.main(NACE_REV_2)
        main_21 = self.main(NACE_REV_21)
        if main_2 is None or main_21 is None:
            return None
        return main_2.digits != main_21.digits


@dataclass(frozen=True, slots=True)
class OrRecord:
    """The OR / VR (commercial register) half of a subject.

    Only currently valid entries belong here: a source must drop rows that carry a deletion
    date (``datumVymazu``) before building this record.
    """

    ico: str
    provenance: Provenance
    obchodni_firma: str | None = None
    predmet_podnikani: tuple[str, ...] = ()
    predmet_cinnosti: tuple[str, ...] = ()
    datum_vzniku: date | None = None
    datum_zapisu: date | None = None
    spisova_znacka: str | None = None


@dataclass(frozen=True, slots=True)
class SubjectRecord:
    """One subject: the RES half, the OR half, and everything needed to attribute the row.

    At least one half is present; a source returns ``None`` instead of an empty record when
    it knows nothing about the IČO.
    """

    ico: str
    res: ResRecord | None = None
    or_record: OrRecord | None = None
    codebook_version: str | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def provenances(self) -> tuple[Provenance, ...]:
        """Provenance of each present half, RES first."""
        return tuple(half.provenance for half in (self.res, self.or_record) if half is not None)

    @property
    def sources(self) -> tuple[Source, ...]:
        """Distinct sources that contributed, in RES-then-OR order."""
        return tuple(dict.fromkeys(item.source for item in self.provenances))

    @property
    def source_label(self) -> str:
        """The ``source`` column of an output row: ``"DWS"``, ``"ARES_LIVE"`` or ``"DWS+ARES_LIVE"``."""
        return "+".join(self.sources)

    @property
    def retrieved_at(self) -> datetime | None:
        """The earliest retrieval time across the present halves."""
        times = [item.retrieved_at for item in self.provenances]
        return min(times) if times else None

    @property
    def snapshot_at(self) -> datetime | None:
        """The oldest snapshot time across the present halves (the conservative answer)."""
        times = [item.snapshot_at for item in self.provenances if item.snapshot_at is not None]
        return min(times) if times else None

    @property
    def timestamp(self) -> datetime | None:
        """The snapshot-or-retrieval timestamp required on every output row."""
        return self.snapshot_at or self.retrieved_at

    @property
    def name(self) -> str | None:
        """Best available name: the RES name, else the OR obchodní firma."""
        if self.res is not None and self.res.name:
            return self.res.name
        if self.or_record is not None and self.or_record.obchodni_firma:
            return self.or_record.obchodni_firma
        return None

    @property
    def nace_mismatch(self) -> bool | None:
        """:attr:`ResRecord.nace_mismatch`, or ``None`` when there is no RES half."""
        return None if self.res is None else self.res.nace_mismatch

    def with_codebook_version(self, version: str | None) -> SubjectRecord:
        """Copy carrying ``version``; the resolver stamps this once the codebooks are known."""
        return replace(self, codebook_version=version)

    def with_note(self, note: str) -> SubjectRecord:
        """Copy with ``note`` appended (deduplicated)."""
        if note in self.notes:
            return self
        return replace(self, notes=(*self.notes, note))

    def merge(self, other: SubjectRecord) -> SubjectRecord:
        """Fill missing halves of ``self`` from ``other`` (used for a partial DWS hit).

        ``self`` wins wherever it has data, so a higher-priority source is never overwritten
        by a fallback. Notes from both are kept.
        """
        if other.ico != self.ico:
            raise ValueError(
                f"cannot merge records of different subjects: {self.ico} / {other.ico}"
            )
        notes = (*self.notes, *(note for note in other.notes if note not in self.notes))
        return replace(
            self,
            res=self.res or other.res,
            or_record=self.or_record or other.or_record,
            codebook_version=self.codebook_version or other.codebook_version,
            notes=notes,
        )


@dataclass(frozen=True, slots=True)
class SubjectCandidate:
    """A name-search hit: enough to identify a subject, not the full record."""

    ico: str
    name: str | None
    source: Source


@runtime_checkable
class SubjectSource(Protocol):
    """What the resolver needs from any source of Czech subject data.

    Implementations must be read-only and must raise :class:`SourceUnavailableError` -
    never return ``None`` - when they cannot answer at all, so the resolver can tell
    "not registered" apart from "could not ask".
    """

    #: Value written into :attr:`Provenance.source`.
    source: Source

    def fetch_by_ico(self, ico: str) -> SubjectRecord | None:
        """Return the subject with this normalized 8-digit IČO, or ``None`` if unknown."""
        ...

    def search_by_name(self, name: str, *, limit: int = 10) -> tuple[SubjectCandidate, ...]:
        """Return candidates whose name matches ``name`` (best effort, may be empty)."""
        ...

    def close(self) -> None:
        """Release connections. Safe to call more than once."""
        ...
