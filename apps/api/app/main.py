from datetime import datetime, timezone
from contextlib import asynccontextmanager
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
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session as DatabaseSession

from .database import SessionLocal, SessionRecord, get_database, initialize_database

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_app_data()
    yield


app = FastAPI(title="Coach IA API", version="2.0.1", lifespan=lifespan)
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
    status: str = "created"
    tournament_name: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    original_filename: str | None = None


class ProcessingStatus(BaseModel):
    session_id: UUID
    status: str
    manifest: dict[str, object] | None = None


class SessionSummary(Session):
    processing_status: str
    complete_hands: int = 0
    partial_hands: int = 0


def initialize_app_data() -> None:
    initialize_database()
    with SessionLocal() as database:
        for directory in UPLOAD_DIR.glob("*") if UPLOAD_DIR.exists() else []:
            try: session_id = str(UUID(directory.name))
            except ValueError: continue
            if database.get(SessionRecord, session_id) is not None: continue
            manifest = read_manifest(UUID(session_id))
            source = next(directory.glob("source.*"), None)
            created = datetime.fromtimestamp(directory.stat().st_mtime, tz=timezone.utc)
            database.add(SessionRecord(id=session_id, owner_email=ADMIN_EMAIL, status=str(manifest.get("status", "uploaded")) if manifest else "uploaded",
                                       original_filename=source.name if source else None, created_at=created))
        database.commit()


def to_session(record: SessionRecord) -> Session:
    return Session(id=UUID(record.id), platform="PPPoker", status=record.status, tournament_name=record.tournament_name,
                   created_at=record.created_at, original_filename=record.original_filename)


def read_manifest(session_id: UUID) -> dict[str, object] | None:
    path = UPLOAD_DIR / str(session_id) / "manifest.json"
    if not path.exists():
        return None


def ensure_session_owner(session_id: UUID, user: str, database: DatabaseSession) -> SessionRecord:
    record = database.get(SessionRecord, str(session_id))
    if record is None or record.owner_email != user:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return record
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


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
def create_session(tournament_name: str | None = None, user: str = Depends(require_user), database: DatabaseSession = Depends(get_database)) -> Session:
    session = Session(tournament_name=tournament_name)
    record = SessionRecord(id=str(session.id), owner_email=user, status=session.status, tournament_name=tournament_name)
    database.add(record); database.commit()
    return to_session(record)


@app.get("/v1/sessions", response_model=list[SessionSummary])
def list_sessions(user: str = Depends(require_user), database: DatabaseSession = Depends(get_database)) -> list[SessionSummary]:
    records = database.scalars(select(SessionRecord).where(SessionRecord.owner_email == user).order_by(SessionRecord.created_at.desc()).limit(50)).all()
    result = []
    for record in records:
        manifest = read_manifest(UUID(record.id))
        processing = str(manifest.get("status", record.status)) if manifest else record.status
        summary = manifest.get("hand_detection", {}).get("summary", {}) if manifest else {}
        result.append(SessionSummary(**to_session(record).model_dump(), processing_status=processing,
                                     complete_hands=int(summary.get("complete_hands", 0)), partial_hands=int(summary.get("partial", 0))))
    return result


@app.get("/v1/sessions/{session_id}", response_model=Session)
def get_session(session_id: UUID, user: str = Depends(require_user), database: DatabaseSession = Depends(get_database)) -> Session:
    record = database.get(SessionRecord, str(session_id))
    if record is None or record.owner_email != user:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return to_session(record)


@app.get("/v1/sessions/{session_id}/processing", response_model=ProcessingStatus)
def processing_status(session_id: UUID, user: str = Depends(require_user), database: DatabaseSession = Depends(get_database)) -> ProcessingStatus:
    ensure_session_owner(session_id, user, database)
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


@app.get("/v1/sessions/{session_id}/frames/{filename}")
def session_frame(session_id: UUID, filename: str, user: str = Depends(require_user), database: DatabaseSession = Depends(get_database)) -> FileResponse:
    ensure_session_owner(session_id, user, database)
    if not filename.startswith("frame-") or not filename.endswith(".jpg") or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Nome de frame inválido")
    path = UPLOAD_DIR / str(session_id) / "frames" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Frame não encontrado")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})


@app.get("/v1/sessions/{session_id}/clips/{filename}")
def session_clip(session_id: UUID, filename: str, user: str = Depends(require_user), database: DatabaseSession = Depends(get_database)) -> FileResponse:
    ensure_session_owner(session_id, user, database)
    if not filename.startswith("hand-") or not filename.endswith(".mp4") or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Nome de clipe inválido")
    path = UPLOAD_DIR / str(session_id) / "clips" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Clipe não encontrado")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=300"})


@app.get("/v1/sessions/{session_id}/evidence/{filename}")
def session_evidence(session_id: UUID, filename: str, user: str = Depends(require_user), database: DatabaseSession = Depends(get_database)) -> FileResponse:
    ensure_session_owner(session_id, user, database)
    if not filename.startswith("rabbit-banner-") or not filename.endswith(".jpg") or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Nome de evidência inválido")
    path = UPLOAD_DIR / str(session_id) / "evidence" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Evidência não encontrada")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})


@app.post("/v1/uploads", response_model=Session, status_code=202)
async def upload_recording(
    video: UploadFile = File(...),
    tournament_name: str | None = Form(default=None),
    user: str = Depends(require_user),
    database: DatabaseSession = Depends(get_database),
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
    record = SessionRecord(id=str(session.id), owner_email=user, status=session.status, tournament_name=tournament_name,
                           original_filename=session.original_filename, created_at=session.created_at)
    database.add(record); database.commit()
    return to_session(record)
