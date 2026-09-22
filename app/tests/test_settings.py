"""Tests of ``config.settings`` (always a fresh ``Settings(_env_file=None)``, never the cache)."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import APP_ROOT, Settings


def test_relative_codebook_dir_resolves_under_app_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEBOOK_DIR", "data/other-codebooks")
    settings = Settings(_env_file=None)
    assert settings.codebook_dir.is_absolute()
    assert settings.codebook_dir == (APP_ROOT / "data" / "other-codebooks").resolve()


def test_absolute_codebook_dir_is_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEBOOK_DIR", str(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.codebook_dir == tmp_path
    assert settings.codebook_path("X.xlsx") == tmp_path / "X.xlsx"


def test_default_codebook_dir_and_file_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CODEBOOK_DIR",
        "CODEBOOK_CTS_BA0036_FILE",
        "CODEBOOK_BA0036_VALID_FILE",
        "CODEBOOK_CTS_OKEC_NACE2_FILE",
        "CODEBOOK_NACE_STAT_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.codebook_dir == APP_ROOT / "data" / "codebooks"
    assert settings.codebook_cts_ba0036_file == "CTS_BA0036_NEW.xlsx"
    assert settings.codebook_ba0036_valid_file == "BA0036_2024_jen_validni.xlsx"
    assert settings.codebook_cts_okec_nace2_file == "CTS_OKEC_NACE2.xlsx"
    assert settings.codebook_nace_stat_file == "NACE_STAT.xlsx"


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_version_label_is_none(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("CODEBOOK_VERSION_LABEL", raw)
    assert Settings(_env_file=None).codebook_version_label is None


def test_version_label_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEBOOK_VERSION_LABEL", "2024-valid")
    assert Settings(_env_file=None).codebook_version_label == "2024-valid"


def test_env_names_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("log_level", "debug")
    assert Settings(_env_file=None).log_level == "debug"
