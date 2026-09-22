"""Data source adapters for foreign issuers: public registers first, then the web.

* :mod:`core.sources.base` - the ``Source`` stamp, :class:`~core.sources.base.Provenance`
  and the error classes behind the fail-soft contract.
* :mod:`core.sources.gleif`, :mod:`core.sources.openfigi`, :mod:`core.sources.identity` -
  ISIN -> issuer: the LEI record (legal name, country, legal form, entity category, parents)
  and the instrument (market name, security type, market sector). Public registers, stamped
  ``GLEIF`` / ``OPENFIGI``; what they return may be shown and put in a prompt.
* :mod:`core.sources.web` - foreign-issuer descriptions from the public web, with
  citable sources, stamped ``WEB``. Never touches the Czech registers' web front ends.

Nothing obtained from DWS (the bank's data warehouse) may ever be sent to an LLM; no
module here reads it.
"""

from core.sources.base import (
    Provenance,
    Source,
    SourceError,
    SourceQueryError,
    SourceResponseError,
    SourceUnavailableError,
)
from core.sources.gleif import GleifSource, LeiRecord, ParentEntity
from core.sources.identity import (
    NO_IDENTITY,
    IssuerIdentifier,
    IssuerIdentity,
    build_identifier,
)
from core.sources.openfigi import FigiInstrument, OpenFigiSource
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
    "EvidenceSource",
    "FigiInstrument",
    "GleifSource",
    "HttpSearchProvider",
    "IssuerEvidence",
    "IssuerIdentifier",
    "IssuerIdentity",
    "LeiRecord",
    "NO_IDENTITY",
    "NullSearchProvider",
    "OpenFigiSource",
    "ParentEntity",
    "Provenance",
    "SearchHit",
    "SearchProvider",
    "Source",
    "SourceError",
    "SourceQueryError",
    "SourceResponseError",
    "SourceUnavailableError",
    "StaticSearchProvider",
    "WebEvidenceGatherer",
    "build_identifier",
    "is_blocked",
]
