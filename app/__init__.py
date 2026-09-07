"""FastAPI 앱 생성 + 라우터 등록 + AI 캐시 DB 커넥션 풀 생명주기."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.db import pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open()
    try:
        yield
    finally:
        pool.close()


app = FastAPI(title="planmate-ai", lifespan=lifespan)
app.include_router(router)
