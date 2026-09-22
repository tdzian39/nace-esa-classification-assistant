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
