"""Issuer identity: what an ISIN tells us about the issuer before anyone searches the web.

The brief's first input is an ISIN, and until now an ISIN on its own produced nothing: it
was validated, then used as a search string, and with no search provider configured the
lookup came back empty. This module resolves it properly:

    ISIN -> GLEIF (LEI record: legal name, country, legal form, category, parents)
         -> OpenFIGI (instrument: market name, security type, market sector)

Both are public, keyless and fail-soft. What comes out is an :class:`IssuerIdentity`: the
best legal name (which becomes the web search query and the name on the page), a Czech
fact sheet that goes into the classifier's evidence next to the web description, and the
citable record pages for the evidence list. Nothing here is a decision - the facts are laid
out for the pre-filter, the model and the reviewer, in that order.

Nothing obtained from DWS ever passes through here; the inputs are an ISIN and two public
registers, so everything in an identity may be shown and put in a prompt.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from config.settings import Settings
from core.sources.base import SourceError
from core.sources.gleif import GleifSource, LeiRecord
from core.sources.openfigi import FigiInstrument, OpenFigiSource
from core.sources.web import EvidenceSource

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IssuerIdentity:
    """Everything the identification step learned about one ISIN.

    Attributes:
        isin: The normalised ISIN that was looked up, or ``None`` when there was none.
        lei_record: GLEIF's view of the issuer, when GLEIF maps the ISIN.
        instrument: OpenFIGI's view of the instrument, when it knows the ISIN.
        sources: Which registers were consulted and answered, in order (``GLEIF``,
            ``OPENFIGI``) - the audit trail and the ``source`` column.
        evidence: Citable record pages, shown under "Podklady".
        notes: Why a half is missing, in words a reviewer can act on.
    """

    isin: str | None = None
    lei_record: LeiRecord | None = None
    instrument: FigiInstrument | None = None
    sources: tuple[str, ...] = ()
    evidence: tuple[EvidenceSource, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def found(self) -> bool:
        return self.lei_record is not None or self.instrument is not None

    @property
    def legal_name(self) -> str | None:
        """The registered name from GLEIF, else the market name from OpenFIGI."""
        if self.lei_record is not None and self.lei_record.legal_name:
            return self.lei_record.legal_name
        if self.instrument is not None and self.instrument.name:
            return self.instrument.name
        return None

    @property
    def lei(self) -> str | None:
        return self.lei_record.lei if self.lei_record is not None else None

    @property
    def country(self) -> str | None:
        return self.lei_record.country if self.lei_record is not None else None

    def facts(self) -> tuple[str, ...]:
        """GLEIF facts first, then the instrument's."""
        lines: list[str] = []
        if self.lei_record is not None:
            lines.extend(self.lei_record.facts())
        if self.instrument is not None:
            lines.extend(self.instrument.facts())
        return tuple(lines)

    def fact_sheet(self) -> str:
        """The facts as one block for the classifier; empty when nothing was found."""
        return " ".join(self.facts())


#: The identity of a request that carried no ISIN, or was looked up with identification off.
NO_IDENTITY = IssuerIdentity()


class IssuerIdentifier:
    """Ask GLEIF, then OpenFIGI, about one ISIN; never raise for an ordinary failure.

    Args:
        settings: ``GLEIF_ENABLED`` / ``OPENFIGI_ENABLED`` switch each source off; the rest
            configures the clients.
        gleif, openfigi: Injected clients (tests). ``None`` builds the real ones lazily.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        gleif: GleifSource | None = None,
        openfigi: OpenFigiSource | None = None,
    ) -> None:
        self._settings = settings
        self._gleif = gleif
        self._openfigi = openfigi

    @property
    def enabled(self) -> bool:
        """Whether at least one register will be asked."""
        return self._settings.gleif_enabled or self._settings.openfigi_enabled

    def _gleif_source(self) -> GleifSource:
        if self._gleif is None:
            self._gleif = GleifSource(self._settings)
        return self._gleif

    def _openfigi_source(self) -> OpenFigiSource:
        if self._openfigi is None:
            self._openfigi = OpenFigiSource(self._settings)
        return self._openfigi

    def identify(self, isin: str | None) -> IssuerIdentity:
        """Resolve one normalised ISIN. Returns :data:`NO_IDENTITY` when there is nothing to do."""
        if not isin:
            return NO_IDENTITY
        if not self.enabled:
            return IssuerIdentity(
                isin=isin, notes=("dohledání emitenta podle ISIN je vypnuto (GLEIF, OpenFIGI)",)
            )

        sources: list[str] = []
        evidence: list[EvidenceSource] = []
        notes: list[str] = []

        lei_record = self._ask(
            "GLEIF",
            self._settings.gleif_enabled,
            lambda: self._gleif_source().find_by_isin(isin),
            sources,
            notes,
            miss="GLEIF nemá k tomuto ISIN přiřazen LEI emitenta",
        )
        if lei_record is not None:
            evidence.append(
                EvidenceSource(
                    url=lei_record.url,
                    title=f"GLEIF – záznam LEI {lei_record.lei}",
                    snippet=lei_record.legal_name or "",
                    fetched=True,
                    retrieved_at=lei_record.provenance.retrieved_at,
                )
            )

        instrument = self._ask(
            "OPENFIGI",
            self._settings.openfigi_enabled,
            lambda: self._openfigi_source().map_isin(isin),
            sources,
            notes,
            miss="OpenFIGI tento ISIN nezná",
        )
        if instrument is not None:
            evidence.append(
                EvidenceSource(
                    url=instrument.url,
                    title=f"OpenFIGI – nástroj {isin}",
                    snippet=instrument.name or "",
                    fetched=True,
                    retrieved_at=instrument.provenance.retrieved_at,
                )
            )

        return IssuerIdentity(
            isin=isin,
            lei_record=lei_record,
            instrument=instrument,
            sources=tuple(sources),
            evidence=tuple(evidence),
            notes=tuple(notes),
        )

    @staticmethod
    def _ask(
        label: str,
        enabled: bool,
        call: Callable[[], object],
        sources: list[str],
        notes: list[str],
        *,
        miss: str,
    ):
        """Run one lookup, recording the source when it answered and a note when it did not.

        A register that could not be asked is a note, not an exception: MO still gets the
        other half and the reason, and the row is never lost.
        """
        if not enabled:
            return None
        try:
            result = call()
        except SourceError as exc:
            LOGGER.warning("%s lookup failed: %s", label, exc)
            notes.append(f"{label}: zdroj se nepodařilo dotázat ({exc})")
            return None
        sources.append(label)
        if result is None:
            notes.append(miss)
        return result

    def close(self) -> None:
        for source in (self._gleif, self._openfigi):
            if source is not None:
                source.close()


def build_identifier(settings: Settings) -> IssuerIdentifier:
    """The standard identifier: real GLEIF and OpenFIGI clients as configured."""
    return IssuerIdentifier(settings)


__all__ = ["NO_IDENTITY", "IssuerIdentifier", "IssuerIdentity", "build_identifier"]
