"""One central database for what a serverless function cannot keep on disk (roadmap D4).

On Vercel nothing but ``/tmp`` is writable and ``/tmp`` dies with the instance, so until now
production kept **no** usage ledger (the daily budget was unenforceable, spend was read on the
provider's page), **no** answer cache across instances, **no** audit trail beyond an hour of
runtime logs, and error reports could only go to Blob. With ``DATABASE_URL`` set, all four
live in one Postgres database - Neon via the Vercel Marketplace, or any Postgres a bank
runs - and every device and every user writes to the same place:

* ``llm_usage``      - the ledger :class:`core.classify.budget.UsageRecorder` fills,
* ``classifications`` - the answer cache :class:`core.classify.cache.ClassificationCache`,
* ``audit_events``   - every lookup and every error report: identifier, user, outcome,
  never content (:mod:`core.audit`),
* ``error_reports``  - the request, the result row and MO's note (:mod:`core.reports`).

The stores here implement the same protocols as the SQLite and file stores next to them,
so nothing downstream knows where a row went. Design rules, the same as for the files:

* **Fail soft on the write path, honest on the read path.** A row that cannot be written is
  a warning, never a failed lookup; a ledger that cannot be read says so, because a budget
  that cannot be measured is not a cap (``can_track``).
* **A connection per operation.** A function instance may serve many requests and is
  discarded without notice; a pooled connection string (Neon's ``-pooler`` host, which the
  Vercel integration puts in ``DATABASE_URL``) makes that cheap. Connect timeout 5 s.
* **The schema creates itself**, once per instance, with ``CREATE TABLE IF NOT EXISTS``.
  There is no migration tool: a new column is added the way the SQLite ledger does it.
* **One SQL, two engines.** The statements are written once with ``?`` placeholders and ISO
  8601 text timestamps (sortable, time-zone explicit, what the SQLite stores already use).
  Postgres gets ``%s`` and ``BIGSERIAL``; SQLite keeps ``?`` and ``AUTOINCREMENT``. The
  SQLite engine exists so the tests, and a laptop, run the very same code path. The Postgres
  engine was verified live on 24 Sept 2026 against the project's Neon database: schema
  created, a lookup and a report written through the app, the shared cache hit, and the
  CLIs reading everything back.

Secrets: the URL carries the password. It is a ``SecretStr`` in settings, never logged
(``describe()`` gives host and database only), and on Vercel a Sensitive variable.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal
from urllib.parse import urlsplit

if TYPE_CHECKING:  # pragma: no cover
    from config.settings import Settings
    from core.classify.budget import UsageRecord, UsageTotals
    from core.classify.models import Classification
    from core.reports import ErrorReport

LOGGER = logging.getLogger(__name__)

Engine = Literal["sqlite", "postgres"]

#: Statements creating the four tables; ``{serial}`` is the engine's autoincrement column.
SCHEMA: Final[tuple[str, ...]] = (
    """CREATE TABLE IF NOT EXISTS llm_usage (
        id {serial},
        at TEXT NOT NULL,
        model TEXT NOT NULL,
        kind TEXT,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        cached_prompt_tokens INTEGER,
        user_name TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS llm_usage_at ON llm_usage (at)",
    """CREATE TABLE IF NOT EXISTS classifications (
        key TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        issuer TEXT,
        model TEXT,
        prompt_version TEXT,
        codebook_version TEXT,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS audit_events (
        id {serial},
        at TEXT NOT NULL,
        event TEXT NOT NULL,
        identifier TEXT NOT NULL,
        user_name TEXT NOT NULL,
        outcome TEXT NOT NULL,
        ico TEXT,
        sources TEXT,
        detail TEXT,
        store TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS audit_events_at ON audit_events (at)",
    """CREATE TABLE IF NOT EXISTS error_reports (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        user_name TEXT NOT NULL,
        identifier TEXT NOT NULL,
        note TEXT NOT NULL,
        report TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS error_reports_created_at ON error_reports (created_at)",
)

_SERIAL: Final[dict[Engine, str]] = {
    "sqlite": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "postgres": "BIGSERIAL PRIMARY KEY",
}
_CONNECT_TIMEOUT_SECONDS: Final[float] = 5.0
_POSTGRES_SCHEMES: Final[frozenset[str]] = frozenset({"postgres", "postgresql"})


class DatabaseError(Exception):
    """The database could not be reached, or refused a statement."""


class Database:
    """A connection recipe plus the schema, for one engine.

    Args:
        url: ``postgresql://user:pass@host/db?sslmode=require`` or ``sqlite:///path``
            (``sqlite:///:memory:`` is refused: each operation opens its own connection, so
            an in-memory database would be empty every time - tests use a file).
        connect: Injected factory returning a DB-API connection; the tests do not need it,
            but it is how another driver would plug in.
    """

    def __init__(self, url: str, *, connect: Callable[[], Any] | None = None) -> None:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme in _POSTGRES_SCHEMES:
            self.engine: Engine = "postgres"
            self._path: Path | None = None
        elif scheme == "sqlite":
            self.engine = "sqlite"
            raw = url[len("sqlite://") :]
            raw = raw[1:] if raw.startswith("/") and len(raw) > 2 and raw[2] == ":" else raw
            if raw in ("", "/", ":memory:", "/:memory:"):
                raise DatabaseError("sqlite:///:memory: cannot be shared between connections")
            self._path = Path(raw)
        else:
            raise DatabaseError(
                f"unsupported database URL scheme {scheme!r}: use postgresql:// or sqlite:///"
            )
        self._url = url
        self._connect = connect
        self._schema_ready = False
        self._lock = threading.Lock()
        self.usable = True

    # -- description --------------------------------------------------------------------

    def describe(self) -> str:
        """``postgres host/db`` or ``sqlite path`` - without credentials, for logs and /health."""
        if self.engine == "sqlite":
            return f"sqlite {self._path}"
        parts = urlsplit(self._url)
        return f"postgres {parts.hostname or '?'}{parts.path or ''}"

    # -- connections --------------------------------------------------------------------

    def _open(self) -> Any:
        if self._connect is not None:
            return self._connect()
        if self.engine == "sqlite":
            assert self._path is not None
            self._path.parent.mkdir(parents=True, exist_ok=True)
            return sqlite3.connect(self._path, timeout=_CONNECT_TIMEOUT_SECONDS)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - the dependency is declared
            raise DatabaseError("psycopg is not installed; DATABASE_URL needs it") from exc
        return psycopg.connect(self._url, connect_timeout=int(_CONNECT_TIMEOUT_SECONDS))

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """One connection, committed on success, rolled back and closed otherwise."""
        try:
            connection = self._open()
        except Exception as exc:
            raise DatabaseError(f"cannot connect to {self.describe()}: {exc}") from exc
        try:
            self._ensure_schema(connection)
            yield connection
            connection.commit()
        except DatabaseError:
            connection.rollback()
            raise
        except Exception as exc:
            connection.rollback()
            raise DatabaseError(f"{self.describe()}: {exc}") from exc
        finally:
            connection.close()

    def _ensure_schema(self, connection: Any) -> None:
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            serial = _SERIAL[self.engine]
            cursor = connection.cursor()
            for statement in SCHEMA:
                cursor.execute(statement.format(serial=serial))
            connection.commit()
            self._schema_ready = True

    def sql(self, statement: str) -> str:
        """The statement in the engine's placeholder style (``?`` written, ``%s`` for Postgres)."""
        return statement.replace("?", "%s") if self.engine == "postgres" else statement

    def execute(self, statement: str, params: Sequence[Any] = ()) -> None:
        with self.connection() as connection:
            connection.cursor().execute(self.sql(statement), tuple(params))

    def query(self, statement: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(self.sql(statement), tuple(params))
            return [tuple(row) for row in cursor.fetchall()]

    def ping(self) -> bool:
        """Can the database be reached and does the schema exist? Never raises."""
        try:
            self.query("SELECT COUNT(*) FROM audit_events")
        except DatabaseError as exc:
            LOGGER.warning("database unreachable: %s", exc)
            return False
        return True


# -- the stores ------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse(at: str) -> datetime:
    when = datetime.fromisoformat(at)
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


class DatabaseLedger:
    """The usage ledger in the central database (:class:`core.classify.budget.UsageRecorder`).

    ``can_track`` is True: with the ledger shared, the daily budget is enforceable in
    production too. A read failure returns empty totals and is logged, like the SQLite ledger;
    :meth:`records` raises, because a workbook that quietly came back empty would say that
    nothing was spent.
    """

    can_track = True
    usable = True

    def __init__(self, database: Database) -> None:
        self.database = database

    def record(
        self,
        *,
        model: str,
        kind: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int | None = None,
        user: str | None = None,
    ) -> None:
        from core.auth import UNKNOWN_USER

        try:
            self.database.execute(
                "INSERT INTO llm_usage (at, model, kind, prompt_tokens, completion_tokens, "
                "cached_prompt_tokens, user_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _now(),
                    model,
                    kind,
                    int(prompt_tokens or 0),
                    int(completion_tokens or 0),
                    None if cached_prompt_tokens is None else int(cached_prompt_tokens),
                    (user or "").strip() or UNKNOWN_USER,
                ),
            )
        except DatabaseError as exc:
            LOGGER.warning("could not record usage: %s", exc)

    def totals_since(self, since: datetime) -> UsageTotals:
        from core.classify.budget import UsageTotals

        try:
            (row,) = self.database.query(
                "SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), "
                "COALESCE(SUM(completion_tokens), 0) FROM llm_usage WHERE at >= ?",
                (since.isoformat(),),
            )
        except DatabaseError as exc:
            LOGGER.warning("could not read usage: %s", exc)
            return UsageTotals()
        return UsageTotals(
            calls=int(row[0]), prompt_tokens=int(row[1]), completion_tokens=int(row[2])
        )

    def today(self) -> UsageTotals:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.totals_since(start)

    def records(self) -> list[UsageRecord]:
        from core.auth import UNKNOWN_USER
        from core.classify.budget import UsageRecord

        rows = self.database.query(
            "SELECT at, model, kind, prompt_tokens, completion_tokens, cached_prompt_tokens, "
            "user_name FROM llm_usage ORDER BY at, id"
        )
        return [
            UsageRecord(
                at=_parse(at),
                model=model,
                kind=kind or "",
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                cached_prompt_tokens=None if cached is None else int(cached),
                user=user or UNKNOWN_USER,
            )
            for at, model, kind, prompt_tokens, completion_tokens, cached, user in rows
        ]


class DatabaseCache:
    """The answer cache in the central database: a repeated issuer costs nothing on any
    instance. Any failure degrades to "no cache", never to an error."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, key: str) -> Classification | None:
        from core.classify.cache import _from_payload

        try:
            rows = self.database.query("SELECT payload FROM classifications WHERE key = ?", (key,))
        except DatabaseError as exc:
            LOGGER.warning("cache read failed: %s", exc)
            return None
        return _from_payload(rows[0][0]) if rows else None

    def put(self, key: str, classification: Classification, *, issuer_name: str | None) -> None:
        from core.classify.cache import _to_payload

        try:
            self.database.execute(
                "INSERT INTO classifications (key, kind, issuer, model, prompt_version, "
                "codebook_version, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET payload = excluded.payload, "
                "issuer = excluded.issuer, model = excluded.model, "
                "prompt_version = excluded.prompt_version, created_at = excluded.created_at",
                (
                    key,
                    classification.kind,
                    issuer_name,
                    classification.model,
                    classification.prompt_version,
                    None,
                    _to_payload(classification),
                    _now(),
                ),
            )
        except DatabaseError as exc:
            LOGGER.warning("cache write failed: %s", exc)

    def __len__(self) -> int:
        try:
            return int(self.database.query("SELECT COUNT(*) FROM classifications")[0][0])
        except DatabaseError:
            return 0


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One ``audit_events`` row read back."""

    at: datetime
    event: str
    identifier: str
    user: str
    outcome: str
    sources: str | None = None
    detail: str | None = None
    store: str | None = None


class DatabaseAuditSink:
    """Keeps the audit events (:mod:`core.audit`) - and nothing retrieved."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def write(self, event: dict[str, Any]) -> None:
        sources = event.get("sources")
        try:
            self.database.execute(
                "INSERT INTO audit_events (at, event, identifier, user_name, outcome, ico, "
                "sources, detail, store) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(event.get("at") or _now()),
                    str(event.get("event") or "lookup"),
                    str(event.get("identifier") or ""),
                    str(event.get("user") or ""),
                    str(event.get("outcome") or ""),
                    event.get("ico"),
                    "+".join(sources) if isinstance(sources, list | tuple) else sources,
                    event.get("detail"),
                    event.get("store"),
                ),
            )
        except DatabaseError as exc:
            LOGGER.warning("audit event not stored: %s", exc)

    def events(self, *, limit: int = 1000) -> list[AuditRow]:
        rows = self.database.query(
            "SELECT at, event, identifier, user_name, outcome, sources, detail, store "
            "FROM audit_events ORDER BY at DESC, id DESC LIMIT ?",
            (int(limit),),
        )
        return [
            AuditRow(
                at=_parse(at),
                event=event,
                identifier=identifier,
                user=user,
                outcome=outcome,
                sources=sources,
                detail=detail,
                store=store,
            )
            for at, event, identifier, user, outcome, sources, detail, store in rows
        ]


class DatabaseReportStore:
    """Error reports (:mod:`core.reports`) in the central database."""

    name = "db"

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, report: ErrorReport) -> str:
        from core.reports import ReportStoreError

        try:
            self.database.execute(
                "INSERT INTO error_reports (id, created_at, user_name, identifier, note, report) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    report.id,
                    report.created_at.isoformat(),
                    report.user,
                    report.identifier,
                    report.note,
                    report.to_json(),
                ),
            )
        except DatabaseError as exc:
            raise ReportStoreError(f"the database refused the report: {exc}") from exc
        return f"{self.database.describe()} error_reports/{report.id}"

    def load(self) -> Iterator[ErrorReport]:
        from core.reports import ErrorReport

        for (payload,) in self.database.query(
            "SELECT report FROM error_reports ORDER BY created_at, id"
        ):
            try:
                data = json.loads(payload)
            except ValueError as exc:
                LOGGER.warning("skipping an unreadable report: %s", exc)
                continue
            yield ErrorReport.from_dict(data)


# -- settings ----------------------------------------------------------------------------------

_DATABASES: dict[str, Database] = {}
_DATABASES_LOCK = threading.Lock()


def get_database(settings: Settings | None = None) -> Database | None:
    """The configured central database, one instance per URL; ``None`` without ``DATABASE_URL``."""
    from config.settings import get_settings

    resolved = settings if settings is not None else get_settings()
    secret = resolved.database_url
    if secret is None:
        return None
    url = secret.get_secret_value().strip()
    if not url:
        return None
    with _DATABASES_LOCK:
        database = _DATABASES.get(url)
        if database is None:
            database = Database(url)
            _DATABASES[url] = database
        return database


__all__ = [
    "AuditRow",
    "Database",
    "DatabaseAuditSink",
    "DatabaseCache",
    "DatabaseError",
    "DatabaseLedger",
    "DatabaseReportStore",
    "SCHEMA",
    "get_database",
]
