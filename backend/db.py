from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db/pixmo.sqlite3"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

def init_db() -> None:
    # 遅延import（循環回避）
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
