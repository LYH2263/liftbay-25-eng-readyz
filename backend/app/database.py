import math
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

_connect_args: dict[str, Any] = {}
if settings.database_url.startswith("postgresql"):
    # 建连阶段的硬超时（秒），避免数据库不可达时 readyz 卡在 TCP 建连上
    _connect_args["connect_timeout"] = max(1, math.ceil(settings.ready_probe_timeout))

engine = create_engine(
    settings.database_url, pool_pre_ping=True, connect_args=_connect_args
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


class DatabaseUnavailable(Exception):
    """就绪探测失败：数据库不可连接或探测超时。"""

    def __init__(self, detail: str = "", *, reason: str = "db_unavailable"):
        self.reason = reason  # db_unavailable | db_timeout
        self.detail = detail
        super().__init__(detail or reason)


def ping_database(session_factory=SessionLocal) -> None:
    """只读探测：能取到连接且业务表（buildings）存在即视为通过。

    仅执行 SELECT，不写呼梯、不写派工回放。失败抛 DatabaseUnavailable。
    """
    db = session_factory()
    try:
        conn = db.connection()
        if conn.dialect.name == "postgresql":
            # 查询阶段的服务端超时（毫秒），整数由配置换算，无注入面
            timeout_ms = max(1, int(settings.ready_probe_timeout * 1000))
            conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
        # LIMIT 1：表为空也返回 0 行成功；表不存在/库不可达则报错
        conn.execute(text("SELECT 1 FROM buildings LIMIT 1"))
    except SQLAlchemyError as exc:
        raise DatabaseUnavailable(str(exc)) from exc
    finally:
        db.close()


# 同步驱动会阻塞线程，用专用线程池加墙钟超时；超时后不 join 工作线程，
# 让本次探针先返回 503（阻塞的查询随后自行结束）。
_ready_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="readyz")


def check_database_ready(session_factory=SessionLocal, timeout: float | None = None) -> None:
    """带墙钟超时的只读就绪探测；失败/超时抛 DatabaseUnavailable。"""
    if timeout is None:
        timeout = settings.ready_probe_timeout
    future = _ready_executor.submit(ping_database, session_factory)
    try:
        future.result(timeout=timeout)
    except FutureTimeout:
        raise DatabaseUnavailable(
            f"database probe timed out after {timeout:g}s", reason="db_timeout"
        )
