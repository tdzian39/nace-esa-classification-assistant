"""Error reports: what MO asked, what the tool answered, and why they think it is wrong.

A reviewer who disagrees with a result, or is not sure of it, presses one button on the page
and may add a short note. The tool then records the **request together with the result it
gave** - the same row the xlsx download carries - so whoever maintains the codebook rules,
the keyword table or the prompt can replay the case exactly. Nothing else captures that:
the audit log deliberately holds no content (``core.audit``), and the model cache holds
answers, not complaints.

Where a report goes is a deployment question, like the codebooks (roadmap D3/D4):

* ``REPORTS_SOURCE=dir`` (the default, for a laptop or Docker with a volume) writes one JSON
  file per report under ``REPORTS_DIR``, ``<date>/<id>.json``, atomically.
* ``REPORTS_SOURCE=blob`` (the Vercel setting) uploads the same JSON to the private Blob
  store the codebooks come from, under ``REPORTS_BLOB_PREFIX``. On Vercel nothing but
  ``/tmp`` is writable and ``/tmp`` does not outlive the instance, so a directory there
  would lose every report - which is why the page warns when it sees that combination.
  The upload is the ``@vercel/blob`` ``put`` call re-done over plain httpx (checked against
  the SDK source on 24 Sept 2026): ``PUT {REPORTS_BLOB_API_URL}/?pathname=<pathname>`` with
  ``authorization: Bearer <token>``, ``x-api-version: 12``, ``x-vercel-blob-access: private``,
  ``x-content-type``, ``x-add-random-suffix: 0``, ``x-allow-overwrite: 0``. Reading the
  reports back is the Vercel dashboard or ``vercel blob`` - listing is a metered call the
  app never makes.
* ``REPORTS_SOURCE=db`` keeps them in the central database (:mod:`core.db`, roadmap D4) -
  the default whenever ``DATABASE_URL`` is set (``auto``).
* ``REPORTS_SOURCE=off`` hides the button.

A report is content - issuer names, descriptions, codes, MO's own words - so it is treated
like the codebooks: never in the repository (``app/data/reports/`` is git-ignored), never in
a log line. The audit log records only that a report was made, by whom, and whether it was
stored (:func:`core.audit.log_report`).

``python -m core.reports --list [--dir PATH]`` prints the reports of the configured store
(the database when ``DATABASE_URL`` is set, else the directory); ``--xlsx PATH`` writes them
as a workbook for whoever reviews them.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol

import httpx

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from config.settings import Settings
    from core.suggest import IssuerSuggestion

LOGGER = logging.getLogger(__name__)

#: The ``x-api-version`` the Blob API expects; ``BLOB_API_VERSION`` in the SDK.
BLOB_API_VERSION: Final[str] = "12"
#: What a report file is called; the id is a UUID4 without dashes.
FILE_NAME: Final[str] = "{id}.json"
#: The columns of the review workbook (``--xlsx``), in order.
REVIEW_COLUMNS: Final[tuple[str, ...]] = (
    "created_at (UTC)",
    "user",
    "note",
    "IN_isin",
    "IN_name",
    "IN_description",
    "issuer_name",
    "NACE_code",
    "NACE_cts_id",
    "NACE_label",
    "NACE_confidence",
    "ESA_code",
    "ESA_cts_id",
    "ESA_label",
    "ESA_confidence",
    "codebook_version",
    "model",
    "prompt_version",
    "commit",
    "id",
)


class ReportStoreError(Exception):
    """The report could not be stored. The page says so; the lookup itself was fine."""


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """One report: the request, the result row as MO saw it, and MO's note.

    Attributes:
        id: UUID4 hex, also the file name.
        created_at: When the report was made (UTC).
        user: Who made it (the signed-in name, or the audit user).
        note: MO's optional words, already trimmed and capped.
        request: ``isin``, ``name``, ``description`` as asked.
        result: The output row (:func:`core.export.columns.json_row`), JSON-ready.
        answered: Whether the model answered both codebooks.
        notes: The lookup's notes, so an abstention's reason travels with the report.
        app: Codebook version, model, prompt version and the deployed commit.
    """

    id: str
    created_at: datetime
    user: str
    note: str
    request: dict[str, str | None]
    result: dict[str, Any]
    answered: bool
    notes: tuple[str, ...] = ()
    app: dict[str, str | None] = field(default_factory=dict)

    @property
    def identifier(self) -> str:
        """What was asked, for the audit line: ISIN, else name, else the description's start."""
        return (
            self.request.get("isin")
            or self.request.get("name")
            or (self.request.get("description") or "")[:60]
            or "-"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "error_report",
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "user": self.user,
            "note": self.note,
            "request": dict(self.request),
            "result": dict(self.result),
            "answered": self.answered,
            "notes": list(self.notes),
            "app": dict(self.app),
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=1)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ErrorReport:
        created = data.get("created_at")
        return cls(
            id=str(data.get("id") or ""),
            created_at=(
                datetime.fromisoformat(created)
                if isinstance(created, str) and created
                else datetime.now(UTC)
            ),
            user=str(data.get("user") or "unknown"),
            note=str(data.get("note") or ""),
            request=dict(data.get("request") or {}),
            result=dict(data.get("result") or {}),
            answered=bool(data.get("answered")),
            notes=tuple(str(n) for n in (data.get("notes") or ())),
            app=dict(data.get("app") or {}),
        )

    def pathname(self, prefix: str = "") -> str:
        """``<prefix><YYYY-MM-DD>/<id>.json`` - one folder per day keeps a listing readable."""
        return f"{prefix}{self.created_at:%Y-%m-%d}/{FILE_NAME.format(id=self.id)}"


def build_report(
    suggestion: IssuerSuggestion,
    *,
    note: str,
    user: str,
    max_note_chars: int = 500,
    commit: str | None = None,
    now: datetime | None = None,
) -> ErrorReport:
    """The report for one lookup, with the note trimmed to ``max_note_chars``."""
    from core.export.columns import json_row, suggestion_row

    request = suggestion.request
    return ErrorReport(
        id=uuid.uuid4().hex,
        created_at=now or datetime.now(UTC),
        user=user,
        note=" ".join((note or "").split())[:max_note_chars],
        request={
            "isin": request.isin,
            "name": request.name,
            "description": request.description,
        },
        result=json_row(suggestion_row(suggestion)),
        answered=suggestion.answered,
        notes=tuple(suggestion.all_notes),
        app={
            "codebook_version": suggestion.codebook_version,
            "model": suggestion.nace.model or suggestion.esa.model,
            "prompt_version": suggestion.nace.prompt_version or suggestion.esa.prompt_version,
            "commit": commit,
        },
    )


class ReportStore(Protocol):
    """Where reports go. ``save`` returns where the report can be found."""

    name: str

    def save(self, report: ErrorReport) -> str: ...


class DirectoryReportStore:
    """One JSON file per report under ``root``; written atomically, never overwritten."""

    name = "dir"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def save(self, report: ErrorReport) -> str:
        path = self.root / report.pathname()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                raise ReportStoreError(f"a report {report.id} already exists")
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".report-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(report.to_json())
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError as exc:
            raise ReportStoreError(f"cannot write the report to {path.parent}: {exc}") from exc
        return str(path)

    def load(self) -> Iterator[ErrorReport]:
        """Every report under ``root``, oldest first. A file that is not a report is skipped."""
        if not self.root.exists():
            return
        for path in sorted(self.root.rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                LOGGER.warning("skipping %s: %s", path, exc)
                continue
            if isinstance(data, Mapping) and data.get("kind") == "error_report":
                yield ErrorReport.from_dict(data)


class BlobReportStore:
    """Upload each report as a private blob, the way ``@vercel/blob``'s ``put`` does."""

    name = "blob"

    def __init__(
        self,
        *,
        token: str,
        store_id: str,
        prefix: str = "reports/",
        api_url: str = "https://vercel.com/api/blob",
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._token = token
        self._store_id = store_id
        self._prefix = prefix
        self._api_url = api_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def save(self, report: ErrorReport) -> str:
        pathname = report.pathname(self._prefix)
        headers = {
            "authorization": f"Bearer {self._token}",
            "x-api-version": BLOB_API_VERSION,
            "x-vercel-blob-store-id": self._store_id,
            "x-vercel-blob-access": "private",
            "x-content-type": "application/json",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "0",
        }
        body = report.to_json().encode("utf-8")
        last: str | None = None
        for attempt in (1, 2):
            try:
                response = self._client.put(
                    f"{self._api_url}/",
                    params={"pathname": pathname},
                    headers=headers,
                    content=body,
                )
            except httpx.HTTPError as exc:
                last = f"Blob is unreachable: {exc}"
                continue
            if response.status_code < 300:
                stored = pathname
                with contextlib.suppress(ValueError):
                    stored = str(response.json().get("pathname") or pathname)
                return stored
            last = f"Blob answered HTTP {response.status_code}"
            if response.status_code < 500:
                break
            del attempt
        raise ReportStoreError(last or "Blob did not answer")


class NullReportStore:
    """Reports are switched off: the button is not shown, and a stray post is refused."""

    name = "off"

    def save(self, report: ErrorReport) -> str:
        raise ReportStoreError("hlášení chyb je vypnuto (REPORTS_SOURCE=off)")


def build_report_store(settings: Settings) -> ReportStore:
    """The store ``REPORTS_SOURCE`` names. Raises :class:`ReportStoreError` for a misconfigured
    blob store, so the fault shows at the first report, in words, rather than as a 500."""
    source = settings.effective_reports_source
    if source == "off":
        return NullReportStore()
    if source == "dir":
        return DirectoryReportStore(settings.reports_dir)
    if source == "db":
        from core.db import DatabaseReportStore, get_database

        database = get_database(settings)
        if database is None:
            raise ReportStoreError("REPORTS_SOURCE=db but DATABASE_URL is not set")
        return DatabaseReportStore(database)
    from core.codebooks.blob import blob_store_id

    token = (
        settings.blob_read_write_token.get_secret_value()
        if settings.blob_read_write_token
        else None
    )
    if not token:
        raise ReportStoreError("REPORTS_SOURCE=blob but BLOB_READ_WRITE_TOKEN is not set")
    try:
        store_id = blob_store_id(token, settings.blob_store_id)
    except Exception as exc:  # the codebook helper raises its own error type
        raise ReportStoreError(str(exc)) from exc
    return BlobReportStore(
        token=token,
        store_id=store_id,
        prefix=settings.reports_blob_prefix,
        api_url=settings.reports_blob_api_url,
        timeout_seconds=settings.codebook_blob_timeout_seconds,
    )


def reports_are_volatile(settings: Settings, environ: Mapping[str, str] | None = None) -> bool:
    """True when a directory store would not outlive the instance (a Vercel function)."""
    environ = os.environ if environ is None else environ
    return settings.effective_reports_source == "dir" and bool(environ.get("VERCEL"))


# -- review ---------------------------------------------------------------------------------


def review_row(report: ErrorReport) -> dict[str, object]:
    result = report.result
    return {
        "created_at (UTC)": report.created_at.astimezone(UTC).replace(tzinfo=None),
        "user": report.user,
        "note": report.note,
        "IN_isin": report.request.get("isin"),
        "IN_name": report.request.get("name"),
        "IN_description": report.request.get("description"),
        "issuer_name": result.get("issuer_name"),
        "NACE_code": result.get("NACE_code"),
        "NACE_cts_id": result.get("NACE_cts_id"),
        "NACE_label": result.get("NACE_label"),
        "NACE_confidence": result.get("NACE_confidence"),
        "ESA_code": result.get("ESA_code"),
        "ESA_cts_id": result.get("ESA_cts_id"),
        "ESA_label": result.get("ESA_label"),
        "ESA_confidence": result.get("ESA_confidence"),
        "codebook_version": report.app.get("codebook_version"),
        "model": report.app.get("model"),
        "prompt_version": report.app.get("prompt_version"),
        "commit": report.app.get("commit"),
        "id": report.id,
    }


def _main(argv: list[str] | None = None) -> int:
    from config.settings import get_settings

    parser = argparse.ArgumentParser(
        prog="python -m core.reports", description="Review the error reports of a directory store."
    )
    parser.add_argument("--dir", type=Path, default=None, help="the store (default: REPORTS_DIR)")
    parser.add_argument("--list", action="store_true", help="print the reports, oldest first")
    parser.add_argument("--xlsx", type=Path, default=None, help="write them as a workbook")
    args = parser.parse_args(argv)
    if not (args.list or args.xlsx):
        parser.print_help()
        return 2
    settings = get_settings()
    if args.dir is None and settings.effective_reports_source == "db":
        store = build_report_store(settings)
        root = getattr(store, "database").describe()  # noqa: B009 - the db store only
        reports = list(store.load())  # type: ignore[attr-defined]
    else:
        root = args.dir or settings.reports_dir
        reports = list(DirectoryReportStore(root).load())
    if args.list:
        print(f"{len(reports)} report(s) in {root}")
        for report in reports:
            row = review_row(report)
            print(
                f"{report.created_at:%Y-%m-%d %H:%M} {report.user:<20} "
                f"{report.identifier:<28} NACE {row['NACE_code'] or '-':<3} "
                f"ESA {row['ESA_code'] or '-':<8} {report.note}"
            )
    if args.xlsx:
        from core.export.xlsx import write_workbook

        write_workbook(
            args.xlsx,
            REVIEW_COLUMNS,
            [review_row(report) for report in reports],
            run_metadata={"nástroj": "ESA a NACE našeptávač – hlášení chyb", "zdroj": str(root)},
        )
        print(f"wrote {args.xlsx}: {len(reports)} report(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(_main())


__all__ = [
    "BlobReportStore",
    "DirectoryReportStore",
    "ErrorReport",
    "NullReportStore",
    "ReportStore",
    "ReportStoreError",
    "build_report",
    "build_report_store",
    "reports_are_volatile",
    "review_row",
]
