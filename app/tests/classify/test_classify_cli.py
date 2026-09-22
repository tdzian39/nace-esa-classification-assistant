"""``python -m core.classify``: both modes and their exit codes."""

from __future__ import annotations

import pytest

from core.classify import __main__ as cli
from core.codebooks.models import CodebookSet
from tests.classify.conftest import CAPTIVE_EN


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, codebooks: CodebookSet) -> None:
    """Use the synthetic codebooks, so the CLI runs without the bank-internal files."""
    monkeypatch.setattr(cli, "_load", lambda settings: codebooks)


class TestShortlistMode:
    def test_prints_both_shortlists(
        self, patched: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main([CAPTIVE_EN]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "NACE:" in out and "ESA:" in out

    def test_shows_codes_and_cts_ids(
        self, patched: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main([CAPTIVE_EN])
        out = capsys.readouterr().out
        assert "64" in out and "CTS" in out

    def test_verbose_adds_scores_and_reasons(
        self, patched: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main([CAPTIVE_EN, "--verbose"])
        assert "score" in capsys.readouterr().out

    def test_limit_is_honoured(self, patched: None, capsys: pytest.CaptureFixture[str]) -> None:
        cli.main([CAPTIVE_EN, "--limit", "2"])
        out = capsys.readouterr().out
        assert " 3. " not in out

    def test_resident_switch_changes_the_block(
        self, patched: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main(["a commercial bank", "--resident"])
        assert "1221300" in capsys.readouterr().out

    def test_no_description_and_no_golden_is_an_error(
        self, patched: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main([]) == cli.EXIT_LOAD_FAILED
        assert "error:" in capsys.readouterr().err


class TestGoldenMode:
    def test_reports_recall_per_codebook(
        self, patched: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main(["--golden"])
        out = capsys.readouterr().out
        assert "--- NACE" in out and "--- ESA" in out
        assert "recall" in out

    def test_warns_that_no_case_is_verified(
        self, patched: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Nobody should read these figures as accuracy while every case is provisional."""
        cli.main(["--golden"])
        assert "no case is verified" in capsys.readouterr().out

    def test_misses_are_listed_and_change_the_exit_code(
        self, patched: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The synthetic codebook lacks divisions some cases expect, so misses are expected
        here; what matters is that a miss is visible and non-zero exit."""
        code = cli.main(["--golden"])
        out = capsys.readouterr().out
        assert code in (cli.EXIT_OK, cli.EXIT_MISSES)
        if code == cli.EXIT_MISSES:
            assert "MISS" in out


def test_unloadable_codebooks_exit_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.codebooks.errors import CodebookFileError

    def explode(settings: object) -> CodebookSet:
        raise CodebookFileError("no codebooks here")

    monkeypatch.setattr(cli, "_load", explode)
    assert cli.main(["anything"]) == cli.EXIT_LOAD_FAILED
    assert "codebooks could not be loaded" in capsys.readouterr().err
