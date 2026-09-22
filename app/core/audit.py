"""Audit log of every subject lookup: what was asked, when, by whom, and what came back.

Hard rule from CLAUDE.md: *Log every lookup: identifier, timestamp, requesting user.*

What is deliberately **not** logged: any retrieved content. The audit trail records the
question and the outcome, never the answer - names, NACE codes and register contents stay
out of the log file, so the log can be kept and shipped without carrying client data.

Events go to the ``core.audit`` logger as a single line, and also as structured fields in
``record.audit`` so a JSON log handler can pick them up without parsing the message.
"""

from __future__ import annotations

import getpass
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from config.settings import Settings

#: Dedicated logger so the audit trail can be routed to its own handler/file.
LOGGER = logging.getLogger("core.audit")

#: What happened to a lookup. ``error`` means the sources failed, not that the subject is absent.
Outcome = Literal["found", "not_found", "ambiguous", "invalid_input", "error"]

_UNKNOWN_USER = "unknown"


def current_user(settings: Settings | None = None) -> str:
    """Identify the requesting user.

    Order: the configured ``LOOKUP_USER``, then the OS login name, then ``"unknown"``.
    Never raises - an unidentifiable user must not stop a lookup, only be recorded as such.
    """
    if settings is not None and settings.lookup_user:
        return settings.lookup_user
    for getter in (lambda: os.environ.get("LOOKUP_USER"), getpass.getuser):
        try:
            value = getter()
        except Exception:  # pragma: no cover - getpass fails only on exotic environments
            continue
        if value:
            return str(value).strip() or _UNKNOWN_USER
    return _UNKNOWN_USER


@dataclass(frozen=True, slots=True)
class LookupEvent:
    """One audited lookup.

    Attributes:
        identifier: What the user asked for, as typed (IČO or name).
        ico: The normalized IČO when one could be derived.
        user: Requesting user, from :func:`current_user`.
        at: Timezone-aware UTC timestamp of the lookup.
        outcome: See :data:`Outcome`.
        sources: Sources actually consulted, in order, e.g. ``("DWS", "ARES_LIVE")``.
        detail: Short machine-readable note (an error class, ``"dws_miss"``, ...). Never
            contains retrieved data.
    """

    identifier: str
    user: str
    at: datetime
    outcome: Outcome
    ico: str | None = None
    sources: tuple[str, ...] = ()
    detail: str | None = None

    def as_dict(self) -> dict[str, object]:
        """JSON-serializable form (``at`` as ISO 8601, ``sources`` as a list)."""
        data = asdict(self)
        data["at"] = self.at.isoformat()
        data["sources"] = list(self.sources)
        return data

    def message(self) -> str:
        """The single log line."""
        parts = [
            f"lookup identifier={self.identifier!r}",
            f"ico={self.ico or '-'}",
            f"user={self.user}",
            f"outcome={self.outcome}",
            f"sources={'+'.join(self.sources) or '-'}",
        ]
        if self.detail:
            parts.append(f"detail={self.detail}")
        return " ".join(parts)


def log_lookup(
    identifier: str,
    *,
    outcome: Outcome,
    user: str,
    ico: str | None = None,
    sources: tuple[str, ...] = (),
    detail: str | None = None,
    logger: logging.Logger | None = None,
) -> LookupEvent:
    """Record one lookup and return the event.

    Failures are logged at WARNING so an operator notices a source outage; everything else
    at INFO. The event is returned so a caller (the CLI, later the API) can also surface it.
    """
    event = LookupEvent(
        identifier=identifier,
        user=user,
        at=datetime.now(UTC),
        outcome=outcome,
        ico=ico,
        sources=sources,
        detail=detail,
    )
    level = logging.WARNING if outcome == "error" else logging.INFO
    (logger or LOGGER).log(level, event.message(), extra={"audit": event.as_dict()})
    return event
