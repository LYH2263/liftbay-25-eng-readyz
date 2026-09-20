"""就绪探针：进程存活之外，还必须确认数据库可连接。

与 ``/api/health``（liveness，仅表示进程能响应）区分：``/api/readyz``
额外执行一次轻量只读查询（建筑物表存在且可查），供编排层决定是否可以
向该实例派工流量，避免“进程在、库挂了”仍被接入请求。

探测严格只读：不创建呼梯、不写派工回放。
"""

import threading
from collections.abc import Callable

from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

# 轻量只读探测：buildings 表可查即认为数据库就绪（空表同样视为就绪）。
_READINESS_SQL = text("SELECT 1 FROM buildings LIMIT 1")


def _describe_error(exc: BaseException) -> str:
    """把底层驱动异常归一化为一句简要、稳定的原因。"""
    orig = getattr(exc, "orig", None)
    target = orig if orig is not None else exc
    name = type(target).__name__
    first_line = str(target).strip().splitlines()[:1]
    detail = first_line[0] if first_line else "no detail"
    return f"database unavailable: {name}: {detail}"[:200]


def probe_database(
    timeout: float | None = None,
    session_factory: Callable | None = None,
) -> tuple[bool, str]:
    """对数据库做一次只读探测，返回 ``(是否就绪, 原因)``。

    超时由独立守护线程兜底：数据库无响应时探针请求在 ``timeout`` 后
    快速失败，而不是被无限挂起；连接建立阶段的超时另由数据库驱动的
    ``connect_timeout`` 限制（见 ``app.database``）。

    函数本身永不抛出——任何异常都归一化为 ``(False, reason)``。
    """
    timeout = settings.db_ready_timeout if timeout is None else timeout
    if session_factory is None:
        # 调用时再解析，便于测试用替身替换 SessionLocal。
        session_factory = SessionLocal
    outcome: dict[str, object] = {}

    def _run() -> None:
        db = None
        try:
            db = session_factory()
            db.execute(_READINESS_SQL)
            outcome["ok"] = True
        except Exception as exc:  # 探测失败需归一化为 not_ready，不能向上抛
            outcome["error"] = exc
        finally:
            if db is not None:
                db.close()

    worker = threading.Thread(target=_run, name="readyz-probe", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        # 守护线程可能稍后才结束（受驱动 connect_timeout 约束），这里不等它。
        return False, f"database probe timeout after {timeout:g}s"
    if outcome.get("ok") is True:
        return True, "ok"
    error = outcome.get("error")
    if error is None:
        return False, "database unavailable: unknown probe failure"
    return False, _describe_error(error)  # type: ignore[arg-type]
