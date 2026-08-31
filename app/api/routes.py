"""v2 용 REST 엔드포인트. Phase A 는 /health 만."""

from fastapi import APIRouter, HTTPException

from app.db import query

router = APIRouter()


@router.get("/health")
def health():
    """앱 기동 + AI 캐시 DB 연결 확인."""
    try:
        query("SELECT 1")
        return {"status": "ok", "db": "up"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "db": "down"},
        )


# TODO(Phase F): POST /api/ai/... 챗봇 엔드포인트 추가 (엔드포인트명은 Q1 확정 후)
