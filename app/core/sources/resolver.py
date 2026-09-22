"""Resolution policy: which source answers a lookup, in what order, and what a miss means.

Source priority from CLAUDE.md: DWS first (governed, internal), the public ARES API only for
IČOs DWS does not have. In practice DWS is UNUSED - no access is being granted - so ARES
answers everything and rows carry ``ARES_LIVE``. The ordering below is unchanged, because
setting ``DWS_DSN`` is all it would take to put the warehouse back in front. The distinction that makes the fallback correct:

* a source returning ``None`` means *the register does not hold this subject* - a fact;
* a source raising :class:`~core.sources.base.SourceUnavailableError` means *we could not
  ask* - not a fact. The resolver records it, moves on, and never reports ``not_found``
  when a higher-priority source was merely unreachable.

A partial hit (DWS has the RES half but not the OR half) is completed from the next source
and the halves keep their own provenance, so one output row can legitimately read
``DWS+ARES_LIVE``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from core.audit import LookupEvent, Outcome, current_user, log_lookup
from core.identifiers.ico import InvalidIcoError, normalize_ico
from core.sources.base import (
    Source,
    SourceError,
    SourceUnavailableError,
    SubjectCandidate,
    SubjectRecord,
    SubjectSource,
)

LOGGER = logging.getLogger(__name__)

#: Outcome of one lookup. Mirrors :data:`core.audit.Outcome`.
LookupStatus = Literal["found", "not_found", "ambiguous", "invalid_input", "error"]

#: A query the user meant as an IČO: digits, possibly with the separators Excel leaves behind.
_LOOKS_NUMERIC_RE = re.compile(r"^[0-9][0-9\s./\-]*$")


@dataclass(frozen=True, slots=True)
class LookupResult:
    """What one query produced, including why it produced nothing.

    Attributes:
        query: The identifier as the user typed it.
        status: See :data:`LookupStatus`.
        ico: The normalized IČO, when one was determined.
        record: The subject, when found.
        candidates: Name-search hits when ``status`` is ``ambiguous``.
        messages: Human-readable notes: source outages, partial hits, why input was rejected.
        sources_tried: Sources consulted, in order.
        resolved_at: When the resolution finished (timezone-aware UTC).
        event: The audit event recorded for this lookup.
    """

    query: str
    status: LookupStatus
    resolved_at: datetime
    ico: str | None = None
    record: SubjectRecord | None = None
    candidates: tuple[SubjectCandidate, ...] = ()
    messages: tuple[str, ...] = field(default=())
    sources_tried: tuple[Source, ...] = ()
    event: LookupEvent | None = None

    @property
    def found(self) -> bool:
        """True when a record was produced."""
        return self.record is not None


class SubjectResolver:
    """Resolve identifiers (IČO or name) to :class:`SubjectRecord` across ordered sources.

    Args:
        sources: Highest priority first. A source that is not configured should simply be
            left out by the caller (see :func:`build_default_resolver`).
        codebook_version: Stamped onto every record so an output row carries it.
        user: Requesting user for the audit log; defaults to :func:`core.audit.current_user`.
    """

    def __init__(
        self,
        sources: Sequence[SubjectSource],
        *,
        codebook_version: str | None = None,
        user: str | None = None,
    ) -> None:
        self._sources = tuple(sources)
        self._codebook_version = codebook_version
        self._user = user or current_user()

    @property
    def sources(self) -> tuple[SubjectSource, ...]:
        return self._sources

    def close(self) -> None:
        """Close every source; one failure does not prevent the others from closing."""
        for source in self._sources:
            try:
                source.close()
            except Exception:  # pragma: no cover - defensive
                LOGGER.debug("closing source %s failed", source.source, exc_info=True)

    def __enter__(self) -> SubjectResolver:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- resolution --------------------------------------------------------------------

    def resolve_many(self, queries: Iterable[str]) -> list[LookupResult]:
        """Resolve several identifiers, preserving input order."""
        return [self.resolve(query) for query in queries]

    def resolve(self, query: str) -> LookupResult:
        """Resolve one identifier and record an audit event."""
        text = (query or "").strip()
        if not text:
            return self._finish(query, "invalid_input", messages=("empty identifier",))

        ico, reason = _parse_ico(text)
        if reason is not None:
            return self._finish(text, "invalid_input", messages=(reason,))
        if ico is not None:
            return self._resolve_ico(text, ico)
        return self._resolve_name(text)

    def _resolve_ico(self, query: str, ico: str) -> LookupResult:
        record: SubjectRecord | None = None
        messages: list[str] = []
        tried: list[Source] = []
        failures = 0

        for source in self._sources:
            tried.append(source.source)
            try:
                found = source.fetch_by_ico(ico)
            except SourceUnavailableError as exc:
                failures += 1
                messages.append(f"{source.source} unavailable: {exc}")
                continue
            except SourceError as exc:
                failures += 1
                messages.append(f"{source.source} failed: {exc}")
                continue

            if found is None:
                messages.append(f"{source.source}: not found")
                continue
            record = found if record is None else record.merge(found)
            if record.res is not None and record.or_record is not None:
                break

        if record is None:
            status: LookupStatus = "error" if failures and failures == len(tried) else "not_found"
            return self._finish(
                query, status, ico=ico, messages=tuple(messages), tried=tuple(tried)
            )

        if record.res is None:
            messages.append("no RES half found in any source")
        if record.or_record is None:
            messages.append("no OR half found in any source")
        return self._finish(
            query,
            "found",
            ico=ico,
            record=record,
            messages=tuple(messages),
            tried=tuple(tried),
        )

    def _resolve_name(self, query: str) -> LookupResult:
        messages: list[str] = []
        tried: list[Source] = []
        failures = 0

        for source in self._sources:
            tried.append(source.source)
            try:
                candidates = source.search_by_name(query)
            except SourceUnavailableError as exc:
                failures += 1
                messages.append(f"{source.source} unavailable: {exc}")
                continue
            except SourceError as exc:
                failures += 1
                messages.append(f"{source.source} failed: {exc}")
                continue

            if not candidates:
                messages.append(f"{source.source}: no name match")
                continue
            if len(candidates) > 1:
                messages.append(
                    f"{source.source}: {len(candidates)} name matches, pick an IČO to continue"
                )
                return self._finish(
                    query,
                    "ambiguous",
                    candidates=candidates,
                    messages=tuple(messages),
                    tried=tuple(tried),
                )

            only = candidates[0]
            messages.append(f"{source.source}: name matched IČO {only.ico}")
            result = self._resolve_ico(query, only.ico)
            return LookupResult(
                query=result.query,
                status=result.status,
                resolved_at=result.resolved_at,
                ico=result.ico,
                record=result.record,
                candidates=candidates,
                messages=(*messages, *result.messages),
                sources_tried=tuple(dict.fromkeys((*tried, *result.sources_tried))),
                event=result.event,
            )

        status: LookupStatus = "error" if failures and failures == len(tried) else "not_found"
        return self._finish(query, status, messages=tuple(messages), tried=tuple(tried))

    # -- bookkeeping -------------------------------------------------------------------

    def _finish(
        self,
        query: str,
        status: LookupStatus,
        *,
        ico: str | None = None,
        record: SubjectRecord | None = None,
        candidates: tuple[SubjectCandidate, ...] = (),
        messages: tuple[str, ...] = (),
        tried: tuple[Source, ...] = (),
    ) -> LookupResult:
        """Stamp the codebook version, write the audit event and build the result."""
        if record is not None:
            record = record.with_codebook_version(self._codebook_version)
        event = log_lookup(
            query,
            outcome=_as_outcome(status),
            user=self._user,
            ico=ico,
            sources=tuple(record.sources) if record is not None else tried,
            detail=None if status == "found" else status,
        )
        return LookupResult(
            query=query,
            status=status,
            resolved_at=event.at,
            ico=ico,
            record=record,
            candidates=candidates,
            messages=messages,
            sources_tried=tried,
            event=event,
        )


def _as_outcome(status: LookupStatus) -> Outcome:
    """:data:`LookupStatus` and :data:`core.audit.Outcome` share their members."""
    return status


def _parse_ico(text: str) -> tuple[str | None, str | None]:
    """Classify a query as IČO, name, or rejected input.

    Returns ``(ico, None)`` for a valid IČO, ``(None, None)`` when the text should be treated
    as a name, and ``(None, reason)`` when it looks like an IČO but cannot be one - an
    all-digit string with a bad check digit is a typo, and searching for it as a company name
    would only hide the mistake.
    """
    if not _LOOKS_NUMERIC_RE.match(text):
        return None, None
    try:
        return normalize_ico(text), None
    except InvalidIcoError as exc:
        return None, f"not a valid IČO ({exc.reason}): {text!r}"


def build_default_resolver(
    settings: object = None,
    *,
    codebook_version: str | None = None,
    user: str | None = None,
    use_dws: bool = True,
    use_ares: bool = True,
) -> SubjectResolver:
    """Build the standard DWS-then-ARES resolver from settings.

    DWS is included only when a DSN is configured and ARES only when enabled, so a developer
    machine with no warehouse access still resolves everything through the public API.
    """
    from config.settings import Settings, get_settings
    from core.sources.ares import AresSource
    from core.sources.dws import DwsSource

    resolved_settings: Settings = settings if isinstance(settings, Settings) else get_settings()
    sources: list[SubjectSource] = []
    if use_dws and resolved_settings.dws_configured:
        sources.append(DwsSource(resolved_settings))
    if use_ares and resolved_settings.ares_enabled:
        sources.append(AresSource(resolved_settings))
    return SubjectResolver(sources, codebook_version=codebook_version, user=user)
