"""Shared fixtures for the source tests: real-shaped register payloads and mock transports.

The GLEIF and OpenFIGI payloads below are trimmed copies of live responses captured on
2026-09-22 (see the comment above each block). They keep the structure the adapters read and
drop the rest. Every client built here runs on ``httpx.MockTransport``, so no test in this
package touches the network.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from typing import Any

import httpx


def payload(document: dict[str, Any]) -> dict[str, Any]:
    """A deep copy, so a test that mutates a payload cannot affect another test."""
    return copy.deepcopy(document)


# -- transport --------------------------------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, *, base_url: str) -> httpx.Client:
    """An ``httpx.Client`` whose transport is ``handler``; no socket is ever opened."""
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=base_url)


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


# -- Wikimedia payloads -----------------------------------------------------------------
# Trimmed copies of live ``www.wikidata.org/w/api.php`` and ``{cs,en}.wikipedia.org`` REST
# answers captured on 2026-09-23 for Deutsche Bank (LEI 7LTWFZYICNSX8D621K86 -> Q66048).
# The P452 claims keep only mainsnak and rank; one deprecated claim is added to prove it is
# dropped.

WIKIDATA_DB_LEI = "7LTWFZYICNSX8D621K86"
WIKIDATA_DB_SEARCH: dict[str, Any] = {
    "batchcomplete": "",
    "query": {"searchinfo": {"totalhits": 1}, "search": [{"ns": 0, "title": "Q66048"}]},
}
WIKIDATA_EMPTY_SEARCH: dict[str, Any] = {
    "batchcomplete": "",
    "query": {"searchinfo": {"totalhits": 0}, "search": []},
}
WIKIDATA_DB_ENTITY: dict[str, Any] = {
    "entities": {
        "Q66048": {
            "type": "item",
            "id": "Q66048",
            "labels": {
                "en": {"language": "en", "value": "Deutsche Bank"},
                "cs": {"language": "cs", "value": "Deutsche Bank"},
            },
            "descriptions": {
                "en": {
                    "language": "en",
                    "value": "German global banking and financial services company",
                },
                "cs": {"language": "cs", "value": "německá banka"},
            },
            "sitelinks": {
                "cswiki": {
                    "site": "cswiki",
                    "title": "Deutsche Bank",
                    "url": "https://cs.wikipedia.org/wiki/Deutsche_Bank",
                },
                "enwiki": {
                    "site": "enwiki",
                    "title": "Deutsche Bank",
                    "url": "https://en.wikipedia.org/wiki/Deutsche_Bank",
                },
            },
        }
    },
    "success": 1,
}


def _industry_claim(qid: str, rank: str = "normal") -> dict[str, Any]:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": "P452",
            "datavalue": {
                "value": {"entity-type": "item", "id": qid},
                "type": "wikibase-entityid",
            },
        },
        "type": "statement",
        "rank": rank,
    }


WIKIDATA_DB_CLAIMS: dict[str, Any] = {
    "claims": {
        "P452": [
            _industry_claim("Q837171"),
            _industry_claim("Q29585689", "preferred"),
            _industry_claim("Q29584334"),
            _industry_claim("Q1", "deprecated"),
        ]
    }
}
WIKIDATA_DB_INDUSTRIES: dict[str, Any] = {
    "entities": {
        "Q837171": {
            "labels": {
                "en": {"language": "en", "value": "financial services"},
                "cs": {"language": "cs", "value": "finanční služba"},
            }
        },
        "Q29585689": {
            "labels": {
                "en": {"language": "en", "value": "other monetary intermediation"},
                "cs": {"language": "cs", "value": "ostatní peněžní zprostředkování"},
            }
        },
        "Q29584334": {
            "labels": {
                "en": {
                    "language": "en",
                    "value": "financial service activities, except insurance and pension funding",
                }
            }
        },
    },
    "success": 1,
}
WIKIPEDIA_DB_CS: dict[str, Any] = {
    "type": "standard",
    "title": "Deutsche Bank",
    "lang": "cs",
    "timestamp": "2023-09-21T15:35:11Z",
    "extract": (
        "Deutsche Bank AG je největší německá banka se sídlem ve Frankfurtu nad Mohanem. "
        "Založena byla v roce 1870.[kdy?]\nSídlí v dvojici mrakodrapů Deutsche-Bank-Hochhaus."
    ),
    "content_urls": {"desktop": {"page": "https://cs.wikipedia.org/wiki/Deutsche_Bank"}},
}
WIKIPEDIA_DB_EN: dict[str, Any] = {
    "type": "standard",
    "title": "Deutsche Bank",
    "lang": "en",
    "timestamp": "2026-09-13T22:12:37Z",
    "extract": (
        "Deutsche Bank AG is a German multinational investment bank and financial services "
        "company headquartered in Frankfurt."
    ),
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Deutsche_Bank"}},
}
WIKIPEDIA_DISAMBIGUATION: dict[str, Any] = {
    "type": "disambiguation",
    "title": "Mercury",
    "extract": "Mercury most commonly refers to: Mercury (planet)",
}


def wikimedia_client(
    *,
    search: dict[str, Any] | None = None,
    entity: dict[str, Any] | None = None,
    claims: dict[str, Any] | None = None,
    industries: dict[str, Any] | None = None,
    summaries: dict[str, dict[str, Any] | int] | None = None,
    wikidata_status: int = 200,
    calls: list[httpx.Request] | None = None,
) -> httpx.Client:
    """Client answering the Wikidata Action API and the Wikipedia summaries.

    Defaults are Deutsche Bank's live answers. ``summaries`` maps a language to a summary
    body or to an HTTP status; a language not in it answers 404. ``wikidata_status`` other
    than 200 makes every Wikidata answer that status.
    """
    summaries = {"cs": WIKIPEDIA_DB_CS, "en": WIKIPEDIA_DB_EN} if summaries is None else summaries

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        host = request.url.host
        if host == "www.wikidata.org":
            if wikidata_status != 200:
                return httpx.Response(wikidata_status, text="boom")
            params = request.url.params
            action = params.get("action")
            if action == "query":
                body = WIKIDATA_DB_SEARCH if search is None else search
            elif action == "wbgetclaims":
                body = WIKIDATA_DB_CLAIMS if claims is None else claims
            elif action == "wbgetentities" and params.get("props") == "labels":
                body = WIKIDATA_DB_INDUSTRIES if industries is None else industries
            elif action == "wbgetentities":
                body = WIKIDATA_DB_ENTITY if entity is None else entity
            else:
                return httpx.Response(400, text="unexpected action")
            return httpx.Response(200, json=payload(body))
        if host.endswith(".wikipedia.org"):
            answer = summaries.get(host.split(".", 1)[0])
            if answer is None:
                return httpx.Response(404, json={"status": 404, "type": "Internal error"})
            if isinstance(answer, int):
                return httpx.Response(answer, text="boom")
            return httpx.Response(200, json=payload(answer))
        return httpx.Response(404, text="unknown host")

    return httpx.Client(transport=httpx.MockTransport(handler))
