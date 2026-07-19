import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./coach_ia_pytest.db"
os.environ["AUTH_SECRET"] = "test-secret-long-enough"
os.environ["ADMIN_EMAIL"] = "admin@test.local"
os.environ["ADMIN_PASSWORD"] = "test-password"

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_login_survives_followup_and_logout() -> None:
    with TestClient(app) as client:
        assert client.get("/v1/auth/me").status_code == 401
        assert client.post("/v1/auth/login", json={"email": "admin@test.local", "password": "test-password"}).status_code == 200
        assert client.get("/v1/auth/me").status_code == 200
        assert client.post("/v1/auth/logout").status_code == 204
        assert client.get("/v1/auth/me").status_code == 401


def test_session_is_persisted_in_history() -> None:
    with TestClient(app) as client:
        client.post("/v1/auth/login", json={"email": "admin@test.local", "password": "test-password"})
        created = client.post("/v1/sessions?tournament_name=Teste").json()
        history = client.get("/v1/sessions")
        assert history.status_code == 200
        assert any(item["id"] == created["id"] for item in history.json())
