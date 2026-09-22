"""ARES fallback: the public REST API of the Czech Ministry of Finance (ares.gov.cz).

Used only for IČOs that DWS does not have (a subject registered since the last warehouse
snapshot, typically). Rows built here are stamped ``source="ARES_LIVE"`` so a reviewer can
see they are live public data rather than governed warehouse data.

Endpoints (verified against the live API on 2026-09-22 with IČO 49240901):

* ``GET /ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty-res/{ico}`` - the RES half.
  ``{"icoId": ..., "zaznamy": [{...}]}`` where a record carries ``obchodniJmeno``,
  ``datumVzniku``, ``datumAktualizace``, ``pravniForma``, ``czNace``/``czNacePrevazujici``,
  ``czNace2008``/``czNacePrevazujici2008`` and
  ``statistickeUdaje.institucionalniSektor2010`` (the ESA 2010 sector, digits only).
* ``GET /ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty-vr/{ico}`` - the OR (veřejný
  rejstřík) half. ``zaznamy[i]`` carries ``obchodniJmeno[]``, ``cinnosti.predmetPodnikani[]``,
  ``cinnosti.predmetCinnosti[]``, ``datumZapisu`` and ``spisovaZnacka[]``. Every list entry is
  ``{"hodnota": ..., "datumZapisu": ..., "datumVymazu": ...}``; an entry with ``datumVymazu``
  is historical and is dropped here.
* ``POST /ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/vyhledat`` - name search.
  TODO: not verified against the live API; confirm the request body and the result keys
  (``ekonomickeSubjekty``, ``pocetCelkem``) before relying on name lookups in production.

Scraping ``apl.czso.cz`` or ``or.justice.cz`` is forbidden by CLAUDE.md; this API is the
sanctioned machine-readable route to the same registers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Final

import httpx

from config.settings import Settings
from core.codebooks.errors import MalformedCodeError
from core.codebooks.normalize import format_esa_code
from core.sources.base import (
    NACE_REV_2,
    NACE_REV_21,
    NaceAssignment,
    OrRecord,
    Provenance,
    ResRecord,
    Source,
    SourceResponseError,
    SourceUnavailableError,
    SubjectCandidate,
    SubjectRecord,
)

LOGGER = logging.getLogger(__name__)

#: Path prefix shared by every endpoint used here.
_REST: Final[str] = "/ekonomicke-subjekty-v-be/rest"
PATH_RES: Final[str] = f"{_REST}/ekonomicke-subjekty-res/{{ico}}"
PATH_VR: Final[str] = f"{_REST}/ekonomicke-subjekty-vr/{{ico}}"
PATH_SEARCH: Final[str] = f"{_REST}/ekonomicke-subjekty/vyhledat"

#: Which ARES field holds which NACE revision.
#:
#: ``czNace2008`` is explicitly the 2008 edition of CZ-NACE, i.e. NACE Rev. 2. ``czNace`` is
#: the current edition, i.e. Rev. 2.1 ("CZ-NACE 2025"). For subjects not yet re-coded the two
#: are identical, which is exactly what ``nace_mismatch`` reports as "no mismatch".
#: TODO: re-confirm with ČSÚ once the CZ-NACE 2025 rollout is complete.
_NACE_FIELDS: Final[tuple[tuple[str, str, str], ...]] = (
    ("czNace2008", "czNacePrevazujici2008", NACE_REV_2),
    ("czNace", "czNacePrevazujici", NACE_REV_21),
)


class AresSource:
    """Read-only client of the public ARES REST API.

    Args:
        settings: Supplies the base URL, the timeout and the User-Agent.
        client: Inject an ``httpx.Client`` to control transport (the tests pass a
            ``MockTransport`` client). When omitted, one is created lazily and closed by
            :meth:`close`.
        sleep, monotonic: Injected by the tests so throttling and backoff can be asserted
            without spending real time.
    """

    source: Source = "ARES_LIVE"

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
                    "User-Agent": self._settings.ares_user_agent,
                },
                follow_redirects=True,
            )
        return self._client

    def _throttle(self) -> None:
        """Keep at least ``ares_min_interval_seconds`` between two requests.

        ARES times out on back-to-back requests (observed while building this adapter), and a
        batch of eighty companies would otherwise look like an attack on a public service.
        """
        interval = self._settings.ares_min_interval_seconds
        if interval <= 0:
            return
        if self._last_request_at is not None:
            wait = interval - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _send(self, method: str, path: str, json_body: object | None) -> httpx.Response:
        """Send one request, retrying transient failures with linear backoff.

        Retried: timeouts, connection errors and 5xx. Not retried: 4xx, which will fail the
        same way however often it is repeated.
        """
        client = self._ensure_client()
        attempts = max(1, self._settings.ares_max_attempts)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            self._throttle()
            try:
                response = client.request(method, path, json=json_body)
            except httpx.TimeoutException as exc:
                last_error = SourceUnavailableError(f"ARES timed out on {path}: {exc}")
            except httpx.HTTPError as exc:
                last_error = SourceUnavailableError(f"ARES is unreachable on {path}: {exc}")
            else:
                if response.status_code < 500:
                    return response
                last_error = SourceUnavailableError(
                    f"ARES returned HTTP {response.status_code} for {path}"
                )
            if attempt < attempts:
                LOGGER.debug("ARES attempt %d/%d failed on %s, retrying", attempt, attempts, path)
                self._sleep(self._settings.ares_min_interval_seconds * attempt)
        raise (
            last_error
            if last_error is not None
            else SourceUnavailableError(f"ARES could not be reached on {path}")
        )

    def _request(self, method: str, path: str, *, json_body: object | None = None) -> Any | None:
        """Perform one request. ``None`` means 404 (the register does not hold the subject).

        Raises:
            SourceUnavailableError: the API could not be reached (network error, 5xx).
            SourceResponseError: the API answered with an unexpected status or non-JSON body.
        """
        response = self._send(method, path, json_body)

        if response.status_code == 404:
            return None
        if response.status_code >= 500:
            raise SourceUnavailableError(f"ARES returned HTTP {response.status_code} for {path}")
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

    def fetch_by_ico(self, ico: str) -> SubjectRecord | None:
        """Fetch both register halves. ``None`` when neither register knows the IČO."""
        retrieved_at = datetime.now(UTC)
        res_payload = self._request("GET", PATH_RES.format(ico=ico))
        vr_payload = self._request("GET", PATH_VR.format(ico=ico))

        res = self._build_res(ico, res_payload, retrieved_at) if res_payload else None
        or_record = self._build_or(ico, vr_payload, retrieved_at) if vr_payload else None
        if res is None and or_record is None:
            return None

        notes: list[str] = []
        if res is None:
            notes.append("ARES: no RES record (statistical register does not hold this IČO)")
        if or_record is None:
            notes.append("ARES: no OR record (subject is not in the veřejný rejstřík)")
        return SubjectRecord(ico=ico, res=res, or_record=or_record, notes=tuple(notes))

    def search_by_name(self, name: str, *, limit: int = 10) -> tuple[SubjectCandidate, ...]:
        """Search subjects by business name.

        TODO: the request body and the response keys below are not verified against the live
        API. Confirm before relying on name search in production.
        """
        payload = self._request(
            "POST",
            PATH_SEARCH,
            json_body={"obchodniJmeno": name, "start": 0, "pocet": max(1, limit)},
        )
        if not isinstance(payload, Mapping):
            return ()
        hits = payload.get("ekonomickeSubjekty")
        if not isinstance(hits, Sequence):
            return ()
        candidates: list[SubjectCandidate] = []
        for hit in hits[:limit]:
            if not isinstance(hit, Mapping):
                continue
            hit_ico = _text(hit.get("ico"))
            if not hit_ico:
                continue
            candidates.append(
                SubjectCandidate(
                    ico=hit_ico,
                    name=_text(hit.get("obchodniJmeno")),
                    source=self.source,
                )
            )
        return tuple(candidates)

    # -- payload -> model --------------------------------------------------------------

    def _build_res(self, ico: str, payload: Any, retrieved_at: datetime) -> ResRecord | None:
        record = _primary_record(payload)
        if record is None:
            return None
        provenance = Provenance(
            source=self.source,
            retrieved_at=retrieved_at,
            snapshot_at=_as_datetime(_parse_date(record.get("datumAktualizace"))),
            detail="ares:res",
        )
        return ResRecord(
            ico=_text(record.get("ico")) or ico,
            provenance=provenance,
            name=_text(record.get("obchodniJmeno")),
            nace=tuple(_nace_assignments(record)),
            esa_sector=_esa_sector(record),
            founded_on=_parse_date(record.get("datumVzniku")),
            legal_form=_text(record.get("pravniForma")),
        )

    def _build_or(self, ico: str, payload: Any, retrieved_at: datetime) -> OrRecord | None:
        record = _primary_record(payload)
        if record is None:
            return None
        snapshot = _parse_date(record.get("datumAktualizace"))
        if snapshot is None and isinstance(payload, Mapping):
            snapshot = _parse_date(payload.get("datumAktualizace"))
        provenance = Provenance(
            source=self.source,
            retrieved_at=retrieved_at,
            snapshot_at=_as_datetime(snapshot),
            detail="ares:vr",
        )
        activities = record.get("cinnosti")
        activities = activities if isinstance(activities, Mapping) else {}
        return OrRecord(
            ico=_text(record.get("ico")) or ico,
            provenance=provenance,
            obchodni_firma=_first_valid_value(record.get("obchodniJmeno")),
            predmet_podnikani=_valid_values(activities.get("predmetPodnikani")),
            predmet_cinnosti=_valid_values(activities.get("predmetCinnosti")),
            datum_vzniku=_parse_date(record.get("datumVzniku")),
            datum_zapisu=_parse_date(record.get("datumZapisu")),
            spisova_znacka=_spisova_znacka(record.get("spisovaZnacka")),
        )


# -- helpers (module level so the parsing is unit-testable without a client) -------------


def _text(value: object) -> str | None:
    """Trimmed text, or ``None`` for missing/blank values."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value: object) -> date | None:
    """Parse an ARES ISO date (``"1993-06-25"``). Anything unparseable becomes ``None``."""
    if isinstance(value, date):
        return value
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
    """The record marked ``primarniZaznam``, else the first one, from a ``zaznamy`` envelope.

    Also accepts a bare record object, so the un-enveloped
    ``/ekonomicke-subjekty/{ico}`` response could be fed in unchanged.
    """
    if not isinstance(payload, Mapping):
        return None
    records = payload.get("zaznamy")
    if records is None:
        return payload if payload.get("ico") else None
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return None
    usable = [item for item in records if isinstance(item, Mapping)]
    if not usable:
        return None
    for item in usable:
        if item.get("primarniZaznam") is True:
            return item
    return usable[0]


def _nace_assignments(record: Mapping[str, Any]) -> list[NaceAssignment]:
    """Build the NACE list of both revisions, marking the prevailing code as main.

    Codes are kept exactly as ARES spells them (``"64190"``, leading zeros intact); nothing
    is truncated to a division here.
    """
    assignments: list[NaceAssignment] = []
    for list_field, main_field, revision in _NACE_FIELDS:
        main_code = _text(record.get(main_field))
        codes = _string_list(record.get(list_field))
        if main_code and main_code not in codes:
            codes = [main_code, *codes]
        for code in codes:
            assignments.append(
                NaceAssignment(code=code, revision=revision, is_main=code == main_code)
            )
    return assignments


def _string_list(value: object) -> list[str]:
    """A JSON array of codes as a list of trimmed strings; a scalar becomes a one-item list."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        text = _text(value)
        return [text] if text else []
    if isinstance(value, Iterable):
        out: list[str] = []
        for item in value:
            text = _text(item)
            if text and text not in out:
                out.append(text)
        return out
    text = _text(value)
    return [text] if text else []


def _esa_sector(record: Mapping[str, Any]) -> str | None:
    """Canonical ESA sector (``"S.12203"``) from ``statistickeUdaje.institucionalniSektor2010``.

    ARES stores the digits only. An unparseable value is dropped rather than passed on: an
    ESA code that no codebook can resolve must not reach an output row.
    """
    stats = record.get("statistickeUdaje")
    if not isinstance(stats, Mapping):
        return None
    raw = _text(stats.get("institucionalniSektor2010"))
    if raw is None:
        return None
    try:
        return format_esa_code(raw)
    except MalformedCodeError:
        LOGGER.warning("ARES: unparseable institutional sector %r, dropped", raw)
        return None


def _valid_entries(value: object) -> list[Mapping[str, Any]]:
    """Currently valid entries of a VR list: mappings without a ``datumVymazu``."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [
        item
        for item in value
        if isinstance(item, Mapping) and _text(item.get("datumVymazu")) is None
    ]


def _valid_values(value: object) -> tuple[str, ...]:
    """The ``hodnota`` texts of the currently valid entries, deduplicated, in order."""
    values: list[str] = []
    for item in _valid_entries(value):
        text = _text(item.get("hodnota"))
        if text and text not in values:
            values.append(text)
    return tuple(values)


def _first_valid_value(value: object) -> str | None:
    """The primary (or first) currently valid ``hodnota``."""
    entries = _valid_entries(value)
    for item in entries:
        if item.get("primarniZaznam") is True:
            text = _text(item.get("hodnota"))
            if text:
                return text
    for item in entries:
        text = _text(item.get("hodnota"))
        if text:
            return text
    return None


def _spisova_znacka(value: object) -> str | None:
    """Format the file reference as ``"B 2051/MSPH"`` from ``{oddil, vlozka, soud}``."""
    entries = _valid_entries(value)
    if not entries:
        return None
    entry = entries[0]
    oddil = _text(entry.get("oddil"))
    vlozka = _text(entry.get("vlozka"))
    soud = _text(entry.get("soud"))
    if not (oddil or vlozka):
        return None
    left = " ".join(part for part in (oddil, vlozka) if part)
    return f"{left}/{soud}" if soud else left
