"""Audit log of every lookup: what was asked, when, by whom, and what came back.

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

#: What happened to a lookup. ``error`` means the sources failed, not that the issuer is unknown.
#: Tool 1 emits only ``found`` and ``not_found`` today. ``ambiguous``, ``invalid_input`` and
#: ``error`` are reserved: their only producer, the Tool 2 resolver, was removed in E0.3.
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
        identifier: What the user asked for, as typed (ISIN, name or the start of a
            description).
        ico: A normalized Czech company identifier (IČO) when the identifier is one. No
            Tool 1 lookup sets it; the field is optional and costs nothing.
        user: Requesting user, from :func:`current_user`.
        at: Timezone-aware UTC timestamp of the lookup.
        outcome: See :data:`Outcome`.
        sources: Sources actually consulted, in order, e.g. ``("GLEIF", "OPENFIGI", "WEB")``.
        detail: Short machine-readable note (an error class, ``"abstained"``, ...). Never
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


def log_report(
    identifier: str,
    *,
    user: str,
    stored: bool,
    store: str,
    logger: logging.Logger | None = None,
) -> None:
    """Record that an error report was made - never what it said.

    The report itself (request, result, note) is content and goes to the report store
    (:mod:`core.reports`); the audit trail keeps only who reported which identifier, and
    whether the store took it. A failed store is a WARNING so an operator notices.
    """
    at = datetime.now(UTC)
    outcome = "stored" if stored else "not_stored"
    (logger or LOGGER).log(
        logging.INFO if stored else logging.WARNING,
        f"report identifier={identifier!r} user={user} outcome={outcome} store={store}",
        extra={
            "audit": {
                "event": "report",
                "identifier": identifier,
                "user": user,
                "at": at.isoformat(),
                "outcome": outcome,
                "store": store,
            }
        },
    )


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

    ``outcome="error"`` is logged at WARNING so an operator notices a source outage (no
    caller emits it yet), everything else at INFO. The event is returned so a caller can
    also surface it; the API does not today.
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
