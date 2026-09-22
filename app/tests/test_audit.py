"""The lookup audit trail: who asked what and when - and what must never be written to it."""

from __future__ import annotations

import logging

import pytest

from config.settings import Settings
from core.audit import Outcome, current_user, log_lookup

ISIN = "DE0005140008"
SOURCES = ("GLEIF", "OPENFIGI", "WEB")
ICO = "49240901"


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
            event = log_lookup(ISIN, outcome="found", user="tester")

        assert event.identifier == ISIN
        assert event.user == "tester"
        assert event.at.tzinfo is not None
        assert f"identifier='{ISIN}'" in caplog.records[0].message
        assert "user=tester" in caplog.records[0].message

    @pytest.mark.parametrize("outcome", ["found", "not_found", "error"])
    def test_the_log_line_states_the_outcome(
        self, caplog: pytest.LogCaptureFixture, outcome: Outcome
    ) -> None:
        """A plain-text handler writes only the message, so the outcome must be in the line."""
        with caplog.at_level(logging.INFO, logger="core.audit"):
            log_lookup(ISIN, outcome=outcome, user="tester")
        assert f"outcome={outcome}" in caplog.records[0].message

    def test_a_given_ico_is_recorded_in_the_event_and_the_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The IČO field was kept in E0.3; while it exists it must carry what it is given."""
        with caplog.at_level(logging.INFO, logger="core.audit"):
            event = log_lookup(ICO, outcome="found", user="tester", ico=ICO)
        assert event.ico == ICO
        assert event.as_dict()["ico"] == ICO
        assert f"ico={ICO}" in caplog.records[0].message

    def test_without_an_ico_the_line_shows_a_dash(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="core.audit"):
            event = log_lookup(ISIN, outcome="found", user="tester")
        assert event.ico is None
        assert "ico=-" in caplog.records[0].message

    def test_structured_fields_are_attached_for_a_json_handler(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="core.audit"):
            log_lookup(ISIN, outcome="found", user="tester", sources=SOURCES)

        assert caplog.records[0].audit["sources"] == ["GLEIF", "OPENFIGI", "WEB"]
        assert "sources=GLEIF+OPENFIGI+WEB" in caplog.records[0].message
        assert caplog.records[0].audit["outcome"] == "found"

    def test_a_source_failure_is_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="core.audit"):
            log_lookup(ISIN, outcome="error", user="tester")
        assert caplog.records[0].levelno == logging.WARNING

    def test_a_miss_is_only_informational(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="core.audit"):
            log_lookup(ISIN, outcome="not_found", user="tester", detail="abstained")
        assert caplog.records[0].levelno == logging.INFO

    def test_retrieved_content_never_reaches_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The audit trail holds the question and the outcome, never the answer."""
        with caplog.at_level(logging.INFO, logger="core.audit"):
            event = log_lookup(ISIN, outcome="found", user="tester", sources=SOURCES)

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

        event = log_lookup(ISIN, outcome="found", user="tester", sources=SOURCES)
        assert json.loads(json.dumps(event.as_dict()))["identifier"] == ISIN
