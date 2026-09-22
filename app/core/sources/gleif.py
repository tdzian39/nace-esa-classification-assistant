"""GLEIF: an ISIN or a LEI -> the legal entity behind it, from the public LEI register.

Why this is the first thing to ask for a foreign issuer: the ESA sector depends mostly on
*what kind of institution* the issuer is, and the LEI record already says so in structured
form - the legal name, the country, the legal form, the entity **category** (``GENERAL``,
``FUND``, ``BRANCH``, ``RESIDENT_GOVERNMENT_ENTITY``, ``INTERNATIONAL_ORGANIZATION``,
``SOLE_PROPRIETOR``) and, through the Level 2 data, the direct and ultimate parent. None of
that needs a search provider or a model, and all of it is public (GLEIF publishes the data
under CC0), so it may be shown to MO and put in a prompt.

Endpoints, verified live on 2026-09-22 (``api.gleif.org``, JSON:API, no key, published
limit 60 requests/minute):

* ``GET /lei-records?filter[isin]=DE0005140008&page[size]=1`` - the record of the issuer of
  an ISIN; ``data`` is an empty list when GLEIF has no mapping for the ISIN (coverage is
  wide but not complete - the iShares Core MSCI World ETF ``IE00B4L5Y983`` is missing while
  OpenFIGI knows it, which is why :mod:`core.sources.identity` asks both).
* ``GET /lei-records/{lei}`` - one record; 404 for an unknown LEI.
* ``GET /lei-records/{lei}/direct-parent`` and ``.../ultimate-parent`` - the parent's own
  LEI record (BMW Finance N.V. -> Bayerische Motoren Werke AG, DE), or **404** when none is
  reported. Then ``.../direct-parent-reporting-exception`` explains why (Deutsche Bank AG:
  ``reason: NO_KNOWN_PERSON``).

Field paths read: ``attributes.lei``, ``attributes.entity.{legalName.name, otherNames[].name,
legalAddress.country, headquartersAddress.country, jurisdiction, category, subCategory,
legalForm.id, legalForm.other, status}`` and ``attributes.registration.status``.

Fail-soft contract, as described in :mod:`core.sources.base`: ``None`` means the register
does not hold the ISIN or LEI; :class:`~core.sources.base.SourceUnavailableError` means it
could not be asked. Never collapse the two.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from config.settings import Settings
from core.sources.base import (
    Provenance,
    Source,
    SourceResponseError,
    SourceUnavailableError,
)

LOGGER = logging.getLogger(__name__)

#: Human-readable record page, the citable form of a LEI (the API URL is JSON).
RECORD_PAGE: Final[str] = "https://search.gleif.org/#/record/{lei}"

#: What GLEIF's entity categories mean, in the words the pre-filter's keyword table already
#: understands ("investment fund", "government", "supranational", ...). The English tail is
#: there for the hint triggers, the Czech head for MO reading the page.
CATEGORY_CS: Final[Mapping[str, str]] = {
    "GENERAL": "běžná právnická osoba",
    "FUND": "investiční fond (investment fund)",
    "BRANCH": "pobočka zahraniční osoby (branch)",
    "SOLE_PROPRIETOR": "podnikající fyzická osoba (sole proprietor)",
    "RESIDENT_GOVERNMENT_ENTITY": "vládní instituce (government)",
    "INTERNATIONAL_ORGANIZATION": "mezinárodní organizace (supranational international organisation)",
}

#: GLEIF sub-categories of government entities, glossed the same way.
SUB_CATEGORY_CS: Final[Mapping[str, str]] = {
    "CENTRAL_GOVERNMENT": "ústřední vláda (central government, sovereign)",
    "STATE_GOVERNMENT": "vláda spolkové země / státu (state government)",
    "LOCAL_GOVERNMENT": "místní samospráva (local government, municipality)",
    "SOCIAL_SECURITY_SYSTEM": "systém sociálního zabezpečení (social security)",
}

#: Why a record reports no parent, in plain words.
EXCEPTION_REASON_CS: Final[Mapping[str, str]] = {
    "NO_KNOWN_PERSON": "žádná mateřská společnost (samostatná jednotka nebo vlastnická struktura bez jedné ovládající osoby)",
    "NATURAL_PERSONS": "ovládána fyzickými osobami",
    "NON_CONSOLIDATING": "mateřská společnost nekonsoliduje",
    "NO_LEI": "mateřská společnost nemá LEI",
    "NON_PUBLIC": "vlastník není zveřejněn",
    "BINDING_LEGAL_COMMITMENTS": "zveřejnění brání právní závazky",
    "CONSENT_NOT_OBTAINED": "mateřská společnost s zveřejněním nesouhlasila",
    "DETRIMENT_NOT_EXCLUDED": "zveřejnění by mohlo poškodit jednotku",
    "DISCLOSURE_DETRIMENTAL": "zveřejnění by mohlo poškodit jednotku",
}


@dataclass(frozen=True, slots=True)
class ParentEntity:
    """The direct or ultimate parent as GLEIF reports it (Level 2 data)."""

    lei: str
    legal_name: str | None
    country: str | None
    jurisdiction: str | None = None


@dataclass(frozen=True, slots=True)
class LeiRecord:
    """One LEI record, reduced to what a classification needs.

    Attributes:
        lei: The 20-character Legal Entity Identifier.
        legal_name: The registered name as GLEIF spells it (often upper case, often in
            the local language: ``DEUTSCHE BANK AKTIENGESELLSCHAFT``).
        other_names: Trading and transliterated names, when any.
        jurisdiction: Legal jurisdiction (ISO 3166 code, or ``EU`` for the EIB).
        legal_address_country, headquarters_country: ISO 3166-1 alpha-2 codes.
        category, sub_category: GLEIF entity category and, for governments, the sub-category.
        legal_form_id: ISO 20275 Entity Legal Form code (``6QQB`` = German AG).
        legal_form_text: Free text GLEIF carries for forms without an ELF code.
        entity_status: ``ACTIVE`` / ``INACTIVE``.
        registration_status: ``ISSUED``, ``LAPSED``, ``RETIRED`` ... - the LEI's own status.
        direct_parent, ultimate_parent: Level 2 parents, when reported.
        parent_exception: The reason GLEIF gives when no parent is reported.
        provenance: ``GLEIF`` with the retrieval time.
    """

    lei: str
    legal_name: str | None
    other_names: tuple[str, ...]
    jurisdiction: str | None
    legal_address_country: str | None
    headquarters_country: str | None
    category: str | None
    sub_category: str | None
    legal_form_id: str | None
    legal_form_text: str | None
    entity_status: str | None
    registration_status: str | None
    provenance: Provenance
    direct_parent: ParentEntity | None = None
    ultimate_parent: ParentEntity | None = None
    parent_exception: str | None = None

    @property
    def country(self) -> str | None:
        """The country to show: legal seat, else headquarters, else jurisdiction."""
        return self.legal_address_country or self.headquarters_country or self.jurisdiction

    @property
    def url(self) -> str:
        """The public record page, for the evidence list."""
        return RECORD_PAGE.format(lei=self.lei)

    @property
    def category_text(self) -> str | None:
        """The category with its gloss: ``"investiční fond (investment fund) [FUND]"``."""
        if not self.category:
            return None
        gloss = CATEGORY_CS.get(self.category)
        return f"{gloss} [{self.category}]" if gloss else self.category

    @property
    def sub_category_text(self) -> str | None:
        if not self.sub_category:
            return None
        gloss = SUB_CATEGORY_CS.get(self.sub_category)
        return f"{gloss} [{self.sub_category}]" if gloss else self.sub_category

    @property
    def parent_abroad(self) -> bool | None:
        """Whether the ultimate parent sits in another country than the entity.

        A fact for the reviewer and the model, not a decision: whether that makes the entity
        "pod zahraniční kontrolou" in BA0036's sense is the classifier's call. ``None`` when
        either country is unknown or no parent is reported.
        """
        if self.ultimate_parent is None or not self.ultimate_parent.country or not self.country:
            return None
        return self.ultimate_parent.country != self.country

    def facts(self) -> tuple[str, ...]:
        """Czech one-liners describing the entity, in reading order.

        Everything here is public register data; the wording deliberately carries the
        English key words the keyword hints react to (see :data:`CATEGORY_CS`).
        """
        lines: list[str] = []
        lines.append(f"GLEIF (LEI {self.lei}): {self.legal_name or '(název neuveden)'}.")
        if self.other_names:
            lines.append("Další názvy: " + "; ".join(self.other_names) + ".")
        seat = self.legal_address_country
        if seat:
            place = f"Země sídla: {seat}"
            if self.headquarters_country and self.headquarters_country != seat:
                place += f", ústředí {self.headquarters_country}"
            if self.jurisdiction and self.jurisdiction != seat:
                place += f", jurisdikce {self.jurisdiction}"
            lines.append(place + ".")
        elif self.jurisdiction:
            lines.append(f"Jurisdikce: {self.jurisdiction}.")
        form = []
        if self.legal_form_id:
            form.append(f"ELF {self.legal_form_id}")
        if self.legal_form_text:
            form.append(self.legal_form_text)
        if form:
            lines.append("Právní forma: " + ", ".join(form) + ".")
        if self.category_text:
            category = f"Kategorie subjektu podle GLEIF: {self.category_text}"
            if self.sub_category_text:
                category += f", {self.sub_category_text}"
            lines.append(category + ".")
        status = []
        if self.entity_status:
            status.append(f"subjekt {self.entity_status}")
        if self.registration_status:
            status.append(f"registrace LEI {self.registration_status}")
        if status:
            lines.append("Stav: " + ", ".join(status) + ".")
        lines.extend(self._parent_facts())
        return tuple(lines)

    def _parent_facts(self) -> list[str]:
        lines: list[str] = []
        if self.direct_parent is not None:
            lines.append("Přímá mateřská společnost: " + _describe_parent(self.direct_parent) + ".")
        if self.ultimate_parent is not None and (
            self.direct_parent is None or self.ultimate_parent.lei != self.direct_parent.lei
        ):
            lines.append(
                "Konečná mateřská společnost: " + _describe_parent(self.ultimate_parent) + "."
            )
        elif self.ultimate_parent is not None:
            lines.append("Konečná mateřská společnost je totožná s přímou.")
        if self.parent_abroad:
            lines.append(
                f"Konečná mateřská společnost sídlí v jiné zemi ({self.ultimate_parent.country}) "  # type: ignore[union-attr]
                f"než emitent ({self.country})."
            )
        elif self.parent_abroad is False:
            lines.append("Konečná mateřská společnost sídlí ve stejné zemi jako emitent.")
        if self.direct_parent is None and self.ultimate_parent is None:
            reason = self.parent_exception
            gloss = EXCEPTION_REASON_CS.get(reason or "")
            if reason:
                lines.append(
                    "Mateřská společnost není v GLEIF uvedena: "
                    + (f"{gloss} [{reason}]" if gloss else reason)
                    + "."
                )
            else:
                lines.append("Mateřská společnost není v GLEIF uvedena.")
        return lines

    def fact_sheet(self) -> str:
        """The facts as one paragraph, for the classifier."""
        return " ".join(self.facts())


def _describe_parent(parent: ParentEntity) -> str:
    name = parent.legal_name or "(název neuveden)"
    return f"{name} ({parent.country})" if parent.country else name


class GleifSource:
    """Read-only client of the GLEIF LEI API.

    Args:
        settings: Base URL, timeout, throttle and retry counts (``GLEIF_*``), User-Agent
            (``WEB_USER_AGENT``).
        client: Inject an ``httpx.Client`` to control transport (the tests pass a
            ``MockTransport`` client). When omitted one is created lazily and closed by
            :meth:`close`.
        sleep, monotonic: Injected by the tests so throttling and backoff can be asserted
            without spending real time.
    """

    source: Source = "GLEIF"

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
                base_url=self._settings.gleif_base_url,
                timeout=self._settings.gleif_timeout_seconds,
                headers={
                    "Accept": "application/vnd.api+json",
                    "User-Agent": self._settings.web_user_agent,
                },
                follow_redirects=True,
            )
        return self._client

    def _throttle(self) -> None:
        """Keep at least ``gleif_min_interval_seconds`` between two requests (60/min limit)."""
        interval = self._settings.gleif_min_interval_seconds
        if interval <= 0:
            return
        if self._last_request_at is not None:
            wait = interval - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _send(self, path: str, params: Mapping[str, str] | None = None) -> httpx.Response:
        """Send one GET, retrying transient failures with linear backoff.

        Retried: timeouts, connection errors, 5xx and 429 (the published rate limit). Not
        retried: other 4xx, which fail the same way however often they are repeated.
        """
        client = self._ensure_client()
        attempts = max(1, self._settings.gleif_max_attempts)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            self._throttle()
            try:
                response = client.get(path, params=params)
            except httpx.TimeoutException as exc:
                last_error = SourceUnavailableError(f"GLEIF timed out on {path}: {exc}")
            except httpx.HTTPError as exc:
                last_error = SourceUnavailableError(f"GLEIF is unreachable on {path}: {exc}")
            else:
                if response.status_code < 500 and response.status_code != 429:
                    return response
                last_error = SourceUnavailableError(
                    f"GLEIF returned HTTP {response.status_code} for {path}"
                )
            if attempt < attempts:
                LOGGER.debug("GLEIF attempt %d/%d failed on %s, retrying", attempt, attempts, path)
                self._sleep(max(self._settings.gleif_min_interval_seconds, 0.5) * attempt)
        raise (
            last_error
            if last_error is not None
            else SourceUnavailableError(f"GLEIF could not be reached on {path}")
        )

    def _request(self, path: str, params: Mapping[str, str] | None = None) -> Any | None:
        """Perform one request. ``None`` means 404 (the register has no such resource).

        Raises:
            SourceUnavailableError: the API could not be reached (network error, 5xx, 429).
            SourceResponseError: the API answered with an unexpected status or non-JSON body.
        """
        response = self._send(path, params)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise SourceResponseError(f"GLEIF returned HTTP {response.status_code} for {path}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceResponseError(f"GLEIF returned a non-JSON body for {path}: {exc}") from exc

    def close(self) -> None:
        """Close the HTTP client when this object created it."""
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> GleifSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- public API --------------------------------------------------------------------

    def find_by_isin(self, isin: str) -> LeiRecord | None:
        """The issuer of ``isin`` (already normalised), or ``None`` when GLEIF has no mapping."""
        payload = self._request("/lei-records", {"filter[isin]": isin, "page[size]": "1"})
        items = _data_list(payload)
        if not items:
            return None
        return self._complete(_parse_record(items[0], self._now()))

    def fetch(self, lei: str) -> LeiRecord | None:
        """The record of ``lei``, or ``None`` for an unknown LEI."""
        payload = self._request(f"/lei-records/{lei.strip().upper()}")
        item = _data_object(payload)
        if item is None:
            return None
        return self._complete(_parse_record(item, self._now()))

    # -- internals ---------------------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _complete(self, record: LeiRecord) -> LeiRecord:
        """Attach the Level 2 parents (two more requests) when configured."""
        if not self._settings.gleif_fetch_parents:
            return record
        direct = self._parent(record.lei, "direct-parent")
        ultimate = self._parent(record.lei, "ultimate-parent")
        exception = None
        if direct is None and ultimate is None:
            exception = self._exception_reason(record.lei)
        return replace(
            record, direct_parent=direct, ultimate_parent=ultimate, parent_exception=exception
        )

    def _parent(self, lei: str, relation: str) -> ParentEntity | None:
        """The parent's LEI record reduced to an identity, or ``None`` when none is reported.

        A parent lookup failing must not lose the record itself, so transport errors here
        are logged and treated as "not reported"; the main record already carries its own
        provenance and a reviewer still sees the entity.
        """
        try:
            payload = self._request(f"/lei-records/{lei}/{relation}")
        except (SourceUnavailableError, SourceResponseError) as exc:
            LOGGER.warning("GLEIF %s of %s could not be read: %s", relation, lei, exc)
            return None
        item = _data_object(payload)
        if item is None:
            return None
        attributes = _mapping(item.get("attributes"))
        entity = _mapping(attributes.get("entity"))
        parent_lei = _text(attributes.get("lei")) or _text(item.get("id"))
        if not parent_lei:
            return None
        return ParentEntity(
            lei=parent_lei,
            legal_name=_text(_mapping(entity.get("legalName")).get("name")),
            country=_text(_mapping(entity.get("legalAddress")).get("country")),
            jurisdiction=_text(entity.get("jurisdiction")),
        )

    def _exception_reason(self, lei: str) -> str | None:
        try:
            payload = self._request(f"/lei-records/{lei}/direct-parent-reporting-exception")
        except (SourceUnavailableError, SourceResponseError) as exc:
            LOGGER.debug("GLEIF reporting exception of %s could not be read: %s", lei, exc)
            return None
        item = _data_object(payload)
        if item is None:
            return None
        return _text(_mapping(item.get("attributes")).get("reason"))


# -- parsing ---------------------------------------------------------------------------


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _data_list(payload: Any) -> list[Mapping[str, Any]]:
    data = _mapping(payload).get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, Mapping)]


def _data_object(payload: Any) -> Mapping[str, Any] | None:
    data = _mapping(payload).get("data")
    return data if isinstance(data, Mapping) else None


def _parse_record(item: Mapping[str, Any], retrieved_at: datetime) -> LeiRecord:
    """Reduce one ``lei-records`` item to a :class:`LeiRecord` (parents attached later)."""
    attributes = _mapping(item.get("attributes"))
    entity = _mapping(attributes.get("entity"))
    registration = _mapping(attributes.get("registration"))
    legal_form = _mapping(entity.get("legalForm"))
    other_names = tuple(
        name
        for name in (
            _text(_mapping(other).get("name"))
            for other in (entity.get("otherNames") or ())
            if isinstance(other, Mapping)
        )
        if name
    )
    lei = _text(attributes.get("lei")) or _text(item.get("id")) or ""
    return LeiRecord(
        lei=lei,
        legal_name=_text(_mapping(entity.get("legalName")).get("name")),
        other_names=other_names,
        jurisdiction=_text(entity.get("jurisdiction")),
        legal_address_country=_text(_mapping(entity.get("legalAddress")).get("country")),
        headquarters_country=_text(_mapping(entity.get("headquartersAddress")).get("country")),
        category=_text(entity.get("category")),
        sub_category=_text(entity.get("subCategory")),
        legal_form_id=_text(legal_form.get("id")),
        legal_form_text=_text(legal_form.get("other")),
        entity_status=_text(entity.get("status")),
        registration_status=_text(registration.get("status")),
        provenance=Provenance(
            source="GLEIF",
            retrieved_at=retrieved_at,
            snapshot_at=_parse_datetime(registration.get("lastUpdateDate")),
            detail="gleif:lei-records",
        ),
    )


def _parse_datetime(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "CATEGORY_CS",
    "EXCEPTION_REASON_CS",
    "RECORD_PAGE",
    "SUB_CATEGORY_CS",
    "GleifSource",
    "LeiRecord",
    "ParentEntity",
]
