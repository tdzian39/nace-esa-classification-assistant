"""Fixtures for every test."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from config.settings import get_settings


@pytest.fixture(autouse=True)
def _sign_in_off_unless_asked(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A developer's ``app/.env`` must not change what the tests see: ``APP_PASSWORD_HASH``
    would put every page test behind the sign-in page, ``DATABASE_URL`` would send the
    stores to the central database, ``ADMIN_PASSWORD_HASH`` would grow a link. An
    environment variable outranks ``.env``, and blank means unset; tests that want one
    pass it to ``Settings`` directly, which outranks both."""
    for name in (
        "APP_PASSWORD_HASH",
        "SESSION_SECRET",
        "ADMIN_PASSWORD_HASH",
        "DATABASE_URL",
        "POSTGRES_URL",
    ):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
