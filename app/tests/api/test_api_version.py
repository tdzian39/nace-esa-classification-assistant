"""``/api/version``: the deployment's commit, read from Vercel's variables, never raising."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import VERSION_ENV, app


@pytest.mark.parametrize("on_vercel", [True, False], ids=["vercel-git-build", "no-variables"])
def test_version_reports_the_vercel_git_metadata_or_nulls(
    monkeypatch: pytest.MonkeyPatch, on_vercel: bool
) -> None:
    values = {
        "VERCEL_GIT_COMMIT_SHA": "738adb9d62f9bbad62fe23d7a4d73107789290d7",
        "VERCEL_GIT_COMMIT_REF": "main",
        "VERCEL_GIT_COMMIT_MESSAGE": "Add version endpoint",
        "VERCEL_ENV": "production",
        "VERCEL_URL": "nace-esa-assistant-abc123.vercel.app",
    }
    for name in VERSION_ENV.values():
        monkeypatch.delenv(name, raising=False)
        if on_vercel:
            monkeypatch.setenv(name, values[name])

    # No lifespan: the endpoint must not depend on codebooks or settings being loaded.
    response = TestClient(app).get("/api/version")

    assert response.status_code == 200
    expected = {key: values[name] if on_vercel else None for key, name in VERSION_ENV.items()}
    assert response.json() == expected
