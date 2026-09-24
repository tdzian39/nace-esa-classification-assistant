"""The shared password, the typed name and the session cookie (core/auth.py)."""

from __future__ import annotations

import pytest

from core.auth import (
    NAME_LIMIT,
    SessionSigner,
    hash_password,
    is_password_hash,
    normalize_name,
    verify_password,
)

#: Fast hashes: the tests check behaviour, not the work factor.
FAST = 1_000


def fast_hash(password: str) -> str:
    return hash_password(password, iterations=FAST)


class TestPassword:
    def test_the_right_password_matches_and_a_wrong_one_does_not(self) -> None:
        encoded = fast_hash("spolecne-heslo")
        assert verify_password("spolecne-heslo", encoded)
        assert not verify_password("spolecne-hesl0", encoded)

    def test_the_hash_is_salted(self) -> None:
        assert fast_hash("same") != fast_hash("same")

    def test_the_default_work_factor_is_owasps(self) -> None:
        assert hash_password("x").split("$")[:2] == ["pbkdf2_sha256", "600000"]

    def test_the_password_is_not_in_the_hash(self) -> None:
        assert "spolecne-heslo" not in fast_hash("spolecne-heslo")

    @pytest.mark.parametrize("encoded", ["", "plain", "md5$1$a$b", "pbkdf2_sha256$x$a$b"])
    def test_a_malformed_hash_is_a_mismatch_not_an_error(self, encoded: str) -> None:
        assert not verify_password("anything", encoded)
        assert not is_password_hash(encoded)

    def test_a_real_hash_is_recognised(self) -> None:
        assert is_password_hash(fast_hash("x"))

    def test_an_empty_password_cannot_be_hashed(self) -> None:
        with pytest.raises(ValueError):
            hash_password("")


class TestName:
    @pytest.mark.parametrize(
        ("typed", "kept"),
        [
            ("jan novak", "jan novak"),
            ("Jan Novák", "jan novak"),
            ("  JAN   NOVÁK  ", "jan novak"),
            ("Jiří Šťastný-Čech", "jiri stastny-cech"),
            ("Ondřej Žďárský", "ondrej zdarsky"),
            ("jan.novak@rb.cz", "jan.novak rb.cz"),
        ],
    )
    def test_one_person_is_one_ledger_key_however_it_is_typed(self, typed: str, kept: str) -> None:
        assert normalize_name(typed) == kept

    @pytest.mark.parametrize("typed", ["", "   ", "!!!", "Unknown", " UNKNOWN "])
    def test_nothing_usable_or_the_reserved_name_is_refused(self, typed: str) -> None:
        assert normalize_name(typed) is None

    def test_it_is_capped(self) -> None:
        assert len(normalize_name("a" * 500) or "") == NAME_LIMIT


class TestSession:
    HASH = fast_hash("spolecne-heslo")
    SIGNER = SessionSigner(secret=b"s" * 32, max_age_seconds=3600, password_hash=HASH)

    def test_a_cookie_carries_its_name(self) -> None:
        token = self.SIGNER.issue("jan novak", now=1_000)
        assert self.SIGNER.verify(token, now=1_500) == "jan novak"

    def test_it_expires(self) -> None:
        token = self.SIGNER.issue("jan novak", now=1_000)
        assert self.SIGNER.verify(token, now=1_000 + 3601) is None

    def test_another_secret_does_not_accept_it(self) -> None:
        token = self.SIGNER.issue("jan novak", now=1_000)
        other = SessionSigner(secret=b"t" * 32, max_age_seconds=3600, password_hash=self.HASH)
        assert other.verify(token, now=1_500) is None

    def test_a_new_password_ends_every_session(self) -> None:
        token = self.SIGNER.issue("jan novak", now=1_000)
        rotated = SessionSigner(
            secret=b"s" * 32, max_age_seconds=3600, password_hash=fast_hash("nove-heslo")
        )
        assert rotated.verify(token, now=1_500) is None

    def test_the_name_cannot_be_swapped(self) -> None:
        """Re-signing is the only way to change the name, and that needs the secret."""
        jan = self.SIGNER.issue("jan novak", now=1_000)
        petr = self.SIGNER.issue("petr svoboda", now=1_000)
        forged = f"{petr.split('.')[0]}.{jan.split('.')[1]}"
        assert self.SIGNER.verify(forged, now=1_500) is None

    @pytest.mark.parametrize("token", [None, "", "garbage", "a.b.c", "!!!.???"])
    def test_garbage_is_nobody(self, token: str | None) -> None:
        assert self.SIGNER.verify(token) is None
