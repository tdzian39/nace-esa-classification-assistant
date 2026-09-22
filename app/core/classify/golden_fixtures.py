"""Recorded register answers for the golden set: identity without the network, reproducibly.

The golden runner must score what the pipeline scores - the description *plus* the issuer's
register fact sheet (:meth:`~core.sources.identity.IssuerIdentity.fact_sheet`) - so a case with
an ISIN needs the GLEIF and OpenFIGI answers. Asking the live registers on every run would make
the numbers drift (records change, parents are added, rate limits bite) and would put the
network into the tests. So the answers are captured once with the real clients
(``python -m core.classify --golden-capture``), trimmed to the fields the parsers read, dated,
and replayed through ``httpx.MockTransport``. A request with no recorded answer is an error,
never a silent miss: a replay that quietly degraded to "register unavailable" would score a
different fact sheet than the capture saw.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Final

import httpx

from config.settings import APP_ROOT, Settings
from core.classify.golden import GoldenCase, GoldenError
from core.sources.gleif import GleifSource
from core.sources.identity import IssuerIdentifier
from core.sources.openfigi import OpenFigiSource

#: Where the recorded answers live, next to the cases they belong to.
FIXTURES_PATH: Final[Path] = APP_ROOT / "tests" / "golden" / "identity.json"

#: Every key :mod:`core.sources.gleif` and :mod:`core.sources.openfigi` read from a payload.
#: Anything else (links, addresses beyond the country, registration details, ...) is dropped,
#: which keeps the file small and free of data nobody looks at.
KEPT_KEYS: Final[frozenset[str]] = frozenset(
    {
        # GLEIF
        "data",
        "id",
        "attributes",
        "lei",
        "entity",
        "legalName",
        "name",
        "otherNames",
        "legalAddress",
        "headquartersAddress",
        "country",
        "jurisdiction",
        "category",
        "subCategory",
        "legalForm",
        "other",
        "status",
        "registration",
        "lastUpdateDate",
        "reason",
        # OpenFIGI
        "error",
        "warning",
        "figi",
        "ticker",
        "securityType",
        "securityType2",
        "marketSector",
        "exchCode",
    }
)

#: OpenFIGI lists every listing of an instrument (hundreds for a large share); the parser
#: reads the first, so a few are plenty.
MAX_LIST_ITEMS: Final[int] = 3


def request_key(request: httpx.Request) -> str:
    """``GET <url>``, or ``POST <url> <body>`` - what a recorded answer is filed under."""
    key = f"{request.method} {request.url}"
    if request.method == "POST":
        key += " " + request.content.decode("utf-8")
    return key


def trim(payload: object) -> object:
    """Keep only :data:`KEPT_KEYS` (recursively) and the first :data:`MAX_LIST_ITEMS` items."""
    if isinstance(payload, dict):
        return {key: trim(value) for key, value in payload.items() if key in KEPT_KEYS}
    if isinstance(payload, list):
        return [trim(item) for item in payload[:MAX_LIST_ITEMS]]
    return payload


class RecordingTransport(httpx.BaseTransport):
    """Send requests to the real network and remember each trimmed answer."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport or httpx.HTTPTransport()
        self.answers: dict[str, dict[str, object]] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._transport.handle_request(request)
        content = response.read()
        try:
            body: object = trim(json.loads(content)) if content else None
        except ValueError:
            body = None
        self.answers[request_key(request)] = {"status": response.status_code, "body": body}
        return httpx.Response(
            response.status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(body).encode("utf-8") if body is not None else b"",
            request=request,
        )


def replay_transport(answers: dict[str, dict[str, object]]) -> httpx.MockTransport:
    """A transport answering from ``answers``; an unrecorded request raises :class:`GoldenError`."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = request_key(request)
        if key not in answers:
            raise GoldenError(
                f"no recorded register answer for {key}; run "
                "'python -m core.classify --golden-capture' to record it"
            )
        answer = answers[key]
        body = answer.get("body")
        return httpx.Response(
            int(answer["status"]),  # type: ignore[call-overload]
            headers={"content-type": "application/json"},
            content=json.dumps(body).encode("utf-8") if body is not None else b"",
            request=request,
        )

    return httpx.MockTransport(handler)


def identifier_over(
    settings: Settings,
    transport: httpx.BaseTransport,
    *,
    sleep: Callable[[float], None] | None = None,
) -> IssuerIdentifier:
    """The real GLEIF/OpenFIGI adapters, talking through ``transport``.

    The GLEIF client carries the same headers as the one :class:`GleifSource` builds for
    itself; OpenFIGI sets its headers per request. ``sleep`` (default: the adapters' own)
    lets a replay skip the throttle, which only matters against the live registers.
    """
    gleif_client = httpx.Client(
        transport=transport,
        base_url=settings.gleif_base_url,
        timeout=settings.gleif_timeout_seconds,
        headers={"Accept": "application/vnd.api+json", "User-Agent": settings.web_user_agent},
        follow_redirects=True,
    )
    figi_client = httpx.Client(
        transport=transport,
        base_url=settings.openfigi_base_url,
        timeout=settings.openfigi_timeout_seconds,
        follow_redirects=True,
    )
    extra = {"sleep": sleep} if sleep is not None else {}
    return IssuerIdentifier(
        settings,
        gleif=GleifSource(settings, client=gleif_client, **extra),
        openfigi=OpenFigiSource(settings, client=figi_client, **extra),
    )


def capture(
    cases: Iterable[GoldenCase],
    settings: Settings,
    *,
    captured_on: date,
    path: Path | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Ask the live registers about every case with an ISIN and write the answers to ``path``.

    Throttled exactly like production (GLEIF 1 request per second, OpenFIGI one per 2.5 s
    keyless), so about 30 ISINs take a few minutes. Returns the number of recorded answers.
    """
    recorder = RecordingTransport(transport)
    identifier = identifier_over(settings, recorder)
    for case in cases:
        if case.isin:
            identifier.identify(case.isin)
    target = path or FIXTURES_PATH
    document = {
        "_comment": [
            "GLEIF and OpenFIGI answers for the ISINs of tests/golden/cases.json, trimmed to the",
            "fields the parsers read. Replayed by core.classify.golden_fixtures so the golden run",
            "needs no network. Re-record with: python -m core.classify --golden-capture",
        ],
        "captured_on": captured_on.isoformat(),
        "user_agent": settings.web_user_agent,
        "answers": dict(sorted(recorder.answers.items())),
    }
    target.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return len(recorder.answers)


def load_answers(path: Path | None = None) -> dict[str, dict[str, object]]:
    """The recorded answers, or an empty dict when nothing has been captured yet."""
    target = path or FIXTURES_PATH
    if not target.is_file():
        return {}
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GoldenError(f"{target.name} is not valid JSON: {exc}") from exc
    answers = document.get("answers") if isinstance(document, dict) else None
    if not isinstance(answers, dict):
        raise GoldenError(f"{target.name} has no 'answers' object")
    return answers


def replaying_identifier(
    settings: Settings, answers: dict[str, dict[str, object]]
) -> IssuerIdentifier:
    """An identifier that answers from the recording, with the throttle switched off."""
    return identifier_over(settings, replay_transport(answers), sleep=lambda seconds: None)


__all__ = [
    "FIXTURES_PATH",
    "KEPT_KEYS",
    "RecordingTransport",
    "capture",
    "identifier_over",
    "load_answers",
    "replay_transport",
    "replaying_identifier",
    "request_key",
    "trim",
]
