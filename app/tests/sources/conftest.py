"""Shared fixtures for the source tests: real-shaped ARES payloads and a fake DWS driver.

The ARES payloads below are trimmed copies of live responses for IČO 49240901
(Raiffeisenbank a.s.) and 00177041 (Škoda Auto a.s.), captured on 2026-09-22. They keep the
structure that matters - the ``zaznamy`` envelope, both ``czNace`` families, the nested
``statistickeUdaje`` and the ``hodnota``/``datumVymazu`` entry shape - and drop the address
blocks. No test in this package touches the network.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from typing import Any

import httpx
import pytest

from config.settings import Settings

# -- ARES payloads ----------------------------------------------------------------------

RES_PAYLOAD: dict[str, Any] = {
    "icoId": "49240901",
    "zaznamy": [
        {
            "ico": "49240901",
            "obchodniJmeno": "Raiffeisenbank a.s.",
            "pravniForma": "121",
            "datumVzniku": "1993-06-25",
            "datumAktualizace": "2023-06-29",
            "czNace": ["64190"],
            "czNace2008": ["64190"],
            "statistickeUdaje": {
                "institucionalniSektor2010": "12203",
                "kategoriePoctuPracovniku": "460",
            },
            "primarniZaznam": True,
            "czNacePrevazujici": "64190",
            "czNacePrevazujici2008": "64190",
        }
    ],
}

#: A subject whose two revisions genuinely differ (Rev. 2 25500 -> Rev. 2.1 25400) and whose
#: prevailing code changed, so ``nace_mismatch`` is True.
RES_PAYLOAD_MISMATCH: dict[str, Any] = {
    "icoId": "00177041",
    "zaznamy": [
        {
            "ico": "00177041",
            "obchodniJmeno": "Škoda Auto a.s.",
            "datumVzniku": "1991-04-20",
            "datumAktualizace": "2026-06-05",
            "czNace2008": ["29100", "25500", "25610", "45200"],
            "czNacePrevazujici2008": "29100",
            "czNace": ["29200", "25400", "25510"],
            "czNacePrevazujici": "29200",
            "statistickeUdaje": {"institucionalniSektor2010": "11003"},
            "primarniZaznam": True,
        }
    ],
}

VR_PAYLOAD: dict[str, Any] = {
    "icoId": "49240901",
    "datumAktualizace": "2026-09-05",
    "stavSubjektu": "AKTIVNI",
    "zaznamy": [
        {
            "ico": "49240901",
            "datumZapisu": "1993-06-25",
            "primarniZaznam": True,
            "obchodniJmeno": [
                {
                    "hodnota": "Agrobanka Praha, a.s.",
                    "datumZapisu": "1993-06-25",
                    "datumVymazu": "2006-01-01",
                },
                {
                    "hodnota": "Raiffeisenbank a.s.",
                    "datumZapisu": "2006-01-02",
                    "primarniZaznam": True,
                },
            ],
            "cinnosti": {
                "predmetPodnikani": [
                    {"hodnota": "bankovní obchody", "datumZapisu": "1993-06-25"},
                    {
                        "hodnota": "směnárenská činnost",
                        "datumZapisu": "1995-01-01",
                        "datumVymazu": "2010-01-01",
                    },
                ],
                "predmetCinnosti": [
                    {"hodnota": "pronájem nemovitostí", "datumZapisu": "2001-01-01"},
                ],
            },
            "spisovaZnacka": [
                {"datumZapisu": "1993-06-25", "soud": "MSPH", "oddil": "B", "vlozka": "2051"}
            ],
        }
    ],
}


def payload(document: dict[str, Any]) -> dict[str, Any]:
    """A deep copy, so a test that mutates a payload cannot affect another test."""
    return copy.deepcopy(document)


# -- settings ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    """Settings with no DWS configured and ARES throttling disabled (tests must not sleep)."""
    return Settings(
        dws_dsn=None,
        ares_enabled=True,
        ares_base_url="https://ares.test",
        ares_min_interval_seconds=0.0,
        ares_max_attempts=1,
        lookup_user="tester",
    )


# -- ARES transport ---------------------------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, *, base_url: str = "https://ares.test") -> httpx.Client:
    """An ``httpx.Client`` whose transport is ``handler``; no socket is ever opened."""
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=base_url)


def routed_client(
    *,
    res: dict[str, Any] | None = None,
    vr: dict[str, Any] | None = None,
    search: dict[str, Any] | None = None,
    status: int = 200,
    calls: list[str] | None = None,
) -> httpx.Client:
    """Client answering the three ARES endpoints; a ``None`` payload becomes a 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        if "ekonomicke-subjekty-res" in request.url.path:
            body = res
        elif "ekonomicke-subjekty-vr" in request.url.path:
            body = vr
        else:
            body = search
        if body is None:
            return httpx.Response(404, json={"kod": "NENALEZENO"})
        return httpx.Response(status, json=body)

    return make_client(handler)


# -- fake DWS driver --------------------------------------------------------------------


class FakeCursor:
    """Minimal DBAPI cursor: records executed statements, replays canned result sets.

    ``responses`` is the connection's own queue, shared by reference: the adapter opens a
    fresh cursor per statement, so a per-cursor copy would replay the first result set for
    every query and hide ordering mistakes.
    """

    def __init__(self, responses: list[tuple[Sequence[str], Sequence[Sequence[Any]]]]) -> None:
        self._responses = responses
        self._current: tuple[Sequence[str], Sequence[Sequence[Any]]] = ((), ())
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    @property
    def description(self) -> list[tuple[str, None]]:
        return [(name, None) for name in self._current[0]]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        self.executed.append((sql, params))
        self._current = self._responses.pop(0) if self._responses else ((), ())
        return self

    def fetchall(self) -> list[Sequence[Any]]:
        return list(self._current[1])

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    """Minimal DBAPI connection handing out one :class:`FakeCursor` per ``cursor()`` call."""

    def __init__(self, responses: Sequence[tuple[Sequence[str], Sequence[Sequence[Any]]]]) -> None:
        self._responses = list(responses)
        self.cursors: list[FakeCursor] = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self._responses)  # shared queue, not a copy
        self.cursors.append(cursor)
        return cursor

    def close(self) -> None:
        self.closed = True


def dws_rows(
    *,
    res: Sequence[Sequence[Any]] = (),
    or_rows: Sequence[Sequence[Any]] = (),
    nace: Sequence[Sequence[Any]] = (),
    activities: Sequence[Sequence[Any]] = (),
) -> list[tuple[Sequence[str], Sequence[Sequence[Any]]]]:
    """Canned result sets in the order :meth:`DwsSource.fetch_by_ico` issues its queries.

    Order: RES row, OR row, then (only when the corresponding row existed) the RES NACE
    child rows and the OR activity child rows.
    """
    responses: list[tuple[Sequence[str], Sequence[Sequence[Any]]]] = [
        (("ico", "name", "esa_sector", "founded_on", "legal_form", "snapshot_at"), res),
        (
            (
                "ico",
                "obchodni_firma",
                "datum_vzniku",
                "datum_zapisu",
                "spisova_znacka",
                "snapshot_at",
            ),
            or_rows,
        ),
    ]
    if res:
        responses.append((("ico", "code", "revision", "is_main", "label"), nace))
    if or_rows:
        responses.append((("ico", "kind", "text"), activities))
    return responses


# -- GLEIF payloads ---------------------------------------------------------------------
# Trimmed copies of live ``api.gleif.org`` responses captured on 2026-09-22: the record of
# Deutsche Bank AG (issuer of DE0005140008), BMW Finance N.V. with its parent BMW AG, Land
# Berlin (a state government) and the European Investment Bank (an international
# organisation). Only ``attributes`` fields the adapter reads are kept.


def _gleif_item(
    lei: str,
    name: str,
    *,
    country: str,
    jurisdiction: str,
    category: str,
    sub_category: str | None = None,
    legal_form: str | None = None,
    legal_form_text: str | None = None,
    registration: str = "ISSUED",
    other_names: Sequence[str] = (),
    has_parent_link: bool = False,
) -> dict[str, Any]:
    parent_links = (
        {
            "relationship-record": f"https://api.gleif.org/api/v1/lei-records/{lei}/direct-parent-relationship",
            "lei-record": f"https://api.gleif.org/api/v1/lei-records/{lei}/direct-parent",
        }
        if has_parent_link
        else {
            "reporting-exception": f"https://api.gleif.org/api/v1/lei-records/{lei}/direct-parent-reporting-exception"
        }
    )
    return {
        "type": "lei-records",
        "id": lei,
        "attributes": {
            "lei": lei,
            "entity": {
                "legalName": {"name": name, "language": "de"},
                "otherNames": [
                    {"name": other, "type": "TRADING_OR_OPERATING_NAME"} for other in other_names
                ],
                "legalAddress": {"city": "-", "country": country},
                "headquartersAddress": {"city": "-", "country": country},
                "jurisdiction": jurisdiction,
                "category": category,
                "subCategory": sub_category,
                "legalForm": {"id": legal_form, "other": legal_form_text},
                "status": "ACTIVE",
            },
            "registration": {
                "status": registration,
                "lastUpdateDate": "2026-04-07T08:05:10Z",
                "corroborationLevel": "FULLY_CORROBORATED",
            },
        },
        "relationships": {
            "direct-parent": {"links": parent_links},
            "ultimate-parent": {"links": parent_links},
            "isins": {
                "links": {"related": f"https://api.gleif.org/api/v1/lei-records/{lei}/isins"}
            },
        },
        "links": {"self": f"https://api.gleif.org/api/v1/lei-records/{lei}"},
    }


GLEIF_DEUTSCHE_BANK: dict[str, Any] = _gleif_item(
    "7LTWFZYICNSX8D621K86",
    "DEUTSCHE BANK AKTIENGESELLSCHAFT",
    country="DE",
    jurisdiction="DE",
    category="GENERAL",
    legal_form="6QQB",
)
GLEIF_BMW_FINANCE: dict[str, Any] = _gleif_item(
    "5299006ZHG3IXU0PNJ56",
    "BMW Finance N.V.",
    country="NL",
    jurisdiction="NL",
    category="GENERAL",
    legal_form="B5PM",
    has_parent_link=True,
)
GLEIF_BMW_AG: dict[str, Any] = _gleif_item(
    "YEH5ZCD6E441RHVHD759",
    "Bayerische Motoren Werke Aktiengesellschaft",
    country="DE",
    jurisdiction="DE",
    category="GENERAL",
    legal_form="6QQB",
)
GLEIF_LAND_BERLIN: dict[str, Any] = _gleif_item(
    "529900Y6Q7R44JF7XX56",
    "Land Berlin",
    country="DE",
    jurisdiction="DE",
    category="RESIDENT_GOVERNMENT_ENTITY",
    sub_category="STATE_GOVERNMENT",
    legal_form="8888",
    legal_form_text="Legal Entity of Public Law",
)
GLEIF_EIB: dict[str, Any] = _gleif_item(
    "5493006YXS1U5GIHE750",
    "European Investment Bank",
    country="LU",
    jurisdiction="EU",
    category="INTERNATIONAL_ORGANIZATION",
    legal_form="9999",
    legal_form_text="BANK - EIB Statute",
)
#: A fund (category FUND) whose LEI has lapsed - real record, third hit of the EIB name search.
GLEIF_FUND: dict[str, Any] = _gleif_item(
    "254900WLCMY556V12F36",
    "EUROPEAN INVESTMENT BANK & PRIVATE CAPITAL TRUST",
    country="US",
    jurisdiction="US-CT",
    category="FUND",
    legal_form="8888",
    registration="LAPSED",
)

#: The 404 body GLEIF returns for a relation that is not reported.
GLEIF_NOT_FOUND: dict[str, Any] = {
    "errors": [
        {"status": "404", "title": "Resource not found", "detail": "Related resource not found"}
    ]
}


def _gleif_404() -> httpx.Response:
    return httpx.Response(404, json=payload(GLEIF_NOT_FOUND))


def gleif_client(
    *,
    by_isin: dict[str, dict[str, Any]] | None = None,
    records: dict[str, dict[str, Any]] | None = None,
    parents: dict[str, dict[str, Any]] | None = None,
    exceptions: dict[str, str] | None = None,
    status: int = 200,
    calls: list[str] | None = None,
) -> httpx.Client:
    """Client answering the GLEIF endpoints the adapter uses; anything unknown is a 404.

    ``by_isin`` maps ISIN -> record item, ``records`` LEI -> item, ``parents`` maps
    ``"LEI/direct-parent"`` / ``"LEI/ultimate-parent"`` -> the parent's item, ``exceptions``
    LEI -> reporting-exception reason. ``status`` other than 200 makes every answer that
    status (to drive outages). ``calls`` collects the requested URLs.
    """
    by_isin = by_isin or {}
    records = records or {}
    parents = parents or {}
    exceptions = exceptions or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if status != 200:
            return httpx.Response(status, text="boom")
        path = request.url.path
        if path.endswith("/lei-records"):
            item = by_isin.get(request.url.params.get("filter[isin]", ""))
            return httpx.Response(200, json={"data": [payload(item)] if item else []})
        tail = path.split("/lei-records/", 1)[1] if "/lei-records/" in path else ""
        parts = tail.split("/")
        lei = parts[0]
        if len(parts) == 1:
            item = records.get(lei)
            return httpx.Response(200, json={"data": payload(item)}) if item else _gleif_404()
        relation = parts[1]
        if relation in ("direct-parent", "ultimate-parent"):
            item = parents.get(f"{lei}/{relation}")
            return httpx.Response(200, json={"data": payload(item)}) if item else _gleif_404()
        if relation == "direct-parent-reporting-exception":
            reason = exceptions.get(lei)
            if reason is None:
                return _gleif_404()
            return httpx.Response(
                200,
                json={
                    "data": {
                        "type": "reporting-exceptions",
                        "id": f"{lei}|0|x|DIRECT_ACCOUNTING_CONSOLIDATION_PARENT|",
                        "attributes": {
                            "lei": lei,
                            "category": "DIRECT_ACCOUNTING_CONSOLIDATION_PARENT",
                            "reason": reason,
                        },
                    }
                },
            )
        return _gleif_404()

    return make_client(handler, base_url="https://api.gleif.org/api/v1")


# -- OpenFIGI payloads ------------------------------------------------------------------
# Trimmed copies of live ``api.openfigi.com/v3/mapping`` answers captured on 2026-09-22.

FIGI_DEUTSCHE_BANK: list[dict[str, Any]] = [
    {
        "data": [
            {
                "figi": "BBG000BBZTH2",
                "name": "DEUTSCHE BANK AG-REGISTERED",
                "ticker": "DBK",
                "exchCode": "GR",
                "compositeFIGI": "BBG000BBZTH2",
                "securityType": "Common Stock",
                "marketSector": "Equity",
                "shareClassFIGI": "BBG001S683N3",
                "securityType2": "Common Stock",
                "securityDescription": "DBK",
            },
            {
                "figi": "BBG000BBZTV6",
                "name": "DEUTSCHE BANK AG-REGISTERED",
                "ticker": "DBK",
                "exchCode": "GF",
                "compositeFIGI": "BBG000BBZTH2",
                "securityType": "Common Stock",
                "marketSector": "Equity",
                "shareClassFIGI": "BBG001S683N3",
                "securityType2": "Common Stock",
                "securityDescription": "DBK",
            },
        ]
    }
]
#: IE00B4L5Y983 - known to OpenFIGI, absent from GLEIF's ISIN mapping.
FIGI_FUND: list[dict[str, Any]] = [
    {
        "data": [
            {
                "figi": "BBG000P71QK5",
                "name": "ISHARES CORE MSCI WORLD",
                "ticker": "IWDA",
                "exchCode": "NA",
                "compositeFIGI": "BBG000P71PV5",
                "securityType": "ETP",
                "marketSector": "Equity",
                "shareClassFIGI": "BBG001T5K109",
                "securityType2": "Mutual Fund",
                "securityDescription": "IWDA",
            }
        ]
    }
]
#: A well-formed ISIN OpenFIGI has never heard of (FR0129895324, a BMW Finance bond).
FIGI_NOT_FOUND: list[dict[str, Any]] = [{"warning": "No identifier found."}]
#: What a malformed identifier yields (XS0000000001).
FIGI_INVALID: list[dict[str, Any]] = [{"error": "Invalid idValue format."}]


def figi_client(
    body: object,
    *,
    status: int = 200,
    calls: list[httpx.Request] | None = None,
) -> httpx.Client:
    """Client answering ``POST /mapping`` with ``body``; ``calls`` collects the requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if status != 200:
            return httpx.Response(status, text="boom")
        return httpx.Response(200, json=copy.deepcopy(body))

    return make_client(handler, base_url="https://api.openfigi.com/v3")
