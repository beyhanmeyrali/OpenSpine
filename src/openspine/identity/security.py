"""Identity security primitives.

Three concerns live here:

1. **Password hashing** — argon2id via `argon2-cffi`. Argon2id is the
   PHC winner and the right choice for low-entropy human-chosen
   secrets. We use library defaults, which are tuned for ~50ms login
   verification on commodity hardware and remain interactive while
   raising the cost to brute-forcers.

2. **TOTP** — RFC 6238 via `pyotp`. Default 30-second step, 6 digits,
   SHA-1 (the RFC 6238 reference algorithm; widely supported by
   authenticator apps).

3. **Opaque tokens** — 256-bit cryptographic randoms, base64url-encoded,
   prefixed for visual identification. **Stored as SHA-256 of the
   plaintext, not argon2id.**

   Why SHA-256 and not argon2id for tokens (a deliberate divergence
   from `docs/identity/authentication.md` v0):

   Argon2 is designed to slow down brute-force attacks against
   *low-entropy* secrets (passwords, PINs). Authentication tokens here
   are 256 bits of cryptographic randomness — there is no entropy
   weakness for argon2 to defend against. Running argon2 per-request
   on every authenticated API call would add ~50ms of latency on the
   hot path with zero security benefit.

   SHA-256 of the plaintext is:
   - **One-way** — a stolen DB cannot be reversed to recover the
     original token (SHA-256 over a 256-bit random has 2^256
     preimage difficulty).
   - **Constant-time-comparable** via `hmac.compare_digest`.
   - **Indexable** for O(1) lookup at the unique-key level.

   The doc has been updated to match.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

# Library defaults: time_cost=3, memory_cost=64 MiB, parallelism=4. These
# tune for roughly 50ms login on modern hardware. If profiling later
# shows login is significantly slower or faster, retune here — the hash
# format is forward-compatible (PasswordHasher.check_needs_rehash will
# tell the service whether to re-hash on next successful login).
_password_hasher = PasswordHasher()


def hash_password(plaintext: str) -> str:
    """Return an argon2id-encoded hash of `plaintext`.

    The output includes the algorithm parameters, salt, and digest in
    the standard PHC format — callers store it as-is in
    `id_credential.secret_hash`.
    """
    return _password_hasher.hash(plaintext)


def verify_password(plaintext: str, encoded_hash: str) -> bool:
    """Return True iff `plaintext` matches `encoded_hash`.

    Constant-time inside `argon2-cffi`. Returns False on mismatch
    rather than raising, so callers can keep the structured-error
    response generic ("authentication_failed", no enumeration leak).
    """
    try:
        return _password_hasher.verify(encoded_hash, plaintext)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_needs_rehash(encoded_hash: str) -> bool:
    """True if the stored hash uses outdated argon2 parameters.

    The auth service calls this after a successful verify; if True, it
    re-hashes the plaintext and updates the credential row. This lets
    us tune `_password_hasher` upward over time without forcing a
    fleet-wide password reset.
    """
    return _password_hasher.check_needs_rehash(encoded_hash)


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


def new_totp_secret() -> str:
    """Generate a fresh base32-encoded TOTP shared secret.

    The secret is what the user scans into their authenticator app. It
    is what we store in `id_credential.secret_hash` for `kind =
    'totp_secret'` rows. (Note: not actually a *hash* — we need the
    secret to verify codes — but the column is reused for symmetry with
    other credential kinds.)
    """
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, *, account_name: str, issuer: str) -> str:
    """Return the otpauth:// URI used by authenticator-app QR codes.

    Format per the Google Authenticator key URI spec. `account_name`
    is typically the username; `issuer` is the deployment label.
    """
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code with default tolerance.

    `pyotp.TOTP.verify` accepts a one-step window by default
    (`valid_window=1` in some examples; we keep the stricter default
    of 0 to avoid replay risk on a single-step lateness — users can
    re-enter the next code). Returns False on mismatch rather than
    raising, mirroring `verify_password`.
    """
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code)


# ---------------------------------------------------------------------------
# Opaque tokens
# ---------------------------------------------------------------------------

# Visual prefix per token kind. Per `docs/identity/authentication.md`
# §"Token security": prefixes make tokens identifiable on sight in logs
# and during admin debugging.
TOKEN_PREFIXES: dict[str, str] = {
    "user_api": "osp_user_",
    "agent": "osp_agent_",
    "service": "osp_svc_",
}

# 32 bytes = 256 bits of entropy.
_TOKEN_SECRET_BYTES = 32


class GeneratedToken:
    """Result of issuing a new token.

    `plaintext` is shown to the caller exactly once. `prefix` and
    `secret_hash` are what get stored in `id_token`. `lookup_hash` is
    an alias of `secret_hash` for clarity in the verification path —
    callers look up the token row by `secret_hash = sha256(plaintext)`.
    """

    __slots__ = ("plaintext", "prefix", "secret_hash")

    def __init__(self, *, plaintext: str, prefix: str, secret_hash: str) -> None:
        self.plaintext = plaintext
        self.prefix = prefix
        self.secret_hash = secret_hash

    def __repr__(self) -> str:
        # Don't leak the plaintext into logs via repr.
        return f"GeneratedToken(prefix={self.prefix!r}, secret_hash=<redacted>)"


def issue_token(kind: str) -> GeneratedToken:
    """Generate a new opaque token for the given `kind`.

    The plaintext is the value to return to the caller; it is never
    stored. The `secret_hash` is the SHA-256 hex digest of the
    plaintext and is what gets persisted in `id_token.secret_hash`.
    """
    if kind not in TOKEN_PREFIXES:
        raise ValueError(f"Unknown token kind {kind!r}. Allowed: {sorted(TOKEN_PREFIXES)}.")
    raw = secrets.token_bytes(_TOKEN_SECRET_BYTES)
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    prefix_label = TOKEN_PREFIXES[kind]
    plaintext = f"{prefix_label}{body}"
    secret_hash = hash_token_plaintext(plaintext)
    # `prefix` is a short visual id used by humans/admins (the kind
    # marker plus the first 8 chars of the body). It must NOT include
    # any portion of the secret beyond what is acceptable to leak in
    # logs; 8 base64 chars = 48 bits, low enough that even partial
    # disclosure is not a meaningful pre-image start.
    visible_prefix = f"{prefix_label}{body[:8]}"
    return GeneratedToken(plaintext=plaintext, prefix=visible_prefix, secret_hash=secret_hash)


def hash_token_plaintext(plaintext: str) -> str:
    """Return the SHA-256 hex digest of a token plaintext.

    Used both at issuance (for storage) and at verification (for the
    `WHERE secret_hash = ?` lookup). Stable across processes.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def constant_time_token_match(presented: str, stored_hash: str) -> bool:
    """Compare a presented token against a stored SHA-256 hash.

    The lookup path is `secret_hash = ?` (O(1)) — this function is
    used as a defence-in-depth check in tests / fallback paths where
    a row was loaded by a non-unique key. Constant-time to avoid
    timing oracles even though the cost is dominated by I/O elsewhere.
    """
    return hmac.compare_digest(hash_token_plaintext(presented), stored_hash)


# ---------------------------------------------------------------------------
# Session ids
# ---------------------------------------------------------------------------


def issue_session_id() -> tuple[str, str]:
    """Return (plaintext, sha256_hex) for a new session id.

    Plaintext goes in the cookie; sha256 goes in `id_session.session_hash`.
    Same rationale as opaque tokens: 256-bit randoms gain nothing from
    argon2 storage, and the cookie validation path runs on every request.
    """
    raw = secrets.token_bytes(_TOKEN_SECRET_BYTES)
    plaintext = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return plaintext, hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


__all__ = [
    "TOKEN_PREFIXES",
    "GeneratedToken",
    "constant_time_token_match",
    "hash_password",
    "hash_token_plaintext",
    "issue_session_id",
    "issue_token",
    "new_totp_secret",
    "password_needs_rehash",
    "totp_provisioning_uri",
    "verify_password",
    "verify_totp",
]
