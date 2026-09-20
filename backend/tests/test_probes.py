"""探针测试：readyz 必须探测数据库，health 仅表示进程存活。

用测试替身（FakeSession）模拟数据库连接失败与无响应超时，
无需真实 Postgres。
"""

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.orm import sessionmaker

from app.api.router import health, readyz
from app.api.router import api_router
from app.services import readiness


class FakeSession:
    """记录调用的 Session 替身：可配置成功 / 抛错 / 慢查询。"""

    instances: list["FakeSession"] = []

    def __init__(self, mode: str = "ok", delay: float = 0.0):
        self.mode = mode
        self.delay = delay
        self.executed = []
        self.closed = False
        FakeSession.instances.append(self)

    def execute(self, statement, params=None):
        self.executed.append(str(statement))
        if self.delay:
            time.sleep(self.delay)
        if self.mode == "connection_error":
            # 模拟 psycopg2.OperationalError 被 SQLAlchemy 包装的形态。
            raise SAOperationalError(
                str(statement),
                params or {},
                ConnectionError("could not connect to server: Connection refused"),
            )
        if self.mode == "generic_error":
            raise RuntimeError("relation does not exist")
        return None

    def close(self):
        self.closed = True


@pytest.fixture
def fake_factory(monkeypatch):
    def _factory(mode: str = "ok", delay: float = 0.0):
        FakeSession.instances = []

        def factory():
            return FakeSession(mode=mode, delay=delay)

        monkeypatch.setattr(readiness, "SessionLocal", factory)
        return factory

    return _factory


# ---------- probe_database 单元测试 ----------


def test_probe_success_is_readonly(fake_factory):
    fake_factory(mode="ok")
    ready, reason = readiness.probe_database(timeout=2)
    assert ready is True
    assert reason == "ok"
    session = FakeSession.instances[0]
    # 只执行了一条只读探测语句，且明确查建筑物表。
    assert len(session.executed) == 1
    sql = session.executed[0].lower()
    assert "select" in sql and "buildings" in sql
    assert "insert" not in sql and "update" not in sql and "delete" not in sql
    assert session.closed is True


def test_probe_connection_failure(fake_factory):
    fake_factory(mode="connection_error")
    ready, reason = readiness.probe_database(timeout=2)
    assert ready is False
    assert "database unavailable" in reason
    assert "Connection refused" in reason
    assert FakeSession.instances[0].closed is True


def test_probe_generic_failure(fake_factory):
    fake_factory(mode="generic_error")
    ready, reason = readiness.probe_database(timeout=2)
    assert ready is False
    assert "RuntimeError" in reason
    assert "relation does not exist" in reason


def test_probe_timeout_returns_false_without_hanging(fake_factory):
    # 模拟数据库无响应：查询耗时 1s，但探针超时设为 0.05s。
    fake_factory(mode="ok", delay=1.0)
    started = time.monotonic()
    ready, reason = readiness.probe_database(timeout=0.05)
    elapsed = time.monotonic() - started
    assert ready is False
    assert "timeout" in reason
    # 必须在超时点附近返回，而不是等慢查询结束。
    assert elapsed < 0.5


def test_probe_never_raises(fake_factory):
    def boom():
        raise ConnectionError("socket broken")

    ready, reason = readiness.probe_database(timeout=2, session_factory=boom)
    assert ready is False
    assert "database unavailable" in reason


# ---------- HTTP 处理器层测试 ----------


def test_readyz_endpoint_success(fake_factory):
    fake_factory(mode="ok")
    result = readyz()
    assert result == {"status": "ready", "checks": {"database": "ok"}}


def test_readyz_endpoint_connection_failure_returns_503(fake_factory):
    fake_factory(mode="connection_error")
    response = readyz()
    assert response.status_code == 503
    body = json.loads(response.body)
    # 稳定字段：编排层可按 status 精确判断。
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]
    assert "Connection refused" in body["checks"]["database"]


def test_readyz_endpoint_timeout_returns_503(fake_factory, monkeypatch):
    monkeypatch.setattr(readiness.settings, "db_ready_timeout", 0.05)
    fake_factory(mode="ok", delay=1.0)
    response = readyz()
    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["status"] == "not_ready"
    assert "timeout" in body["checks"]["database"]


def test_health_still_ok_when_database_down(fake_factory):
    """库挂了：readyz 失败，但存活探针 health 必须仍成功。"""
    fake_factory(mode="connection_error")
    assert readyz().status_code == 503
    assert health() == {"status": "ok"}


def test_health_still_ok_when_database_hangs(fake_factory, monkeypatch):
    monkeypatch.setattr(readiness.settings, "db_ready_timeout", 0.05)
    fake_factory(mode="ok", delay=1.0)
    assert readyz().status_code == 503
    assert health() == {"status": "ok"}


# ---------- 启动容错：数据库启动期不可用不能拖垮进程 ----------


def test_app_starts_when_db_unavailable(monkeypatch):
    import app.main as main

    def raise_on_create(*args, **kwargs):
        raise ConnectionError("database down at startup")

    monkeypatch.setattr(main.Base.metadata, "create_all", raise_on_create)
    # 启动不应抛出；进程以未就绪状态存活，health 仍可响应。
    with TestClient(main.app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------- 真实 SQL 链路的 HTTP 集成测试（sqlite 替身库） ----------


def test_http_readyz_against_real_sqlite(monkeypatch):
    # StaticPool 让探测工作线程复用同一内存库连接，从而能看到建好的表。
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.database import Base
    from app.models import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(readiness, "SessionLocal", factory)

    probe_app = FastAPI()
    probe_app.include_router(api_router, prefix="/api")
    with TestClient(probe_app) as client:
        ready_resp = client.get("/api/readyz")
        assert ready_resp.status_code == 200
        assert ready_resp.json()["status"] == "ready"

        health_resp = client.get("/api/health")
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "ok"}

    engine.dispose()


def test_http_readyz_503_when_sqlite_unreachable(monkeypatch):
    def unreachable_factory():
        raise SAOperationalError(
            "SELECT 1", {}, ConnectionError("database is shut down")
        )

    monkeypatch.setattr(readiness, "SessionLocal", unreachable_factory)

    probe_app = FastAPI()
    probe_app.include_router(api_router, prefix="/api")
    with TestClient(probe_app) as client:
        ready_resp = client.get("/api/readyz")
        assert ready_resp.status_code == 503
        assert ready_resp.json()["status"] == "not_ready"

        health_resp = client.get("/api/health")
        assert health_resp.status_code == 200
