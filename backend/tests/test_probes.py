"""探针测试：readyz 必须反映数据库真实可用性，health 只表示进程存活。

不依赖真实 Postgres：用测试替身模拟连接失败与阻塞超时。
可重复执行：docker compose exec api pytest -q
"""

import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.router import check_database_ready
from app.database import Base, DatabaseUnavailable, engine, ping_database
from app.main import app


client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _create_tables():
    """真实 SQLite：建表后 readyz 端到端可用。"""
    Base.metadata.create_all(bind=engine)
    yield


# ---------- 替身 ----------


class FakeResult:
    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self, log, *, fail=False, block_event=None):
        self.log = log
        self.dialect = SimpleNamespace(name="postgresql")
        self._fail = fail
        self._block_event = block_event

    def execute(self, statement, params=None):
        sql = str(statement)
        self.log.append(sql)
        if self._block_event is not None:
            # 模拟数据库无响应：阻塞到测试放行或 5 秒兜底
            self._block_event.wait(timeout=5)
        if self._fail:
            raise OperationalError(
                sql, params, ConnectionError("simulated connection refused")
            )
        return FakeResult()


class FakeSession:
    def __init__(self, conn):
        self._conn = conn
        self.closed = False

    def connection(self):
        return self._conn

    def close(self):
        self.closed = True


class FakeSessionFactory:
    def __init__(self, *, fail=False, block_event=None):
        self.log: list[str] = []
        self.sessions: list[FakeSession] = []
        self._conn = FakeConnection(self.log, fail=fail, block_event=block_event)

    def __call__(self):
        session = FakeSession(self._conn)
        self.sessions.append(session)
        return session


# ---------- ping_database 单元行为 ----------


def test_ping_database_is_read_only():
    factory = FakeSessionFactory()
    ping_database(factory)  # 不抛异常即通过

    assert factory.log, "应当至少执行一次探测 SQL"
    assert any("buildings" in sql for sql in factory.log), "应探测建筑物表"
    for sql in factory.log:
        head = sql.strip().upper()
        assert head.startswith(("SELECT", "SET")), f"只允许只读语句，实际: {sql}"
        assert not any(
            kw in head for kw in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP")
        ), f"探针不得有写操作: {sql}"
    assert all(s.closed for s in factory.sessions), "探测后会话必须关闭"


def test_ping_database_raises_on_connection_failure():
    factory = FakeSessionFactory(fail=True)
    with pytest.raises(DatabaseUnavailable) as exc_info:
        ping_database(factory)
    assert exc_info.value.reason == "db_unavailable"


def test_check_database_ready_times_out():
    release = threading.Event()
    factory = FakeSessionFactory(block_event=release)
    try:
        with pytest.raises(DatabaseUnavailable) as exc_info:
            check_database_ready(factory, timeout=0.05)
        assert exc_info.value.reason == "db_timeout"
    finally:
        release.set()  # 放行阻塞中的工作线程，避免拖慢解释器退出


def test_check_database_ready_passes():
    check_database_ready(FakeSessionFactory(), timeout=1.0)


def test_check_database_ready_real_engine_failure():
    """真实引擎指向不可打开的数据库文件：失败被包成 DatabaseUnavailable。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    broken = sessionmaker(bind=create_engine("sqlite:////nonexistent/dir/fail.db"))
    with pytest.raises(DatabaseUnavailable) as exc_info:
        check_database_ready(broken, timeout=1.0)
    assert exc_info.value.reason == "db_unavailable"


# ---------- HTTP 探针：存活与就绪分离 ----------


def test_health_succeeds_when_database_is_down(monkeypatch):
    def boom():
        raise DatabaseUnavailable("simulated", reason="db_unavailable")

    monkeypatch.setattr("app.api.router.check_database_ready", boom)

    health_res = client.get("/api/health")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "ok"

    ready_res = client.get("/api/readyz")
    assert ready_res.status_code == 503
    body = ready_res.json()
    assert body["status"] == "not_ready"  # 稳定字段
    assert body["reason"] == "db_unavailable"


def test_readyz_fails_on_timeout_but_health_still_ok(monkeypatch):
    def boom():
        raise DatabaseUnavailable("simulated timeout", reason="db_timeout")

    monkeypatch.setattr("app.api.router.check_database_ready", boom)

    ready_res = client.get("/api/readyz")
    assert ready_res.status_code == 503
    assert ready_res.json() == {"status": "not_ready", "reason": "db_timeout"}

    assert client.get("/api/health").status_code == 200


def test_readyz_ok_when_database_reachable(monkeypatch):
    monkeypatch.setattr(
        "app.api.router.check_database_ready", lambda: None
    )
    res = client.get("/api/readyz")
    assert res.status_code == 200
    assert res.json() == {"status": "ready"}


def test_readyz_end_to_end_against_real_sqlite():
    """真实引擎 + 真实 buildings 表（空表也算成功），不替换任何函数。"""
    res = client.get("/api/readyz")
    assert res.status_code == 200
    assert res.json() == {"status": "ready"}
    assert client.get("/api/health").status_code == 200
