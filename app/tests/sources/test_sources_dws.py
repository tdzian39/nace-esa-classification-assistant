"""DWS adapter: the read-only guard, statement shape, row mapping and failure handling.

No warehouse and no ODBC driver are involved: every test injects a fake DBAPI connection.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from config.settings import Settings
from core.sources.base import NACE_REV_2, NACE_REV_21, SourceQueryError, SourceUnavailableError
from core.sources.dws import (
    COLUMNS_RES,
    TABLES,
    DwsSource,
    ensure_read_only,
    sql_or_by_ico,
    sql_res_by_ico,
    sql_res_nace_by_ico,
    sql_search_by_name,
)
from tests.sources.conftest import FakeConnection, dws_rows

SNAPSHOT = date(2026, 8, 31)


def make_source(connection: FakeConnection, *, schema: str | None = "DWH") -> DwsSource:
    return DwsSource(Settings(dws_schema=schema), connect_factory=lambda: connection)


class TestReadOnlyGuard:
    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM RES_SUBJEKT",
            "UPDATE RES_SUBJEKT SET ICO = '1'",
            "INSERT INTO RES_SUBJEKT VALUES (1)",
            "DROP TABLE RES_SUBJEKT",
            "EXEC sp_who",
            "MERGE INTO RES_SUBJEKT USING x ON 1=1",
        ],
    )
    def test_writes_are_refused(self, sql: str) -> None:
        with pytest.raises(SourceQueryError):
            ensure_read_only(sql)

    def test_chained_statements_are_refused(self) -> None:
        with pytest.raises(SourceQueryError, match="chained"):
            ensure_read_only("SELECT 1; DROP TABLE RES_SUBJEKT")

    def test_select_embedding_a_write_keyword_is_refused(self) -> None:
        """Belt and braces: a subselect must not smuggle a write past the guard."""
        with pytest.raises(SourceQueryError):
            ensure_read_only("SELECT * FROM t WHERE x IN (DELETE FROM y)")

    @pytest.mark.parametrize(
        "sql", ["SELECT 1", "  select ICO FROM t", "WITH x AS (SELECT 1) SELECT * FROM x"]
    )
    def test_reads_pass(self, sql: str) -> None:
        assert ensure_read_only(sql) == sql

    def test_every_generated_statement_is_read_only(self) -> None:
        for builder in (sql_res_by_ico, sql_res_nace_by_ico, sql_or_by_ico, sql_search_by_name):
            assert ensure_read_only(builder("DWH"))

    def test_fetch_all_refuses_a_write(self) -> None:
        source = make_source(FakeConnection([]))
        with pytest.raises(SourceQueryError):
            source._fetch_all("DELETE FROM RES_SUBJEKT", ())


class TestStatements:
    def test_schema_qualifies_the_table(self) -> None:
        assert f"DWH.{TABLES['res_subject']}" in sql_res_by_ico("DWH")

    def test_no_schema_leaves_the_bare_name(self) -> None:
        sql = sql_res_by_ico(None)
        assert TABLES["res_subject"] in sql
        assert "DWH." not in sql

    def test_columns_are_aliased_to_logical_names(self) -> None:
        assert f"{COLUMNS_RES['name']} AS name" in sql_res_by_ico(None)

    def test_the_ico_is_a_bind_parameter_never_inlined(self) -> None:
        """The identifier must travel as a parameter; no statement interpolates user input."""
        source = make_source(FakeConnection(dws_rows(res=[("49240901",) + (None,) * 5])))
        source.fetch_by_ico("49240901")
        sql, params = source._connection.cursors[0].executed[0]
        assert "49240901" not in sql
        assert params == ("49240901",)


class TestRowMapping:
    def _connection(self) -> FakeConnection:
        return FakeConnection(
            dws_rows(
                res=[
                    ("49240901", "Raiffeisenbank a.s.", "12203", date(1993, 6, 25), "121", SNAPSHOT)
                ],
                or_rows=[
                    (
                        "49240901",
                        "Raiffeisenbank a.s.",
                        date(1993, 6, 25),
                        date(1993, 6, 25),
                        "B 2051/MSPH",
                        SNAPSHOT,
                    )
                ],
                nace=[
                    ("49240901", "64190", "2008", 1, "Ostatní peněžní zprostředkování"),
                    ("49240901", "66190", "2008", 0, None),
                    ("49240901", "64190", "2025", "Y", None),
                ],
                activities=[
                    ("49240901", "PODNIKANI", "bankovní obchody"),
                    ("49240901", "CINNOST", "pronájem nemovitostí"),
                ],
            )
        )

    def test_res_half(self) -> None:
        record = make_source(self._connection()).fetch_by_ico("49240901")
        res = record.res
        assert res.name == "Raiffeisenbank a.s."
        assert res.esa_sector == "S.12203"
        assert res.founded_on == date(1993, 6, 25)
        assert res.provenance.source == "DWS"
        assert res.provenance.snapshot_at == datetime(2026, 8, 31, tzinfo=UTC)

    def test_nace_list_keeps_full_codes_and_both_revisions(self) -> None:
        res = make_source(self._connection()).fetch_by_ico("49240901").res
        assert res.main(NACE_REV_2).code == "64190"
        assert [item.code for item in res.others(NACE_REV_2)] == ["66190"]
        assert res.main(NACE_REV_21).code == "64190"
        assert res.nace_mismatch is False

    def test_main_flag_accepts_warehouse_spellings(self) -> None:
        res = make_source(self._connection()).fetch_by_ico("49240901").res
        assert res.main(NACE_REV_21) is not None  # flag came in as "Y"

    def test_activities_are_split_by_kind(self) -> None:
        or_record = make_source(self._connection()).fetch_by_ico("49240901").or_record
        assert or_record.predmet_podnikani == ("bankovní obchody",)
        assert or_record.predmet_cinnosti == ("pronájem nemovitostí",)

    def test_unknown_revision_row_is_skipped_not_guessed(self) -> None:
        connection = FakeConnection(
            dws_rows(
                res=[("49240901", "X", None, None, None, None)],
                nace=[("49240901", "64190", "REV9", 1, None)],
            )
        )
        res = make_source(connection).fetch_by_ico("49240901").res
        assert res.nace == ()

    def test_unparseable_esa_sector_is_dropped(self) -> None:
        connection = FakeConnection(dws_rows(res=[("49240901", "X", "S.1.5", None, None, None)]))
        assert make_source(connection).fetch_by_ico("49240901").res.esa_sector is None


class TestMissingRows:
    def test_unknown_ico_returns_none(self) -> None:
        assert make_source(FakeConnection(dws_rows())).fetch_by_ico("49240901") is None

    def test_res_only_row_is_noted(self) -> None:
        connection = FakeConnection(dws_rows(res=[("49240901",) + (None,) * 5]))
        record = make_source(connection).fetch_by_ico("49240901")
        assert record.or_record is None
        assert any("no OR row" in note for note in record.notes)


class TestFailures:
    def test_a_connection_failure_is_unavailable(self) -> None:
        def explode() -> object:
            raise SourceUnavailableError("no driver")

        source = DwsSource(Settings(), connect_factory=explode)
        with pytest.raises(SourceUnavailableError):
            source.fetch_by_ico("49240901")

    def test_a_query_failure_is_unavailable(self) -> None:
        class BrokenConnection(FakeConnection):
            def cursor(self) -> object:
                raise RuntimeError("connection reset")

        source = make_source(BrokenConnection([]))
        with pytest.raises(SourceUnavailableError, match="cursor"):
            source.fetch_by_ico("49240901")

    def test_not_configured_without_a_dsn(self) -> None:
        assert DwsSource(Settings(dws_dsn=None)).configured is False

    def test_configured_with_a_dsn(self) -> None:
        assert DwsSource(Settings(dws_dsn="DSN=dwh")).configured is True


def test_search_by_name_uses_a_like_parameter() -> None:
    connection = FakeConnection([(("ico", "name"), [("49240901", "Raiffeisenbank a.s.")])])
    source = make_source(connection)
    candidates = source.search_by_name("raiffeisen")

    assert [item.ico for item in candidates] == ["49240901"]
    assert connection.cursors[0].executed[0][1] == ("%RAIFFEISEN%",)


def test_close_closes_the_connection() -> None:
    connection = FakeConnection(dws_rows(res=[("49240901",) + (None,) * 5]))
    source = make_source(connection)
    source.fetch_by_ico("49240901")
    source.close()
    assert connection.closed
