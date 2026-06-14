from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

DB_PATH = Path(__file__).parent.parent.parent / "data" / "poetry.db"
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


def init_db() -> None:
    from poetry_submitter import models  # noqa: F401 — registers all tables
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine, expire_on_commit=False)
