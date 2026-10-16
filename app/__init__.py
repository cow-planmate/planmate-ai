"""FastAPI 앱 생성 + 라우터 등록 + AI 캐시 DB 커넥션 풀 생명주기 + v2 지역 색인 선조회."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.data.region_index_client import prefetch_in_background
from app.db import pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open()
    prefetch_in_background()
    try:
        yield
    finally:
        pool.close()


app = FastAPI(title="planmate-ai", lifespan=lifespan)
app.include_router(router)
