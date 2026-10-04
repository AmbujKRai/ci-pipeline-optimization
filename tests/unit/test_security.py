import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.security import (
    AuthError,
    PasswordPolicyError,
    create_access_token,
    decode_access_token,
    hash_password,
    validate_password,
    verify_password,
)

SECRET = "unit-test-secret-key-long-enough-1234567890"
PRODUCTION_ROUNDS = 12


@pytest.mark.parametrize(
    "password", ["Passw0rd!", "abcdefg1", "12345678a", "Ünïcødé123", "correct horse battery 9"]
)
def test_valid_passwords_pass_the_policy(password):
    validate_password(password)


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("short1", "at least 8"),
        ("abcdefgh", "letter and one digit"),
        ("12345678", "letter and one digit"),
        (" Passw0rd", "whitespace"),
        ("Passw0rd ", "whitespace"),
        ("a1" + "é" * 40, "72 bytes"),
    ],
)
def test_weak_passwords_are_refused(password, message):
    with pytest.raises(PasswordPolicyError, match=message):
        validate_password(password)


# Hashing at the production cost factor is deliberately slow (that is the point of bcrypt).


@pytest.mark.slow
@pytest.mark.parametrize("password", ["Passw0rd!", "another-Secret-42", "Ünïcødé123"])
def test_production_strength_hash_round_trip(password):
    hashed = hash_password(password, PRODUCTION_ROUNDS)
    assert hashed.startswith("$2b$12$")
    assert verify_password(password, hashed)
    assert not verify_password(password + "x", hashed)


@pytest.mark.slow
def test_hashes_are_salted():
    first = hash_password("Passw0rd!", PRODUCTION_ROUNDS)
    second = hash_password("Passw0rd!", PRODUCTION_ROUNDS)
    assert first != second
    assert verify_password("Passw0rd!", first)
    assert verify_password("Passw0rd!", second)


@pytest.mark.slow
@pytest.mark.parametrize("rounds", [10, 11, 12])
def test_cost_factor_is_recorded_in_the_hash(rounds):
    assert hash_password("Passw0rd!", rounds)[4:6] == f"{rounds:02d}"


@pytest.mark.parametrize("bad_hash", ["not-a-hash", "!disabled", ""])
def test_malformed_hashes_never_verify(bad_hash):
    assert verify_password("Passw0rd!", bad_hash) is False


def test_overlong_input_does_not_verify():
    hashed = hash_password("Passw0rd!", 4)
    assert verify_password("a" * 100, hashed) is False


def test_token_round_trip():
    token = create_access_token(subject="42", role="admin", secret=SECRET, expires_minutes=5)
    payload = decode_access_token(token, SECRET)
    assert (payload["sub"], payload["role"], payload["iss"]) == ("42", "admin", "shoplite")


def test_expired_token_is_refused():
    issued = datetime.now(UTC) - timedelta(hours=2)
    token = create_access_token(
        subject="1", role="customer", secret=SECRET, expires_minutes=60, now=issued
    )
    with pytest.raises(AuthError, match="expired"):
        decode_access_token(token, SECRET)


def test_token_signed_with_another_key_is_refused():
    token = create_access_token(subject="1", role="customer", secret=SECRET, expires_minutes=5)
    with pytest.raises(AuthError, match="invalid"):
        decode_access_token(token, "a-different-secret-key-also-long-enough")


def test_tampered_token_is_refused():
    token = create_access_token(subject="1", role="customer", secret=SECRET, expires_minutes=5)
    header, payload, signature = token.split(".")
    forged = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    forged["role"] = "admin"
    forged_payload = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    with pytest.raises(AuthError):
        decode_access_token(f"{header}.{forged_payload}.{signature}", SECRET)


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x"])
def test_garbage_tokens_are_refused(garbage):
    with pytest.raises(AuthError):
        decode_access_token(garbage, SECRET)


def test_token_without_subject_is_refused():
    now = datetime.now(UTC)
    token = jwt.encode(
        {"iss": "shoplite", "iat": now, "exp": now + timedelta(minutes=5)}, SECRET, "HS256"
    )
    with pytest.raises(AuthError):
        decode_access_token(token, SECRET)


def test_token_from_another_issuer_is_refused():
    now = datetime.now(UTC)
    claims = {"sub": "1", "iss": "someone-else", "iat": now, "exp": now + timedelta(minutes=5)}
    with pytest.raises(AuthError):
        decode_access_token(jwt.encode(claims, SECRET, "HS256"), SECRET)


def test_unsigned_token_is_refused():
    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    claims = {"sub": "1", "role": "admin", "iss": "shoplite", "iat": exp - 300, "exp": exp}
    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."
    with pytest.raises(AuthError):
        decode_access_token(unsigned, SECRET)
