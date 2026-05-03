"""Unit tests for `openspine.identity.security`.

No DB needed — pure-cryptography paths.
"""

from __future__ import annotations

import hashlib

import pyotp
import pytest

from openspine.identity import security

# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def test_hash_password_round_trips() -> None:
    encoded = security.hash_password("correct horse battery staple")
    assert encoded.startswith("$argon2id$")
    assert security.verify_password("correct horse battery staple", encoded)


def test_verify_password_rejects_wrong_password() -> None:
    encoded = security.hash_password("hunter2")
    assert not security.verify_password("hunter3", encoded)


def test_verify_password_returns_false_on_garbage_hash() -> None:
    # A clearly-malformed hash should return False, not raise — the
    # auth surface keeps the structured-error envelope generic.
    assert not security.verify_password("anything", "not-a-real-hash")


def test_password_needs_rehash_is_false_for_fresh_hash() -> None:
    encoded = security.hash_password("anything")
    assert not security.password_needs_rehash(encoded)


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


def test_new_totp_secret_is_base32() -> None:
    secret = security.new_totp_secret()
    # base32 alphabet — pyotp uses the standard 26 letters + 2-7.
    assert all(ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for ch in secret)
    # pyotp default secret length.
    assert len(secret) == 32


def test_totp_provisioning_uri_includes_account_and_issuer() -> None:
    secret = "JBSWY3DPEHPK3PXP"
    uri = security.totp_provisioning_uri(secret, account_name="amina", issuer="OpenSpine")
    assert uri.startswith("otpauth://totp/")
    assert "amina" in uri
    assert "OpenSpine" in uri
    assert secret in uri


def test_verify_totp_accepts_current_code() -> None:
    secret = security.new_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert security.verify_totp(secret, code)


def test_verify_totp_rejects_wrong_code() -> None:
    secret = security.new_totp_secret()
    assert not security.verify_totp(secret, "000000")


def test_verify_totp_rejects_malformed_code() -> None:
    secret = security.new_totp_secret()
    assert not security.verify_totp(secret, "12345")  # too short
    assert not security.verify_totp(secret, "1234567")  # too long
    assert not security.verify_totp(secret, "abcdef")  # non-digit


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(security.TOKEN_PREFIXES))
def test_issue_token_per_kind_returns_prefixed_plaintext_and_hash(kind: str) -> None:
    issued = security.issue_token(kind)
    expected_prefix = security.TOKEN_PREFIXES[kind]
    assert issued.plaintext.startswith(expected_prefix)
    assert issued.prefix.startswith(expected_prefix)
    assert len(issued.prefix) == len(expected_prefix) + 8
    # SHA-256 hex digest is 64 chars.
    assert len(issued.secret_hash) == 64
    assert issued.secret_hash == hashlib.sha256(issued.plaintext.encode()).hexdigest()


def test_issue_token_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="Unknown token kind"):
        security.issue_token("nope")


def test_issued_tokens_are_unique() -> None:
    tokens = {security.issue_token("user_api").plaintext for _ in range(50)}
    assert len(tokens) == 50


def test_constant_time_token_match_round_trips() -> None:
    issued = security.issue_token("agent")
    assert security.constant_time_token_match(issued.plaintext, issued.secret_hash)
    assert not security.constant_time_token_match("osp_agent_wrong", issued.secret_hash)


def test_generated_token_repr_does_not_leak_plaintext() -> None:
    issued = security.issue_token("service")
    assert issued.plaintext not in repr(issued)
    assert "<redacted>" in repr(issued)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_issue_session_id_returns_plaintext_and_matching_hash() -> None:
    plaintext, hashed = security.issue_session_id()
    assert isinstance(plaintext, str)
    assert len(hashed) == 64
    assert hashed == hashlib.sha256(plaintext.encode()).hexdigest()


def test_issued_session_ids_are_unique() -> None:
    sessions = {security.issue_session_id()[0] for _ in range(50)}
    assert len(sessions) == 50
