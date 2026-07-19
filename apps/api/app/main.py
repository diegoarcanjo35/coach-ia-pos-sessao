from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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


class Session(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    platform: Literal["PPPoker"] = "PPPoker"
    status: Literal["created", "uploaded", "queued"] = "created"
    tournament_name: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    original_filename: str | None = None


SESSIONS: dict[UUID, Session] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "coach-ia-api", "version": app.version}


@app.post("/v1/sessions", response_model=Session, status_code=201)
def create_session(tournament_name: str | None = None) -> Session:
    session = Session(tournament_name=tournament_name)
    SESSIONS[session.id] = session
    return session


@app.get("/v1/sessions/{session_id}", response_model=Session)
def get_session(session_id: UUID) -> Session:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return session


@app.post("/v1/uploads", response_model=Session, status_code=202)
async def upload_recording(
    video: UploadFile = File(...),
    tournament_name: str | None = Form(default=None),
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
    with destination.open("wb") as output:
        while chunk := await video.read(1024 * 1024):
            output.write(chunk)
    await video.close()
    queue = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    await queue.rpush("coach-ia:jobs", json.dumps({"id": str(session.id), "video_path": str(destination)}))
    await queue.aclose()
    session.status = "queued"
    SESSIONS[session.id] = session
    return session
