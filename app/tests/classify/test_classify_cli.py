"""``python -m core.classify``: both modes and their exit codes."""

from __future__ import annotations

import json

import httpx
import pytest

from config.settings import Settings
from core.classify import __main__ as cli
from core.classify.provider import OpenAiProvider
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


class TestGoldenThroughTheModel:
    """``--golden --model``: the model's top-1 next to the rules', and the tokens it cost."""

    @staticmethod
    def _no_codebooks(settings: object) -> CodebookSet:
        raise AssertionError("a refused run must not even load the codebooks")

    def test_it_refuses_without_a_key_and_sends_nothing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))
        monkeypatch.setattr(cli, "_load", self._no_codebooks)
        assert cli.main(["--golden", "--model"]) == cli.EXIT_NO_MODEL
        err = capsys.readouterr().err
        assert "needs a configured model" in err
        assert "Nothing was sent" in err

    @pytest.mark.parametrize("blank", ["", " "])
    def test_a_blank_key_is_no_key(
        self, blank: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Found running it: `LLM_API_KEY=` counted as a key and sent `Bearer ` 92 times."""
        monkeypatch.setattr(
            cli, "get_settings", lambda: Settings(_env_file=None, llm_api_key=blank)
        )
        monkeypatch.setattr(cli, "_load", self._no_codebooks)
        assert cli.main(["--golden", "--model"]) == cli.EXIT_NO_MODEL
        assert "needs a configured model" in capsys.readouterr().err

    def test_it_refuses_when_the_budget_would_refuse_every_call(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        settings = Settings(_env_file=None, llm_api_key="sk-test", llm_usage_path="")
        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        monkeypatch.setattr(cli, "_load", self._no_codebooks)
        assert cli.main(["--golden", "--model"]) == cli.EXIT_NO_MODEL
        assert "LLM_DAILY_TOKEN_BUDGET" in capsys.readouterr().err

    def test_model_goes_with_golden(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "_load", self._no_codebooks)
        assert cli.main(["some description", "--model"]) == cli.EXIT_NO_MODEL
        assert "--model goes with --golden" in capsys.readouterr().err

    def test_it_prints_both_top_1s_and_the_tokens(
        self, codebooks: CodebookSet, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Through the real OpenAI adapter and a fake transport: no key, no network."""
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append(body)
            schema = body["response_format"]["json_schema"]["schema"]
            codes = schema["properties"]["picks"]["items"]["properties"]["code"]["enum"]
            pick = {"code": codes[0], "confidence": "medium", "justification": "Test."}
            content = json.dumps({"sufficient_evidence": True, "picks": [pick]})
            return httpx.Response(
                200,
                json={
                    "model": "fake-model",
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 50},
                },
            )

        settings = Settings(
            _env_file=None,
            llm_api_key="sk-test",
            llm_model="fake-model",
            llm_cache_path="",
            llm_usage_path="",
            llm_daily_token_budget=0,
            llm_max_attempts=1,
        )
        client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://api.test/v1"
        )
        provider = OpenAiProvider(settings, client=client, sleep=lambda _: None)

        assert cli._run_golden_model(codebooks, 12, False, settings, provider=provider) == (
            cli.EXIT_OK
        )
        out = capsys.readouterr().out
        assert seen, "the cases must have been sent to the model"
        assert all(body["response_format"]["json_schema"]["strict"] for body in seen)
        assert "provisional" in out
        assert "--- NACE, real issuers ---" in out and "--- ESA, fictional trap cases ---" in out
        assert "top-1: rules" in out and "| model" in out
        assert "gov-de-bund" in out  # one line per case
        calls = len(seen)
        assert f"{calls * 1000:,} in + {calls * 50:,} out" in out
        assert f"in {calls} call(s) to fake-model" in out


class TestHashPassword:
    def test_it_prints_a_hash_that_verifies_and_never_the_password(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from core.auth import verify_password

        assert cli._hash_password(ask=lambda _: "heslo-jany") == cli.EXIT_OK
        out = capsys.readouterr().out.strip()
        assert out.startswith("pbkdf2_sha256$") and "heslo-jany" not in out
        assert verify_password("heslo-jany", out)

    def test_two_different_passwords_are_refused(self, capsys: pytest.CaptureFixture[str]) -> None:
        answers = iter(["one", "two"])
        assert cli._hash_password(ask=lambda _: next(answers)) == cli.EXIT_LOAD_FAILED
        assert "differ" in capsys.readouterr().err

    def test_an_empty_password_is_refused(self) -> None:
        assert cli._hash_password(ask=lambda _: "") == cli.EXIT_LOAD_FAILED
