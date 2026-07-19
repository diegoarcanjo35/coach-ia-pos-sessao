from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from redis.asyncio import Redis

app = FastAPI(title="Coach IA API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("/data/uploads")
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com").lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "2048")) * 1024 * 1024
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"


class LoginRequest(BaseModel):
    email: str
    password: str


def _session_token(email: str) -> str:
    payload = base64.urlsafe_b64encode(email.encode()).decode().rstrip("=")
    signature = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def require_user(coach_session: str | None = Cookie(default=None)) -> str:
    if not coach_session or not AUTH_SECRET:
        raise HTTPException(status_code=401, detail="Autenticação necessária")
    try:
        payload, signature = coach_session.rsplit(".", 1)
        expected = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        email = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=401, detail="Sessão inválida") from None
    if email.lower() != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Acesso negado")
    return email


class Session(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    platform: Literal["PPPoker"] = "PPPoker"
    status: Literal["created", "uploaded", "queued"] = "created"
    tournament_name: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    original_filename: str | None = None


class ProcessingStatus(BaseModel):
    session_id: UUID
    status: str
    manifest: dict[str, object] | None = None


SESSIONS: dict[UUID, Session] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "coach-ia-api", "version": app.version}


@app.post("/v1/auth/login")
def login(credentials: LoginRequest, response: Response) -> dict[str, str]:
    valid_email = hmac.compare_digest(credentials.email.lower(), ADMIN_EMAIL)
    valid_password = bool(ADMIN_PASSWORD) and hmac.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (valid_email and valid_password and AUTH_SECRET):
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    response.set_cookie(
        "coach_session", _session_token(ADMIN_EMAIL), httponly=True,
        secure=COOKIE_SECURE, samesite="lax", max_age=60 * 60 * 12, path="/",
    )
    return {"email": ADMIN_EMAIL}


@app.post("/v1/auth/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie("coach_session", path="/")


@app.get("/v1/auth/me")
def me(email: str = Depends(require_user)) -> dict[str, str]:
    return {"email": email}


@app.post("/v1/sessions", response_model=Session, status_code=201)
def create_session(tournament_name: str | None = None, _user: str = Depends(require_user)) -> Session:
    session = Session(tournament_name=tournament_name)
    SESSIONS[session.id] = session
    return session


@app.get("/v1/sessions/{session_id}", response_model=Session)
def get_session(session_id: UUID, _user: str = Depends(require_user)) -> Session:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return session


@app.get("/v1/sessions/{session_id}/processing", response_model=ProcessingStatus)
def processing_status(session_id: UUID, _user: str = Depends(require_user)) -> ProcessingStatus:
    directory = UPLOAD_DIR / str(session_id)
    if not directory.exists():
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        return ProcessingStatus(session_id=session_id, status="queued")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=503, detail="Manifesto temporariamente indisponível") from None
    return ProcessingStatus(session_id=session_id, status=str(manifest.get("status", "unknown")), manifest=manifest)


@app.post("/v1/uploads", response_model=Session, status_code=202)
async def upload_recording(
    video: UploadFile = File(...),
    tournament_name: str | None = Form(default=None),
    _user: str = Depends(require_user),
) -> Session:
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Formato de vídeo não suportado")

    session = Session(
        tournament_name=tournament_name,
        status="uploaded",
        original_filename=Path(video.filename or "session.mp4").name,
    )
    destination = UPLOAD_DIR / str(session.id) / f"source{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with destination.open("wb") as output:
        while chunk := await video.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                output.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Arquivo excede o limite permitido")
            output.write(chunk)
    await video.close()
    queue = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    await queue.rpush("coach-ia:jobs", json.dumps({"id": str(session.id), "video_path": str(destination)}))
    await queue.aclose()
    session.status = "queued"
    SESSIONS[session.id] = session
    return session
