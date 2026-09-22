"""The Vercel configuration files agree with each other and with the code (roadmap E1).

Each of these mistakes deploys without complaint and fails later: a ``functions`` key that
matches no entrypoint is silently ignored (so ``maxDuration`` falls back to the default), an
upload that forgets ``.vercelignore`` ships the bank-internal codebooks into the bundle, and an
unpinned Python moves to 3.14 the day Vercel changes its default. Facts verified against the
Vercel docs and the ``vercel/vercel`` builder source on 22 Sept 2026.
"""

from __future__ import annotations

import importlib
import json
import tomllib

from fastapi import FastAPI

from config.settings import APP_ROOT

PYPROJECT = tomllib.loads((APP_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
VERCEL = json.loads((APP_ROOT / "vercel.json").read_text(encoding="utf-8"))
IGNORED = {
    line.strip()
    for line in (APP_ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
}


def entrypoint() -> tuple[str, str]:
    module, _, variable = PYPROJECT["tool"]["vercel"]["entrypoint"].partition(":")
    return module, variable


def test_the_entrypoint_is_the_fastapi_app() -> None:
    module, variable = entrypoint()
    assert isinstance(getattr(importlib.import_module(module), variable), FastAPI)


def test_the_functions_key_is_the_entrypoint_file() -> None:
    """Vercel keys function options on the entrypoint path; an unknown key is ignored."""
    module, _ = entrypoint()
    assert list(VERCEL["functions"]) == [module.replace(".", "/") + ".py"]


def test_the_duration_cap_fits_every_plan() -> None:
    """60 s is within Hobby (300 s with Fluid, 60 s without) and Pro alike."""
    (options,) = VERCEL["functions"].values()
    assert 0 < options["maxDuration"] <= 60


def test_the_function_runs_in_frankfurt_under_the_fastapi_preset() -> None:
    assert VERCEL["regions"] == ["fra1"]
    assert VERCEL["framework"] == "fastapi"


def test_no_catch_all_rewrite_is_configured() -> None:
    """Under the FastAPI preset a rewrite to one path makes the app see only that path."""
    assert "rewrites" not in VERCEL
    assert not (APP_ROOT / "api" / "index.py").exists()


def test_a_cli_upload_leaves_secrets_and_codebooks_behind() -> None:
    """The Vercel CLI reads .vercelignore, never .gitignore."""
    assert {".env", ".env.*", "data/", "*.xlsx", "tests/", ".vercel/"} <= IGNORED


def test_the_bundle_excludes_them_too() -> None:
    (options,) = VERCEL["functions"].values()
    for pattern in ("tests/**", "data/**", "**/*.xlsx", ".env"):
        assert pattern in options["excludeFiles"]


def test_python_is_pinned_to_the_lowest_supported_version() -> None:
    pinned = (APP_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert pinned == "3.12"
    assert PYPROJECT["project"]["requires-python"] == f">={pinned}"


def test_the_runtime_dependencies_carry_no_server_and_no_pandas() -> None:
    """Vercel's runtime brings its own uvicorn; pandas was dropped in E0.3."""
    names = [
        dependency.split(">")[0].split("[")[0]
        for dependency in PYPROJECT["project"]["dependencies"]
    ]
    assert "uvicorn" not in names
    assert "pandas" not in names
    assert any(
        item.startswith("uvicorn")
        for item in PYPROJECT["project"]["optional-dependencies"]["server"]
    )


def test_uv_does_not_build_a_second_copy_of_the_app() -> None:
    assert PYPROJECT["tool"]["uv"]["package"] is False
