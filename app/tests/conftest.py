"""Fixtures for every test."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from config.settings import get_settings


@pytest.fixture(autouse=True)
def _sign_in_off_unless_asked(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A developer's ``app/.env`` with ``APP_PASSWORD_HASH`` must not put every page test
    behind the sign-in page. An environment variable outranks ``.env``, and blank means
    unset; the sign-in tests pass their hash to ``Settings`` directly, which outranks both."""
    monkeypatch.setenv("APP_PASSWORD_HASH", "")
    monkeypatch.setenv("SESSION_SECRET", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
