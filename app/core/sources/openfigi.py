"""OpenFIGI: an ISIN -> the instrument (name, security type, market sector).

The second identification source, and the fallback when GLEIF has no mapping for an ISIN.
It does not know legal entities - it knows *instruments* - so what it contributes is the
issuer name as the market spells it (``DEUTSCHE BANK AG-REGISTERED``) and two facts about
the paper: the security type (``Common Stock``, ``ETP``, ``EURO-DOLLAR`` ...) and the market
sector (``Equity``, ``Corp``, ``Govt``, ``Mtge``, ``Muni`` ...). The sector is a real clue for
the ESA side: a ``Govt`` issuer is a government and a ``Mtge`` issuer is usually a
securitisation vehicle, whatever its name says.

Verified live on 2026-09-22 (``api.openfigi.com/v3``):

* ``POST /mapping`` with body ``[{"idType": "ID_ISIN", "idValue": "DE0005140008"}]``
  returns one item per query: ``{"data": [{figi, name, ticker, exchCode, securityType,
  securityType2, marketSector, ...}, ...]}`` (one entry per listing, 289 for Deutsche Bank),
  ``{"warning": "No identifier found."}`` for an unknown ISIN, or ``{"error": "..."}`` for a
  malformed one.
* Rate limit without a key, read from the response headers: ``ratelimit-policy: 25;w=60``
  (25 requests per minute). With a free key (header ``X-OPENFIGI-APIKEY``) the published limit
  is 250 per minute; lower ``OPENFIGI_MIN_INTERVAL_SECONDS`` accordingly.

Public data (Bloomberg's Open Symbology, free to use); the same fail-soft contract as GLEIF.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import httpx

from config.settings import Settings
from core.sources.base import (
    Provenance,
    Source,
    SourceResponseError,
    SourceUnavailableError,
)

LOGGER = logging.getLogger(__name__)

#: Header carrying the optional API key.
API_KEY_HEADER: Final[str] = "X-OPENFIGI-APIKEY"

#: What Bloomberg's market sectors mean, worded so the keyword hints react where it matters.
MARKET_SECTOR_CS: Final[Mapping[str, str]] = {
    "Equity": "akcie nebo podílový list (equity)",
    "Corp": "korporátní dluhopis (corporate bond)",
    "Govt": "vládní dluhopis (government, sovereign)",
    "Muni": "komunální dluhopis (municipality, local government)",
    "Mtge": "hypoteční / sekuritizovaný nástroj (mortgage-backed, asset-backed)",
    "Pfd": "prioritní akcie (preferred stock)",
    "Curncy": "měnový nástroj (currency)",
    "Comdty": "komoditní nástroj (commodity)",
    "Index": "index",
    "M-Mkt": "nástroj peněžního trhu (money market)",
}


@dataclass(frozen=True, slots=True)
class FigiInstrument:
    """One instrument as OpenFIGI describes it, taken from the first listing returned."""

    isin: str
    figi: str | None
    name: str | None
    ticker: str | None
    security_type: str | None
    security_type2: str | None
    market_sector: str | None
    exchange_code: str | None
    listings: int
    provenance: Provenance

    @property
    def url(self) -> str:
        """The public search page for the ISIN, for the evidence list."""
        return f"https://www.openfigi.com/search#!?q={self.isin}"

    @property
    def market_sector_text(self) -> str | None:
        if not self.market_sector:
            return None
        gloss = MARKET_SECTOR_CS.get(self.market_sector)
        return f"{gloss} [{self.market_sector}]" if gloss else self.market_sector

    def facts(self) -> tuple[str, ...]:
        """Czech one-liners about the instrument."""
        lines = [f"OpenFIGI (ISIN {self.isin}): {self.name or '(název neuveden)'}."]
        kind = [part for part in (self.security_type, self.security_type2) if part]
        if kind:
            lines.append("Typ nástroje: " + " / ".join(dict.fromkeys(kind)) + ".")
        if self.market_sector_text:
            lines.append(f"Tržní sektor: {self.market_sector_text}.")
        return tuple(lines)

    def fact_sheet(self) -> str:
        return " ".join(self.facts())


class OpenFigiSource:
    """Read-only client of the OpenFIGI mapping API.

    Args:
        settings: Base URL, timeout, throttle, retries and the optional key (``OPENFIGI_*``);
            User-Agent from ``WEB_USER_AGENT``.
        client: Inject an ``httpx.Client`` to control transport (tests).
        sleep, monotonic: Injected by the tests so throttling can be asserted without waiting.
    """

    source: Source = "OPENFIGI"

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

    def headers(self) -> dict[str, str]:
        """Request headers; the key is added only when configured and is never logged."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self._settings.web_user_agent,
        }
        if self._settings.openfigi_api_key is not None:
            headers[API_KEY_HEADER] = self._settings.openfigi_api_key.get_secret_value()
        return headers

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._settings.openfigi_base_url,
                timeout=self._settings.openfigi_timeout_seconds,
                follow_redirects=True,
            )
        return self._client

    def _throttle(self) -> None:
        interval = self._settings.openfigi_min_interval_seconds
        if interval <= 0:
            return
        if self._last_request_at is not None:
            wait = interval - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _send(self, body: object) -> httpx.Response:
        """POST once, retrying timeouts, connection errors, 5xx and 429 with linear backoff."""
        client = self._ensure_client()
        attempts = max(1, self._settings.openfigi_max_attempts)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            self._throttle()
            try:
                response = client.post("/mapping", json=body, headers=self.headers())
            except httpx.TimeoutException as exc:
                last_error = SourceUnavailableError(f"OpenFIGI timed out: {exc}")
            except httpx.HTTPError as exc:
                last_error = SourceUnavailableError(f"OpenFIGI is unreachable: {exc}")
            else:
                if response.status_code < 500 and response.status_code != 429:
                    return response
                last_error = SourceUnavailableError(
                    f"OpenFIGI returned HTTP {response.status_code}"
                )
            if attempt < attempts:
                LOGGER.debug("OpenFIGI attempt %d/%d failed, retrying", attempt, attempts)
                self._sleep(max(self._settings.openfigi_min_interval_seconds, 0.5) * attempt)
        raise (
            last_error
            if last_error is not None
            else SourceUnavailableError("OpenFIGI could not be reached")
        )

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> OpenFigiSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- public API --------------------------------------------------------------------

    def map_isin(self, isin: str) -> FigiInstrument | None:
        """The instrument behind ``isin`` (already normalised), or ``None`` when unknown.

        Raises:
            SourceUnavailableError: the API could not be reached (network error, 5xx, 429).
            SourceResponseError: the API answered with an unexpected status or body.
        """
        response = self._send([{"idType": "ID_ISIN", "idValue": isin}])
        if response.status_code >= 400:
            raise SourceResponseError(f"OpenFIGI returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceResponseError(f"OpenFIGI returned a non-JSON body: {exc}") from exc
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], Mapping):
            raise SourceResponseError("OpenFIGI returned an unexpected body shape")
        first = payload[0]
        entries = [item for item in (first.get("data") or ()) if isinstance(item, Mapping)]
        if not entries:
            # "No identifier found." is a fact about the ISIN; "Invalid idValue format." is
            # one about our input. Neither is an outage, so both are a plain miss.
            LOGGER.debug(
                "OpenFIGI has nothing for %s: %s", isin, first.get("warning") or first.get("error")
            )
            return None
        entry = entries[0]
        return FigiInstrument(
            isin=isin,
            figi=_text(entry.get("figi")),
            name=_text(entry.get("name")),
            ticker=_text(entry.get("ticker")),
            security_type=_text(entry.get("securityType")),
            security_type2=_text(entry.get("securityType2")),
            market_sector=_text(entry.get("marketSector")),
            exchange_code=_text(entry.get("exchCode")),
            listings=len(entries),
            provenance=Provenance(
                source="OPENFIGI", retrieved_at=datetime.now(UTC), detail="openfigi:mapping"
            ),
        )


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["API_KEY_HEADER", "MARKET_SECTOR_CS", "FigiInstrument", "OpenFigiSource"]
