from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./lease.db")


def build_engine(url: str | None = None):
    target = url or database_url()
    connect_args = {"check_same_thread": False} if target.startswith("sqlite") else {}
    return create_engine(target, pool_pre_ping=True, connect_args=connect_args)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    from . import tables  # noqa: F401

    Base.metadata.create_all(engine)
