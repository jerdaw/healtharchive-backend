from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_database_config


class Base(DeclarativeBase):
    """
    Base class for ORM models.

    All SQLAlchemy models in this project should inherit from this.
    """


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    """
    Lazily construct and cache the SQLAlchemy engine.
    """
    global _engine
    if _engine is None:
        db_cfg = get_database_config()
        # echo can be toggled via environment later if desired.
        _engine = create_engine(db_cfg.database_url, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """
    Return a cached sessionmaker bound to the engine.
    """
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            future=True,
        )
    return _SessionLocal


@contextmanager
def get_session() -> Iterator[Session]:
    """
    Context manager yielding a database session.

    Commits on success, rolls back on exception, and always closes.
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["Base", "get_engine", "get_session", "get_session_factory"]
