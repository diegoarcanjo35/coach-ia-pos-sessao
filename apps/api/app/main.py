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

from .database import SessionLocal, SessionRecord, UserRecord, get_database, initialize_database

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_app_data()
    yield


app = FastAPI(title="Coach IA API", version="3.2.0-ai-calibration", lifespan=lifespan)
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


class UserCreate(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "player"] = "player"


class UserPasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class UserView(BaseModel):
    email: str
    role: Literal["admin", "player"]
    active: bool
    created_at: datetime


class SessionMetadataUpdate(BaseModel):
    tournament_name: str | None = Field(default=None, max_length=255)


def _session_token(email: str) -> str:
    payload = base64.urlsafe_b64encode(email.encode()).decode().rstrip("=")
    signature = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return f"pbkdf2_sha256$240000${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256": return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.urlsafe_b64decode(salt), int(rounds))
        return hmac.compare_digest(base64.urlsafe_b64encode(digest).decode(), expected)
    except (ValueError, TypeError):
        return False


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
    return email.lower()


def require_active_user(email: str = Depends(require_user), database: DatabaseSession = Depends(get_database)) -> UserRecord:
    user = database.get(UserRecord, email)
    if user is None or not user.active:
        raise HTTPException(status_code=403, detail="Usuário inativo ou inexistente")
    return user


def require_admin(user: UserRecord = Depends(require_active_user)) -> UserRecord:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso administrativo necessário")
    return user


def require_active_email(user: UserRecord = Depends(require_active_user)) -> str:
    return user.email


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
    duration_seconds: float = 0
    size_bytes: int = 0
    frame_count: int = 0
    review_finalized: bool = False
    favorite: bool = False
    archived: bool = False
    session_tags: list[str] = Field(default_factory=list)


class HandReviewDetail(BaseModel):
    tag: str = Field(default="", max_length=80)
    difficulty: Literal["", "easy", "medium", "hard"] = ""
    note: str = Field(default="", max_length=1000)


class LobbyReviewValue(BaseModel):
    players: str = Field(default="", max_length=30)
    remaining: str = Field(default="", max_length=30)
    average_stack: str = Field(default="", max_length=30)
    prize: str = Field(default="", max_length=80)


class ReviewState(BaseModel):
    notes: str = Field(default="", max_length=4000)
    hands: dict[str, Literal["approved", "rejected"]] = Field(default_factory=dict)
    lobby: dict[str, Literal["confirmed", "rejected"]] = Field(default_factory=dict)
    rabbits: dict[str, Literal["confirmed", "rejected"]] = Field(default_factory=dict)
    hand_details: dict[str, HandReviewDetail] = Field(default_factory=dict)
    lobby_values: dict[str, LobbyReviewValue] = Field(default_factory=dict)
    finalized: bool = False
    favorite: bool = False
    archived: bool = False
    session_tags: list[str] = Field(default_factory=list, max_length=12)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def initialize_app_data() -> None:
    initialize_database()
    with SessionLocal() as database:
        if ADMIN_PASSWORD and database.get(UserRecord, ADMIN_EMAIL) is None:
            database.add(UserRecord(email=ADMIN_EMAIL, password_hash=hash_password(ADMIN_PASSWORD), role="admin", active=True))
            database.commit()
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
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def ensure_session_owner(session_id: UUID, user: str, database: DatabaseSession) -> SessionRecord:
    record = database.get(SessionRecord, str(session_id))
    if record is None or record.owner_email != user:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return record


def review_path(session_id: UUID) -> Path:
    return UPLOAD_DIR / str(session_id) / "review.json"


def read_review_file(session_id: UUID) -> ReviewState:
    path = review_path(session_id)
    if not path.exists():
        return ReviewState()
    try:
        return ReviewState.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ReviewState()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "coach-ia-api", "version": app.version}


@app.post("/v1/auth/login")
def login(credentials: LoginRequest, response: Response, database: DatabaseSession = Depends(get_database)) -> dict[str, str]:
    user = database.get(UserRecord, credentials.email.lower().strip())
    if not (user and user.active and AUTH_SECRET and verify_password(credentials.password, user.password_hash)):
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    response.set_cookie(
        "coach_session", _session_token(user.email), httponly=True,
        secure=COOKIE_SECURE, samesite="lax", max_age=60 * 60 * 12, path="/",
    )
    return {"email": user.email, "role": user.role}


@app.post("/v1/auth/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie("coach_session", path="/")


@app.get("/v1/auth/me")
def me(user: UserRecord = Depends(require_active_user)) -> dict[str, str]:
    return {"email": user.email, "role": user.role}


@app.get("/v1/admin/users", response_model=list[UserView])
def list_users(_admin: UserRecord = Depends(require_admin), database: DatabaseSession = Depends(get_database)) -> list[UserView]:
    users = database.scalars(select(UserRecord).order_by(UserRecord.created_at.desc())).all()
    return [UserView(email=item.email, role=item.role, active=item.active, created_at=item.created_at) for item in users]


@app.post("/v1/admin/users", response_model=UserView, status_code=201)
def create_user(payload: UserCreate, _admin: UserRecord = Depends(require_admin), database: DatabaseSession = Depends(get_database)) -> UserView:
    email = payload.email.lower().strip()
    if "@" not in email or database.get(UserRecord, email) is not None:
        raise HTTPException(status_code=409, detail="E-mail inválido ou já cadastrado")
    user = UserRecord(email=email, password_hash=hash_password(payload.password), role=payload.role, active=True)
    database.add(user); database.commit()
    return UserView(email=user.email, role=user.role, active=user.active, created_at=user.created_at)


@app.post("/v1/admin/users/{email}/toggle", response_model=UserView)
def toggle_user(email: str, admin: UserRecord = Depends(require_admin), database: DatabaseSession = Depends(get_database)) -> UserView:
    user = database.get(UserRecord, email.lower())
    if user is None: raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if user.email == admin.email: raise HTTPException(status_code=409, detail="O administrador atual não pode desativar a própria conta")
    user.active = not user.active; database.commit()
    return UserView(email=user.email, role=user.role, active=user.active, created_at=user.created_at)


@app.post("/v1/admin/users/{email}/password", status_code=204)
def reset_user_password(email: str, payload: UserPasswordReset, _admin: UserRecord = Depends(require_admin), database: DatabaseSession = Depends(get_database)) -> None:
    user = database.get(UserRecord, email.lower())
    if user is None: raise HTTPException(status_code=404, detail="Usuário não encontrado")
    user.password_hash = hash_password(payload.password); database.commit()


@app.post("/v1/sessions", response_model=Session, status_code=201)
def create_session(tournament_name: str | None = None, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> Session:
    session = Session(tournament_name=tournament_name)
    record = SessionRecord(id=str(session.id), owner_email=user, status=session.status, tournament_name=tournament_name)
    database.add(record); database.commit()
    return to_session(record)


@app.get("/v1/sessions", response_model=list[SessionSummary])
def list_sessions(user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> list[SessionSummary]:
    records = database.scalars(select(SessionRecord).where(SessionRecord.owner_email == user).order_by(SessionRecord.created_at.desc()).limit(50)).all()
    result = []
    for record in records:
        manifest = read_manifest(UUID(record.id))
        review = read_review_file(UUID(record.id))
        processing = str(manifest.get("status", record.status)) if manifest else record.status
        summary = manifest.get("hand_detection", {}).get("summary", {}) if manifest else {}
        metadata = manifest.get("metadata", {}) if manifest else {}
        segmentation = manifest.get("segmentation", {}) if manifest else {}
        result.append(SessionSummary(**to_session(record).model_dump(), processing_status=processing,
                                     complete_hands=int(summary.get("complete_hands", 0)), partial_hands=int(summary.get("partial", 0)),
                                     duration_seconds=float(metadata.get("duration_seconds", 0)), size_bytes=int(metadata.get("size_bytes", 0)),
                                     frame_count=int(segmentation.get("frame_count", 0)), review_finalized=review.finalized,
                                     favorite=review.favorite, archived=review.archived, session_tags=review.session_tags))
    return result


@app.get("/v1/sessions/export")
def export_sessions(user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> Response:
    sessions = list_sessions(user, database)
    payload = {"platform": "PPPoker", "post_session_only": True, "exported_at": datetime.now(timezone.utc).isoformat(),
               "sessions": [item.model_dump(mode="json") for item in sessions]}
    return Response(content=json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="coach-ia-sessions.json"'})


@app.get("/v1/sessions/{session_id}", response_model=Session)
def get_session(session_id: UUID, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> Session:
    record = database.get(SessionRecord, str(session_id))
    if record is None or record.owner_email != user:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return to_session(record)


@app.post("/v1/sessions/{session_id}/metadata", response_model=Session)
def update_session_metadata(session_id: UUID, update: SessionMetadataUpdate, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> Session:
    record = ensure_session_owner(session_id, user, database)
    record.tournament_name = update.tournament_name.strip() if update.tournament_name else None
    database.commit()
    return to_session(record)


@app.post("/v1/sessions/{session_id}/retry", status_code=202)
async def retry_session(session_id: UUID, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> dict[str, str]:
    record = ensure_session_owner(session_id, user, database)
    source = next((UPLOAD_DIR / str(session_id)).glob("source.*"), None)
    if source is None:
        raise HTTPException(status_code=404, detail="Vídeo original não encontrado")
    manifest = read_manifest(session_id) or {}
    if str(manifest.get("status", record.status)) != "failed":
        raise HTTPException(status_code=409, detail="Somente sessões com falha podem ser reprocessadas")
    queue = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    await queue.rpush("coach-ia:jobs", json.dumps({"id": str(session_id), "video_path": str(source)}))
    await queue.aclose()
    record.status = "queued"; database.commit()
    return {"status": "queued", "session_id": str(session_id)}


@app.get("/v1/sessions/{session_id}/processing", response_model=ProcessingStatus)
def processing_status(session_id: UUID, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> ProcessingStatus:
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


@app.get("/v1/sessions/{session_id}/review", response_model=ReviewState)
def get_review(session_id: UUID, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> ReviewState:
    ensure_session_owner(session_id, user, database)
    path = review_path(session_id)
    if not path.exists():
        return ReviewState()
    try:
        return ReviewState.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="Revisão temporariamente indisponível") from None


@app.post("/v1/sessions/{session_id}/review", response_model=ReviewState)
def save_review(session_id: UUID, review: ReviewState, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> ReviewState:
    ensure_session_owner(session_id, user, database)
    review.updated_at = datetime.now(timezone.utc)
    path = review_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(review.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
    return review


@app.get("/v1/sessions/{session_id}/review/export")
def export_review(session_id: UUID, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> Response:
    review = get_review(session_id, user, database)
    manifest = read_manifest(session_id) or {}
    payload = {"session_id": str(session_id), "platform": "PPPoker", "post_session_only": True,
               "review": review.model_dump(mode="json"),
               "summary": manifest.get("hand_detection", {}).get("summary", {}),
               "classification": manifest.get("classification", {}),
               "exported_at": datetime.now(timezone.utc).isoformat()}
    return Response(content=json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="review-{session_id}.json"'})


@app.get("/v1/sessions/{session_id}/frames/{filename}")
def session_frame(session_id: UUID, filename: str, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> FileResponse:
    ensure_session_owner(session_id, user, database)
    if not filename.startswith("frame-") or not filename.endswith(".jpg") or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Nome de frame inválido")
    path = UPLOAD_DIR / str(session_id) / "frames" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Frame não encontrado")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})


@app.get("/v1/sessions/{session_id}/clips/{filename}")
def session_clip(session_id: UUID, filename: str, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> FileResponse:
    ensure_session_owner(session_id, user, database)
    if not filename.startswith("hand-") or not filename.endswith(".mp4") or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Nome de clipe inválido")
    path = UPLOAD_DIR / str(session_id) / "clips" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Clipe não encontrado")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=300"})


@app.get("/v1/sessions/{session_id}/evidence/{filename}")
def session_evidence(session_id: UUID, filename: str, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> FileResponse:
    ensure_session_owner(session_id, user, database)
    if not filename.startswith("rabbit-banner-") or not filename.endswith(".jpg") or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Nome de evidência inválido")
    path = UPLOAD_DIR / str(session_id) / "evidence" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Evidência não encontrada")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})


@app.get("/v1/sessions/{session_id}/ai-evidence/{filename}")
def session_ai_evidence(session_id: UUID, filename: str, user: str = Depends(require_active_email), database: DatabaseSession = Depends(get_database)) -> FileResponse:
    ensure_session_owner(session_id, user, database)
    if not filename.startswith("hand-") or "-frame-" not in filename or not filename.endswith(".jpg") or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Nome de evidência da IA inválido")
    path = UPLOAD_DIR / str(session_id) / "ai-evidence" / filename
    if not path.is_file(): raise HTTPException(status_code=404, detail="Evidência da IA não encontrada")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control":"private, max-age=300"})


@app.post("/v1/uploads", response_model=Session, status_code=202)
async def upload_recording(
    video: UploadFile = File(...),
    tournament_name: str | None = Form(default=None),
    user: str = Depends(require_active_email),
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
