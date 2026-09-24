"""The ``/probe`` diagnostics page: can this deployment reach the public registers, and if not, why.

Ported from the design source (``jaeksrampota/esa-nace-naseptavac``, ``src/main/probe.py``,
specified in its ``docs/egress-endpoints.md`` section 6) and adapted to Vercel, where outbound
traffic is open. The question is no longer "what does the corporate proxy allow" but "does this
deployment work, and which instance answered": the page is the first thing to open when a
register "stops working" or a new deployment misbehaves.

* Two fixed host lists: the sources the tool uses today (GLEIF, OpenFIGI, and Wikidata plus
  Wikipedia in ``WIKIPEDIA_LANGUAGES`` while ``WIKIMEDIA_ENABLED`` - the default) and, with
  ``?set=all``, the ones roadmap E5 may add (ESMA FIRDS; Wikimedia too when switched off).
  One harmless request per host, a 5-second timeout, no retry, one after another. The only
  inputs are switches between fixed alternatives, so nothing a user types reaches the network.
* Nothing runs at import time and nothing is cached, and ``/health`` never calls into this
  module: a register outage must never fail a health check or a deployment.
* The report names settings and environment variables but shows values only where they are
  not secret (a token is reported as present or absent, a proxy without its credentials).
* The status taxonomy tells the failure modes apart - ``timeout`` vs ``dns`` vs ``tls`` vs
  ``blocked`` vs a host that answered with an error or with something unexpected - because
  each one has a different fix. The proxy statuses stay for local runs behind a corporate proxy
  (httpx honours ``HTTPS_PROXY``); on Vercel they do not occur.
"""

from __future__ import annotations

import os
import platform
import ssl
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from config.settings import Settings

#: One request per host is allowed this long, connect and read alike.
TIMEOUT_SECONDS: Final[float] = 5.0
#: Deutsche Bank AG - the LEI record verified live on 17 and 22 Sept 2026.
LEI: Final[str] = "7LTWFZYICNSX8D621K86"
#: Its registered share, known to GLEIF, OpenFIGI and FIRDS.
ISIN: Final[str] = "DE0005140008"
WIKIPEDIA_TITLE: Final[str] = "Deutsche_Bank"

FIRDS_URL: Final[str] = "https://registers.esma.europa.eu/solr/esma_registers_firds/select"
WIKIDATA_URL: Final[str] = "https://www.wikidata.org"
WIKIPEDIA_URL: Final[str] = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"

Status = Literal[
    "ok",
    "unexpected_body",
    "http_error",
    "proxy_auth",
    "tls",
    "proxy_error",
    "dns",
    "timeout",
    "blocked",
    "error",
]

#: What each status means, in the page's language.
STATUS_CS: Final[Mapping[Status, str]] = {
    "ok": "dostupné, odpověď v pořádku",
    "unexpected_body": "odpověděl někdo jiný nebo něco jiného, než se čekalo",
    "http_error": "host odpověděl chybou HTTP",
    "proxy_auth": "proxy vyžaduje ověření (HTTP 407)",
    "tls": "chyba TLS (certifikát)",
    "proxy_error": "nastavená proxy je nedostupná",
    "dns": "název hosta se nepřeloží (DNS)",
    "timeout": f"bez odpovědi do {TIMEOUT_SECONDS:g} s",
    "blocked": "spojení odmítnuto nebo blokováno",
    "error": "jiná chyba",
}

#: Library versions worth seeing when a deployment behaves differently from a laptop.
LIBRARIES: Final[tuple[str, ...]] = (
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic-settings",
    "httpx",
    "openpyxl",
    "jinja2",
)

_DNS_MARKERS: Final[tuple[str, ...]] = (
    "getaddrinfo",
    "Name or service not known",
    "nodename nor servname",
    "Temporary failure in name resolution",
    "No address associated",
    "Errno 11001",
    "Errno -2",
    "Errno -3",
)
_TLS_MARKERS: Final[tuple[str, ...]] = ("CERTIFICATE_VERIFY_FAILED", "SSL", "TLS")


@dataclass(frozen=True, slots=True)
class Check:
    """One fixed probe: a request and the test its answer must pass."""

    key: str
    host: str
    label: str
    method: str
    url: str
    expect: Callable[[object], str | None]
    in_use: bool = True
    headers: Mapping[str, str] = field(default_factory=dict)
    json_body: object = None
    then: Callable[[object], Check | None] | None = None


@dataclass(frozen=True, slots=True)
class HostResult:
    """The outcome of one check (and of its follow-up, when it has one)."""

    key: str
    host: str
    label: str
    in_use: bool
    method: str
    url: str
    status: Status
    http_status: int | None
    ms: int
    detail: str
    steps: tuple[Mapping[str, object], ...] = ()

    @property
    def status_cs(self) -> str:
        return STATUS_CS[self.status]

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "host": self.host,
            "label": self.label,
            "in_use": self.in_use,
            "method": self.method,
            "url": self.url,
            "status": self.status,
            "status_cs": self.status_cs,
            "http_status": self.http_status,
            "ms": self.ms,
            "detail": self.detail,
            "steps": [dict(step) for step in self.steps],
        }


# -- what a good answer looks like ---------------------------------------------------------


def _expect_gleif(body: object) -> str | None:
    data = body.get("data") if isinstance(body, dict) else None
    attributes = data.get("attributes") if isinstance(data, dict) else None
    entity = attributes.get("entity") if isinstance(attributes, dict) else None
    legal_name = entity.get("legalName") if isinstance(entity, dict) else None
    name = legal_name.get("name") if isinstance(legal_name, dict) else None
    return f"legalName: {name}" if name else None


def _expect_openfigi(body: object) -> str | None:
    if not isinstance(body, list) or not body or not isinstance(body[0], dict):
        return None
    first = body[0]
    if isinstance(first.get("data"), list):
        records = first["data"]
        name = str(records[0].get("name", "")) if records and isinstance(records[0], dict) else ""
        return f"data: {len(records)} záznamů" + (f" – {name}" if name else "")
    if "warning" in first:
        return f"warning: {first['warning']}"
    return None


def _expect_firds(body: object) -> str | None:
    response = body.get("response") if isinstance(body, dict) else None
    found = response.get("numFound") if isinstance(response, dict) else None
    return f"numFound: {found}" if isinstance(found, int) else None


def _expect_wikidata_search(body: object) -> str | None:
    query = body.get("query") if isinstance(body, dict) else None
    hits = query.get("search") if isinstance(query, dict) else None
    if not isinstance(hits, list):
        return None
    first = f" – {hits[0].get('title', '')}" if hits and isinstance(hits[0], dict) else ""
    return f"search: {len(hits)} výsledků{first}"


def _expect_wikidata_entity(body: object) -> str | None:
    entities = body.get("entities") if isinstance(body, dict) else None
    if not isinstance(entities, dict):
        return None
    for qid, item in entities.items():
        labels = item.get("labels") if isinstance(item, dict) else None
        english = labels.get("en") if isinstance(labels, dict) else None
        label = english.get("value") if isinstance(english, dict) else None
        return f"{qid}: {label or '(bez anglického štítku)'}"
    return None


def _expect_wikipedia(body: object) -> str | None:
    extract = body.get("extract") if isinstance(body, dict) else None
    if not isinstance(extract, str) or not extract:
        return None
    return "extract: " + extract[:70] + ("…" if len(extract) > 70 else "")


def _wikidata_entity_step(
    body: object, headers: Mapping[str, str], *, in_use: bool
) -> Check | None:
    """After the LEI search: the entity record of the first hit, or ``None`` if nothing came."""
    query = body.get("query") if isinstance(body, dict) else None
    hits = query.get("search") if isinstance(query, dict) else None
    title = str(hits[0].get("title", "")) if isinstance(hits, list) and hits else ""
    if not title.startswith("Q"):
        return None
    return Check(
        key="wikidata_entity",
        host="www.wikidata.org",
        label="Wikidata – položka",
        method="GET",
        # The same small call the adapter makes; Special:EntityData is the whole item (443 KB).
        url=(
            f"{WIKIDATA_URL}/w/api.php?action=wbgetentities&ids={title}"
            "&props=labels|descriptions|sitelinks/urls&languages=cs|en&format=json"
        ),
        expect=_expect_wikidata_entity,
        in_use=in_use,
        headers=headers,
    )


# -- the fixed list --------------------------------------------------------------------------


def checks(settings: Settings, *, extended: bool = False) -> tuple[Check, ...]:
    """The sources in use (GLEIF, OpenFIGI, Wikimedia when on), plus the rest when ``extended``."""
    agent = {"User-Agent": settings.web_user_agent}
    figi_headers = {**agent, "Content-Type": "application/json", "Accept": "application/json"}
    if settings.openfigi_api_key:
        figi_headers["X-OPENFIGI-APIKEY"] = settings.openfigi_api_key.get_secret_value()
    items = [
        Check(
            key="gleif",
            host=urlsplit(settings.gleif_base_url).hostname or "api.gleif.org",
            label="GLEIF LEI API",
            method="GET",
            url=f"{settings.gleif_base_url.rstrip('/')}/lei-records/{LEI}",
            expect=_expect_gleif,
            headers={**agent, "Accept": "application/vnd.api+json"},
        ),
        Check(
            key="openfigi",
            host=urlsplit(settings.openfigi_base_url).hostname or "api.openfigi.com",
            label="OpenFIGI",
            method="POST",
            url=f"{settings.openfigi_base_url.rstrip('/')}/mapping",
            expect=_expect_openfigi,
            headers=figi_headers,
            json_body=[{"idType": "ID_ISIN", "idValue": ISIN}],
        ),
    ]
    wikimedia_in_use = settings.wikimedia_enabled and settings.web_enabled
    languages = [
        lang.strip().lower() for lang in settings.wikipedia_languages.split(",") if lang.strip()
    ] or ["cs", "en"]
    wikimedia = [
        Check(
            key="wikidata",
            host="www.wikidata.org",
            label="Wikidata – hledání podle LEI",
            method="GET",
            url=(
                f"{WIKIDATA_URL}/w/api.php?action=query&list=search"
                f"&srsearch=haswbstatement:P1278={LEI}&format=json"
            ),
            expect=_expect_wikidata_search,
            in_use=wikimedia_in_use,
            headers=agent,
            then=lambda body: _wikidata_entity_step(body, agent, in_use=wikimedia_in_use),
        ),
        *(
            Check(
                key=f"wikipedia_{lang}",
                host=f"{lang}.wikipedia.org",
                label=f"Wikipedia ({lang.upper()}) – souhrn článku",
                method="GET",
                url=WIKIPEDIA_URL.format(lang=lang, title=WIKIPEDIA_TITLE),
                expect=_expect_wikipedia,
                in_use=wikimedia_in_use,
                headers=agent,
            )
            for lang in languages
        ),
    ]
    if wikimedia_in_use:
        items += wikimedia
    if extended:
        items.append(
            Check(
                key="firds",
                host="registers.esma.europa.eu",
                label="ESMA FIRDS",
                method="GET",
                url=f"{FIRDS_URL}?q=isin:{ISIN}&wt=json&rows=1",
                expect=_expect_firds,
                in_use=False,
                headers=agent,
            )
        )
        if not wikimedia_in_use:
            items += wikimedia
    return tuple(items)


# -- running them ----------------------------------------------------------------------------


def _first_line(text: str, limit: int = 200) -> str:
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    return line[:limit]


def _classify_connect_error(exc: httpx.HTTPError) -> Status:
    """``tls``, ``dns`` or ``blocked`` for a failed connection, from the error chain."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < 6:
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, ssl.SSLError) for item in chain):
        return "tls"
    text = " ".join(str(item) for item in chain)
    if any(marker in text for marker in _TLS_MARKERS):
        return "tls"
    if any(marker in text for marker in _DNS_MARKERS):
        return "dns"
    return "blocked"


def _json_or_none(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def run_check(client: httpx.Client, check: Check) -> HostResult:
    """One bounded request; never raises."""
    started = time.perf_counter()
    http_status: int | None = None
    body: object = None
    try:
        response = client.request(
            check.method,
            check.url,
            headers=dict(check.headers),
            json=check.json_body,
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.ProxyError as exc:
        status, detail = "proxy_error", _first_line(str(exc))
    except httpx.TimeoutException as exc:
        status, detail = "timeout", _first_line(str(exc)) or type(exc).__name__
    except (httpx.ConnectError, httpx.NetworkError) as exc:
        status, detail = _classify_connect_error(exc), _first_line(str(exc))
    except httpx.HTTPError as exc:
        status, detail = "error", _first_line(f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - a diagnostics page must render whatever happens
        status, detail = "error", _first_line(f"{type(exc).__name__}: {exc}")
    else:
        http_status = response.status_code
        body = _json_or_none(response)
        if http_status == 407:
            status, detail = "proxy_auth", _first_line(response.text)
        elif 200 <= http_status < 300:
            confirmed = check.expect(body) if body is not None else None
            if confirmed:
                status, detail = "ok", confirmed
            else:
                status = "unexpected_body"
                detail = _first_line(response.text) or "(prázdná odpověď)"
        else:
            status, detail = "http_error", _first_line(response.text)
    result = HostResult(
        key=check.key,
        host=check.host,
        label=check.label,
        in_use=check.in_use,
        method=check.method,
        url=check.url,
        status=status,  # type: ignore[arg-type]
        http_status=http_status,
        ms=round((time.perf_counter() - started) * 1000),
        detail=detail,
    )
    follow_up = check.then(body) if status == "ok" and check.then is not None else None
    if follow_up is None:
        return result
    second = run_check(client, follow_up)
    return HostResult(
        key=result.key,
        host=result.host,
        label=result.label,
        in_use=result.in_use,
        method=result.method,
        url=result.url,
        status=second.status,
        http_status=second.http_status,
        ms=result.ms + second.ms,
        detail=f"{result.detail} → {second.detail}",
        steps=(
            {
                "label": result.label,
                "status": result.status,
                "http_status": result.http_status,
                "ms": result.ms,
                "detail": result.detail,
            },
            {
                "label": second.label,
                "status": second.status,
                "http_status": second.http_status,
                "ms": second.ms,
                "detail": second.detail,
            },
        ),
    )


# -- the surroundings ------------------------------------------------------------------------


def _redact_url(value: str) -> str:
    """A proxy URL without its credentials."""
    parts = urlsplit(value)
    if parts.username or parts.password:
        host = parts.hostname or ""
        netloc = f"***@{host}" + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return value


def configuration(settings: Settings) -> list[dict[str, object]]:
    """The settings that decide how a lookup behaves; secrets as present/absent only."""

    def secret(name: str, value: object) -> dict[str, object]:
        return {"name": name, "present": bool(value), "value": "***" if value else None}

    def plain(name: str, value: object) -> dict[str, object]:
        shown = value if value not in (None, "") else None
        return {"name": name, "present": shown is not None, "value": shown}

    rows = [
        plain("CODEBOOK_SOURCE", settings.codebook_source),
        secret("BLOB_READ_WRITE_TOKEN", settings.blob_read_write_token),
        plain("BLOB_STORE_ID", settings.blob_store_id),
        plain("GLEIF_ENABLED", settings.gleif_enabled),
        plain("OPENFIGI_ENABLED", settings.openfigi_enabled),
        secret("OPENFIGI_API_KEY", settings.openfigi_api_key),
        plain("GLEIF_TIMEOUT_SECONDS", settings.gleif_timeout_seconds),
        plain("OPENFIGI_TIMEOUT_SECONDS", settings.openfigi_timeout_seconds),
        secret("WEB_SEARCH_URL", settings.web_search_url),
        plain("LLM_ENABLED", settings.llm_enabled),
        secret("LLM_API_KEY", settings.llm_api_key),
        plain("WEB_USER_HEADER", settings.web_user_header),
        plain("PROBE_ENABLED", settings.probe_enabled),
    ]
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
        value = os.environ.get(name) or os.environ.get(name.lower()) or ""
        rows.append(plain(name, _redact_url(value) if value and name != "NO_PROXY" else value))
    return rows


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "?"


def runtime(settings: Settings) -> dict[str, object]:
    """What answered: interpreter, libraries, and on Vercel the deployment and the region."""
    from config import settings as settings_module

    return {
        "python": platform.python_version(),
        "implementation": sys.implementation.name,
        "system": f"{platform.system()} {platform.release()}",
        "vercel": bool(os.environ.get("VERCEL")),
        "vercel_env": os.environ.get("VERCEL_ENV"),
        "region": os.environ.get("VERCEL_REGION"),
        "deployment_id": os.environ.get("VERCEL_DEPLOYMENT_ID"),
        "commit": (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "")[:12] or None,
        "commit_ref": os.environ.get("VERCEL_GIT_COMMIT_REF"),
        "entrypoint": os.environ.get("__VC_HANDLER_ENTRYPOINT"),
        # Proves the source copy of the app was imported (templates and paths depend on it).
        "settings_module": settings_module.__file__,
        "user_agent": settings.web_user_agent,
        "libraries": {name: _version(name) for name in LIBRARIES},
    }


def report(
    settings: Settings,
    *,
    extended: bool = False,
    client: httpx.Client | None = None,
    codebooks: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Run the probes now and return the whole report as JSON-safe dicts and lists."""
    owned = client is None
    http = client or httpx.Client(follow_redirects=False)
    try:
        started = time.perf_counter()
        hosts = [run_check(http, check) for check in checks(settings, extended=extended)]
    finally:
        if owned:
            http.close()
    in_use = [host for host in hosts if host.in_use]
    return {
        "set": "all" if extended else "in_use",
        "ok": all(host.status == "ok" for host in in_use),
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "timeout_s": TIMEOUT_SECONDS,
        "total_ms": round((time.perf_counter() - started) * 1000),
        "hosts": [host.as_dict() for host in hosts],
        "codebooks": dict(codebooks) if codebooks is not None else None,
        "configuration": configuration(settings),
        "runtime": runtime(settings),
    }
