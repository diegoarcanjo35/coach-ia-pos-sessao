import os
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class SessionRecord(Base):
    __tablename__ = "poker_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_email: Mapped[str] = mapped_column(String(320), index=True)
    platform: Mapped[str] = mapped_column(String(32), default="PPPoker")
    status: Mapped[str] = mapped_column(String(64), default="created", index=True)
    tournament_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


database_url = os.getenv("DATABASE_URL", "postgresql+psycopg://coach_ia:change-me@postgres:5432/coach_ia")
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def initialize_database() -> None:
    Base.metadata.create_all(engine)


def get_database():
    database = SessionLocal()
    try:
        yield database
    finally:
        database.close()
