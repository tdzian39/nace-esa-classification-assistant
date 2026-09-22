"""The lookup audit trail: who asked what and when - and what must never be written to it."""

from __future__ import annotations

import logging

import pytest

from config.settings import Settings
from core.audit import current_user, log_lookup


class TestCurrentUser:
    def test_settings_win(self) -> None:
        assert current_user(Settings(lookup_user="mo.analyst")) == "mo.analyst"

    def test_environment_is_used_when_settings_are_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOOKUP_USER", "reporting.batch")
        assert current_user(Settings(lookup_user=None)) == "reporting.batch"

    def test_falls_back_to_the_os_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOOKUP_USER", raising=False)
        monkeypatch.setattr("getpass.getuser", lambda: "winuser")
        assert current_user(Settings(lookup_user=None)) == "winuser"

    def test_never_raises_when_the_user_is_unidentifiable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unknown user is recorded as unknown; it must not abort the lookup."""
        monkeypatch.delenv("LOOKUP_USER", raising=False)

        def explode() -> str:
            raise OSError("no login name")

        monkeypatch.setattr("getpass.getuser", explode)
        assert current_user(Settings(lookup_user=None)) == "unknown"


class TestLogLookup:
    def test_records_identifier_timestamp_and_user(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="core.audit"):
            event = log_lookup("49240901", outcome="found", user="tester", ico="49240901")

        assert event.identifier == "49240901"
        assert event.user == "tester"
        assert event.at.tzinfo is not None
        assert "identifier='49240901'" in caplog.records[0].message
        assert "user=tester" in caplog.records[0].message

    def test_structured_fields_are_attached_for_a_json_handler(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="core.audit"):
            log_lookup("49240901", outcome="found", user="tester", sources=("DWS",))

        assert caplog.records[0].audit["sources"] == ["DWS"]
        assert caplog.records[0].audit["outcome"] == "found"

    def test_a_source_failure_is_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="core.audit"):
            log_lookup("49240901", outcome="error", user="tester")
        assert caplog.records[0].levelno == logging.WARNING

    def test_a_miss_is_only_informational(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="core.audit"):
            log_lookup("49240901", outcome="not_found", user="tester")
        assert caplog.records[0].levelno == logging.INFO

    def test_retrieved_content_never_reaches_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The audit trail holds the question and the outcome, never the answer."""
        with caplog.at_level(logging.INFO, logger="core.audit"):
            event = log_lookup(
                "49240901", outcome="found", user="tester", ico="49240901", sources=("DWS",)
            )

        assert set(event.as_dict()) == {
            "identifier",
            "ico",
            "user",
            "at",
            "outcome",
            "sources",
            "detail",
        }

    def test_event_is_json_serializable(self) -> None:
        import json

        event = log_lookup("49240901", outcome="found", user="tester", sources=("DWS",))
        assert json.loads(json.dumps(event.as_dict()))["identifier"] == "49240901"
