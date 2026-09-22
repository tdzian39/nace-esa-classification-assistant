"""DWS adapter: the only place in the application that contains SQL.

.. note::

   **UNUSED as of 2026-09-22.** The bank is not granting DWS access for this project, so
   nothing constructs this adapter in normal operation: Tool 1 never touched it, and Tool 2
   resolves everything through the public ARES API (its rows are stamped ``ARES_LIVE``
   accordingly). It is kept, tested and ready in case read-only access is granted later -
   :func:`~core.sources.resolver.build_default_resolver` already includes it the moment
   ``DWS_DSN`` is set, and no other code needs to change.

   Every table and column name below is still an unconfirmed placeholder. Do not treat the
   TODO markers as stale: they are exactly what would have to be answered before a first
   real query.


CLAUDE.md: *Until real table names are confirmed, put all SQL behind a single ``dws.py``
adapter with clearly named functions and TODO markers on table/column names.* Every object
and column name below is therefore a placeholder gathered into :data:`TABLES` and the
``COLUMNS_*`` maps - correcting them once the warehouse team confirms the schema is a edit
of those dictionaries, not of the query logic.

Read-only by construction:

* every statement is a literal in this module, built from the name maps, never from caller
  input (the IČO always travels as a bind parameter);
* :func:`ensure_read_only` refuses anything that is not a single ``SELECT``/``WITH``,
  so a future edit cannot smuggle in a write;
* the connection is opened from a read-only account configured through the environment.

Nothing loaded here may ever reach an LLM prompt (hard rule in CLAUDE.md); the LLM path
built in step 6 must take its input from :mod:`core.sources.web` and the codebooks only.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Final

from config.settings import Settings
from core.codebooks.errors import MalformedCodeError
from core.codebooks.normalize import format_esa_code
from core.sources.base import (
    NACE_REV_2,
    NACE_REV_21,
    NaceAssignment,
    NaceRevision,
    OrRecord,
    Provenance,
    ResRecord,
    Source,
    SourceQueryError,
    SourceUnavailableError,
    SubjectCandidate,
    SubjectRecord,
)

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------
# TODO(dws-schema): every name in this block is a placeholder. Confirm with the DWS team and
# correct here only - no other module names a table or a column.
# ---------------------------------------------------------------------------------------

#: Logical name -> object name in DWS (without schema; the schema comes from settings).
TABLES: Final[dict[str, str]] = {
    "res_subject": "RES_SUBJEKT",  # TODO(dws-schema): RES snapshot, one row per IČO
    "res_nace": "RES_NACE",  # TODO(dws-schema): child table, one row per NACE code
    "or_subject": "OR_SUBJEKT",  # TODO(dws-schema): OR snapshot, one row per IČO
    "or_activity": "OR_PREDMET",  # TODO(dws-schema): child table, one row per activity
}

#: RES columns. Keys are the logical names used by the mapping code below.
COLUMNS_RES: Final[dict[str, str]] = {
    "ico": "ICO",  # TODO(dws-schema)
    "name": "NAZEV",  # TODO(dws-schema)
    "esa_sector": "ESA2010_SEKTOR",  # TODO(dws-schema): digits or S.xxxxx, both accepted
    "founded_on": "DATUM_VZNIKU",  # TODO(dws-schema)
    "legal_form": "PRAVNI_FORMA",  # TODO(dws-schema)
    "snapshot_at": "SNAPSHOT_DATE",  # TODO(dws-schema): when the snapshot was taken
}

#: RES NACE child table.
COLUMNS_RES_NACE: Final[dict[str, str]] = {
    "ico": "ICO",  # TODO(dws-schema)
    "code": "NACE_KOD",  # TODO(dws-schema): FULL code, not the 2-digit division
    "revision": "NACE_REVIZE",  # TODO(dws-schema): see REVISION_VALUES below
    "is_main": "PREVAZUJICI",  # TODO(dws-schema): 1/Y/true for the prevailing activity
    "label": "NACE_TEXT",  # TODO(dws-schema): optional
}

#: OR columns.
COLUMNS_OR: Final[dict[str, str]] = {
    "ico": "ICO",  # TODO(dws-schema)
    "obchodni_firma": "OBCHODNI_FIRMA",  # TODO(dws-schema)
    "datum_vzniku": "DATUM_VZNIKU",  # TODO(dws-schema)
    "datum_zapisu": "DATUM_ZAPISU",  # TODO(dws-schema)
    "spisova_znacka": "SPISOVA_ZNACKA",  # TODO(dws-schema)
    "snapshot_at": "SNAPSHOT_DATE",  # TODO(dws-schema)
}

#: OR activity child table.
COLUMNS_OR_ACTIVITY: Final[dict[str, str]] = {
    "ico": "ICO",  # TODO(dws-schema)
    "kind": "DRUH",  # TODO(dws-schema): see ACTIVITY_KINDS below
    "text": "TEXT",  # TODO(dws-schema)
}

#: How the RES NACE table spells each revision. TODO(dws-schema): confirm the domain values.
REVISION_VALUES: Final[dict[str, NaceRevision]] = {
    "2": NACE_REV_2,
    "REV2": NACE_REV_2,
    "2008": NACE_REV_2,
    "CZ-NACE 2008": NACE_REV_2,
    "2.1": NACE_REV_21,
    "REV21": NACE_REV_21,
    "2025": NACE_REV_21,
    "CZ-NACE 2025": NACE_REV_21,
}

#: How the OR activity table spells each kind. TODO(dws-schema): confirm the domain values.
ACTIVITY_KINDS: Final[dict[str, str]] = {
    "PODNIKANI": "podnikani",
    "PREDMET_PODNIKANI": "podnikani",
    "P": "podnikani",
    "CINNOST": "cinnost",
    "PREDMET_CINNOSTI": "cinnost",
    "C": "cinnost",
}

#: Truthy spellings of a flag column across the usual warehouse conventions.
_TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "Y", "A", "T", "TRUE", "ANO", "YES"})

#: A statement this adapter is willing to execute: one SELECT (or CTE), nothing chained.
_READ_ONLY_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(insert|update|delete|merge|drop|truncate|alter|create|grant|revoke|exec|execute|call)\b",
    re.IGNORECASE,
)


def ensure_read_only(sql: str) -> str:
    """Return ``sql`` unchanged, or raise if it is not a single read-only statement.

    Raises:
        SourceQueryError: the statement is not a ``SELECT``/``WITH``, contains a DML or DDL
            keyword, or chains a second statement with ``;``.
    """
    if not _READ_ONLY_RE.match(sql):
        raise SourceQueryError("DWS adapter refuses a statement that is not SELECT/WITH")
    # Chaining is checked first: "SELECT 1; DROP TABLE x" is a chaining problem, and naming
    # the keyword instead would send the reader after the wrong cause.
    if ";" in sql.strip().rstrip(";"):
        raise SourceQueryError("DWS adapter refuses chained statements")
    if _FORBIDDEN_RE.search(sql):
        raise SourceQueryError("DWS adapter refuses a statement containing a write keyword")
    return sql


def _qualify(table_key: str, schema: str | None) -> str:
    """``SCHEMA.TABLE`` when a schema is configured, otherwise the bare table name."""
    table = TABLES[table_key]
    return f"{schema}.{table}" if schema else table


def _select(columns: Mapping[str, str]) -> str:
    """``COL AS logical_name`` list, so the row mapper reads logical keys only."""
    return ", ".join(f"{actual} AS {logical}" for logical, actual in columns.items())


# ---------------------------------------------------------------------------------------
# Statements. TODO(dws-schema): the join/filter logic assumes one row per IČO in the parent
# tables and a plain IČO foreign key in the child tables; revisit once the model is known.
# Parameter style is the ODBC/DBAPI ``?``; change here if the confirmed driver uses ``:1``.
# ---------------------------------------------------------------------------------------


def sql_res_by_ico(schema: str | None) -> str:
    """One RES row for an IČO."""
    return ensure_read_only(
        f"SELECT {_select(COLUMNS_RES)} FROM {_qualify('res_subject', schema)} "
        f"WHERE {COLUMNS_RES['ico']} = ?"
    )


def sql_res_nace_by_ico(schema: str | None) -> str:
    """Every NACE code of an IČO, both revisions, main flag included."""
    return ensure_read_only(
        f"SELECT {_select(COLUMNS_RES_NACE)} FROM {_qualify('res_nace', schema)} "
        f"WHERE {COLUMNS_RES_NACE['ico']} = ? "
        f"ORDER BY {COLUMNS_RES_NACE['revision']}, {COLUMNS_RES_NACE['code']}"
    )


def sql_or_by_ico(schema: str | None) -> str:
    """One OR row for an IČO."""
    return ensure_read_only(
        f"SELECT {_select(COLUMNS_OR)} FROM {_qualify('or_subject', schema)} "
        f"WHERE {COLUMNS_OR['ico']} = ?"
    )


def sql_or_activities_by_ico(schema: str | None) -> str:
    """Předmět podnikání and předmět činnosti rows of an IČO."""
    return ensure_read_only(
        f"SELECT {_select(COLUMNS_OR_ACTIVITY)} FROM {_qualify('or_activity', schema)} "
        f"WHERE {COLUMNS_OR_ACTIVITY['ico']} = ?"
    )


def sql_search_by_name(schema: str | None) -> str:
    """IČO + name of subjects whose RES name contains the search text (case-insensitive)."""
    return ensure_read_only(
        f"SELECT {COLUMNS_RES['ico']} AS ico, {COLUMNS_RES['name']} AS name "
        f"FROM {_qualify('res_subject', schema)} "
        f"WHERE UPPER({COLUMNS_RES['name']}) LIKE ? "
        f"ORDER BY {COLUMNS_RES['name']}"
    )


ConnectFactory = Callable[[], Any]


def default_connect_factory(settings: Settings) -> ConnectFactory:
    """Build the factory that opens the real read-only DWS connection.

    The driver is imported lazily so the package installs and the tests run on a machine
    that has no ODBC driver at all.
    """

    def connect() -> Any:
        if not settings.dws_configured:
            raise SourceUnavailableError(
                "DWS is not configured (set DWS_DSN in the environment or app/.env)"
            )
        try:  # pragma: no cover - requires a driver and a warehouse
            import pyodbc  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise SourceUnavailableError(
                "no ODBC driver available: install pyodbc (TODO: add it to pyproject once the "
                "confirmed DWS driver is known) or inject a connect factory"
            ) from exc
        try:  # pragma: no cover - requires a driver and a warehouse
            connection = pyodbc.connect(
                settings.dws_dsn,
                user=settings.dws_user,
                password=(
                    settings.dws_password.get_secret_value() if settings.dws_password else None
                ),
                timeout=int(settings.dws_timeout_seconds),
                readonly=True,
            )
        except Exception as exc:
            raise SourceUnavailableError(f"cannot connect to DWS: {exc}") from exc
        return connection

    return connect


class DwsSource:
    """Read-only DWS adapter and the primary source for Czech subjects.

    Args:
        settings: Supplies the schema and the connection details.
        connect_factory: Injected for tests and for a future non-ODBC driver. When omitted,
            :func:`default_connect_factory` is used.
    """

    source: Source = "DWS"

    def __init__(
        self, settings: Settings, *, connect_factory: ConnectFactory | None = None
    ) -> None:
        self._settings = settings
        self._schema = settings.dws_schema
        self._connect = connect_factory or default_connect_factory(settings)
        self._connection: Any | None = None
        self._configured = connect_factory is not None or settings.dws_configured

    @property
    def configured(self) -> bool:
        """False when no DSN is set and no factory was injected; the resolver then skips DWS."""
        return self._configured

    def _ensure_connection(self) -> Any:
        if self._connection is None:
            self._connection = self._connect()
        return self._connection

    def _fetch_all(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        """Run one read-only statement and return rows as ``{logical_column: value}``."""
        ensure_read_only(sql)
        connection = self._ensure_connection()
        try:
            cursor = connection.cursor()
        except Exception as exc:
            raise SourceUnavailableError(f"DWS cursor could not be opened: {exc}") from exc
        try:
            cursor.execute(sql, tuple(params))
            description = cursor.description or ()
            names = [str(column[0]).lower() for column in description]
            return [dict(zip(names, row, strict=False)) for row in cursor.fetchall()]
        except SourceUnavailableError:
            raise
        except Exception as exc:
            raise SourceUnavailableError(f"DWS query failed: {exc}") from exc
        finally:
            close = getattr(cursor, "close", None)
            if callable(close):
                close()

    def close(self) -> None:
        """Close the warehouse connection if one was opened."""
        if self._connection is not None:
            close = getattr(self._connection, "close", None)
            if callable(close):
                close()
            self._connection = None

    def __enter__(self) -> DwsSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- public API --------------------------------------------------------------------

    def fetch_by_ico(self, ico: str) -> SubjectRecord | None:
        """Both halves of a subject from the warehouse; ``None`` when the snapshot lacks it."""
        retrieved_at = datetime.now(UTC)
        res_rows = self._fetch_all(sql_res_by_ico(self._schema), (ico,))
        or_rows = self._fetch_all(sql_or_by_ico(self._schema), (ico,))
        if not res_rows and not or_rows:
            return None

        res: ResRecord | None = None
        if res_rows:
            nace_rows = self._fetch_all(sql_res_nace_by_ico(self._schema), (ico,))
            res = self._build_res(ico, res_rows[0], nace_rows, retrieved_at)
        or_record: OrRecord | None = None
        if or_rows:
            activity_rows = self._fetch_all(sql_or_activities_by_ico(self._schema), (ico,))
            or_record = self._build_or(ico, or_rows[0], activity_rows, retrieved_at)

        notes: list[str] = []
        if res is None:
            notes.append("DWS: no RES row for this IČO")
        if or_record is None:
            notes.append("DWS: no OR row for this IČO")
        return SubjectRecord(ico=ico, res=res, or_record=or_record, notes=tuple(notes))

    def search_by_name(self, name: str, *, limit: int = 10) -> tuple[SubjectCandidate, ...]:
        """Case-insensitive contains-search on the RES name."""
        pattern = f"%{name.strip().upper()}%"
        rows = self._fetch_all(sql_search_by_name(self._schema), (pattern,))
        candidates: list[SubjectCandidate] = []
        for row in rows[:limit]:
            row_ico = _text(row.get("ico"))
            if not row_ico:
                continue
            candidates.append(
                SubjectCandidate(ico=row_ico, name=_text(row.get("name")), source=self.source)
            )
        return tuple(candidates)

    # -- row -> model ------------------------------------------------------------------

    def _provenance(
        self, row: Mapping[str, Any], retrieved_at: datetime, detail: str
    ) -> Provenance:
        return Provenance(
            source=self.source,
            retrieved_at=retrieved_at,
            snapshot_at=_as_datetime(_parse_date(row.get("snapshot_at"))),
            detail=detail,
        )

    def _build_res(
        self,
        ico: str,
        row: Mapping[str, Any],
        nace_rows: Sequence[Mapping[str, Any]],
        retrieved_at: datetime,
    ) -> ResRecord:
        return ResRecord(
            ico=_text(row.get("ico")) or ico,
            provenance=self._provenance(row, retrieved_at, "dws:res"),
            name=_text(row.get("name")),
            nace=tuple(_nace_assignments(nace_rows)),
            esa_sector=_esa_sector(row.get("esa_sector")),
            founded_on=_parse_date(row.get("founded_on")),
            legal_form=_text(row.get("legal_form")),
        )

    def _build_or(
        self,
        ico: str,
        row: Mapping[str, Any],
        activity_rows: Sequence[Mapping[str, Any]],
        retrieved_at: datetime,
    ) -> OrRecord:
        podnikani: list[str] = []
        cinnosti: list[str] = []
        for activity in activity_rows:
            text = _text(activity.get("text"))
            if not text:
                continue
            kind = ACTIVITY_KINDS.get((_text(activity.get("kind")) or "").upper())
            target = cinnosti if kind == "cinnost" else podnikani
            if text not in target:
                target.append(text)
        return OrRecord(
            ico=_text(row.get("ico")) or ico,
            provenance=self._provenance(row, retrieved_at, "dws:or"),
            obchodni_firma=_text(row.get("obchodni_firma")),
            predmet_podnikani=tuple(podnikani),
            predmet_cinnosti=tuple(cinnosti),
            datum_vzniku=_parse_date(row.get("datum_vzniku")),
            datum_zapisu=_parse_date(row.get("datum_zapisu")),
            spisova_znacka=_text(row.get("spisova_znacka")),
        )


# -- helpers ----------------------------------------------------------------------------


def _text(value: object) -> str | None:
    """Trimmed text, or ``None`` for ``NULL``/blank."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value: object) -> date | None:
    """Accept a driver ``date``/``datetime`` or an ISO string; anything else becomes ``None``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        LOGGER.debug("DWS: unparseable date %r", value)
        return None


def _as_datetime(value: date | None) -> datetime | None:
    """A date becomes UTC midnight; ``None`` passes through."""
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _is_true(value: object) -> bool:
    """Truthiness across the warehouse flag conventions (1/Y/A/T/TRUE/ANO)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = _text(value)
    return text is not None and text.upper() in _TRUE_VALUES


def _esa_sector(value: object) -> str | None:
    """Canonical ESA sector text; an unparseable value is dropped with a warning."""
    raw = _text(value)
    if raw is None:
        return None
    try:
        return format_esa_code(raw)
    except MalformedCodeError:
        LOGGER.warning("DWS: unparseable ESA sector %r, dropped", raw)
        return None


def _nace_assignments(rows: Sequence[Mapping[str, Any]]) -> list[NaceAssignment]:
    """Map RES NACE child rows; full codes are kept verbatim, never truncated.

    Rows whose revision is not in :data:`REVISION_VALUES` are skipped with a warning rather
    than guessed at: a code filed under the wrong revision would corrupt ``nace_mismatch``.
    """
    assignments: list[NaceAssignment] = []
    for row in rows:
        code = _text(row.get("code"))
        if not code:
            continue
        raw_revision = (_text(row.get("revision")) or "").upper()
        revision = REVISION_VALUES.get(raw_revision)
        if revision is None:
            LOGGER.warning(
                "DWS: unknown NACE revision %r for code %s, row skipped", raw_revision, code
            )
            continue
        assignments.append(
            NaceAssignment(
                code=code,
                revision=revision,
                is_main=_is_true(row.get("is_main")),
                label=_text(row.get("label")),
            )
        )
    return assignments
