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


# ═══════════════════════════════════════════════════════════════════════════════
#  E-5 나머지 케이스 (`13` §5-3 S / §5-6 T / §5-8 X) — `17` "다음" 절.
#  핵심 검증축: "한 요청 → op 여러 개 fan-out" 을 LLM 이 끝까지 내는가
#  (첫 블록만 하고 말거나 / 반복 op 에서 필드 스킵 = E5-1 재발 여부).
#  비결정적 → 경로 + fan-out 개수 + 필드 보존 불변식만 단언. 정확 장소 X.
# ═══════════════════════════════════════════════════════════════════════════════

# 부산 + 임베딩 있는 실 content_id (place_embedding ∩ ai_place_v, regn 26)
ATTR_A = "126658"    # 태종대 (12)
ATTR_B = "2606221"   # 영도 흰여울해안터널 (12)
REST_LUNCH = "2852289"   # 해운대해물칼국수 (39)
REST_DINNER = "133533"   # 금오횟집 (39)
CAFE = "2832729"     # 모모스커피 본점 (39)
ACCOM_A = "142978"   # 리베로호텔 (32)
ACCOM_B = "143099"   # 호텔일루아 (32)


def _b(bid, ttid, pid, name, start, end, cat, memo=None):
    d = {"blockId": bid, "timeTableId": ttid, "placeId": pid, "placeName": name,
         "blockStartTime": start, "blockEndTime": end, "blockCategory": cat}
    if memo:
        d["memo"] = memo
    return d


def _two_day_plan():
    """1·2일차 각 3블록(관광·점심·숙소) — 전부 실 blockId + memo. T/S4 fan-out 용."""
    return [
        _b(801, 41, ATTR_A, "태종대", "10:00:00", "11:30:00", "ATTRACTION", "예약함"),
        _b(802, 41, REST_LUNCH, "해운대해물칼국수", "12:00:00", "13:00:00", "RESTAURANT", "점심"),
        _b(803, 41, ACCOM_A, "리베로호텔", "21:00:00", "22:00:00", "ACCOMMODATION", "체크인 21시"),
        _b(811, 42, ATTR_B, "영도 흰여울해안터널", "10:00:00", "11:30:00", "ATTRACTION", "산책"),
        _b(812, 42, REST_DINNER, "금오횟집", "12:00:00", "13:00:00", "RESTAURANT", "회"),
        _b(813, 42, ACCOM_B, "호텔일루아", "21:00:00", "22:00:00", "ACCOMMODATION", "2박째"),
    ]


def _mins(hm):
    h, m = hm.split(":")[:2]
    return int(h) * 60 + int(m)


# ── S3 — near + query + subtype 조합 치환 ─────────────────────────────────
def test_s3_replace_cafe_nearby_quiet():
    blocks = [
        _b(821, 41, ATTR_A, "태종대", "10:00:00", "11:30:00", "ATTRACTION"),
        _b(822, 41, CAFE, "모모스커피 본점", "14:00:00", "15:00:00", "RESTAURANT", "카페"),
    ]
    r = agent.run(_req("이 카페 대신 근처에 조용한 데로 바꿔줘", blocks=blocks))
    assert r.plan is not None
    by_id = {b.block_id: b for b in r.plan.place_blocks}
    assert len(r.plan.place_blocks) == 2                    # 삽입/삭제 없음
    assert 822 in by_id
    assert by_id[822].place_id not in (None, CAFE)          # 실제 치환됨
    assert by_id[822].block_category == "RESTAURANT"        # 카페 = RESTAURANT 유지
    assert by_id[821].place_id == ATTR_A                    # 관광 블록 안 건드림
    _valid_block(by_id[822])


# ── S4 — 매일 밤 숙소 블록 전부 치환 (updateBlock ×N) ────────────────────
def test_s4_cheaper_accommodation_all_nights():
    blocks = _two_day_plan()
    r = agent.run(_req("숙소를 좀 더 싼 데로 바꿔줘", blocks=blocks))
    assert r.plan is not None
    by_id = {b.block_id: b for b in r.plan.place_blocks}
    accom = [by_id[i] for i in (803, 813) if i in by_id]
    assert len(accom) == 2
    # 핵심: 양일 숙소 둘 다 치환 (첫날만 하고 말면 실패 = fan-out 미완)
    assert all(b.place_id not in (None, ACCOM_A, ACCOM_B) for b in accom), \
        [b.place_id for b in accom]
    assert all(b.block_category == "ACCOMMODATION" for b in accom)
    # 숙소 아닌 블록은 그대로 (placeId·memo 보존)
    assert by_id[801].place_id == ATTR_A and by_id[801].memo == "예약함"
    assert by_id[802].place_id == REST_LUNCH
    assert by_id[811].place_id == ATTR_B
    assert by_id[812].place_id == REST_DINNER


# ── S7 — query + near + "저녁 이후" 시각 계산 삽입 ──────────────────────────
def test_s7_add_nightview_after_dinner():
    tts = [
        {"timeTableId": 41, "date": "2026-10-11", "timeTableStartTime": "09:00:00", "timeTableEndTime": "23:00:00"},
        {"timeTableId": 42, "date": "2026-10-12", "timeTableStartTime": "09:00:00", "timeTableEndTime": "23:00:00"},
    ]
    blocks = [
        _b(831, 41, REST_DINNER, "금오횟집", "18:00:00", "19:30:00", "RESTAURANT", "저녁"),
        _b(832, 41, ACCOM_A, "리베로호텔", "21:30:00", "22:30:00", "ACCOMMODATION"),
    ]
    r = agent.run(_req("1일차 저녁 먹고 야경 볼 데 추가해줘", blocks=blocks, timetables=tts))
    assert r.plan is not None
    new = [b for b in r.plan.place_blocks if b.block_id is None]
    assert len(new) == 1                                    # 삽입 1개
    nb = new[0]
    _valid_block(nb)
    assert nb.date == "2026-10-11"
    assert nb.block_category == "ATTRACTION"
    assert _mins(nb.block_start_time) >= _mins("19:30")     # "저녁 먹고" = 저녁 종료 이후
    # 기존 2블록 보존
    assert {b.block_id for b in r.plan.place_blocks if b.block_id} == {831, 832}


# ── T1 — 1·2일차 순서 스왑 (updateBlock ×date 스왑 ×N) ────────────────────
def test_t1_swap_day1_day2():
    blocks = _two_day_plan()
    d1_pids = {ATTR_A, REST_LUNCH, ACCOM_A}
    d2_pids = {ATTR_B, REST_DINNER, ACCOM_B}
    r = agent.run(_req("1일차랑 2일차 순서 바꿔줘", blocks=blocks))
    assert r.plan is not None
    on = {"2026-10-11": set(), "2026-10-12": set()}
    for b in r.plan.place_blocks:
        if b.date in on:
            on[b.date].add(b.place_id)
    # 스왑 완료: 1일차에 원래 2일차 장소들, 그 반대도
    assert on["2026-10-11"] == d2_pids, on
    assert on["2026-10-12"] == d1_pids, on
    # memo 보존 (updateBlock 파셜)
    by_id = {b.block_id: b for b in r.plan.place_blocks}
    assert by_id[801].memo == "예약함" and by_id[801].date == "2026-10-12"


# ── T5 — 전체 30분씩 뒤로 (updateBlock ×전체) ─────────────────────────────
def test_t5_shift_all_30min():
    blocks = _two_day_plan()
    orig = {b["blockId"]: b["blockStartTime"][:5] for b in blocks}
    r = agent.run(_req("전체적으로 30분씩 뒤로 밀어줘", blocks=blocks))
    assert r.plan is not None
    by_id = {b.block_id: b for b in r.plan.place_blocks}
    assert set(by_id) == set(orig)                          # 삭제/삽입 없음
    shifted = 0
    for bid, o in orig.items():
        delta = _mins(by_id[bid].block_start_time) - _mins(o)
        assert delta == 30, f"블록 {bid}: {o} → {by_id[bid].block_start_time} (Δ{delta})"
        shifted += 1
    assert shifted == len(orig)                             # 전체 이동 (첫 블록만 X)


# ── T6 — 숙소 빼고 다 삭제 (deleteBlock ×N) ───────────────────────────────
def test_t6_delete_all_but_accommodation():
    blocks = _two_day_plan()
    r = agent.run(_req("숙소 빼고 다 지워줘", blocks=blocks))
    assert r.plan is not None
    left = r.plan.place_blocks
    assert {b.block_id for b in left} == {803, 813}, [b.block_id for b in left]
    assert all(b.block_category == "ACCOMMODATION" for b in left)


# ── X3 — 대중교통 동선: 못 함 안내 ───────────────────────────────────────
def test_x3_public_transit_routing_unsupported():
    blocks = _two_day_plan()
    r = agent.run(_req("차가 없어서 그런데 대중교통으로 다닐 수 있게 동선 짜줘", blocks=blocks))
    print("\n[X3] userMessage:", r.user_message)
    assert r.user_message.strip()
    assert r.shown_places == []
    # 대중교통 라우팅은 없음 → 장소를 지어내 새 일정 만들지 않음
    if r.plan is not None:
        assert len(r.plan.place_blocks) == len(blocks)
        assert all(b.block_id is not None for b in r.plan.place_blocks)


# ── X6 — 애견동반: amenities 미매핑 → degrade + 안내 ──────────────────────
def test_x6_pet_friendly_degrades():
    r = agent.run(_req("부산에서 애견동반 되는 카페 추천해줘"))
    print("\n[X6] userMessage:", r.user_message)
    assert r.plan is None                                   # 순수 추천 턴
    assert r.user_message.strip()
    assert 1 <= len(r.shown_places) <= 10                   # 못 걸러도 대체 후보는 제시
    for s in r.shown_places:
        assert s.content_id and s.title and s.category


# ── recentMessages — 직전 검색 뉘앙스 연속 (C3 "이거 말고 몇 개 더") ────────
def test_recent_messages_carry_search_nuance():
    shown = [
        {"contentId": CAFE, "title": "모모스커피 본점", "category": "restaurant"},
        {"contentId": "2852289", "title": "해운대해물칼국수", "category": "restaurant"},
        {"contentId": "133533", "title": "금오횟집", "category": "restaurant"},
    ]
    req = _req("이거 말고 몇 개 더 보여줘", shown=shown).model_copy(
        update={"recent_messages": ["부산 바다 보이는 조용한 카페 추천해줘"]})
    r = agent.run(req)
    print("\n[recent] userMessage:", r.user_message)
    assert r.plan is None                                   # 순수 추천 턴 유지
    assert 1 <= len(r.shown_places) <= 10
    seen = {s["contentId"] for s in shown}
    assert all(s.content_id not in seen for s in r.shown_places)   # 본 것 제외됨


# ── 실사용 멀티턴 — 첫 요청 + 재질문 3회 (프론트 echo/recent 누적 흉내) ──────
def test_multiturn_recommendation_chain():
    turns = [
        "부산에서 바다 보이는 조용한 카페 3곳만 추천해줘",
        "1번은 별로야, 다른 데로 몇 개 더",       # "바다 보이는 조용한" 생략
        "좀 더 시내 쪽으로",                        # 대부분 생략 + 새 제약
        "그 중에 주차 되는 곳 위주로",              # 누적 + 새 제약
    ]
    recent: list[str] = []
    echo = None
    for i, msg in enumerate(turns, 1):
        req = _req(msg, shown=echo).model_copy(update={"recent_messages": list(recent)})
        r = agent.run(req)
        print(f"\n[턴{i}] {msg}\n   → {r.user_message[:200]}")
        assert r.plan is None, f"턴{i}: 순수추천인데 plan 이 SET"
        assert 1 <= len(r.shown_places) <= 10, f"턴{i}: shownPlaces {len(r.shown_places)}"
        for s in r.shown_places:
            assert s.content_id and s.title and s.category and s.overview
        if echo:
            prev = {s["contentId"] for s in echo}
            assert all(s.content_id not in prev for s in r.shown_places), f"턴{i}: 직전 장소 재등장"
        echo = [{"contentId": s.content_id, "title": s.title, "category": s.category}
                for s in r.shown_places]
        recent = (recent + [msg])[-3:]
    assert len(recent) == 3                                 # 바운드 (턴1 발화는 밀려남)
