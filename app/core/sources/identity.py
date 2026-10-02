"""Issuer identity: what an ISIN tells us about the issuer before anyone searches the web.

The brief's first input is an ISIN, and until now an ISIN on its own produced nothing: it
was validated, then used as a search string, and with no search provider configured the
lookup came back empty. This module resolves it properly:

    ISIN -> GLEIF (LEI record: legal name, country, legal form, category, parents)
         -> OpenFIGI (instrument: market name, security type, market sector)
    IČO  -> GLEIF (the Czech entity registered under it)
    a Czech issuer -> RES through ARES (prevailing NACE, institutional sector; 2 Oct 2026)

All are public, keyless and fail-soft. An issuer is **resident** when GLEIF's legal seat is
CZ, or, with no LEI record, when the user typed an IČO - decided by the register's seat,
never by the ISIN prefix (foreign issuers have CZ ISINs, Czech ones XS ISINs). What comes out is an :class:`IssuerIdentity`: the
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
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from config.settings import Settings
from core.sources.ares import AresSource, ResRecord
from core.sources.base import SourceError
from core.sources.gleif import GleifSource, LeiRecord
from core.sources.openfigi import FigiInstrument, OpenFigiSource
from core.sources.web import EvidenceSource

if TYPE_CHECKING:  # pragma: no cover
    from core.sources.ecb import EcbEntry, EcbRegister

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
        ecb_entry: The ECB list membership of the LEI, when a list has it.
        ecb_as_of: The lists' date when they were asked and none had the LEI.
        typed_ico: The IČO the user typed (normalised), if any.
        res_record: RES's record of a resident issuer, when RES had one.
    """

    isin: str | None = None
    lei_record: LeiRecord | None = None
    instrument: FigiInstrument | None = None
    sources: tuple[str, ...] = ()
    evidence: tuple[EvidenceSource, ...] = ()
    notes: tuple[str, ...] = field(default=())
    ecb_entry: EcbEntry | None = None
    ecb_as_of: str | None = None
    typed_ico: str | None = None
    res_record: ResRecord | None = None

    @property
    def found(self) -> bool:
        return (
            self.lei_record is not None
            or self.instrument is not None
            or self.res_record is not None
        )

    @property
    def legal_name(self) -> str | None:
        """The registered name from GLEIF, else the market name from OpenFIGI, else RES's."""
        if self.lei_record is not None and self.lei_record.legal_name:
            return self.lei_record.legal_name
        if self.instrument is not None and self.instrument.name:
            return self.instrument.name
        if self.res_record is not None and self.res_record.name:
            return self.res_record.name
        return None

    @property
    def resident(self) -> bool:
        """A Czech issuer: GLEIF's legal seat is CZ, or - with no LEI record - an IČO was typed.

        The register's seat decides, never the ISIN prefix: a foreign bank may issue under a CZ
        ISIN and ČEZ under an XS one. A typed IČO does not outvote a foreign seat (the ISIN wins).
        """
        if self.lei_record is not None:
            return self.lei_record.legal_address_country == "CZ"
        return self.typed_ico is not None

    @property
    def ico(self) -> str | None:
        """The resident issuer's IČO: RES's record, else GLEIF's ``registeredAs``, else typed."""
        if self.res_record is not None:
            return self.res_record.ico
        if self.lei_record is not None:
            return self.lei_record.ico
        return self.typed_ico

    @property
    def ecb_known(self) -> bool:
        """Whether the ECB lists were read for the LEI (a membership, or a stated absence)."""
        return self.ecb_entry is not None or self.ecb_as_of is not None

    @property
    def lei(self) -> str | None:
        return self.lei_record.lei if self.lei_record is not None else None

    @property
    def country(self) -> str | None:
        return self.lei_record.country if self.lei_record is not None else None

    def facts(self) -> tuple[str, ...]:
        """The ISIN's own class, GLEIF facts, the ECB lists, RES, then the instrument's."""
        lines: list[str] = []
        if self.isin and self.isin.startswith("EU"):
            # ISO 6166 gives the prefix "EU" only to EU institutions and bodies (the EU,
            # ESM, EFSF, Euratom). GLEIF files the EU as a GENERAL Belgian public-law body and
            # OpenFIGI its bonds as Govt, so the identifier is the one structured signal.
            lines.append(
                "ISIN má kód země EU, přidělovaný institucím a orgánům Evropské unie "
                "(EU institution, supranational) [ISIN_EU]."
            )
        if self.lei_record is not None:
            lines.extend(self.lei_record.facts())
        if self.ecb_entry is not None:
            lines.extend(self.ecb_entry.facts())
        elif self.ecb_as_of:
            from core.sources.ecb import absence_fact

            lines.append(absence_fact(self.ecb_as_of))
        if self.res_record is not None:
            lines.extend(self.res_record.facts())
        if self.instrument is not None:
            lines.extend(self.instrument.facts())
        return tuple(lines)

    def fact_sheet(self) -> str:
        """The facts as one block for the classifier; empty when nothing was found."""
        return " ".join(self.facts())


#: The identity of a request that carried no ISIN, or was looked up with identification off.
NO_IDENTITY = IssuerIdentity()


class IssuerIdentifier:
    """Ask GLEIF, then OpenFIGI, about one ISIN, and RES about a Czech issuer; never raise.

    Args:
        settings: ``GLEIF_ENABLED`` / ``OPENFIGI_ENABLED`` / ``ARES_ENABLED`` switch each source
            off; the rest configures the clients.
        gleif, openfigi, ares: Injected clients (tests). ``None`` builds the real ones lazily.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        gleif: GleifSource | None = None,
        openfigi: OpenFigiSource | None = None,
        ecb: EcbRegister | None = None,
        firds: Callable[[str], str | None] | None = None,
        ares: AresSource | None = None,
    ) -> None:
        self._settings = settings
        self._gleif = gleif
        self._openfigi = openfigi
        self._ecb = ecb
        self._firds = firds
        self._ares = ares

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

    def _ares_source(self) -> AresSource:
        if self._ares is None:
            self._ares = AresSource(self._settings)
        return self._ares

    def identify(
        self, isin: str | None, *, name: str | None = None, ico: str | None = None
    ) -> IssuerIdentity:
        """Resolve one normalised ISIN, else a typed IČO, else the typed ``name`` in GLEIF.

        Returns :data:`NO_IDENTITY` when there is nothing to do. The IČO and the name are
        asked only with no ISIN: an ISIN is the identifier, the rest claims about one. A LEI
        found any way is then looked up in the ECB lists, and a resident issuer in RES.
        """
        identity = self._identify(isin, name, ico)
        if ico is not None:
            identity = replace(identity, typed_ico=ico)
        return self._with_res(self._with_ecb(identity))

    def _with_ecb(self, identity: IssuerIdentity) -> IssuerIdentity:
        """Add the ECB list membership of the identity's LEI; a database failure is a note."""
        if self._ecb is None or identity.lei is None:
            return identity
        from core.db import DatabaseError

        try:
            entry = self._ecb.find(identity.lei)
            as_of = None if entry is not None else self._ecb.as_of()
        except DatabaseError as exc:
            LOGGER.warning("ECB lists could not be read: %s", exc)
            return replace(
                identity, notes=(*identity.notes, "ECB seznamy: databázi se nepodařilo dotázat")
            )
        if entry is None and as_of is None:
            return identity
        evidence = identity.evidence
        if entry is not None:
            evidence = (
                *evidence,
                EvidenceSource(
                    url=entry.url,
                    title=f"ECB – seznam finančních institucí ({entry.list_name}, {entry.as_of})",
                    snippet=entry.name or "",
                    fetched=True,
                ),
            )
        return replace(identity, ecb_entry=entry, ecb_as_of=as_of, evidence=evidence)

    def _identify(
        self, isin: str | None, name: str | None, ico: str | None = None
    ) -> IssuerIdentity:
        if not isin:
            if ico:
                return self._identify_ico(ico)
            return self._identify_name(name) if name else NO_IDENTITY
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
        if lei_record is None and "GLEIF" in sources and self._settings.firds_enabled:
            lei_record = self._from_firds(isin, sources, notes)
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

    def _from_firds(self, isin: str, sources: list[str], notes: list[str]) -> LeiRecord | None:
        """GLEIF has no ISIN mapping: ask ESMA FIRDS for the issuer's LEI, then GLEIF for it."""
        from core.sources.firds import firds_lei

        ask = self._firds or (lambda code: firds_lei(code, self._settings))
        lei = self._ask(
            "FIRDS",
            True,
            lambda: ask(isin),
            sources,
            notes,
            miss="ani ESMA FIRDS neuvádí k tomuto ISIN LEI emitenta",
        )
        if not lei:
            return None
        record = self._ask(
            "GLEIF",
            True,
            lambda: self._gleif_source().fetch(lei),
            [],
            notes,
            miss=f"GLEIF nezná LEI {lei}, který uvádí ESMA FIRDS",
        )
        if record is not None:
            notes.append(f"LEI emitenta doplněn z ESMA FIRDS ({lei}), GLEIF ISIN nemapuje")
        return record

    def _identify_ico(self, ico: str) -> IssuerIdentity:
        """GLEIF by IČO: the Czech entity registered under it - its LEI keys the ECB lists."""
        if not self._settings.gleif_enabled:
            return IssuerIdentity()
        sources: list[str] = []
        notes: list[str] = []
        record = self._ask(
            "GLEIF",
            True,
            lambda: self._gleif_source().find_by_ico(ico),
            sources,
            notes,
            miss=f"GLEIF nevede k IČO {ico} žádný záznam LEI",
        )
        evidence: tuple[EvidenceSource, ...] = ()
        if record is not None:
            evidence = (
                EvidenceSource(
                    url=record.url,
                    title=f"GLEIF – záznam LEI {record.lei} (podle IČO)",
                    snippet=record.legal_name or "",
                    fetched=True,
                    retrieved_at=record.provenance.retrieved_at,
                ),
            )
        return IssuerIdentity(
            lei_record=record, sources=tuple(sources), evidence=evidence, notes=tuple(notes)
        )

    def _with_res(self, identity: IssuerIdentity) -> IssuerIdentity:
        """Ask RES (through ARES) about a resident issuer; every way it cannot is a note.

        RES's 404 and an outage stay apart (:meth:`_ask`): "RES does not hold the IČO" is an
        answer, "RES could not be asked" is not.
        """
        typed = identity.typed_ico
        record = identity.lei_record
        notes = list(identity.notes)
        if not identity.resident:
            if typed is not None and record is not None:
                seat = record.legal_address_country or "?"
                notes.append(
                    f"zadané IČO {typed} k emitentovi ISIN nepatří (sídlo podle GLEIF {seat}) – "
                    "výsledek vychází z ISIN"
                )
                return replace(identity, notes=tuple(notes))
            return identity
        ico = identity.ico
        if typed is not None and ico is not None and typed != ico:
            notes.append(
                f"zadané IČO {typed} se liší od IČO emitenta podle GLEIF ({ico}) – použito {ico}"
            )
        if ico is None:
            notes.append(
                "GLEIF u emitenta neuvádí IČO (podílové fondy vlastní IČO nemají) – kódy z RES "
                "nelze převzít"
            )
            return replace(identity, notes=tuple(notes))
        if not self._settings.ares_enabled:
            notes.append(
                "RES se nedotazuje, ARES je vypnutý (ARES_ENABLED) – kódy z RES nepřevzaty"
            )
            return replace(identity, notes=tuple(notes))
        sources = list(identity.sources)
        res = self._ask(
            "RES",
            True,
            lambda: self._ares_source().fetch_res(ico),
            sources,
            notes,
            miss=(
                f"RES (ARES) nemá záznam s IČO {ico} – nové subjekty se v ARES objevují asi se "
                "třítýdenním zpožděním a zaniklé z něj mizí"
            ),
        )
        evidence = identity.evidence
        if res is not None:
            evidence = (
                *evidence,
                EvidenceSource(
                    url=res.url,
                    title=f"RES (ČSÚ) přes ARES – IČO {res.ico}",
                    snippet=res.name or "",
                    fetched=True,
                    retrieved_at=res.provenance.retrieved_at,
                ),
            )
        return replace(
            identity, res_record=res, sources=tuple(sources), notes=tuple(notes), evidence=evidence
        )

    def _identify_name(self, name: str) -> IssuerIdentity:
        """GLEIF by name: the one active entity called ``name``, flagged as a name match."""
        settings = self._settings
        if not (settings.gleif_enabled and settings.gleif_name_match):
            return NO_IDENTITY
        sources: list[str] = []
        notes: list[str] = []
        answer = self._ask(
            "GLEIF", True, lambda: self._gleif_source().match_name(name), sources, notes, miss=""
        )
        record, tied = answer if answer is not None else (None, 0)
        if record is None:
            if answer is not None:
                notes.append(_name_miss(name, tied))
            return IssuerIdentity(sources=tuple(sources), notes=tuple(notes))
        notes.append(
            f"emitent dohledán v GLEIF podle názvu, ne podle ISIN: {record.legal_name} "
            f"(LEI {record.lei}) – ověřte, že jde o správný subjekt"
        )
        return IssuerIdentity(
            lei_record=record,
            sources=tuple(sources),
            evidence=(
                EvidenceSource(
                    url=record.url,
                    title=f"GLEIF – záznam LEI {record.lei} (podle názvu)",
                    snippet=record.legal_name or "",
                    fetched=True,
                    retrieved_at=record.provenance.retrieved_at,
                ),
            ),
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
        for source in (self._gleif, self._openfigi, self._ares):
            if source is not None:
                source.close()


def _name_miss(name: str, tied: int) -> str:
    """What to type instead, when a name found no entity or several."""
    if tied > 1:
        return (
            f"název „{name}“ odpovídá v GLEIF {tied} různým subjektům, emitenta nelze určit – "
            "upřesněte ho: zadejte celý oficiální název včetně právní formy "
            "(např. „OMV AG“ místo „OMV“), nebo ISIN; český subjekt zadejte IČO"
        )
    return (
        f"v GLEIF není žádný aktivní subjekt s názvem „{name}“ – zkontrolujte pravopis, "
        "zadejte celý oficiální název včetně právní formy (např. „OMV Aktiengesellschaft“), "
        "nebo ISIN; český subjekt zadejte IČO"
    )


def build_identifier(settings: Settings) -> IssuerIdentifier:
    """The standard identifier: real GLEIF and OpenFIGI clients as configured."""
    from core.db import get_database
    from core.sources.ecb import EcbRegister

    database = get_database(settings) if settings.ecb_enabled else None
    return IssuerIdentifier(settings, ecb=EcbRegister(database) if database else None)


__all__ = ["NO_IDENTITY", "IssuerIdentifier", "IssuerIdentity", "build_identifier"]
