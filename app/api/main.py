"""The FastAPI application: the suggester page, its JSON endpoint, the xlsx download and /probe.

Design notes worth keeping:

* **The codebooks are loaded once per process, lazily**: at startup when the server runs
  the lifespan (uvicorn locally, and Vercel, which runs it before the first request), or
  else on the first request that needs them. The consistency check runs there. An
  inconsistent or missing codebook set **never serves a suggestion** - emitting a CTS ID
  from a bad codebook is the failure nobody would catch downstream - but it no longer stops
  the process: suggestion requests answer 503 with the reason while ``/health``, the empty
  form and ``/probe`` keep working, so an operator can see what is wrong. On Vercel a
  startup that raises takes the whole instance down, ``/health`` included (verified against
  the runtime source on 22 Sept 2026). ``python -m core.codebooks --no-strict`` prints the
  full report.
* **On Vercel the codebooks come from a private Blob store** (``CODEBOOK_SOURCE=blob``,
  roadmap D3): the repository is public and they are bank-internal. A failed load is
  remembered for :data:`RETRY_AFTER_SECONDS` rather than retried on every request, which
  would spend Blob operations and bury the logs.
* **An ISIN is resolved before anything is searched.** GLEIF gives the issuer's legal
  name, country, legal form, entity category and parents, OpenFIGI the instrument. Both
  are public registers; they land in the evidence list, the row and the audit trail as
  ``GLEIF`` / ``OPENFIGI``.
* **No server-side session.** The result page carries its inputs, and the download re-runs
  the same request. That is cheap because the classifier caches, and it means a bookmarked
  or shared URL behaves the same for everybody.
* **Every lookup is audited** through :mod:`core.audit`, which records the identifier, the
  time and the user - never the retrieved content.
* **Sign-in is optional and fails closed** (:mod:`core.auth`). With ``APP_PASSWORD_HASH`` set,
  every page but ``/login``, ``/health`` and ``/api/version`` needs the signed session cookie:
  the shared password plus a name, which is the audited user and the one model calls are
  charged to. A hash that cannot be read, or no ``SESSION_SECRET``, means nobody gets in -
  never everybody.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Final
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from config.settings import APP_ROOT, Settings, get_settings
from core.audit import current_user, log_lookup, log_report
from core.auth import (
    SESSION_COOKIE,
    SessionSigner,
    is_password_hash,
    normalize_name,
    verify_password,
)
from core.classify.budget import spending_as
from core.codebooks.errors import CodebookError
from core.codebooks.loaders import load_and_check
from core.reports import (
    ReportStore,
    ReportStoreError,
    build_report,
    build_report_store,
    reports_are_volatile,
)
from core.suggest import IssuerSuggestion, SuggestionRequest, SuggestionService, build_service

LOGGER = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=str(APP_ROOT / "ui" / "templates"))

#: After a failed codebook load, suggestion requests answer 503 at once for this long before
#: the next attempt, so a missing Blob token does not become one download per request.
RETRY_AFTER_SECONDS: Final[float] = 30.0

#: One loaded service per process, shared by every request (tests put theirs here directly).
#: Keys: ``settings``, ``service``, ``codebook_error``, ``codebook_error_at``,
#: ``codebook_loaded_ms``.
_state: dict[str, object] = {}
_load_lock = threading.Lock()

_STARTED_MONOTONIC: Final[float] = time.monotonic()
_STARTED_UTC: Final[datetime] = datetime.now(UTC)


class CodebooksUnavailableError(RuntimeError):
    """No usable codebook set, so no suggestion can be served (HTTP 503 with the reason)."""


def _load_service(settings: Settings) -> SuggestionService:
    """Load and check the codebooks and build the service, once per process.

    Concurrent first requests (Fluid compute runs several in one instance) wait for one load
    instead of starting their own. A failure is remembered, and repeated for
    :data:`RETRY_AFTER_SECONDS` without another attempt.

    Raises:
        CodebooksUnavailableError: the codebooks could not be fetched, read or checked.
    """
    with _load_lock:
        service = _state.get("service")
        if service is not None:
            return service  # type: ignore[return-value]
        failed_at = _state.get("codebook_error_at")
        if isinstance(failed_at, float) and time.monotonic() - failed_at < RETRY_AFTER_SECONDS:
            raise CodebooksUnavailableError(str(_state.get("codebook_error")))
        started = time.perf_counter()
        try:
            codebooks, report = load_and_check(settings, strict=False)
            if not report.ok:
                raise CodebooksUnavailableError(f"codebooks are inconsistent: {report.summary()}")
            service = build_service(settings, codebooks=codebooks)
        except Exception as exc:
            expected = isinstance(exc, CodebookError | CodebooksUnavailableError)
            reason = str(exc) if expected else f"{type(exc).__name__}: {exc}"
            if not expected:
                LOGGER.exception("unexpected failure while loading the codebooks")
            LOGGER.error("codebooks unavailable: %s", reason)
            _state["codebook_error"] = reason
            _state["codebook_error_at"] = time.monotonic()
            raise CodebooksUnavailableError(reason) from exc
        _state["service"] = service
        _state["codebook_loaded_ms"] = round((time.perf_counter() - started) * 1000)
        _state.pop("codebook_error", None)
        _state.pop("codebook_error_at", None)
        LOGGER.info("%s (loaded in %d ms)", codebooks.describe(), _state["codebook_loaded_ms"])
        return service


def _report_store(settings: Settings) -> ReportStore:
    """The error-report store, built once per process; tests put their own in ``_state``."""
    store = _state.get("reports")
    if store is None:
        store = build_report_store(settings)
        _state["reports"] = store
    return store  # type: ignore[return-value]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging and warm the service. Never raises: see the module docstring."""
    settings = get_settings()
    logging.basicConfig(
        level=logging.getLevelName(settings.log_level.strip().upper() or "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _state["settings"] = settings
    # A failure is already logged and remembered; suggestion requests answer 503 with it.
    with suppress(CodebooksUnavailableError):
        _load_service(settings)
    try:
        yield
    finally:
        _state.clear()


app = FastAPI(
    title="ESA a NACE našeptávač",
    description="Návrh položek číselníků NACE a ESA pro zahraniční emitenty (Middle Office).",
    version="0.1.0",
    lifespan=lifespan,
)


def get_app_settings() -> Settings:
    return _state.get("settings") or get_settings()  # type: ignore[return-value]


def get_service() -> SuggestionService:
    """The loaded service; loads it on first use (raises CodebooksUnavailableError)."""
    service = _state.get("service")
    if service is not None:
        return service  # type: ignore[return-value]
    return _load_service(get_app_settings())


ServiceDep = Annotated[SuggestionService, Depends(get_service)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


@app.exception_handler(CodebooksUnavailableError)
async def codebooks_unavailable(
    request: Request, exc: CodebooksUnavailableError
) -> JSONResponse | PlainTextResponse:
    """503 with the reason: JSON for the API, plain text for the download."""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "codebooks unavailable", "reason": str(exc)}, 503)
    return PlainTextResponse(f"Číselníky nejsou k dispozici: {exc}", status_code=503)


def codebook_status(settings: Settings) -> dict[str, object]:
    """What /health and /probe say about the codebooks, without ever loading them."""
    service = _state.get("service")
    error = _state.get("codebook_error")
    return {
        "state": "loaded" if service else ("error" if error else "not_loaded"),
        "source": settings.codebook_source,
        "version": service.codebooks.version.id if service else None,  # type: ignore[union-attr]
        "loaded_ms": _state.get("codebook_loaded_ms") if service else None,
        "error": None if service else error,
    }


# -- sign-in -----------------------------------------------------------------------------

#: Reachable without signing in: the sign-in itself, and what Vercel and an operator poll.
PUBLIC_PATHS: Final[frozenset[str]] = frozenset({"/login", "/logout", "/health", "/api/version"})

#: Hosts a browser reaches over plain HTTP, where a ``Secure`` cookie would never come back.
_PLAIN_HTTP_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "testserver"})


@dataclass(frozen=True, slots=True)
class SignIn:
    """The sign-in configuration: the shared password and how sessions are signed.

    ``problem`` is set when sign-in is configured but nobody can sign in - it is shown on
    the sign-in page, and every protected page stays closed.
    """

    signer: SessionSigner | None = None
    problem: str | None = None

    def user_of(self, request: Request) -> str | None:
        if self.signer is None:
            return None
        return self.signer.verify(request.cookies.get(SESSION_COOKIE))


def sign_in_config(settings: Settings) -> SignIn | None:
    """``None`` when sign-in is off (no ``APP_PASSWORD_HASH``); never raises."""
    if settings.app_password_hash is None:
        return None
    password_hash = settings.app_password_hash.get_secret_value().strip()
    if not is_password_hash(password_hash):
        return SignIn(
            problem="APP_PASSWORD_HASH není hash hesla; vytvořte ho příkazem "
            "python -m core.classify --hash-password."
        )
    if settings.session_secret is None:
        return SignIn(problem="Chybí SESSION_SECRET, přihlášení není možné.")
    signer = SessionSigner(
        secret=settings.session_secret.get_secret_value().encode("utf-8"),
        max_age_seconds=round(settings.session_hours * 3600),
        password_hash=password_hash,
    )
    return SignIn(signer=signer)


def _safe_next(target: str | None) -> str:
    """Where to go after signing in: a path on this site only, never another site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


def _cookie_is_secure(request: Request) -> bool:
    return request.url.hostname not in _PLAIN_HTTP_HOSTS


@app.middleware("http")
async def require_sign_in(request: Request, call_next):  # noqa: ANN001, ANN201 - Starlette's
    """Let a request through only with a valid session, when sign-in is on."""
    config = sign_in_config(get_app_settings())
    if config is None or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    user = config.user_of(request)
    if user is not None:
        request.state.user = user
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "sign-in required"}, status_code=401)
    if request.headers.get("HX-Request"):
        # htmx would swap the sign-in page into the result region; send the browser there.
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    if request.method != "GET":
        return RedirectResponse("/login", status_code=303)
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


def signed_in_user(request: Request | None) -> str | None:
    """The name the session cookie proved, once the middleware has checked it."""
    return getattr(request.state, "user", None) if request is not None else None


def request_user(http_request: Request | None, settings: Settings) -> str:
    """Who is asking.

    With sign-in on, the signed-in name and nothing else: a header is anything a browser
    chooses to send.

    In a server the OS account is the *service* account, so auditing it would record the
    same name for everybody and would not satisfy "log the requesting user". The signed-in
    user comes from a header set by the reverse proxy or SSO in front of this app
    (``WEB_USER_HEADER``); that proxy must strip any client-supplied copy of it, since
    anything a browser can set is not an identity. On Vercel there is no such proxy - it
    passes client headers through - so ``WEB_USER_HEADER`` stays empty there (roadmap E2).

    Falls back to :func:`~core.audit.current_user` for local runs, where the OS account
    really is the person.
    """
    user = signed_in_user(http_request)
    if user is not None:
        return user
    header = settings.web_user_header.strip()
    if http_request is not None and header and settings.app_password_hash is None:
        value = (http_request.headers.get(header) or "").strip()
        if value:
            return value[:120]
    return current_user(settings)


def _run(
    service: SuggestionService,
    settings: Settings,
    request: SuggestionRequest,
    http_request: Request | None = None,
) -> IssuerSuggestion:
    """Run one lookup, audit it, and charge its model calls to whoever asked."""
    identifier = request.isin or request.name or (request.description or "")[:60]
    user = request_user(http_request, settings)
    with spending_as(user):
        suggestion = service.suggest(request)
    log_lookup(
        identifier,
        outcome="found" if suggestion.answered else "not_found",
        user=user,
        sources=suggestion.sources,
        detail=None if suggestion.answered else "abstained",
    )
    return suggestion


# -- health and diagnostics --------------------------------------------------------------


@app.get("/health", include_in_schema=False)
def health(settings: SettingsDep) -> JSONResponse:
    """Liveness plus the facts an operator needs. Never loads the codebooks itself.

    503 only when a codebook load has failed: an instance that has not loaded them yet is
    healthy (Vercel loads them at startup, a local run on the first lookup).
    """
    codebooks = codebook_status(settings)
    return JSONResponse(
        {
            "status": {"loaded": "ok", "not_loaded": "starting"}.get(
                str(codebooks["state"]), "error"
            ),
            "codebook_version": codebooks["version"],
            "codebooks": codebooks,
            "model": settings.llm_model if settings.llm_api_key else None,
            "llm_configured": settings.llm_api_key is not None and settings.llm_enabled,
            "search_configured": bool(settings.web_search_url),
            "wikimedia_enabled": settings.web_enabled and settings.wikimedia_enabled,
            "gleif_enabled": settings.gleif_enabled,
            "openfigi_enabled": settings.openfigi_enabled,
            "python": platform.python_version(),
            "region": os.environ.get("VERCEL_REGION"),
            "vercel_env": os.environ.get("VERCEL_ENV"),
            "commit": (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "")[:12] or None,
            "instance_started": _STARTED_UTC.isoformat(timespec="seconds"),
            "uptime_s": round(time.monotonic() - _STARTED_MONOTONIC),
        },
        status_code=503 if codebooks["state"] == "error" else 200,
    )


#: ``/api/version`` key -> the variable Vercel sets on every deployment of a Git commit.
VERSION_ENV: Final[dict[str, str]] = {
    "commit_sha": "VERCEL_GIT_COMMIT_SHA",
    "commit_ref": "VERCEL_GIT_COMMIT_REF",
    "commit_message": "VERCEL_GIT_COMMIT_MESSAGE",
    "env": "VERCEL_ENV",
    "deployment_url": "VERCEL_URL",
}


@app.get("/api/version")
def version() -> dict[str, str | None]:
    """Which commit this deployment was built from: Vercel's own variables, nothing else.

    No settings, no codebooks, no outbound call, so it answers even when the rest cannot. A
    variable Vercel did not set (a CLI deploy has no Git metadata, a local run has none at
    all) is ``null``.
    """
    return {key: os.environ.get(name) or None for key, name in VERSION_ENV.items()}


@app.get("/probe", include_in_schema=False, response_model=None)
def probe(
    request: Request,
    settings: SettingsDep,
    which: Annotated[str, Query(alias="set")] = "",
    output: Annotated[str, Query(alias="format")] = "html",
) -> HTMLResponse | JSONResponse:
    """Diagnostics: one fixed, harmless request per register, plus the runtime facts.

    ``?set=all`` adds the E5 candidates (FIRDS, Wikidata, Wikipedia); ``?format=json`` returns
    the machine-readable report. Nothing a user types reaches the network.
    """
    if not settings.probe_enabled:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    from core.probe import report

    result = report(settings, extended=which == "all", codebooks=codebook_status(settings))
    if output == "json":
        return JSONResponse(result)
    return TEMPLATES.TemplateResponse(
        request=request, name="probe.html", context={"report": result}
    )


# -- sign-in pages -----------------------------------------------------------------------


def _login_page(
    request: Request,
    *,
    next_path: str,
    name: str = "",
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="login.html",
        context={"next": next_path, "name": name, "error": error},
        status_code=status_code,
    )


@app.get("/login", response_class=HTMLResponse, include_in_schema=False, response_model=None)
def login_form(
    request: Request,
    settings: SettingsDep,
    next: Annotated[str, Query()] = "/",  # noqa: A002 - the conventional parameter name
) -> HTMLResponse | RedirectResponse:
    """The sign-in form; straight on when sign-in is off or the session is still valid."""
    config = sign_in_config(settings)
    target = _safe_next(next)
    if config is None or config.user_of(request) is not None:
        return RedirectResponse(target, status_code=303)
    return _login_page(request, next_path=target, error=config.problem)


@app.post("/login", response_class=HTMLResponse, include_in_schema=False, response_model=None)
def login_submit(
    request: Request,
    settings: SettingsDep,
    name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",  # noqa: A002 - the conventional parameter name
) -> HTMLResponse | RedirectResponse:
    """Check the name and password; on success set the session cookie and go on."""
    config = sign_in_config(settings)
    target = _safe_next(next)
    if config is None:
        return RedirectResponse(target, status_code=303)
    if config.signer is None:
        return _login_page(
            request, next_path=target, name=name, error=config.problem, status_code=503
        )
    user = normalize_name(name)
    if user is None:
        return _login_page(
            request, next_path=target, name=name, error="Zadejte své jméno.", status_code=422
        )
    if not verify_password(password, config.signer.password_hash):
        LOGGER.info("sign-in refused name=%r", user)
        return _login_page(
            request, next_path=target, name=name, error="Nesprávné heslo.", status_code=401
        )
    LOGGER.info("sign-in user=%r", user)
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        config.signer.issue(user),
        max_age=config.signer.max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=_cookie_is_secure(request),
        path="/",
    )
    return response


@app.post("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    """Forget the session in this browser."""
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_is_secure(request),
    )
    return response


# -- the page ----------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request, settings: SettingsDep) -> HTMLResponse:
    """The empty form."""
    return TEMPLATES.TemplateResponse(
        request=request,
        name="suggest.html",
        context={
            "suggestion": None,
            "form": {},
            "warnings": _warnings(settings),
            "user": signed_in_user(request),
        },
    )


@app.post("/suggest", response_class=HTMLResponse, include_in_schema=False)
def suggest_form(
    request: Request,
    settings: SettingsDep,
    isin: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Handle the form. Returns the whole page; htmx swaps the result region.

    Without usable codebooks the page comes back with the reason and the form as typed
    (HTTP 503), rather than a bare error: MO should see why, and not lose the input.
    """
    payload = SuggestionRequest(isin=isin, name=name, description=description)
    form = {"isin": isin, "name": name, "description": description}

    def page_with(error: str, status_code: int = 200) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="suggest.html",
            context={
                "suggestion": None,
                "form": form,
                "error": error,
                "warnings": _warnings(settings),
                "user": signed_in_user(request),
            },
            status_code=status_code,
        )

    if payload.is_empty:
        return page_with("Vyplňte alespoň jednu položku.")
    try:
        service = get_service()
    except CodebooksUnavailableError as exc:
        return page_with(f"Číselníky nejsou k dispozici, návrh teď nelze vytvořit: {exc}", 503)
    suggestion = _run(service, settings, payload, request)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="suggest.html",
        context={
            "suggestion": suggestion,
            "form": form,
            "warnings": _warnings(settings),
            "user": signed_in_user(request),
            "reports_enabled": settings.reports_source != "off",
        },
    )


@app.post("/report", response_class=HTMLResponse, include_in_schema=False)
def report_form(
    request: Request,
    settings: SettingsDep,
    isin: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """MO disagrees with a result, or is not sure of it: store the request with the result.

    The lookup is re-run like the download is (the classifier caches, so the result is the
    one on screen and it costs nothing), the report is written to the configured store, and
    the same page comes back with the result and a line saying whether the report was kept.
    A store failure is said on the page, never hidden - and never a 500.
    """
    payload = SuggestionRequest(isin=isin, name=name, description=description)
    form = {"isin": isin, "name": name, "description": description}

    def page_with(error: str, status_code: int = 200) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="suggest.html",
            context={
                "suggestion": None,
                "form": form,
                "error": error,
                "warnings": _warnings(settings),
                "user": signed_in_user(request),
                "reports_enabled": settings.reports_source != "off",
            },
            status_code=status_code,
        )

    if payload.is_empty:
        return page_with("Hlášení bez dotazu nelze uložit.")
    try:
        service = get_service()
    except CodebooksUnavailableError as exc:
        return page_with(f"Číselníky nejsou k dispozici, návrh teď nelze vytvořit: {exc}", 503)
    suggestion = _run(service, settings, payload, request)
    user = request_user(request, settings)
    report = build_report(
        suggestion,
        note=note,
        user=user,
        max_note_chars=settings.reports_max_note_chars,
        commit=os.environ.get("VERCEL_GIT_COMMIT_SHA") or None,
    )
    store_name = settings.reports_source
    try:
        store = _report_store(settings)
        store_name = store.name
        location = store.save(report)
    except ReportStoreError as exc:
        LOGGER.warning("error report %s not stored: %s", report.id, exc)
        log_report(report.identifier, user=user, stored=False, store=store_name)
        outcome = {"ok": False, "id": report.id, "message": str(exc)}
    else:
        LOGGER.info("error report %s stored at %s", report.id, location)
        log_report(report.identifier, user=user, stored=True, store=store_name)
        outcome = {"ok": True, "id": report.id, "message": ""}
    return TEMPLATES.TemplateResponse(
        request=request,
        name="suggest.html",
        context={
            "suggestion": suggestion,
            "form": form,
            "warnings": _warnings(settings),
            "user": signed_in_user(request),
            "reports_enabled": settings.reports_source != "off",
            "report": outcome,
        },
    )


# -- JSON + download ---------------------------------------------------------------------


@app.post("/api/suggest")
def suggest_json(
    payload: SuggestionRequest,
    service: ServiceDep,
    settings: SettingsDep,
    request: Request = None,  # noqa: B008 - FastAPI injects it
) -> JSONResponse:
    """The same lookup as JSON, for another system or a script."""
    from core.export.columns import json_row, suggestion_row

    if payload.is_empty:
        return JSONResponse({"detail": "give at least one of isin, name, description"}, 422)
    suggestion = _run(service, settings, payload, request)
    return JSONResponse(
        {
            "answered": suggestion.answered,
            "issuer_name": suggestion.issuer_name,
            "description": suggestion.description,
            "identity": _identity_json(suggestion.identity),
            "row": json_row(suggestion_row(suggestion)),
            "nace": _classification_json(suggestion.nace, suggestion.nace_proposal),
            "esa": _classification_json(suggestion.esa, suggestion.esa_proposal),
            "notes": list(suggestion.all_notes),
        }
    )


@app.get("/suggest.xlsx", include_in_schema=False)
def suggest_xlsx(
    request: Request,
    service: ServiceDep,
    settings: SettingsDep,
    isin: Annotated[str, Query()] = "",
    name: Annotated[str, Query()] = "",
    description: Annotated[str, Query()] = "",
) -> StreamingResponse:
    """Download the result as xlsx.

    The lookup is re-run rather than held in a session: the classifier caches, so this costs
    nothing, and a link stays valid for whoever opens it.
    """
    import io

    from openpyxl import load_workbook

    from core.export.columns import SUGGESTION_COLUMNS, suggestion_row
    from core.export.xlsx import write_workbook

    payload = SuggestionRequest(isin=isin, name=name, description=description)
    suggestion = _run(service, settings, payload, request) if not payload.is_empty else None
    rows = [suggestion_row(suggestion)] if suggestion else []

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "navrh.xlsx"
        write_workbook(
            path,
            SUGGESTION_COLUMNS,
            rows,
            run_metadata={
                "nástroj": "ESA a NACE našeptávač",
                "vytvořeno (UTC)": (
                    suggestion.created_at.replace(tzinfo=None) if suggestion else None
                ),
                "uživatel": request_user(request, settings),
                "verze číselníku": suggestion.codebook_version if suggestion else None,
                "model": suggestion.nace.model if suggestion else None,
            },
        )
        data = path.read_bytes()
        # Read back rather than trusting the writer: a corrupt download is worse than an error.
        load_workbook(io.BytesIO(data)).close()

    stem = (suggestion.issuer_name if suggestion else None) or "navrh"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _attachment(stem)},
    )


def _attachment(stem: str) -> str:
    """``Content-Disposition`` for ``<stem>.xlsx`` that survives any issuer name.

    HTTP headers are latin-1, so a raw "Česká spořitelna" made the download fail with a 500.
    The plain ``filename`` gets an ASCII copy; ``filename*`` (RFC 6266 / RFC 5987) carries the
    real name, which every current browser prefers.
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)[:40] or "navrh"
    ascii_only = "".join(ch if ch.isascii() else "_" for ch in safe)
    return f"attachment; filename=\"{ascii_only}.xlsx\"; filename*=UTF-8''{quote(safe)}.xlsx"


# -- helpers -----------------------------------------------------------------------------


def _classification_json(classification: object, proposal: object = None) -> dict[str, object]:
    """The model's answer, plus the proposal: the model's pick or, when it abstained, a rule's."""
    return {
        "abstained": classification.abstained,  # type: ignore[attr-defined]
        "reason": classification.abstain_reason,  # type: ignore[attr-defined]
        "proposal": (
            {
                "code": proposal.code,  # type: ignore[attr-defined]
                "cts_id": proposal.cts_id,  # type: ignore[attr-defined]
                "label": proposal.label,  # type: ignore[attr-defined]
                "basis": proposal.basis,  # type: ignore[attr-defined]
                "confidence": proposal.confidence,  # type: ignore[attr-defined]
                "justification": proposal.justification,  # type: ignore[attr-defined]
                "tied": list(proposal.tied),  # type: ignore[attr-defined]
            }
            if proposal is not None
            else None
        ),
        "suggestions": [
            {
                "code": item.code,
                "cts_id": item.cts_id,
                "label": item.label,
                "confidence": item.confidence,
                "justification": item.justification,
                "rank": item.rank,
            }
            for item in classification.suggestions  # type: ignore[attr-defined]
        ],
    }


def _identity_json(identity: object) -> dict[str, object]:
    """What the registers said about the ISIN, for a script that wants the facts, not prose."""
    record = identity.lei_record  # type: ignore[attr-defined]
    instrument = identity.instrument  # type: ignore[attr-defined]
    return {
        "isin": identity.isin,  # type: ignore[attr-defined]
        "lei": identity.lei,  # type: ignore[attr-defined]
        "legal_name": identity.legal_name,  # type: ignore[attr-defined]
        "country": identity.country,  # type: ignore[attr-defined]
        "category": record.category if record else None,
        "sub_category": record.sub_category if record else None,
        "legal_form": record.legal_form_id if record else None,
        "ultimate_parent": (
            {
                "lei": record.ultimate_parent.lei,
                "name": record.ultimate_parent.legal_name,
                "country": record.ultimate_parent.country,
            }
            if record and record.ultimate_parent
            else None
        ),
        "instrument": (
            {
                "name": instrument.name,
                "security_type": instrument.security_type,
                "market_sector": instrument.market_sector,
            }
            if instrument
            else None
        ),
        "sources": list(identity.sources),  # type: ignore[attr-defined]
        "facts": list(identity.facts()),  # type: ignore[attr-defined]
    }


def _warnings(settings: Settings) -> list[str]:
    """What is not configured, said once at the top of the page rather than per result."""
    warnings: list[str] = []
    if not (settings.llm_enabled and settings.llm_api_key):
        # Deliberately phrased as a mode, not a fault: running without the model is the
        # current intended state, and the narrowed codebook is a usable result on its own.
        warnings.append(
            "Deterministický režim: nástroj zúží číselník na kandidáty s jejich CTS ID a kód "
            "navrhne jen tam, kde rozhodlo pravidlo (kategorie v GLEIF nebo klíčové slovo); "
            "ověření a výběr jsou na vás. (Model se zapne po nastavení LLM_API_KEY.)"
        )
    wikimedia = settings.web_enabled and settings.wikimedia_enabled
    if not settings.web_search_url and wikimedia:
        warnings.append(
            "Popis činnosti se dohledá na Wikipedii podle LEI z registru GLEIF, tedy jen pro "
            "emitenta zadaného ISIN; u ostatních (a kde Wikipedie nic nemá) jej zadejte ručně."
        )
    elif not settings.web_search_url:
        warnings.append(
            "Vyhledávání na webu není nastaveno (WEB_SEARCH_URL); zadejte popis činnosti ručně."
        )
    if not (settings.gleif_enabled or settings.openfigi_enabled):
        warnings.append(
            "Dohledání emitenta podle ISIN je vypnuto (GLEIF_ENABLED, OPENFIGI_ENABLED); "
            "zadejte název emitenta nebo popis činnosti."
        )
    if reports_are_volatile(settings):
        warnings.append(
            "Hlášení chyb se ukládají do dočasného adresáře, který Vercel po chvíli zahodí "
            "(REPORTS_SOURCE=dir); pro trvalé uložení nastavte REPORTS_SOURCE=blob."
        )
    return warnings
