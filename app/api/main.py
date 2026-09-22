"""The FastAPI application: the suggester page, its JSON endpoint and the xlsx download.

Design notes worth keeping:

* **The codebooks are loaded once, at startup**, and the consistency check runs there. An
  inconsistent codebook should stop the service coming up, not surface as a wrong CTS ID in
  a report three weeks later. ``CODEBOOK_STRICT=false`` relaxes it for local work.
* **An ISIN is resolved before anything is searched.** GLEIF gives the issuer's legal
  name, country, legal form, entity category and parents, OpenFIGI the instrument. Both
  are public registers; they land in the evidence list, the row and the audit trail as
  ``GLEIF`` / ``OPENFIGI``.
* **No server-side session.** The result page carries its inputs, and the download re-runs
  the same request. That is cheap because the classifier caches, and it means a bookmarked
  or shared URL behaves the same for everybody.
* **Every lookup is audited** through :mod:`core.audit`, which records the identifier, the
  time and the user - never the retrieved content.
* Tool 2's endpoints (``/batch``, ``/lookup``) are not mounted: that tool is parked, and its
  CLI (``python -m core.batch``) still works.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from config.settings import APP_ROOT, Settings, get_settings
from core.audit import current_user, log_lookup
from core.codebooks.loaders import load_and_check
from core.suggest import IssuerSuggestion, SuggestionRequest, SuggestionService, build_service

LOGGER = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=str(APP_ROOT / "ui" / "templates"))

#: Set at startup so every request shares one loaded codebook set and one classifier.
_state: dict[str, object] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load and check the codebooks once, then build the service."""
    settings = get_settings()
    logging.basicConfig(
        level=logging.getLevelName(settings.log_level.strip().upper() or "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    codebooks, report = load_and_check(settings, strict=False)
    LOGGER.info("%s", codebooks.describe())
    if not report.ok:
        # Emitting a CTS ID from an inconsistent codebook is the one failure nobody would
        # catch downstream, so it stops the service rather than degrading it.
        LOGGER.error("codebooks are inconsistent:\n%s", report.summary())
        raise RuntimeError(f"codebooks are inconsistent: {report.summary()}")

    _state["settings"] = settings
    _state["service"] = build_service(settings, codebooks=codebooks)
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


def get_service() -> SuggestionService:
    service = _state.get("service")
    if service is None:  # pragma: no cover - only before startup completes
        raise RuntimeError("the suggestion service is not ready")
    return service  # type: ignore[return-value]


def get_app_settings() -> Settings:
    return _state.get("settings") or get_settings()  # type: ignore[return-value]


ServiceDep = Annotated[SuggestionService, Depends(get_service)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def request_user(http_request: Request | None, settings: Settings) -> str:
    """Who is asking.

    In a server the OS account is the *service* account, so auditing it would record the
    same name for everybody and would not satisfy "log the requesting user". The signed-in
    user comes from a header set by the reverse proxy or SSO in front of this app
    (``WEB_USER_HEADER``); that proxy must strip any client-supplied copy of it, since
    anything a browser can set is not an identity.

    Falls back to :func:`~core.audit.current_user` for the CLI and for local runs, where the
    OS account really is the person.
    """
    header = settings.web_user_header.strip()
    if http_request is not None and header:
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
    """Run one lookup and audit it."""
    identifier = request.isin or request.name or (request.description or "")[:60]
    suggestion = service.suggest(request)
    log_lookup(
        identifier,
        outcome="found" if suggestion.answered else "not_found",
        user=request_user(http_request, settings),
        sources=suggestion.sources,
        detail=None if suggestion.answered else "abstained",
    )
    return suggestion


# -- health ------------------------------------------------------------------------------


@app.get("/health", include_in_schema=False)
def health(settings: SettingsDep) -> JSONResponse:
    """Liveness plus the facts an operator needs: codebook version and model."""
    service = _state.get("service")
    codebook_version = service.codebooks.version.id if service else None  # type: ignore[union-attr]
    return JSONResponse(
        {
            "status": "ok" if service else "starting",
            "codebook_version": codebook_version,
            "model": settings.llm_model if settings.llm_api_key else None,
            "llm_configured": settings.llm_api_key is not None and settings.llm_enabled,
            "search_configured": bool(settings.web_search_url),
            "gleif_enabled": settings.gleif_enabled,
            "openfigi_enabled": settings.openfigi_enabled,
        }
    )


# -- the page ----------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request, settings: SettingsDep) -> HTMLResponse:
    """The empty form."""
    return TEMPLATES.TemplateResponse(
        request=request,
        name="suggest.html",
        context={"suggestion": None, "form": {}, "warnings": _warnings(settings)},
    )


@app.post("/suggest", response_class=HTMLResponse, include_in_schema=False)
def suggest_form(
    request: Request,
    service: ServiceDep,
    settings: SettingsDep,
    isin: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Handle the form. Returns the whole page; htmx swaps the result region."""
    payload = SuggestionRequest(isin=isin, name=name, description=description)
    form = {"isin": isin, "name": name, "description": description}
    if payload.is_empty:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="suggest.html",
            context={
                "suggestion": None,
                "form": form,
                "error": "Vyplňte alespoň jednu položku.",
                "warnings": _warnings(settings),
            },
        )
    suggestion = _run(service, settings, payload, request)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="suggest.html",
        context={"suggestion": suggestion, "form": form, "warnings": _warnings(settings)},
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
            "nace": _classification_json(suggestion.nace),
            "esa": _classification_json(suggestion.esa),
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
    filename = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)[:40] or "navrh"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
    )


# -- helpers -----------------------------------------------------------------------------


def _classification_json(classification: object) -> dict[str, object]:
    return {
        "abstained": classification.abstained,  # type: ignore[attr-defined]
        "reason": classification.abstain_reason,  # type: ignore[attr-defined]
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
            "Deterministický režim: nástroj zúží číselník na kandidáty s jejich CTS ID, "
            "výběr konkrétního kódu je na vás. (Automatický výběr se zapne po nastavení "
            "LLM_API_KEY.)"
        )
    if not settings.web_search_url:
        warnings.append(
            "Vyhledávání na webu není nastaveno (WEB_SEARCH_URL); zadejte popis činnosti ručně."
        )
    if not (settings.gleif_enabled or settings.openfigi_enabled):
        warnings.append(
            "Dohledání emitenta podle ISIN je vypnuto (GLEIF_ENABLED, OPENFIGI_ENABLED); "
            "zadejte název emitenta nebo popis činnosti."
        )
    return warnings
