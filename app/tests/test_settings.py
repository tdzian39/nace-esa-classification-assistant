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


@pytest.mark.parametrize("raw", ["", "   "])
@pytest.mark.parametrize(
    ("variable", "field"),
    [("LOOKUP_USER", "lookup_user"), ("OPENFIGI_API_KEY", "openfigi_api_key")],
)
def test_blank_optional_values_mean_not_set(
    monkeypatch: pytest.MonkeyPatch, variable: str, field: str, raw: str
) -> None:
    """A whitespace-only LOOKUP_USER must fall back to the OS user, not be audited as a name."""
    monkeypatch.setenv(variable, raw)
    assert getattr(Settings(_env_file=None), field) is None


def test_a_set_lookup_user_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOKUP_USER", "mo.analyst")
    assert Settings(_env_file=None).lookup_user == "mo.analyst"


def test_an_old_env_file_with_removed_variables_still_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An app/.env written before the Tool 2 removal must not stop the app starting."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DWS_DSN=DSN=dwh\nARES_ENABLED=true\nLOG_LEVEL=WARNING\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)
    assert settings.log_level == "WARNING"
    assert not hasattr(settings, "dws_dsn")
