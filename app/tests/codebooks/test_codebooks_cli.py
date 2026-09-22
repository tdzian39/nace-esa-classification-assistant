"""Tests of ``python -m core.codebooks`` (in-process and as a subprocess)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.codebooks.__main__ import EXIT_INCONSISTENT, EXIT_LOAD_FAILED, EXIT_OK, main
from core.codebooks.loaders import load_codebooks

from .conftest import (
    FILE_NAMES,
    CodebookRows,
    MakeCodebookDir,
    corrupt_sheet_xml,
    make_settings,
    write_xlsx,
)

APP_DIR = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "CODEBOOK_CTS_BA0036_FILE": FILE_NAMES["cts_ba0036"],
        "CODEBOOK_BA0036_VALID_FILE": FILE_NAMES["ba0036_valid"],
        "CODEBOOK_CTS_OKEC_NACE2_FILE": FILE_NAMES["cts_okec_nace2"],
        "CODEBOOK_NACE_STAT_FILE": FILE_NAMES["nace_stat"],
        "CODEBOOK_VERSION_LABEL": "",
        "LOG_LEVEL": "WARNING",
    }
    return subprocess.run(
        [sys.executable, "-m", "core.codebooks", *args],
        cwd=APP_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def test_cli_ok_prints_version(codebook_dir: Path) -> None:
    expected = load_codebooks(make_settings(codebook_dir)).version.id
    result = _run("--dir", str(codebook_dir))
    assert result.returncode == EXIT_OK, result.stderr
    assert expected in result.stdout
    assert "Codebook consistency OK" in result.stdout
    assert "I_CTS_ESA_NOT_VALID_LEAF" in result.stdout


def test_cli_broken_dir_returns_1(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [row for row in defaults.cts_ba0036 if row[1] != "S.14"]
    directory = make_codebook_dir(tmp_path / "broken", cts_ba0036=rows)
    result = _run("--dir", str(directory))
    assert result.returncode == EXIT_INCONSISTENT
    assert "E_VALID_ESA_WITHOUT_CTS_ID" in result.stdout
    assert "Codebook consistency FAILED" in result.stdout
    assert "cb-" in result.stdout  # describe() is still printed thanks to the error's codebooks

    lenient = _run("--dir", str(directory), "--no-strict")
    assert lenient.returncode == EXIT_INCONSISTENT
    assert "E_VALID_ESA_WITHOUT_CTS_ID" in lenient.stdout


def test_cli_nonexistent_dir_returns_2(tmp_path: Path) -> None:
    result = _run("--dir", str(tmp_path / "nowhere"))
    assert result.returncode == EXIT_LOAD_FAILED
    assert "not found" in result.stderr
    assert result.stdout == ""


def test_cli_json(codebook_dir: Path) -> None:
    result = _run("--dir", str(codebook_dir), "--json")
    assert result.returncode == EXIT_OK, result.stderr
    document = json.loads(result.stdout)
    assert document["ok"] is True
    assert document["version"]["id"].startswith("cb-")
    assert document["version"]["label"] is None
    assert {f["name"] for f in document["version"]["files"]} == set(FILE_NAMES)
    assert document["counts"] == {"error": 0, "warning": 0, "info": 2}
    assert {f["code"] for f in document["findings"]} == {
        "I_CTS_ESA_NOT_VALID_LEAF",
        "I_EMITTABLE_IDS",
    }
    assert document["summary"].startswith("codebooks cb-")


def test_cli_json_on_unreadable_file_returns_2(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir
) -> None:
    directory = make_codebook_dir(tmp_path / "cb")
    (directory / FILE_NAMES["nace_stat"]).write_text("not a workbook", encoding="utf-8")
    result = _run("--dir", str(directory), "--json")
    assert result.returncode == EXIT_LOAD_FAILED
    assert result.stdout == ""
    assert "NACE_STAT.xlsx" in result.stderr


def test_main_in_process(
    codebook_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.codebooks.__main__ as cli

    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(codebook_dir))
    assert main(["--json"]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert main(["--dir", str(codebook_dir / "missing")]) == EXIT_LOAD_FAILED
    assert "not found" in capsys.readouterr().err


def test_cli_json_with_errors_returns_1_and_findings(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    rows = [row for row in defaults.cts_ba0036 if row[1] != "S.14"]
    result = _run("--dir", str(make_codebook_dir(tmp_path / "broken", cts_ba0036=rows)), "--json")
    assert result.returncode == EXIT_INCONSISTENT
    document = json.loads(result.stdout)
    assert document["ok"] is False
    assert document["counts"]["error"] == 1
    assert document["findings"][0]["code"] == "E_VALID_ESA_WITHOUT_CTS_ID"
    assert document["findings"][0]["details"]["codes"] == ["S.14"]
    assert document["version"]["id"].startswith("cb-")


def test_cli_schema_error_returns_2(tmp_path: Path, make_codebook_dir: MakeCodebookDir) -> None:
    directory = make_codebook_dir(tmp_path / "cb")
    write_xlsx(directory / FILE_NAMES["ba0036_valid"], ("A", "B", "C"), [(1, 2, 3)])
    result = _run("--dir", str(directory))
    assert result.returncode == EXIT_LOAD_FAILED
    assert "BA0036_2024_jen_validni.xlsx" in result.stderr and "headers found" in result.stderr
    assert result.stdout == ""


def test_cli_corrupted_sheet_returns_2(tmp_path: Path, make_codebook_dir: MakeCodebookDir) -> None:
    directory = make_codebook_dir(tmp_path / "cb")
    corrupt_sheet_xml(directory / FILE_NAMES["nace_stat"], truncate=True)
    result = _run("--dir", str(directory), "--json")
    assert result.returncode == EXIT_LOAD_FAILED
    assert result.stdout == ""
    assert "NACE_STAT.xlsx" in result.stderr and "Traceback" not in result.stderr
