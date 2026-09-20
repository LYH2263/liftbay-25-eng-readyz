from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings


def _connect_args(url: str) -> dict:
    # 仅对 psycopg2/Postgres 设置连接建立超时；其他后端（如 sqlite 测试）忽略。
    if url.startswith(("postgresql+psycopg2://", "postgresql://")):
        return {"connect_timeout": settings.db_connect_timeout}
    return {}


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=_connect_args(settings.database_url),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
