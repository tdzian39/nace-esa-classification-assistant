"""RES through ARES: a Czech IČO -> the subject's prevailing activity and institutional sector.

Why this is here (2 Oct 2026, Jakub's decision after MO asked through Reporting): for a Czech
(resident) issuer the two codes this tool suggests are already on record. RES, the ČSÚ
statistical register of economic subjects, holds every Czech subject's prevailing activity in
CZ-NACE and its institutional sector in ČSÚ's ESA 2010 classification, and the Ministry of
Finance's ARES publishes RES as a keyless REST API. This reverses the scope line of 22 Sept
2026 ("nothing here reads ARES", PR #5) for resident issuers only; a foreign issuer never
reaches this module. OR (the public register, ARES's VR endpoint) adds no codes, so it is not
called.

Endpoint, verified live on 2026-10-02 (17 subjects, 40-110 ms and about 1 KB each, no key):

    GET https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty-res/{ico}

``{"icoId": ..., "zaznamy": [{...}]}``; the record marked ``primarniZaznam`` (else the first)
carries ``obchodniJmeno``, ``pravniForma`` (ČSÚ code: 121 a.s., 205 družstvo, 421 odštěpný
závod zahraniční právnické osoby, ...), ``datumAktualizace``, ``czNacePrevazujici`` (CZ-NACE
2025, 2 to 5 digits without dots: ``64190``, ``651``, ``35``), ``czNacePrevazujici2008`` (the
2008 edition, NACE Rev. 2) and ``statistickeUdaje.institucionalniSektor2010`` (ČSÚ's 5-digit
sector, whose last digit is the control type: 1 veřejné, 2 národní soukromé, 3 pod zahraniční
kontrolou). 404 (``NENALEZENO``) means RES has no such subject; 400 means a malformed IČO, which
this module never sends (eight digits only). The Ministry may block more than 500 requests a
minute.

ARES's copy of RES lags ČSÚ by about three weeks (the RES a OR tool found no record for
subjects founded after about 10 Sept, on 2 Oct 2026) and drops dissolved subjects, so a 404 is
said as exactly that, never as "the subject does not exist".

Ported from the adapter PR #5 removed (``cafc974^:app/core/sources/ares.py``): the same
transport - httpx, throttle, retries with linear backoff, ``None`` vs
:class:`~core.sources.base.SourceUnavailableError` - cut down to the RES half. Never scrape
``apl.czso.cz`` or ``or.justice.cz``: ARES is the official machine-readable route to RES.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final

import httpx

from config.settings import Settings
from core.sources.base import (
    Provenance,
    Source,
    SourceQueryError,
    SourceResponseError,
    SourceUnavailableError,
)

LOGGER = logging.getLogger(__name__)

#: The RES endpoint, relative to ``ARES_BASE_URL``.
PATH_RES: Final[str] = "/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty-res/{ico}"

#: The subject's page in the ARES web application - the citable form of a RES record.
RES_PAGE: Final[str] = "https://ares.gov.cz/ekonomicke-subjekty?ico={ico}"

_ICO_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{8}")
#: What RES puts in a NACE field: 2 to 6 digits, no dots.
_NACE_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{2,6}")
#: A ČSÚ institutional sector: five digits, or the single ``0`` (Nezjištěno).
_SECTOR_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{5}|0")

#: ČSÚ legal forms (ARES codelist ``PravniForma``) an issuer is likely to have, for the fact
#: sheet. A code missing here is shown bare; nothing decides on these names.
LEGAL_FORMS_CS: Final[Mapping[str, str]] = {
    "111": "veřejná obchodní společnost",
    "112": "společnost s ručením omezeným",
    "113": "společnost komanditní",
    "117": "nadace",
    "118": "nadační fond",
    "121": "akciová společnost",
    "141": "obecně prospěšná společnost",
    "161": "ústav",
    "205": "družstvo",
    "301": "státní podnik",
    "313": "Česká národní banka",
    "325": "organizační složka státu",
    "331": "příspěvková organizace",
    "421": "odštěpný závod zahraniční právnické osoby",
    "601": "vysoká škola",
    "706": "spolek",
    "801": "obec nebo městská část hlavního města Prahy",
    "804": "kraj a hlavní město Praha",
    "932": "evropská společnost",
    "933": "evropská družstevní společnost",
}


@dataclass(frozen=True, slots=True)
class ResRecord:
    """One subject's RES record, reduced to what the two codes need.

    Attributes:
        ico: The 8-digit IČO asked about.
        name: ``obchodniJmeno`` - the name the tool shows for a resident issuer.
        legal_form: ``pravniForma``, the ČSÚ legal-form code (``121``).
        sector: ``institucionalniSektor2010``, ČSÚ's 5-digit sector (``12203``).
        nace: ``czNacePrevazujici``, the prevailing activity in CZ-NACE 2025, as RES spells it.
        nace_2008: ``czNacePrevazujici2008``, the same in CZ-NACE 2008 (NACE Rev. 2).
        updated_on: ``datumAktualizace``, the date RES's record was last updated.
        provenance: ``RES`` with the retrieval time.
    """

    ico: str
    name: str | None
    legal_form: str | None
    sector: str | None
    nace: str | None
    nace_2008: str | None
    updated_on: date | None
    provenance: Provenance

    @property
    def url(self) -> str:
        """The subject's ARES page, for the evidence list."""
        return RES_PAGE.format(ico=self.ico)

    @property
    def as_of(self) -> str | None:
        """``datumAktualizace`` as ISO text (``2026-09-04``), the "stav k" of every code taken."""
        return self.updated_on.isoformat() if self.updated_on else None

    def facts(self) -> tuple[str, ...]:
        """One Czech line for the fact sheet, ending in the bracketed ``[RES]``."""
        from core.codebooks.res_esa import SECTOR_NAMES_CS

        parts = [f"{self.name or '(název neuveden)'}, IČO {self.ico}"]
        if self.legal_form:
            form = LEGAL_FORMS_CS.get(self.legal_form)
            parts.append(f"právní forma {self.legal_form}" + (f" {form}" if form else ""))
        if self.sector:
            name = SECTOR_NAMES_CS.get(self.sector)
            parts.append(f"institucionální sektor {self.sector}" + (f" {name}" if name else ""))
        else:
            parts.append("institucionální sektor neuveden")
        if self.nace:
            activity = f"převažující činnost CZ-NACE 2025 {self.nace}"
            if self.nace_2008 and self.nace_2008 != self.nace:
                activity += f" (CZ-NACE 2008 {self.nace_2008})"
            parts.append(activity)
        elif self.nace_2008:
            parts.append(
                f"převažující činnost CZ-NACE 2008 {self.nace_2008}, CZ-NACE 2025 neuvedena"
            )
        else:
            parts.append("převažující činnost neuvedena")
        stamp = f", stav k {self.as_of}" if self.as_of else ""
        return (f"RES (ČSÚ, přes ARES{stamp}): " + "; ".join(parts) + " [RES].",)


class AresSource:
    """Read-only client of the RES endpoint of the public ARES REST API.

    Args:
        settings: Base URL, timeout, throttle and retry counts (``ARES_*``), User-Agent
            (``WEB_USER_AGENT``).
        client: Inject an ``httpx.Client`` to control transport (the tests pass a
            ``MockTransport`` client). When omitted one is created lazily and closed by
            :meth:`close`.
        sleep, monotonic: Injected by the tests so throttling and backoff can be asserted
            without spending real time.
    """

    source: Source = "RES"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    # -- transport ---------------------------------------------------------------------

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._settings.ares_base_url,
                timeout=self._settings.ares_timeout_seconds,
                headers={
                    "Accept": "application/json",
                    "User-Agent": self._settings.web_user_agent,
                },
                follow_redirects=True,
            )
        return self._client

    def _throttle(self) -> None:
        """Keep at least ``ares_min_interval_seconds`` between two requests."""
        interval = self._settings.ares_min_interval_seconds
        if interval <= 0:
            return
        if self._last_request_at is not None:
            wait = interval - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _send(self, path: str) -> httpx.Response:
        """Send one GET, retrying transient failures with linear backoff.

        Retried: timeouts, connection errors, 5xx and 429 (the Ministry's rate limit). Not
        retried: other 4xx, which fail the same way however often they are repeated.
        """
        client = self._ensure_client()
        attempts = max(1, self._settings.ares_max_attempts)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            self._throttle()
            try:
                response = client.get(path)
            except httpx.TimeoutException as exc:
                last_error = SourceUnavailableError(f"ARES timed out on {path}: {exc}")
            except httpx.HTTPError as exc:
                last_error = SourceUnavailableError(f"ARES is unreachable on {path}: {exc}")
            else:
                if response.status_code < 500 and response.status_code != 429:
                    return response
                last_error = SourceUnavailableError(
                    f"ARES returned HTTP {response.status_code} for {path}"
                )
            if attempt < attempts:
                LOGGER.debug("ARES attempt %d/%d failed on %s, retrying", attempt, attempts, path)
                self._sleep(max(self._settings.ares_min_interval_seconds, 0.5) * attempt)
        raise (
            last_error
            if last_error is not None
            else SourceUnavailableError(f"ARES could not be reached on {path}")
        )

    def _request(self, path: str) -> Any | None:
        """Perform one request. ``None`` means 404 (RES does not hold the subject).

        Raises:
            SourceUnavailableError: ARES could not be reached (network error, 5xx, 429).
            SourceResponseError: ARES answered with another error status or a non-JSON body.
        """
        response = self._send(path)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise SourceResponseError(f"ARES returned HTTP {response.status_code} for {path}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceResponseError(f"ARES returned a non-JSON body for {path}: {exc}") from exc

    def close(self) -> None:
        """Close the HTTP client when this object created it."""
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> AresSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- public API --------------------------------------------------------------------

    def fetch_res(self, ico: str) -> ResRecord | None:
        """The RES record of ``ico`` (8 digits), or ``None`` when RES does not hold it.

        Raises:
            SourceQueryError: ``ico`` is not eight digits; nothing was sent.
            SourceUnavailableError, SourceResponseError: see :meth:`_request`.
        """
        if not _ICO_RE.fullmatch(ico):
            raise SourceQueryError(f"{ico!r} is not an 8-digit IČO; RES was not asked")
        payload = self._request(PATH_RES.format(ico=ico))
        if payload is None:
            return None
        record = _primary_record(payload)
        if record is None:
            raise SourceResponseError(f"ARES answered for {ico} without a RES record")
        stats = _mapping(record.get("statistickeUdaje"))
        return ResRecord(
            ico=_text(record.get("ico")) or ico,
            name=_text(record.get("obchodniJmeno")),
            legal_form=_text(record.get("pravniForma")),
            sector=_matching(stats.get("institucionalniSektor2010"), _SECTOR_RE, "sector"),
            nace=_matching(record.get("czNacePrevazujici"), _NACE_RE, "CZ-NACE 2025"),
            nace_2008=_matching(record.get("czNacePrevazujici2008"), _NACE_RE, "CZ-NACE 2008"),
            updated_on=_parse_date(record.get("datumAktualizace")),
            provenance=Provenance(
                source="RES",
                retrieved_at=datetime.now(UTC),
                snapshot_at=_as_datetime(_parse_date(record.get("datumAktualizace"))),
                detail="ares:res",
            ),
        )


# -- parsing (module level so it is testable without a client) ---------------------------


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _matching(value: object, pattern: re.Pattern[str], what: str) -> str | None:
    """The value when it has the expected shape; anything else is dropped with a warning.

    A code no codebook can resolve must not become a rule: an odd value is reported, not used.
    """
    text = _text(value)
    if text is None:
        return None
    if pattern.fullmatch(text):
        return text
    LOGGER.warning("ARES: unexpected %s %r, ignored", what, text)
    return None


def _parse_date(value: object) -> date | None:
    """An ARES ISO date (``"2026-09-04"``); anything unparseable becomes ``None``."""
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        LOGGER.debug("ARES: unparseable date %r", value)
        return None


def _as_datetime(value: date | None) -> datetime | None:
    """A date becomes UTC midnight; ``None`` passes through."""
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _primary_record(payload: Any) -> Mapping[str, Any] | None:
    """The record marked ``primarniZaznam``, else the first one, from a ``zaznamy`` envelope."""
    records = _mapping(payload).get("zaznamy")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return None
    usable = [item for item in records if isinstance(item, Mapping)]
    for item in usable:
        if item.get("primarniZaznam") is True:
            return item
    return usable[0] if usable else None


__all__ = ["LEGAL_FORMS_CS", "PATH_RES", "RES_PAGE", "AresSource", "ResRecord"]
