"""The sign-in page: one shared password and a typed name; who gets in, and whom a lookup's
model calls are charged to."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import main as api
from config.settings import Settings
from core.auth import SESSION_COOKIE, hash_password
from core.classify.budget import Budget, BudgetedProvider, SqliteLedger
from core.classify.provider import StubLlmProvider
from tests.api.test_api_suggest import answer, make_service
from tests.classify.conftest import build_codebooks

PASSWORD = "spolecne-heslo"
PASSWORD_HASH = hash_password(PASSWORD, iterations=1_000)
SECRET = "a-test-secret-that-is-long-enough-000"


def wire(ledger: SqliteLedger | None = None, **overrides: object) -> TestClient:
    settings = {
        "llm_api_key": None,
        "llm_cache_path": None,
        "app_password_hash": PASSWORD_HASH,
        "session_secret": SECRET,
        **overrides,
    }
    stub = StubLlmProvider({"NACE": answer("64"), "ESA": answer("2002703")})
    provider = (
        BudgetedProvider(stub, budget=Budget(daily_token_budget=0), ledger=ledger)
        if ledger is not None
        else stub
    )
    api._state["settings"] = Settings(**settings)
    api._state["service"] = make_service(build_codebooks(), provider=provider)
    return TestClient(api.app)


@pytest.fixture
def client() -> Iterator[TestClient]:
    try:
        yield wire()
    finally:
        api._state.clear()


@pytest.fixture
def ledger(tmp_path: Path) -> SqliteLedger:
    return SqliteLedger(tmp_path / "usage.sqlite3")


def sign_in(client: TestClient, name: str = "jan novak", password: str = PASSWORD, **form):
    return client.post(
        "/login", data={"name": name, "password": password, **form}, follow_redirects=False
    )


class TestTheGate:
    def test_the_page_sends_a_stranger_to_sign_in_and_back(self, client: TestClient) -> None:
        response = client.get("/?isin=x", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=%2F%3Fisin%3Dx"

    def test_the_api_answers_401_rather_than_a_page(self, client: TestClient) -> None:
        assert client.post("/api/suggest", json={"name": "Nordkap"}).status_code == 401

    def test_htmx_is_told_to_go_to_the_sign_in_page(self, client: TestClient) -> None:
        response = client.post("/suggest", data={"name": "Nordkap"}, headers={"HX-Request": "true"})
        assert response.status_code == 401
        assert response.headers["HX-Redirect"] == "/login"

    def test_the_download_is_closed_too(self, client: TestClient) -> None:
        response = client.get("/suggest.xlsx?name=Nordkap", follow_redirects=False)
        assert response.status_code == 303

    @pytest.mark.parametrize("path", ["/health", "/api/version", "/login"])
    def test_health_version_and_sign_in_stay_open(self, client: TestClient, path: str) -> None:
        assert client.get(path, follow_redirects=False).status_code == 200

    def test_a_forged_cookie_is_nobody(self, client: TestClient) -> None:
        client.cookies.set(SESSION_COOKIE, "eyJ.forged")
        assert client.get("/", follow_redirects=False).status_code == 303

    def test_the_user_header_is_no_way_in(self, client: TestClient) -> None:
        """Anything a browser can send is not an identity."""
        response = client.get("/", headers={"X-Remote-User": "jan"}, follow_redirects=False)
        assert response.status_code == 303


class TestTheForm:
    def test_it_asks_for_a_name_and_the_shared_password(self, client: TestClient) -> None:
        text = client.get("/login").text
        assert 'name="name"' in text and 'type="password"' in text
        assert "Společné heslo" in text

    def test_it_asks_for_the_name_in_lower_case_without_diacritics(
        self, client: TestClient
    ) -> None:
        assert "malými písmeny a bez diakritiky" in client.get("/login").text

    def test_it_asks_only_what_sign_in_needs(self, client: TestClient) -> None:
        """The page is about signing in; the usage report is documented elsewhere."""
        text = client.get("/login").text.lower()
        for word in ("zaznamen", "ledger", "náklad", "útrat", "volání modelu"):
            assert word not in text


class TestSigningIn:
    def test_the_password_and_a_name_let_you_in_and_on(self, client: TestClient) -> None:
        response = sign_in(client, next="/?isin=x")
        assert response.status_code == 303
        assert response.headers["location"] == "/?isin=x"
        cookie = response.headers["set-cookie"]
        assert SESSION_COOKIE in cookie and "HttpOnly" in cookie
        assert "samesite=lax" in cookie.lower()
        page = client.get("/")
        assert page.status_code == 200
        assert "jan novak" in page.text and "Odhlásit" in page.text

    def test_the_name_is_normalised(self, client: TestClient) -> None:
        sign_in(client, name="  Jan  Novák ")
        chip = '<span class="who" title="Přihlášený uživatel">jan novak</span>'
        assert chip in client.get("/").text

    def test_anyone_with_the_password_gets_in_under_their_own_name(
        self, client: TestClient
    ) -> None:
        sign_in(client, name="petr svoboda")
        assert "petr svoboda" in client.get("/").text

    def test_a_wrong_password_is_refused_and_keeps_the_name(self, client: TestClient) -> None:
        response = sign_in(client, password="nope")
        assert response.status_code == 401
        assert "Nesprávné heslo" in response.text
        assert 'value="jan novak"' in response.text
        assert SESSION_COOKIE not in response.headers.get("set-cookie", "")

    @pytest.mark.parametrize("name", ["", "   ", "!!!", "unknown"])
    def test_a_name_is_required(self, client: TestClient, name: str) -> None:
        response = sign_in(client, name=name)
        assert response.status_code == 422
        assert "Zadejte své jméno" in response.text

    @pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/", "/\\evil"])
    def test_it_never_sends_anyone_to_another_site(self, client: TestClient, target: str) -> None:
        assert sign_in(client, next=target).headers["location"] == "/"

    def test_signing_out_forgets_the_session(self, client: TestClient) -> None:
        sign_in(client)
        response = client.post("/logout", follow_redirects=False)
        assert response.headers["location"] == "/login"
        assert client.get("/", follow_redirects=False).status_code == 303

    def test_a_signed_in_visit_to_the_form_goes_straight_on(self, client: TestClient) -> None:
        sign_in(client)
        response = client.get("/login?next=/probe", follow_redirects=False)
        assert response.headers["location"] == "/probe"

    def test_a_new_password_signs_everybody_out(self, client: TestClient) -> None:
        sign_in(client)
        api._state["settings"] = Settings(
            llm_api_key=None,
            llm_cache_path=None,
            app_password_hash=hash_password("nove-heslo", iterations=1_000),
            session_secret=SECRET,
        )
        assert client.get("/", follow_redirects=False).status_code == 303


class TestFailsClosed:
    def teardown_method(self) -> None:
        api._state.clear()

    def test_a_hash_without_a_secret_lets_nobody_in(self) -> None:
        client = wire(session_secret=None)
        assert client.get("/", follow_redirects=False).status_code == 303
        assert "SESSION_SECRET" in client.get("/login").text
        assert sign_in(client).status_code == 503

    def test_a_plain_password_instead_of_a_hash_lets_nobody_in(self) -> None:
        client = wire(app_password_hash="heslo123")
        assert client.get("/", follow_redirects=False).status_code == 303
        page = client.get("/login").text
        assert "APP_PASSWORD_HASH" in page and "heslo123" not in page
        assert sign_in(client, password="heslo123").status_code == 503

    def test_without_a_password_the_app_is_open_as_before(self) -> None:
        client = wire(app_password_hash=None, session_secret=None)
        assert client.get("/").status_code == 200
        assert client.get("/login", follow_redirects=False).headers["location"] == "/"


class TestSpendPerUser:
    def teardown_method(self) -> None:
        api._state.clear()

    def test_a_lookup_is_charged_to_the_typed_name(self, ledger: SqliteLedger) -> None:
        client = wire(ledger)
        sign_in(client, name="Jan Novák")
        assert client.post("/suggest", data={"name": "Nordkap Funding B.V."}).status_code == 200
        records = ledger.records()
        assert records, "the stubbed model was called"
        assert {record.user for record in records} == {"jan novak"}

    def test_the_api_and_the_download_are_charged_too(self, ledger: SqliteLedger) -> None:
        client = wire(ledger)
        sign_in(client)
        client.post("/api/suggest", json={"name": "Nordkap"})
        client.get("/suggest.xlsx?name=Petrov")
        assert {record.user for record in ledger.records()} == {"jan novak"}

    def test_two_people_are_two_lines(self, ledger: SqliteLedger) -> None:
        client = wire(ledger)
        sign_in(client, name="jan novak")
        client.post("/suggest", data={"name": "Nordkap"})
        client.post("/logout")
        sign_in(client, name="petr svoboda")
        client.post("/suggest", data={"name": "Petrov"})
        assert {record.user for record in ledger.records()} == {"jan novak", "petr svoboda"}

    def test_the_audit_names_the_signed_in_user(
        self, ledger: SqliteLedger, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = wire(ledger)
        sign_in(client)
        with caplog.at_level("INFO"):
            client.post("/suggest", data={"name": "Nordkap"})
        assert any("user=jan novak" in message for message in caplog.messages)
