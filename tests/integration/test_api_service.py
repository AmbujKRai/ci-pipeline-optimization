from collections.abc import Iterator

from sqlalchemy.exc import OperationalError

from app.database import get_db
from tests.conftest import make_settings


def test_health_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_a_broken_database(app, client):
    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("database is gone"))

    def broken_db() -> Iterator[BrokenSession]:
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_version_reports_the_build(client):
    body = client.get("/version").json()
    assert body["git_sha"] == "0123456789abcdef"
    assert body["environment"] == "test"
    assert body["version"] == "1.0.0"


def test_every_response_carries_the_build_header(client):
    assert client.get("/health").headers["X-App-Version"] == "1.0.0+0123456"


def test_openapi_and_docs_are_served(client):
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "ShopLite API"
    assert "/orders/{order_id}/pay" in spec["paths"]
    assert client.get("/docs").status_code == 200


def test_unknown_route_is_404(client):
    assert client.get("/no-such-page").status_code == 404


def test_dashboard_renders_with_demo_data():
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(make_settings(seed_demo_data=True, git_sha="feedface1234"))
    with TestClient(app) as client:
        page = client.get("/")
    assert page.status_code == 200
    assert "Store dashboard" in page.text
    assert 'data-testid="deployed-sha">feedfac<' in page.text
    assert "Basmati Rice 5 kg" in page.text
    assert "₹" in page.text


def test_dashboard_renders_on_an_empty_database(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "No sales yet." in page.text


def test_static_stylesheet_is_served(client):
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert "--accent" in response.text


def test_seeding_is_idempotent(app, db, settings):
    from sqlalchemy import func, select

    from app.models import User
    from app.seed import seed_demo_data

    assert seed_demo_data(db, settings) is True
    users = db.scalar(select(func.count(User.id)))
    assert seed_demo_data(db, settings) is False
    assert db.scalar(select(func.count(User.id))) == users
