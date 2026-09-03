"""E-1 조립 레이어 테스트 — LLM op → 완성 plan.

Gemini 불필요. hydrate 는 로컬 부산 DB.
content_id: 1018702(관광 12) 1128306(식당 39) 1162526(숙박 32) 1046349(관광 12)
"""

import pytest

from app.models import ChatbotRequest, LlmOutput
from app.services.assembly import assemble

ATTR_A = "1018702"
ATTR_B = "1046349"
FOOD = "1128306"
LODGE = "1162526"


def _req(place_blocks=None, pending=None, shown=None, message="테스트"):
    base_plan = {
        "planFrame": {"planId": "p1", "planName": "부산", "destinationId": 20,
                      "destinationName": "부산", "adultCount": 2, "childCount": 0},
        "timetables": [
            {"timeTableId": 41, "date": "2026-10-11", "timeTableStartTime": "09:00:00", "timeTableEndTime": "20:00:00"},
            {"timeTableId": 42, "date": "2026-10-12", "timeTableStartTime": "09:00:00", "timeTableEndTime": "20:00:00"},
        ],
        "placeBlocks": place_blocks or [],
    }
    payload = {
        "planId": "p1", "message": message,
        "planContext": base_plan,
        "pendingContext": pending,
        "shownPlaces": shown,
        "userContext": {"preferredThemes": [], "regionCodes": [{"ldongRegnCd": "26"}], "signguCodes": []},
    }
    return ChatbotRequest.model_validate(payload)


def _block(bid, tt, start, cat, pid, memo=None):
    return {"blockId": bid, "timeTableId": tt, "placeId": pid, "placeName": f"blk{bid}",
            "blockStartTime": start, "blockEndTime": "23:59:00", "blockCategory": cat, "memo": memo}


# ── 기본: actions 없으면 plan=None ──────────────────────────────────────────
def test_no_actions_plan_none():
    out = LlmOutput.model_validate({"userMessage": "며칠 가세요?", "actions": [], "shownPlaceIds": []})
    resp = assemble(out, _req())
    assert resp.plan is None and resp.shown_places == []
    assert resp.user_message == "며칠 가세요?"


# ── createBlock — hydrate + id 없음 ────────────────────────────────────────
def test_create_block_hydrates(db):
    out = LlmOutput.model_validate({
        "userMessage": "넣었어요", "shownPlaceIds": [],
        "actions": [{"op": "createBlock", "date": "2026-10-11", "blockStartTime": "14:00:00",
                     "blockEndTime": "15:00:00", "blockCategory": "ATTRACTION", "placeId": ATTR_A}],
    })
    resp = assemble(out, _req())
    assert resp.plan is not None
    b = resp.plan.place_blocks[0]
    assert b.block_id is None
    assert b.place_id == ATTR_A
    assert b.place_name and b.place_name != ""      # DB 에서 hydrate
    assert b.latitude is not None and b.longitude is not None


# ── updateBlock by blockId — memo·id 보존, placeId 만 교체 ─────────────────
def test_update_block_by_id_keeps_memo(db):
    blocks = [_block(811, 41, "12:00:00", "RESTAURANT", FOOD, memo="여기 예약함")]
    out = LlmOutput.model_validate({
        "userMessage": "바꿨어요", "shownPlaceIds": [],
        "actions": [{"op": "updateBlock", "target": {"blockId": 811}, "placeId": ATTR_A}],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    b = resp.plan.place_blocks[0]
    assert b.block_id == 811
    assert b.memo == "여기 예약함"          # 보존
    assert b.place_id == ATTR_A             # 교체
    assert b.block_start_time == "12:00"  # 시각 유지


# ── #6: placeId 교체 시 content_type 역유도가 LLM blockCategory 를 이김 ────────
def test_update_block_category_from_content_type_not_llm(db):
    blocks = [_block(811, 41, "12:00:00", "RESTAURANT", FOOD)]
    out = LlmOutput.model_validate({
        "userMessage": "바꿔", "shownPlaceIds": [],
        # ATTR_A = content_type 12 → ATTRACTION. LLM 이 틀리게 RESTAURANT 실어도 무시돼야.
        "actions": [{"op": "updateBlock", "target": {"blockId": 811},
                     "placeId": ATTR_A, "blockCategory": "RESTAURANT"}],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    assert resp.plan.place_blocks[0].block_category == "ATTRACTION"


# ── #8b: 점유 슬롯에 createBlock → drop, 원본 유지 ─────────────────────────
def test_create_block_on_occupied_slot_dropped(db):
    blocks = [_block(811, 41, "12:00:00", "RESTAURANT", FOOD, memo="원본")]
    out = LlmOutput.model_validate({
        "userMessage": "추가", "shownPlaceIds": [],
        "actions": [{"op": "createBlock", "date": "2026-10-11", "blockStartTime": "12:00:00",
                     "blockEndTime": "13:00:00", "blockCategory": "ATTRACTION", "placeId": ATTR_A}],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    slot = [b for b in resp.plan.place_blocks if b.block_start_time == "12:00"]
    assert len(slot) == 1 and slot[0].block_id == 811 and slot[0].memo == "원본"


# ── #8: 기존 날짜에 createTimeTable → drop ────────────────────────────────
def test_create_timetable_existing_date_dropped(db):
    out = LlmOutput.model_validate({
        "userMessage": "추가", "shownPlaceIds": [],
        "actions": [{"op": "createTimeTable", "date": "2026-10-12",
                     "timeTableStartTime": "09:00", "timeTableEndTime": "20:00"}],
    })
    resp = assemble(out, _req())   # _req 기본 timetables = 10-11, 10-12
    dates = [t.date for t in resp.plan.timetables]
    assert dates == ["2026-10-11", "2026-10-12"]   # 안 늘어남


# ── updateBlock by 자연키 (새 블록, blockId 없음) ──────────────────────────
def test_update_block_by_natural_key(db):
    pending = {
        "planFrame": {"planId": "p1", "adultCount": 2, "childCount": 0},
        "timetables": [{"timeTableId": 42, "date": "2026-10-12",
                        "timeTableStartTime": "09:00:00", "timeTableEndTime": "20:00:00"}],
        "placeBlocks": [{"placeId": FOOD, "placeName": "옛식당", "date": "2026-10-12",
                         "blockStartTime": "18:00:00", "blockEndTime": "19:30:00",
                         "blockCategory": "RESTAURANT"}],
    }
    out = LlmOutput.model_validate({
        "userMessage": "저녁 딴 데로", "shownPlaceIds": [],
        "actions": [{"op": "updateBlock",
                     "target": {"date": "2026-10-12", "blockStartTime": "18:00:00"},
                     "placeId": ATTR_A}],
    })
    resp = assemble(out, _req(pending=pending))
    b = resp.plan.place_blocks[0]
    assert b.block_id is None
    assert b.place_id == ATTR_A


# ── updateBlock fuzzy 매칭 (정확 시각 없음 → 최근접) ────────────────────────
def test_update_block_fuzzy(db):
    pending = {
        "planFrame": {"planId": "p1", "adultCount": 0, "childCount": 0},
        "timetables": [{"timeTableId": 42, "date": "2026-10-12",
                        "timeTableStartTime": "09:00:00", "timeTableEndTime": "20:00:00"}],
        "placeBlocks": [
            {"placeId": FOOD, "date": "2026-10-12", "blockStartTime": "12:30:00",
             "blockEndTime": "13:30:00", "blockCategory": "RESTAURANT"},
            {"placeId": ATTR_B, "date": "2026-10-12", "blockStartTime": "18:15:00",
             "blockEndTime": "19:30:00", "blockCategory": "RESTAURANT"},
        ],
    }
    out = LlmOutput.model_validate({
        "userMessage": "저녁", "shownPlaceIds": [],
        "actions": [{"op": "updateBlock",
                     "target": {"date": "2026-10-12", "blockStartTime": "18:00:00",
                                "blockCategory": "RESTAURANT"},
                     "placeId": LODGE}],
    })
    resp = assemble(out, _req(pending=pending))
    # 18:00 없음 → 18:15 블록에 매칭
    hit = [b for b in resp.plan.place_blocks if b.place_id == LODGE]
    assert len(hit) == 1 and hit[0].block_start_time == "18:15"


# ── deleteBlock ────────────────────────────────────────────────────────────
def test_delete_block(db):
    blocks = [_block(811, 41, "12:00:00", "RESTAURANT", FOOD),
              _block(812, 41, "14:00:00", "ATTRACTION", ATTR_A)]
    out = LlmOutput.model_validate({
        "userMessage": "뺐어요", "shownPlaceIds": [],
        "actions": [{"op": "deleteBlock", "target": {"blockId": 811}}],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    ids = [b.block_id for b in resp.plan.place_blocks]
    assert ids == [812]


# ── 유지 블록 누락 방지 — 안 건드린 블록은 결과에 남아야 (14 §1) ───────────
def test_untouched_blocks_preserved(db):
    blocks = [
        _block(811, 41, "10:00:00", "ATTRACTION", ATTR_A, memo="1일차 메모"),
        _block(830, 42, "10:00:00", "ATTRACTION", ATTR_B, memo="2일차 메모"),
    ]
    out = LlmOutput.model_validate({
        "userMessage": "1일차에 하나 추가", "shownPlaceIds": [],
        "actions": [{"op": "createBlock", "date": "2026-10-11", "blockStartTime": "15:00:00",
                     "blockEndTime": "16:00:00", "blockCategory": "RESTAURANT", "placeId": FOOD}],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    by_id = {b.block_id: b for b in resp.plan.place_blocks}
    assert 811 in by_id and by_id[811].memo == "1일차 메모"
    assert 830 in by_id and by_id[830].memo == "2일차 메모"
    assert any(b.block_id is None and b.place_id == FOOD for b in resp.plan.place_blocks)


# ── replaceDayBlocks — from 핸들 → 빌더 블록으로 그 날만 교체 ───────────────
def test_replace_day_blocks(db):
    blocks = [_block(811, 41, "10:00:00", "ATTRACTION", ATTR_A, memo="1일차"),
              _block(830, 42, "10:00:00", "ATTRACTION", ATTR_B, memo="2일차 옛것")]
    gen_cache = {"gen_itin:call_1": {"blocks": [
        {"date": "2026-10-12", "blockStartTime": "09:30", "blockEndTime": "12:00",
         "blockCategory": "ATTRACTION", "placeId": FOOD, "placeName": "새것",
         "placeContentTypeId": "39", "placeAddress": "부산", "placeThumbnailUrl": "",
         "placeCopyrightDivCd": "", "latitude": 35.1, "longitude": 129.0},
    ], "notes": {}}}
    out = LlmOutput.model_validate({
        "userMessage": "2일차 다시", "shownPlaceIds": [],
        "actions": [{"op": "replaceDayBlocks", "date": "2026-10-12", "from": "gen_itin:call_1"}],
    })
    resp = assemble(out, _req(place_blocks=blocks), gen_itin_cache=gen_cache)
    d1 = [b for b in resp.plan.place_blocks if b.date == "2026-10-11"]
    d2 = [b for b in resp.plan.place_blocks if b.date == "2026-10-12"]
    assert len(d1) == 1 and d1[0].block_id == 811 and d1[0].memo == "1일차"   # 1일차 그대로
    assert len(d2) == 1 and d2[0].block_id is None and d2[0].place_id == FOOD  # 2일차 교체 (메모 소멸 = 의도)


# ── dropped op — 없는 blockId 참조는 조용히 무시 ──────────────────────────
def test_missing_blockid_op_dropped(db):
    out = LlmOutput.model_validate({
        "userMessage": "삭제", "shownPlaceIds": [],
        "actions": [{"op": "deleteBlock", "target": {"blockId": 99999}}],
    })
    resp = assemble(out, _req())   # 블록 없음
    assert resp.plan is not None and resp.plan.place_blocks == []


# ── shownPlaces (rich) — search_cache + 미절삭 overview ────────────────────
def test_shown_places_rich(db):
    out = LlmOutput.model_validate({
        "userMessage": "카페 2곳", "actions": [],
        "shownPlaceIds": [ATTR_A, FOOD],
    })
    search_cache = {
        ATTR_A: {"content_id": ATTR_A, "title": "국제시장", "category": "ATTRACTION",
                 "content_type_id": "12", "copyright_div_cd": "Type3", "addr1": "부산 중구",
                 "mapx": 129.0, "mapy": 35.1, "overview": "짧은 요약...", "thumbnail": "http://x"},
        FOOD: {"content_id": FOOD, "title": "태종대짬뽕", "category": "RESTAURANT",
               "content_type_id": "39", "copyright_div_cd": "", "addr1": "부산 영도구",
               "mapx": 129.1, "mapy": 35.05, "overview": "...", "thumbnail": "",
               "firstmenu": "짬뽕", "opentimefood": "10:00~21:00"},
    }
    resp = assemble(out, _req(), search_cache=search_cache)
    assert resp.plan is None
    assert len(resp.shown_places) == 2
    sp = {s.content_id: s for s in resp.shown_places}
    assert sp[ATTR_A].title == "국제시장" and sp[ATTR_A].latitude == 35.1
    assert sp[ATTR_A].overview and sp[ATTR_A].overview != "짧은 요약..."   # DB 재조회로 교체
    assert sp[FOOD].first_menu == "짬뽕" and sp[FOOD].open_time == "10:00~21:00"


# ── resequenceBlocks — 시각 재배치 ────────────────────────────────────────
def test_resequence(db):
    blocks = [
        _block(1, 41, "12:00:00", "RESTAURANT", FOOD),
        _block(2, 41, "14:00:00", "ATTRACTION", ATTR_A),
        _block(3, 41, "18:00:00", "RESTAURANT", ATTR_B),
    ]
    # 좌표 필요 → hydrate 안 하므로 수동
    req = _req(place_blocks=blocks)
    for b, (la, lo) in zip(req.plan_context.place_blocks, [(35.0, 129.0), (35.5, 129.5), (35.02, 128.9)]):
        b.latitude, b.longitude = la, lo
    out = LlmOutput.model_validate({
        "userMessage": "동선 정리", "shownPlaceIds": [],
        "actions": [{"op": "resequenceBlocks", "scope": "plan"}],
    })
    resp = assemble(out, req)
    starts = sorted(b.block_start_time for b in resp.plan.place_blocks)
    assert starts == ["12:00", "14:00", "18:00"]   # 시각 슬롯은 그대로
    # 관광(2)은 위치 고정, 식당 2개만 재배치 가능
    attr = [b for b in resp.plan.place_blocks if b.block_category == "ATTRACTION"][0]
    assert attr.block_start_time == "14:00"


def test_resequence_preview_blocks_no_collapse(db):
    """blockId 없는 프리뷰 블록 여러 개 → (date,None) 키 붕괴로 뭉개지면 안 됨 (#4)."""
    pending = {
        "planFrame": {"planId": "p1", "adultCount": 0, "childCount": 0},
        "timetables": [{"timeTableId": 41, "date": "2026-10-11",
                        "timeTableStartTime": "09:00:00", "timeTableEndTime": "20:00:00"}],
        "placeBlocks": [
            {"placeId": FOOD, "date": "2026-10-11", "blockStartTime": "12:00:00",
             "blockEndTime": "13:00:00", "blockCategory": "RESTAURANT"},
            {"placeId": ATTR_A, "date": "2026-10-11", "blockStartTime": "14:00:00",
             "blockEndTime": "15:00:00", "blockCategory": "ATTRACTION"},
            {"placeId": ATTR_B, "date": "2026-10-11", "blockStartTime": "18:00:00",
             "blockEndTime": "19:00:00", "blockCategory": "RESTAURANT"},
        ],
    }
    req = _req(pending=pending)
    for b, (la, lo) in zip(req.pending_context.place_blocks,
                           [(35.0, 129.0), (35.5, 129.5), (35.02, 128.9)]):
        b.latitude, b.longitude = la, lo
    out = LlmOutput.model_validate({
        "userMessage": "동선 정리", "shownPlaceIds": [],
        "actions": [{"op": "resequenceBlocks", "scope": "plan"}],
    })
    resp = assemble(out, req)
    pids = sorted(b.place_id for b in resp.plan.place_blocks)
    assert pids == sorted([FOOD, ATTR_A, ATTR_B])          # 셋 다 살아있음, 안 뭉개짐
    assert len(resp.plan.place_blocks) == 3
    assert sorted(b.block_start_time for b in resp.plan.place_blocks) == ["12:00", "14:00", "18:00"]
