"""Data source adapters. Priority: DWS -> ARES live -> web (foreign issuers only).

* :mod:`core.sources.base` - the record model (RES half + OR half + provenance) and the
  :class:`~core.sources.base.SubjectSource` interface.
* :mod:`core.sources.dws` - the read-only warehouse adapter; the only module with SQL.
  UNUSED: no DWS access is being granted for this project. Kept ready; it activates by
  itself if ``DWS_DSN`` is ever set.
* :mod:`core.sources.ares` - the public ares.gov.cz fallback, stamped ``ARES_LIVE``.
* :mod:`core.sources.resolver` - the DWS-then-ARES policy and the audit trail.
* :mod:`core.sources.web` - foreign-issuer descriptions from the public web, with
  citable sources. Never touches the Czech registers' web front ends.

Nothing obtained through :mod:`core.sources.dws` may ever be sent to an LLM.
"""

from core.sources.ares import AresSource
from core.sources.base import (
    NACE_REV_2,
    NACE_REV_21,
    NaceAssignment,
    NaceRevision,
    OrRecord,
    Provenance,
    ResRecord,
    Source,
    SourceError,
    SourceQueryError,
    SourceResponseError,
    SourceUnavailableError,
    SubjectCandidate,
    SubjectRecord,
    SubjectSource,
)
from core.sources.dws import DwsSource
from core.sources.resolver import (
    LookupResult,
    LookupStatus,
    SubjectResolver,
    build_default_resolver,
)
from core.sources.web import (
    BLOCKED_HOSTS,
    EvidenceSource,
    HttpSearchProvider,
    IssuerEvidence,
    NullSearchProvider,
    SearchHit,
    SearchProvider,
    StaticSearchProvider,
    WebEvidenceGatherer,
    is_blocked,
)

__all__ = [
    "BLOCKED_HOSTS",
    "NACE_REV_2",
    "NACE_REV_21",
    "AresSource",
    "DwsSource",
    "EvidenceSource",
    "HttpSearchProvider",
    "IssuerEvidence",
    "LookupResult",
    "LookupStatus",
    "NaceAssignment",
    "NullSearchProvider",
    "NaceRevision",
    "OrRecord",
    "Provenance",
    "ResRecord",
    "SearchHit",
    "SearchProvider",
    "Source",
    "SourceError",
    "SourceQueryError",
    "SourceResponseError",
    "SourceUnavailableError",
    "SubjectCandidate",
    "SubjectRecord",
    "StaticSearchProvider",
    "SubjectResolver",
    "SubjectSource",
    "WebEvidenceGatherer",
    "build_default_resolver",
    "is_blocked",
]
