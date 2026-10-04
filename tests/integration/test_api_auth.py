import pytest

from app.models import User
from tests.conftest import TEST_PASSWORD, login

NEW_USER = {"email": "asha@example.com", "full_name": "Asha Patil", "password": "Secure123pass"}


def test_register_creates_a_customer(client):
    response = client.post("/auth/register", json=NEW_USER)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "asha@example.com"
    assert body["role"] == "customer"
    assert "password" not in body
    assert "password_hash" not in body


def test_register_refuses_a_duplicate_email(client):
    client.post("/auth/register", json=NEW_USER)
    duplicate = {**NEW_USER, "email": "ASHA@example.com"}
    assert client.post("/auth/register", json=duplicate).status_code == 409


@pytest.mark.parametrize("password", ["short1", "lettersonly", "123456789", " Spaced123"])
def test_register_enforces_the_password_policy(client, password):
    response = client.post("/auth/register", json={**NEW_USER, "password": password})
    assert response.status_code == 422


@pytest.mark.parametrize("email", ["plain", "a@b", "@example.com"])
def test_register_rejects_invalid_emails(client, email):
    assert client.post("/auth/register", json={**NEW_USER, "email": email}).status_code == 422


def test_registered_user_can_log_in(client):
    client.post("/auth/register", json=NEW_USER)
    response = client.post(
        "/auth/login", json={"email": NEW_USER["email"], "password": NEW_USER["password"]}
    )
    assert response.status_code == 200
    token = response.json()
    assert token["token_type"] == "bearer"
    assert token["expires_in"] == 3600


def test_login_email_is_case_insensitive(client, make_user):
    make_user(email="mixed@example.com")
    response = client.post(
        "/auth/login", json={"email": "Mixed@Example.com", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [("member@example.com", "WrongPass1"), ("nobody@example.com", TEST_PASSWORD)],
)
def test_bad_credentials_are_refused(client, make_user, email, password):
    make_user(email="member@example.com")
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_me_requires_a_token(client):
    response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_me_returns_the_logged_in_user(client, make_user):
    user = make_user(email="me@example.com")
    body = client.get("/auth/me", headers=login(client, user.email)).json()
    assert body["email"] == "me@example.com"


@pytest.mark.parametrize("header", ["Bearer nonsense", "Bearer a.b.c", "Basic dXNlcjpwYXNz"])
def test_me_refuses_bad_tokens(client, header):
    assert client.get("/auth/me", headers={"Authorization": header}).status_code == 401


def test_token_of_a_deleted_user_stops_working(client, db, make_user):
    user = make_user(email="gone@example.com")
    headers = login(client, user.email)
    db.delete(db.get(User, user.id))
    db.commit()
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert "no longer exists" in response.json()["detail"]
