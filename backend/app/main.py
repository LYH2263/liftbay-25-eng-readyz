import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.services.seed import seed_if_empty

logger = logging.getLogger("liftbay")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 数据库启动期不可用不应拖垮进程：health 仍报告存活，readyz 会摘除流量，
    # 数据库恢复后连接池（pool_pre_ping）自动重连，无需重启进程。
    try:
        Base.metadata.create_all(bind=engine)
        if settings.seed_on_empty:
            db = SessionLocal()
            try:
                seed_if_empty(db)
            finally:
                db.close()
    except Exception:
        logger.exception("启动时初始化数据库失败，实例以未就绪状态启动，等待数据库恢复")
    yield


app = FastAPI(title="LiftBay", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")
