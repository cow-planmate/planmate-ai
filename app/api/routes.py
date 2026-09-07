"""v2 용 REST 엔드포인트."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from google.auth.exceptions import GoogleAuthError

from app.config import settings
from app.db import query
from app.models import ChatbotRequest, ChatbotResponse
from app.services import agent

log = logging.getLogger(__name__)
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


def _check_token(x_internal_token: str | None) -> None:
    if x_internal_token != settings.internal_shared_secret:
        raise HTTPException(status_code=401, detail="invalid internal token")


@router.post("/api/chatbot", response_model=ChatbotResponse, response_model_by_alias=True)
def chatbot(req: ChatbotRequest, x_internal_token: str | None = Header(default=None)):
    """v2 챗봇 도메인이 호출. planContext(저장본) + 미반영 상태 → 완성 plan(14).

    에러: Gemini/캐시DB 하드 실패 = 5xx (v2 가 재시도·노출 판단). "변경 없음/되물음/불가안내"는
    200 + plan:null (17 C-4).
    """
    _check_token(x_internal_token)
    try:
        return agent.run(req)
    except HTTPException:
        raise
    except (RuntimeError, GoogleAuthError) as e:  # project 미설정 / ADC 없음·만료 = 설정 오류
        log.exception("[chatbot] 설정 오류")
        raise HTTPException(status_code=503, detail=f"AI 서버 설정 오류: {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("[chatbot] 처리 실패")
        raise HTTPException(status_code=502, detail="요청을 처리하는 중 오류가 발생했습니다.")


# TODO(E-5): 통합 테스트 — 13 §5 케이스 자연어 → 경로/op/plan 검증
