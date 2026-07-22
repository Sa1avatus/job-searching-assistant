from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    if url.startswith("sqlite"):
        get_settings().artifact_directory.mkdir(parents=True, exist_ok=True)
    return create_engine(url, pool_pre_ping=True)


engine = create_database_engine()
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def session_scope() -> Iterator[Session]:
    with SessionFactory() as session:
        yield session
