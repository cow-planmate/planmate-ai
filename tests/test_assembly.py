"""E-1 조립 레이어 테스트 — LLM op → 완성 plan.

Gemini 불필요. hydrate 는 로컬 부산 DB.
content_id: 1018702(관광 12) 1128306(식당 39) 1162526(숙박 32) 1046349(관광 12)
"""

from unittest.mock import patch

import pytest

from app.models import ChatbotRequest, LlmOutput
from app.services.assembly import _resolve_overlap, assemble

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


def _block(bid, tt, start, cat, pid, memo=None, end="23:59:00"):
    return {"blockId": bid, "timeTableId": tt, "placeId": pid, "placeName": f"blk{bid}",
            "blockStartTime": start, "blockEndTime": end, "blockCategory": cat, "memo": memo}


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


# ── #8b(갱신, 겹침 자동 회피 도입 후): 점유 슬롯에 createBlock → drop 아니라
#    가장 가까운 빈 자리로 재배치 + userMessage 헤지. 기존 블록은 안 건드림. ─────
def test_create_block_on_occupied_slot_relocated(db):
    blocks = [_block(811, 41, "12:00:00", "RESTAURANT", FOOD, memo="원본")]  # 12:00~23:59(헬퍼 기본)
    out = LlmOutput.model_validate({
        "userMessage": "추가", "shownPlaceIds": [],
        "actions": [{"op": "createBlock", "date": "2026-10-11", "blockStartTime": "12:00:00",
                     "blockEndTime": "13:00:00", "blockCategory": "ATTRACTION", "placeId": ATTR_A}],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    original = [b for b in resp.plan.place_blocks if b.block_id == 811]
    assert original[0].block_start_time == "12:00" and original[0].memo == "원본"   # 기존 블록 안 건드림

    new_block = [b for b in resp.plan.place_blocks if b.place_id == ATTR_A]
    assert len(new_block) == 1
    # 09:00~12:00 이 유일한 빈 틈 → duration(1h) 만큼 그 틈 끝(11:00~12:00)에 붙는 게 가장 가까움
    assert new_block[0].block_start_time == "11:00" and new_block[0].block_end_time == "12:00"
    assert "겹쳐" in resp.user_message


# ── 겹침 회피 — 두 블록 사이 좁은 틈에 정확히 들어맞는 경우 ────────────────
def test_create_block_fits_between_existing_blocks(db):
    blocks = [
        _block(1, 41, "09:00:00", "RESTAURANT", FOOD, end="10:00:00"),
        _block(2, 41, "11:00:00", "ATTRACTION", ATTR_B, end="13:00:00"),
    ]
    out = LlmOutput.model_validate({
        "userMessage": "커피", "shownPlaceIds": [],
        # 10:30~11:30 요청 — 1과 2 둘 다와 겹침. 유일하게 맞는 틈 = 10:00~11:00(1시간)
        "actions": [{"op": "createBlock", "date": "2026-10-11", "blockStartTime": "10:30:00",
                     "blockEndTime": "11:30:00", "blockCategory": "RESTAURANT", "placeId": LODGE}],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    new_block = [b for b in resp.plan.place_blocks if b.place_id == LODGE][0]
    assert new_block.block_start_time == "10:00" and new_block.block_end_time == "11:00"


# ── 겹침 회피 — 하루가 꽉 차 있으면 창을 늘려서라도 맨 끝에 배치(임계값 없음) ──
def test_create_block_extends_timetable_window_when_day_is_full(db):
    blocks = [_block(1, 41, "09:00:00", "RESTAURANT", FOOD, end="19:00:00")]  # 하루 거의 꽉 참(창 09~20)
    out = LlmOutput.model_validate({
        "userMessage": "저녁", "shownPlaceIds": [],
        "actions": [{"op": "createBlock", "date": "2026-10-11", "blockStartTime": "12:00:00",
                     "blockEndTime": "14:00:00", "blockCategory": "RESTAURANT", "placeId": ATTR_A}],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    new_block = [b for b in resp.plan.place_blocks if b.place_id == ATTR_A][0]
    assert new_block.block_start_time == "19:00" and new_block.block_end_time == "21:00"
    tt = next(t for t in resp.plan.timetables if t.date == "2026-10-11")
    assert tt.time_table_end_time == "21:00"   # 창이 21:00 까지 늘어남


# ── 겹침 회피 — updateBlock 으로 시각을 옮겼는데 다른 블록과 겹치면 재배치 ────
def test_update_block_time_change_relocated_on_overlap(db):
    blocks = [
        _block(811, 41, "12:00:00", "RESTAURANT", FOOD, end="13:00:00"),
        _block(812, 41, "18:00:00", "ATTRACTION", ATTR_B, end="19:00:00", memo="유지될 메모"),
    ]
    out = LlmOutput.model_validate({
        "userMessage": "당겨줘", "shownPlaceIds": [],
        # 812(1시간짜리)를 12:30~13:30 으로 당김 — 811(12:00~13:00)과 겹침
        "actions": [{"op": "updateBlock", "target": {"blockId": 812},
                     "blockStartTime": "12:30:00", "blockEndTime": "13:30:00"}],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    b812 = next(b for b in resp.plan.place_blocks if b.block_id == 812)
    assert b812.block_start_time == "13:00"   # 811 끝(13:00) 바로 뒤로 밀림
    assert b812.block_end_time == "14:00"     # 원래 1시간 duration 유지
    assert b812.memo == "유지될 메모"          # 나머지 필드 보존
    assert "겹쳐" in resp.user_message


# ── 안 겹치면 조정 없음(회귀 방지) ──────────────────────────────────────────
def test_create_block_no_overlap_untouched(db):
    blocks = [_block(1, 41, "09:00:00", "RESTAURANT", FOOD, end="10:00:00")]
    out = LlmOutput.model_validate({
        "userMessage": "추가", "shownPlaceIds": [],
        "actions": [{"op": "createBlock", "date": "2026-10-11", "blockStartTime": "14:00:00",
                     "blockEndTime": "15:00:00", "blockCategory": "ATTRACTION", "placeId": ATTR_A}],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    new_block = [b for b in resp.plan.place_blocks if b.place_id == ATTR_A][0]
    assert new_block.block_start_time == "14:00" and new_block.block_end_time == "15:00"
    assert "겹쳐" not in resp.user_message


# ── 겹침 회피 방어 가드 — 재배치 결과가 원래 자리와 같으면 조정 안 함 취급 ────
# 실서버에서 "'OO' 일정이 기존 일정과 겹쳐 20:30에서 20:30로 조정했어요" 처럼 의미 없는
# 헤지가 나간 걸 발견(08_통합_E2E_테스트_기록.md 발견#7 인접 증상). _nearest_fit 이 desired와
# 같은 지점을 돌려주는 경우, _resolve_overlap 은 조정 없음(None)으로 취급해야 한다.
class _FakeBlock:
    def __init__(self, start, end):
        self.block_start_time = start
        self.block_end_time = end


def test_resolve_overlap_noop_guard_when_relocation_equals_desired():
    siblings = [_FakeBlock("12:00", "13:00")]   # 겹침이 감지되도록(원인은 몰라도 재현만)
    with patch("app.services.assembly._nearest_fit", return_value=_to_min_helper("12:30")):
        result = _resolve_overlap("12:30", "13:30", "09:00", siblings)
    assert result is None


def _to_min_helper(hms: str) -> int:
    h, m = hms.split(":")
    return int(h) * 60 + int(m)


# ── 배치(여러 블록이 같은 날짜를 동시에 겨냥) 겹침 회피 끔 — 도미노 사고 방지 ────
# 실서버 T1("1일차랑 2일차 순서 바꿔줘")에서 8개 블록이 전부 updateBlock{date=...}로 같은
# 날짜를 동시에 겨냥 → 순차 처리 중 겹침→재배치→다음 op 와 또 겹침이 도미노로 이어져
# 여러 블록이 23:59 로 쓸려가는 사고가 실측됨(08_통합_E2E_테스트_기록.md 발견#8).
# D11 스코프는 단일 블록 편집이지 배치가 아니므로, 같은 날짜를 2번 이상 겨냥하면 꺼야 한다.
def test_batch_date_move_skips_overlap_avoidance(db):
    blocks = [
        _block(1, 41, "09:00:00", "RESTAURANT", FOOD, end="10:00:00"),    # 1일차
        _block(2, 41, "11:00:00", "ATTRACTION", ATTR_A, end="13:00:00"),  # 1일차
        _block(3, 42, "09:00:00", "RESTAURANT", FOOD, end="10:00:00"),    # 2일차 — 블록1 이동 도착지와 겹침
    ]
    out = LlmOutput.model_validate({
        "userMessage": "1일차를 2일차로 옮겼어요", "shownPlaceIds": [],
        "actions": [
            {"op": "updateBlock", "target": {"blockId": 1}, "date": "2026-10-12"},
            {"op": "updateBlock", "target": {"blockId": 2}, "date": "2026-10-12"},
        ],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    b1 = next(b for b in resp.plan.place_blocks if b.block_id == 1)
    # 배치로 잡혀 회피가 꺼졌으므로 블록3(09:00-10:00)과 겹쳐도 원래 시각 그대로 이동
    assert b1.date == "2026-10-12" and b1.block_start_time == "09:00"
    assert "겹쳐" not in resp.user_message


# ── 발견#7: 같은 턴의 협조적 멀티op — 겹침 판정은 턴이 다 끝난 최종 상태로 해야 ────
# 실서버 S6("2일차에 해동용궁사 넣어줘")에서 LLM이 createBlock(절, 15:00-17:00) +
# updateBlock(박물관, 15:00-17:30 → 17:00-18:00)을 같은 턴에 냈다. op를 순서대로
# 처리하며 그 자리에서 겹침을 판정하면, createBlock 처리 시점엔 박물관이 아직 안 밀린
# 옛 시각(15:00-17:30)이라 "자리 없음"으로 오판해 절을 저녁 뒤(19:30-21:30)로 밀어버렸다
# (08_통합_E2E_테스트_기록.md 발견#7). 이 케이스는 `_batch_dates()`에 안 걸린다 — blockId로
# 지칭하는 updateBlock은 target.date가 비어 있어 날짜 카운트에 안 잡히기 때문. 턴을 다
# 적용한 뒤 최종 상태로 한 번만 판정하면, 박물관이 이미 17:00로 옮겨간 뒤라 절의
# 15:00-17:00은 애초에 안 겹친다 — 재배치도 헤지 문구도 없어야 한다.
def test_coordinated_multi_op_overlap_resolved_against_final_state(db):
    blocks = [_block(900, 41, "15:00:00", "ATTRACTION", ATTR_B, end="17:30:00")]
    out = LlmOutput.model_validate({
        "userMessage": "절 추가하고 박물관도 옮겼어요", "shownPlaceIds": [],
        "actions": [
            {"op": "createBlock", "date": "2026-10-11", "blockStartTime": "15:00:00",
             "blockEndTime": "17:00:00", "blockCategory": "ATTRACTION", "placeId": ATTR_A},
            {"op": "updateBlock", "target": {"blockId": 900},
             "blockStartTime": "17:00:00", "blockEndTime": "18:00:00"},
        ],
    })
    resp = assemble(out, _req(place_blocks=blocks))
    temple = next(b for b in resp.plan.place_blocks if b.place_id == ATTR_A)
    museum = next(b for b in resp.plan.place_blocks if b.block_id == 900)
    assert temple.block_start_time == "15:00" and temple.block_end_time == "17:00"
    assert museum.block_start_time == "17:00" and museum.block_end_time == "18:00"
    assert "겹쳐" not in resp.user_message


# ── E5-1: target 만 있고 바꿀 필드 없는 updateBlock → drop (no-op 방지) ────
def test_update_block_no_change_dropped(db):
    blocks = [_block(811, 41, "18:00:00", "RESTAURANT", FOOD, memo="원본")]
    out = LlmOutput.model_validate({
        "userMessage": "바꿨어요", "shownPlaceIds": [],
        "actions": [{"op": "updateBlock", "target": {"blockId": 811}}],   # placeId·시각·date 없음
    })
    resp = assemble(out, _req(place_blocks=blocks))
    b = resp.plan.place_blocks[0]
    assert b.block_id == 811 and b.place_id == FOOD and b.memo == "원본"   # 그대로


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


# ── 발견#11 — createTimeTable이 필수 필드 없이 드롭된 뒤 replaceDayBlocks가
#    새 날짜에 고아 블록을 채워도, 최종적으로 타임테이블이 자동 합성돼야 한다 ────
# 실서버 T8("4일차 추가해서 채워줘")에서 LLM이 createTimeTable(시각 없음, 드롭됨) +
# replaceDayBlocks(신규 날짜)를 같이 냈다. replaceDayBlocks는 타임테이블 존재를 확인하지
# 않아, 응답엔 블록이 다 보이는데 plan.timetables엔 그 날짜가 없는 고아 블록이 생겼다 —
# v2가 [반영]을 success:true로 응답하고도 조용히 유실시킴(08_통합_E2E_테스트_기록.md 발견#11).
def test_new_day_without_timetable_gets_synthesized(db):
    blocks = [_block(811, 41, "10:00:00", "ATTRACTION", ATTR_A, memo="1일차")]
    gen_cache = {"gen_itin:call_1": {"blocks": [
        {"date": "2026-10-13", "blockStartTime": "09:00", "blockEndTime": "10:00",
         "blockCategory": "RESTAURANT", "placeId": FOOD, "placeName": "아침",
         "placeContentTypeId": "39", "placeAddress": "부산", "placeThumbnailUrl": "",
         "placeCopyrightDivCd": "", "latitude": 35.1, "longitude": 129.0},
        {"date": "2026-10-13", "blockStartTime": "11:00", "blockEndTime": "13:00",
         "blockCategory": "ATTRACTION", "placeId": ATTR_B, "placeName": "관광",
         "placeContentTypeId": "12", "placeAddress": "부산", "placeThumbnailUrl": "",
         "placeCopyrightDivCd": "", "latitude": 35.1, "longitude": 129.0},
    ], "notes": {}}}
    out = LlmOutput.model_validate({
        "userMessage": "3일차 추가해서 채워드렸습니다", "shownPlaceIds": [],
        "actions": [
            # createTimeTable: 필수 필드(시각) 없이 내서 드롭되는 상황 재현
            {"op": "createTimeTable", "date": "2026-10-13"},
            {"op": "replaceDayBlocks", "date": "2026-10-13", "from": "gen_itin:call_1"},
        ],
    })
    resp = assemble(out, _req(place_blocks=blocks), gen_itin_cache=gen_cache)

    tt = [t for t in resp.plan.timetables if t.date == "2026-10-13"]
    assert len(tt) == 1, "새 날짜 타임테이블이 자동 합성돼야 한다 — 없으면 고아 블록으로 유실됨"
    assert tt[0].time_table_start_time == "09:00" and tt[0].time_table_end_time == "13:00"

    d3 = [b for b in resp.plan.place_blocks if b.date == "2026-10-13"]
    assert len(d3) == 2   # 신규 블록도 정상 유지(고아로 안 사라짐)


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
