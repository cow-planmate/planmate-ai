"""POST /api/chatbot 라우트 계약 — 토큰 / 에러 매핑 / 응답 셰이프.

agent.run 은 목킹 (Gemini·DB 불필요). 라우트 배선만 검증 (17 C-4).
"""

import pytest
from fastapi.testclient import TestClient

from app import app
from app.config import settings
from app.models import ChatbotResponse, PlanState

client = TestClient(app, raise_server_exceptions=False)

_BODY = {
    "planId": "p1",
    "message": "부산 2박3일 짜줘",
    "planContext": {
        "planFrame": {"planId": "p1", "adultCount": 2, "childCount": 0},
        "timetables": [], "placeBlocks": [],
    },
    "userContext": {"preferredThemes": [], "regionCodes": [], "signguCodes": []},
}
_TOKEN = {"X-Internal-Token": settings.internal_shared_secret}


def _mock_agent(monkeypatch, fn):
    monkeypatch.setattr("app.api.routes.agent.run", fn)


# ── 토큰 ───────────────────────────────────────────────────────────────────
def test_missing_token_401():
    assert client.post("/api/chatbot", json=_BODY).status_code == 401


def test_wrong_token_401():
    r = client.post("/api/chatbot", json=_BODY, headers={"X-Internal-Token": "nope"})
    assert r.status_code == 401


# ── 정상 응답 셰이프 ───────────────────────────────────────────────────────
def test_ok_with_plan(monkeypatch):
    plan = PlanState.model_validate({
        "planFrame": {"planId": "p1", "adultCount": 2, "childCount": 0},
        "timetables": [{"timeTableId": 1, "date": "2026-10-11",
                        "timeTableStartTime": "09:00", "timeTableEndTime": "20:00"}],
        "placeBlocks": [],
    })
    _mock_agent(monkeypatch, lambda req: ChatbotResponse(user_message="짰어요", plan=plan))
    r = client.post("/api/chatbot", json=_BODY, headers=_TOKEN)
    assert r.status_code == 200
    body = r.json()
    assert body["userMessage"] == "짰어요"
    assert body["plan"]["timetables"][0]["date"] == "2026-10-11"
    assert body["shownPlaces"] == []


def test_ok_plan_null_is_200(monkeypatch):
    # 되물음/불가안내 = plan:null + 200 (5xx 아님)
    _mock_agent(monkeypatch, lambda req: ChatbotResponse(user_message="어디로 가세요?", plan=None))
    r = client.post("/api/chatbot", json=_BODY, headers=_TOKEN)
    assert r.status_code == 200
    assert r.json()["plan"] is None


# ── 에러 매핑 ──────────────────────────────────────────────────────────────
def test_runtime_error_503(monkeypatch):
    def boom(req):
        raise RuntimeError("GEMINI_API_KEY 미설정")
    _mock_agent(monkeypatch, boom)
    assert client.post("/api/chatbot", json=_BODY, headers=_TOKEN).status_code == 503


def test_generic_error_502(monkeypatch):
    def boom(req):
        raise ValueError("gemini 폭발")
    _mock_agent(monkeypatch, boom)
    assert client.post("/api/chatbot", json=_BODY, headers=_TOKEN).status_code == 502


# ── 바디 검증 ──────────────────────────────────────────────────────────────
def test_malformed_body_422():
    r = client.post("/api/chatbot", json={"message": "x"}, headers=_TOKEN)
    assert r.status_code == 422
