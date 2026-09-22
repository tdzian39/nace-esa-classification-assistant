"""What every data source shares: the ``Source`` stamp, :class:`Provenance` and the errors.

Tool 1 describes a foreign issuer from public sources only - the GLEIF LEI record and the
OpenFIGI instrument for an ISIN (:mod:`core.sources.gleif`, :mod:`core.sources.openfigi`),
and web evidence or a description the user typed (:mod:`core.sources.web`). Each of those
records carries a :class:`Provenance` saying where it came from and when it was obtained.

The error classes encode the fail-soft contract of the register adapters: returning
``None`` means the register does not hold the thing asked for; raising
:class:`SourceUnavailableError` means it could not be asked. The two must never be
collapsed - an outage reported as "not found" is a wrong answer, not a missing one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

#: Where a piece of data came from: ``GLEIF`` and ``OPENFIGI`` are the public registers
#: behind an ISIN (added 2026-09-22), ``WEB`` is web evidence or a user-typed description.
Source = Literal["WEB", "GLEIF", "OPENFIGI"]


class SourceError(Exception):
    """Base class for every failure raised by a data source."""


class SourceUnavailableError(SourceError):
    """The source cannot be reached (network error, timeout, 5xx, or 429 once retries are spent).

    Distinct from "the thing is not there": an unavailable source means the answer is
    unknown, so the caller must say so rather than report a not-found.
    """


class SourceResponseError(SourceError):
    """The source answered, but the answer could not be understood (bad status, bad JSON)."""


class SourceQueryError(SourceError):
    """A request was rejected by the adapter itself, before anything was sent.

    No current adapter raises it; it stays part of the error family for the next adapter
    that validates its own requests.
    """


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a record came from and how fresh it is.

    Attributes:
        source: ``GLEIF``, ``OPENFIGI`` or ``WEB``.
        retrieved_at: When this process obtained the data (timezone-aware UTC).
        snapshot_at: When the underlying data was last updated at the source - e.g. the
            GLEIF ``registration.lastUpdateDate``. ``None`` when the source does not say.
        detail: Free-form origin marker for debugging, e.g. ``"gleif:lei-records"`` or
            ``"user-supplied"``.
    """

    source: Source
    retrieved_at: datetime
    snapshot_at: datetime | None = None
    detail: str | None = None

    @property
    def timestamp(self) -> datetime:
        """The snapshot time when the source states one, else the retrieval time.

        Nothing reads it yet: a suggestion row's ``retrieved_at`` is the time of the lookup.
        """
        return self.snapshot_at or self.retrieved_at
