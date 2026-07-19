import os
from uuid import uuid4

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


def test_review_is_persisted_and_exported() -> None:
    with TestClient(app) as client:
        client.post("/v1/auth/login", json={"email": "admin@test.local", "password": "test-password"})
        session_id = client.post("/v1/sessions?tournament_name=Revisao").json()["id"]
        payload = {"notes": "Revisar ICM", "hands": {"1": "approved"}, "lobby": {}, "rabbits": {},
                   "hand_details": {"1": {"tag": "ICM", "difficulty": "hard", "note": "Bubble"}},
                   "lobby_values": {}, "finalized": True}
        saved = client.post(f"/v1/sessions/{session_id}/review", json=payload)
        assert saved.status_code == 200
        assert client.get(f"/v1/sessions/{session_id}/review").json()["hand_details"]["1"]["tag"] == "ICM"
        exported = client.get(f"/v1/sessions/{session_id}/review/export")
        assert exported.status_code == 200
        assert exported.json()["post_session_only"] is True


def test_session_metadata_and_consolidated_export() -> None:
    with TestClient(app) as client:
        client.post("/v1/auth/login", json={"email": "admin@test.local", "password": "test-password"})
        session_id = client.post("/v1/sessions?tournament_name=Antes").json()["id"]
        updated = client.post(f"/v1/sessions/{session_id}/metadata", json={"tournament_name": "Depois"})
        assert updated.status_code == 200
        assert updated.json()["tournament_name"] == "Depois"
        exported = client.get("/v1/sessions/export")
        assert exported.status_code == 200
        assert any(item["id"] == session_id for item in exported.json()["sessions"])


def test_admin_creates_internal_user_with_isolated_sessions() -> None:
    email = f"player-{uuid4()}@test.local"
    with TestClient(app) as client:
        client.post("/v1/auth/login", json={"email": "admin@test.local", "password": "test-password"})
        created = client.post("/v1/admin/users", json={"email": email, "password": "player-password", "role": "player"})
        assert created.status_code == 201
        client.post("/v1/auth/logout")
        assert client.post("/v1/auth/login", json={"email": email, "password": "player-password"}).status_code == 200
        own_session = client.post("/v1/sessions?tournament_name=Jogador").json()["id"]
        assert any(item["id"] == own_session for item in client.get("/v1/sessions").json())
        assert client.get("/v1/admin/users").status_code == 403
