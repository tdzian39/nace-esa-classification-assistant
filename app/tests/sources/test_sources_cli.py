"""``python -m core.sources``: output shape, exit codes and identifier input handling."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from config.settings import Settings
from core.sources import __main__ as cli
from core.sources.base import (
    NACE_REV_2,
    NACE_REV_21,
    NaceAssignment,
    OrRecord,
    Provenance,
    ResRecord,
    SubjectRecord,
)
from core.sources.resolver import SubjectResolver
from tests.sources.test_sources_resolver import FakeSource

ICO = "49240901"
RETRIEVED = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def full_record() -> SubjectRecord:
    provenance = Provenance(
        source="ARES_LIVE", retrieved_at=RETRIEVED, snapshot_at=datetime(2026, 8, 31, tzinfo=UTC)
    )
    return SubjectRecord(
        ico=ICO,
        res=ResRecord(
            ico=ICO,
            provenance=provenance,
            name="Raiffeisenbank a.s.",
            nace=(
                NaceAssignment("64190", NACE_REV_2, is_main=True),
                NaceAssignment("66190", NACE_REV_2),
                NaceAssignment("64190", NACE_REV_21, is_main=True),
            ),
            esa_sector="S.12203",
            founded_on=date(1993, 6, 25),
        ),
        or_record=OrRecord(
            ico=ICO,
            provenance=provenance,
            obchodni_firma="Raiffeisenbank a.s.",
            predmet_podnikani=("bankovní obchody",),
            datum_zapisu=date(1993, 6, 25),
            spisova_znacka="B 2051/MSPH",
        ),
        codebook_version="cb-0123456789abcdef",
    )


@pytest.fixture
def patched_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CLI at in-memory settings and skip the codebook load."""
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(dws_dsn=None, lookup_user="tester"))
    monkeypatch.setattr(cli, "_codebook_version", lambda settings: "cb-0123456789abcdef")


def use_sources(monkeypatch: pytest.MonkeyPatch, *sources: FakeSource) -> None:
    monkeypatch.setattr(
        cli,
        "build_default_resolver",
        lambda settings, **kwargs: SubjectResolver(
            list(sources), codebook_version=kwargs.get("codebook_version"), user="tester"
        ),
    )


class TestExitCodes:
    def test_found_exits_zero(
        self, patched_cli: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        use_sources(monkeypatch, FakeSource("ARES_LIVE", result=full_record()))
        assert cli.main([ICO]) == cli.EXIT_OK

    def test_not_found_exits_one(
        self, patched_cli: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        use_sources(monkeypatch, FakeSource("ARES_LIVE", result=None))
        assert cli.main([ICO]) == cli.EXIT_INCOMPLETE

    def test_no_identifiers_exits_one(self, patched_cli: None) -> None:
        assert cli.main([]) == cli.EXIT_INCOMPLETE

    def test_no_source_enabled_exits_two(
        self, patched_cli: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_sources(monkeypatch)
        assert cli.main([ICO]) == cli.EXIT_NO_SOURCE

    def test_every_source_failing_exits_two(
        self, patched_cli: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from core.sources.base import SourceUnavailableError

        use_sources(monkeypatch, FakeSource("DWS", error=SourceUnavailableError("down")))
        assert cli.main([ICO]) == cli.EXIT_NO_SOURCE


class TestOutput:
    def test_json_row_carries_source_timestamp_and_codebook_version(
        self, patched_cli: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The three attribution fields every output row must carry."""
        use_sources(monkeypatch, FakeSource("ARES_LIVE", result=full_record()))
        cli.main([ICO, "--json"])
        row = json.loads(capsys.readouterr().out)[0]["record"]

        assert row["source"] == "ARES_LIVE"
        assert row["timestamp"] == "2026-08-31T00:00:00+00:00"
        assert row["codebook_version"] == "cb-0123456789abcdef"

    def test_json_row_splits_both_revisions_and_flags_the_mismatch(
        self, patched_cli: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        use_sources(monkeypatch, FakeSource("ARES_LIVE", result=full_record()))
        cli.main([ICO, "--json"])
        row = json.loads(capsys.readouterr().out)[0]["record"]

        assert row["RES_nace_rev2_main"] == "64190"
        assert row["RES_nace_rev2_other"] == ["66190"]
        assert row["RES_nace_rev21_main"] == "64190"
        assert row["nace_mismatch"] is False

    def test_json_row_keeps_res_and_or_side_by_side(
        self, patched_cli: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        use_sources(monkeypatch, FakeSource("ARES_LIVE", result=full_record()))
        cli.main([ICO, "--json"])
        row = json.loads(capsys.readouterr().out)[0]["record"]

        assert row["RES_esa_sector"] == "S.12203"
        assert row["OR_predmet_podnikani"] == ["bankovní obchody"]
        assert row["OR_spisova_znacka"] == "B 2051/MSPH"

    def test_json_is_ascii_safe_for_a_windows_console(
        self, patched_cli: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        use_sources(monkeypatch, FakeSource("ARES_LIVE", result=full_record()))
        cli.main([ICO, "--json"])
        out = capsys.readouterr().out
        assert "bankovn\\u00ed" in out
        assert json.loads(out)[0]["record"]["OR_predmet_podnikani"] == ["bankovní obchody"]

    def test_text_output_names_the_status(
        self, patched_cli: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        use_sources(monkeypatch, FakeSource("ARES_LIVE", result=full_record()))
        cli.main([ICO])
        out = capsys.readouterr().out
        assert f"=== {ICO} -> found" in out
        assert "RES_esa_sector" in out

    def test_notes_explain_a_miss(
        self, patched_cli: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        use_sources(monkeypatch, FakeSource("ARES_LIVE", result=None))
        cli.main([ICO])
        assert "not found" in capsys.readouterr().out


class TestIdentifierInput:
    def test_file_input_is_appended_and_comments_ignored(
        self,
        patched_cli: None,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        listing = tmp_path / "icos.txt"
        listing.write_text("# monthly review\n49240901\n\n00177041  # Škoda\n", encoding="utf-8")
        source = FakeSource("ARES_LIVE", result=None)
        use_sources(monkeypatch, source)
        cli.main(["--file", str(listing)])

        assert source.ico_calls == ["49240901", "00177041"]

    def test_utf8_bom_file_is_read(
        self,
        patched_cli: None,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Excel and Notepad both write a BOM; it must not become part of the first IČO."""
        listing = tmp_path / "icos.txt"
        listing.write_text("49240901\n", encoding="utf-8-sig")
        source = FakeSource("ARES_LIVE", result=None)
        use_sources(monkeypatch, source)
        cli.main(["--file", str(listing)])

        assert source.ico_calls == ["49240901"]
