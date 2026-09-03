"""E-5 — 에이전트 루프 통합 테스트 (실 Gemini + 부산 DB).

`PLANMATE_LIVE=1 pytest tests/test_agent_live.py` 로만 실행 (기본 skip).
Gemini 는 비결정적 → 단언은 **경로(plan vs shownPlaces) + 셰이프 불변식**만. 정확 내용 X.
`13` §5 케이스 대표 표본.
"""

import pytest

from app.models import ChatbotRequest
from app.services import agent

pytestmark = pytest.mark.usefixtures("live")

ATTR = "1018702"   # 국제시장 먹자골목 (관광 12)


def _req(msg, *, blocks=None, timetables=None, pending=None, shown=None):
    tts = timetables or [
        {"timeTableId": 41, "date": "2026-10-11", "timeTableStartTime": "09:00:00", "timeTableEndTime": "20:00:00"},
        {"timeTableId": 42, "date": "2026-10-12", "timeTableStartTime": "09:00:00", "timeTableEndTime": "20:00:00"},
    ]
    return ChatbotRequest.model_validate({
        "planId": "p1", "message": msg,
        "planContext": {
            "planFrame": {"planId": "p1", "planName": "부산", "destinationId": 20,
                          "destinationName": "부산", "adultCount": 2, "childCount": 0},
            "timetables": tts, "placeBlocks": blocks or [],
        },
        "pendingContext": pending, "shownPlaces": shown,
        "userContext": {"preferredThemes": [{"level": 1, "code": "HS"}],
                        "regionCodes": [{"ldongRegnCd": "26"}], "signguCodes": []},
    })


def _valid_block(b):
    assert b.place_id and b.place_name and b.block_start_time and b.block_end_time
    assert b.block_category in {"ATTRACTION", "ACCOMMODATION", "RESTAURANT"}
    assert b.date


# ── G: 통짜 생성 ───────────────────────────────────────────────────────────
def test_full_itinerary():
    r = agent.run(_req("부산 1박2일 일정 짜줘. 첫날 역사 위주, 저녁 한식"))
    assert r.plan is not None
    assert len(r.plan.place_blocks) >= 4
    for b in r.plan.place_blocks:
        _valid_block(b)
        assert b.block_id is None                       # 전부 새 블록
    dates = {b.date for b in r.plan.place_blocks}
    assert dates <= {"2026-10-11", "2026-10-12"}
    assert r.shown_places == []


# ── S: 특정 장소 이름 삽입 ─────────────────────────────────────────────────
def test_insert_named_place():
    r = agent.run(_req("1일차 오후 3시에 국제시장 먹자골목 넣어줘"))
    assert r.plan is not None
    new = [b for b in r.plan.place_blocks if b.block_id is None]
    assert len(new) == 1                     # 이름 삽입 → 블록 1개 create
    _valid_block(new[0])
    assert new[0].date == "2026-10-11"       # "1일차" → 날짜 리졸브
    # 특정 content_id 는 단언 안 함 — 로컬 DB title ILIKE 랭킹·LLM 단어선택에 좌우돼 헛빨간불.
    # 경로(search_places(name) → createBlock → hydrate)는 위 셰이프 단언으로 충분.


# ── 추천: hasAction 없음 + shownPlaces ────────────────────────────────────
def test_recommendation():
    r = agent.run(_req("부산 카페 3곳 추천해줘"))
    assert r.plan is None
    assert 1 <= len(r.shown_places) <= 10
    for s in r.shown_places:
        assert s.content_id and s.title and s.category
        assert s.overview                              # 미절삭 재조회


# ── P: 유지 블록 보존 + 특정일 재생성 ─────────────────────────────────────
def test_regenerate_one_day_keeps_others():
    blocks = [
        {"blockId": 811, "timeTableId": 41, "placeId": ATTR, "placeName": "기존1",
         "blockStartTime": "10:00:00", "blockEndTime": "11:00:00", "blockCategory": "ATTRACTION", "memo": "예약함"},
        {"blockId": 830, "timeTableId": 42, "placeId": ATTR, "placeName": "기존2",
         "blockStartTime": "10:00:00", "blockEndTime": "11:00:00", "blockCategory": "ATTRACTION", "memo": "메모2"},
    ]
    r = agent.run(_req("2일차만 다시 짜줘", blocks=blocks))
    assert r.plan is not None
    by_id = {b.block_id: b for b in r.plan.place_blocks}
    assert 811 in by_id and by_id[811].memo == "예약함"     # 1일차 유지
    d2 = [b for b in r.plan.place_blocks if b.date == "2026-10-12"]
    assert d2 and all(b.block_id is None for b in d2)        # 2일차 새로


# ── 편집: pendingContext 새 블록을 자연키로 치환 ─────────────────────────
def test_edit_pending_block():
    # E5-1: schema 모드에선 placeId 스킵됐음. final_output_mode="json" 으로 해결 (`17` E5-1).
    pending = {
        "planFrame": {"planId": "p1", "adultCount": 2, "childCount": 0},
        "timetables": [{"timeTableId": 42, "date": "2026-10-12",
                        "timeTableStartTime": "09:00:00", "timeTableEndTime": "20:00:00"}],
        "placeBlocks": [{"placeId": "1128306", "placeName": "태종대짬뽕", "date": "2026-10-12",
                         "blockStartTime": "18:00:00", "blockEndTime": "19:30:00", "blockCategory": "RESTAURANT"}],
    }
    r = agent.run(_req("2일차 저녁 딴 데로 바꿔줘", pending=pending))
    assert r.plan is not None
    din = [b for b in r.plan.place_blocks if b.block_start_time == "18:00"]   # HH:MM canonical
    assert len(din) == 1
    assert din[0].place_id != "1128306"                     # 교체됨
    assert din[0].block_id is None
