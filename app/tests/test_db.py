"""The central database (core/db.py): the four stores on the SQLite engine, the Postgres
placeholders and DDL, and the wiring - settings, builders, the audit sink, the page.

There is no Postgres here, so the Postgres engine is checked for what it sends (placeholders,
the serial column, the connect call), not for what a server answers.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import main as api
from config.settings import Settings
from core import audit
from core.classify.budget import build_ledger
from core.classify.cache import build_cache
from core.classify.models import Classification, Suggestion
from core.classify.provider import StubLlmProvider
from core.db import (
    SCHEMA,
    Database,
    DatabaseAuditSink,
    DatabaseCache,
    DatabaseError,
    DatabaseLedger,
    DatabaseReportStore,
    get_database,
)
from core.reports import ReportStoreError, build_report, build_report_store
from tests.api.test_api_suggest import answer, make_service
from tests.classify.conftest import build_codebooks


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(f"sqlite:///{(tmp_path / 'central.sqlite3').as_posix()}")


class TestDatabase:
    def test_a_sqlite_url_creates_the_schema_on_first_use(self, db: Database) -> None:
        assert db.engine == "sqlite"
        assert db.query("SELECT COUNT(*) FROM llm_usage") == [(0,)]
        assert db.query("SELECT COUNT(*) FROM error_reports") == [(0,)]

    def test_the_postgres_engine_swaps_placeholders_and_serials(self) -> None:
        database = Database("postgresql://u:p@db.example/app?sslmode=require")
        assert database.engine == "postgres"
        assert database.sql("SELECT ? , ?") == "SELECT %s , %s"
        assert database.describe() == "postgres db.example/app"  # no credentials
        assert any(
            "BIGSERIAL PRIMARY KEY" in s.format(serial="BIGSERIAL PRIMARY KEY") for s in SCHEMA
        )

    def test_postgres_connects_through_psycopg_with_a_timeout(self, monkeypatch) -> None:
        seen = {}

        class FakePsycopg:
            @staticmethod
            def connect(url, **kwargs):
                seen["url"] = url
                seen.update(kwargs)
                raise OSError("no server here")

        import sys

        monkeypatch.setitem(sys.modules, "psycopg", FakePsycopg)
        database = Database("postgres://u:p@h/d")
        with pytest.raises(DatabaseError, match="cannot connect to postgres h/d"):
            database.query("SELECT 1")
        assert seen["url"] == "postgres://u:p@h/d"
        assert seen["connect_timeout"] == 5

    def test_memory_and_unknown_schemes_are_refused(self) -> None:
        with pytest.raises(DatabaseError):
            Database("sqlite:///:memory:")
        with pytest.raises(DatabaseError):
            Database("mysql://x")

    def test_ping_never_raises(self, db: Database, monkeypatch) -> None:
        assert db.ping() is True
        monkeypatch.setattr(db, "_open", lambda: (_ for _ in ()).throw(OSError("down")))
        assert db.ping() is False


class TestLedger:
    def test_records_and_totals(self, db: Database) -> None:
        ledger = DatabaseLedger(db)
        ledger.record(model="m", kind="NACE", prompt_tokens=10, completion_tokens=2, user="jana")
        ledger.record(
            model="m", kind="ESA", prompt_tokens=5, completion_tokens=1, cached_prompt_tokens=3
        )
        today = ledger.today()
        assert (today.calls, today.prompt_tokens, today.completion_tokens) == (2, 15, 3)
        assert ledger.totals_since(datetime.now(UTC) + timedelta(days=1)).calls == 0
        rows = ledger.records()
        assert [r.user for r in rows] == ["jana", "unknown"]
        assert rows[1].cached_prompt_tokens == 3 and rows[0].cached_prompt_tokens is None
        assert rows[0].at.tzinfo is not None
        assert ledger.can_track is True

    def test_a_write_failure_is_a_warning(self, db: Database, monkeypatch, caplog) -> None:
        monkeypatch.setattr(db, "_open", lambda: (_ for _ in ()).throw(OSError("down")))
        with caplog.at_level(logging.WARNING):
            DatabaseLedger(db).record(model="m", kind="NACE", prompt_tokens=1, completion_tokens=1)
        assert "could not record usage" in caplog.text
        assert DatabaseLedger(db).today().calls == 0

    def test_records_raise_when_unreadable(self, db: Database, monkeypatch) -> None:
        monkeypatch.setattr(db, "_open", lambda: (_ for _ in ()).throw(OSError("down")))
        with pytest.raises(DatabaseError):
            DatabaseLedger(db).records()

    def test_the_builder_prefers_the_database(self, db: Database, tmp_path: Path) -> None:
        assert isinstance(build_ledger(tmp_path / "x.sqlite3", db), DatabaseLedger)
        assert not isinstance(build_ledger(tmp_path / "x.sqlite3"), DatabaseLedger)


def classification() -> Classification:
    return Classification(
        kind="NACE",
        suggestions=(
            Suggestion(
                kind="NACE",
                code="64",
                cts_id="512",
                label="Fin.",
                confidence="high",
                justification="j",
            ),
        ),
        model="m",
        prompt_version="v",
    )


class TestCache:
    def test_put_get_and_replace(self, db: Database) -> None:
        cache = DatabaseCache(db)
        assert cache.get("k") is None
        cache.put("k", classification(), issuer_name="X")
        got = cache.get("k")
        assert got is not None and got.suggestions[0].code == "64"
        cache.put("k", classification(), issuer_name="Y")  # ON CONFLICT: no error, one row
        assert len(cache) == 1
        assert db.query("SELECT issuer FROM classifications") == [("Y",)]

    def test_failures_degrade_to_no_cache(self, db: Database, monkeypatch) -> None:
        monkeypatch.setattr(db, "_open", lambda: (_ for _ in ()).throw(OSError("down")))
        cache = DatabaseCache(db)
        cache.put("k", classification(), issuer_name=None)
        assert cache.get("k") is None
        assert len(cache) == 0

    def test_the_builder_prefers_the_database(self, db: Database, tmp_path: Path) -> None:
        assert isinstance(build_cache(tmp_path / "c.sqlite3", db), DatabaseCache)


class TestAuditSink:
    def test_lookups_and_reports_land_in_the_table(self, db: Database) -> None:
        sink = DatabaseAuditSink(db)
        audit.set_audit_sink(sink)
        try:
            audit.log_lookup("DE0005140008", outcome="found", user="jana", sources=("GLEIF", "WEB"))
            audit.log_report("DE0005140008", user="jana", stored=True, store="db")
        finally:
            audit.set_audit_sink(None)
        events = sink.events()
        assert [(e.event, e.outcome) for e in events] == [("report", "stored"), ("lookup", "found")]
        assert events[1].sources == "GLEIF+WEB"
        assert events[0].store == "db"
        assert events[1].user == "jana"

    def test_a_failing_sink_never_fails_the_lookup(self, db: Database, monkeypatch, caplog) -> None:
        monkeypatch.setattr(db, "_open", lambda: (_ for _ in ()).throw(OSError("down")))
        audit.set_audit_sink(DatabaseAuditSink(db))
        try:
            with caplog.at_level(logging.WARNING):
                event = audit.log_lookup("X", outcome="found", user="u")
        finally:
            audit.set_audit_sink(None)
        assert event.identifier == "X"
        assert "audit event not stored" in caplog.text

    def test_no_content_column_exists(self) -> None:
        table = next(s for s in SCHEMA if "audit_events (" in s)
        for word in ("description", "name", "payload", "result"):
            assert word not in table.lower().replace("user_name", "")


def a_report():
    from core.suggest import SuggestionRequest

    provider = StubLlmProvider({"NACE": answer("64"), "ESA": answer("2002703")})
    suggestion = make_service(build_codebooks(), provider=provider).suggest(
        SuggestionRequest(name="Nordkap Funding B.V.")
    )
    return build_report(suggestion, note="špatně", user="jana")


class TestReportStore:
    def test_save_and_load(self, db: Database) -> None:
        store = DatabaseReportStore(db)
        item = a_report()
        location = store.save(item)
        assert location.endswith(f"error_reports/{item.id}")
        (back,) = list(store.load())
        assert back == item
        assert json.loads(db.query("SELECT report FROM error_reports")[0][0])["note"] == "špatně"

    def test_a_duplicate_or_outage_is_a_store_error(self, db: Database, monkeypatch) -> None:
        store = DatabaseReportStore(db)
        item = a_report()
        store.save(item)
        with pytest.raises(ReportStoreError):
            store.save(item)
        monkeypatch.setattr(db, "_open", lambda: (_ for _ in ()).throw(OSError("down")))
        with pytest.raises(ReportStoreError, match="refused"):
            store.save(a_report())


class TestSettings:
    def url(self, tmp_path: Path) -> str:
        return f"sqlite:///{(tmp_path / 'c.sqlite3').as_posix()}"

    def test_database_url_is_read_under_both_names(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("DATABASE_URL", self.url(tmp_path))
        assert Settings().database_url is not None
        monkeypatch.delenv("DATABASE_URL")
        monkeypatch.setenv("POSTGRES_URL", self.url(tmp_path))
        assert Settings().database_url is not None
        monkeypatch.setenv("POSTGRES_URL", "   ")
        assert Settings().database_url is None

    def test_auto_reports_follow_the_database(self, tmp_path: Path) -> None:
        assert Settings().effective_reports_source == "dir"
        with_db = Settings(database_url=self.url(tmp_path))
        assert with_db.effective_reports_source == "db"
        assert isinstance(build_report_store(with_db), DatabaseReportStore)
        assert (
            Settings(database_url=self.url(tmp_path), reports_source="dir").effective_reports_source
            == "dir"
        )

    def test_db_without_a_url_is_a_readable_error(self) -> None:
        with pytest.raises(ReportStoreError, match="DATABASE_URL"):
            build_report_store(Settings(reports_source="db"))

    def test_get_database_is_one_instance_per_url(self, tmp_path: Path) -> None:
        settings = Settings(database_url=self.url(tmp_path))
        assert get_database(settings) is get_database(settings)
        assert get_database(Settings()) is None


@pytest.fixture
def client(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'central.sqlite3').as_posix()}"
    settings = Settings(llm_api_key=None, llm_cache_path=None, database_url=url)
    provider = StubLlmProvider({"NACE": answer("64"), "ESA": answer("2002703")})
    api._state["settings"] = settings
    api._state["service"] = make_service(build_codebooks(), provider=provider)
    database = get_database(settings)
    assert database is not None
    audit.set_audit_sink(DatabaseAuditSink(database))
    try:
        yield TestClient(api.app), database
    finally:
        audit.set_audit_sink(None)
        api._state.clear()


class TestPage:
    def test_health_names_the_database_without_its_password(self, client) -> None:
        test_client, _ = client
        body = test_client.get("/health").json()
        assert body["database"].startswith("sqlite ")
        assert body["reports"] == "db"

    def test_a_lookup_and_a_report_are_kept_centrally(self, client) -> None:
        test_client, database = client
        test_client.post("/suggest", data={"name": "Nordkap"}, headers={"X-Remote-User": "jana"})
        page = test_client.post(
            "/report", data={"name": "Nordkap", "note": "x"}, headers={"X-Remote-User": "jana"}
        ).text
        assert "Hlášení uloženo" in page
        events = DatabaseAuditSink(database).events()
        assert [e.event for e in events] == ["report", "lookup", "lookup"]
        assert all(e.user == "jana" for e in events)
        assert database.query("SELECT COUNT(*) FROM error_reports") == [(1,)]

    def test_no_volatile_warning_with_a_database(self, client, monkeypatch) -> None:
        test_client, _ = client
        monkeypatch.setenv("VERCEL", "1")
        assert "dočasného adresáře" not in test_client.get("/").text
