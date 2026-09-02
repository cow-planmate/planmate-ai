"""generate_itinerary 빌더 통합 테스트 — 로컬 DB + 부산(regn 26) subset.

`query` 슬롯은 embed_query 를 목킹 (Gemini API 콜 없음). 대부분 슬롯은 query 없이.
"""

import pytest

from app.services import candidates
from app.services.itinerary import generate_itinerary

pytestmark = pytest.mark.usefixtures("db")

_V2_BLOCK_KEYS = {
    "date", "blockStartTime", "blockEndTime", "blockCategory", "placeId", "placeName",
    "placeContentTypeId", "placeAddress", "placeThumbnailUrl", "placeCopyrightDivCd",
    "latitude", "longitude",
}


def _day(date, *slots):
    return {"date": date, "slots": list(slots)}


def _slot(start, end, category, **kw):
    return {"start": start, "end": end, "category": category, **kw}


def test_basic_shape_and_category_mapping():
    r = generate_itinerary(
        day_plans=[_day(
            "2026-10-11",
            _slot("09:30", "12:00", "attraction", theme="역사"),
            _slot("12:30", "13:30", "restaurant", cuisine="한식"),
            _slot("20:00", "21:00", "lodging"),
        )],
        region="부산",
        seed="t1",
    )
    assert r["notes"] == {}
    assert len(r["blocks"]) == 3
    for b in r["blocks"]:
        assert set(b) == _V2_BLOCK_KEYS
        assert b["date"] == "2026-10-11"
        assert b["placeId"] and b["placeName"]
    assert r["blocks"][0]["blockCategory"] == "ATTRACTION"
    assert r["blocks"][1]["blockCategory"] == "RESTAURANT"
    assert r["blocks"][2]["blockCategory"] == "ACCOMMODATION"


def test_no_duplicate_places_across_slots():
    r = generate_itinerary(
        day_plans=[_day(
            "2026-10-11",
            *[_slot(f"{h:02d}:00", f"{h+1:02d}:00", "restaurant") for h in range(9, 15)],
        )],
        region="부산",
        seed="t2",
    )
    ids = [b["placeId"] for b in r["blocks"]]
    assert len(ids) == len(set(ids))          # 슬롯 간 중복 없음
    assert len(ids) == 6


def test_exclude_ids_respected():
    first = generate_itinerary(
        day_plans=[_day("2026-10-11", _slot("12:00", "13:00", "restaurant"))],
        region="부산", seed="t3",
    )["blocks"][0]["placeId"]

    r = generate_itinerary(
        day_plans=[_day("2026-10-11", _slot("12:00", "13:00", "restaurant"))],
        region="부산", seed="t3", exclude_ids=[first],
    )
    assert r["blocks"][0]["placeId"] != first


def test_accommodation_reused_across_days():
    r = generate_itinerary(
        day_plans=[
            _day("2026-10-11", _slot("20:00", "21:00", "lodging")),
            _day("2026-10-12", _slot("20:00", "21:00", "lodging")),
            _day("2026-10-13", _slot("09:00", "12:00", "attraction")),
        ],
        region="부산", seed="t4",
    )
    lodging = [b for b in r["blocks"] if b["blockCategory"] == "ACCOMMODATION"]
    assert len(lodging) == 2
    assert lodging[0]["placeId"] == lodging[1]["placeId"]           # 재사용
    assert lodging[0]["date"] == "2026-10-11" and lodging[1]["date"] == "2026-10-12"


def test_accommodation_not_reused_when_flag_off():
    r = generate_itinerary(
        day_plans=[
            _day("2026-10-11", _slot("20:00", "21:00", "lodging")),
            _day("2026-10-12", _slot("20:00", "21:00", "lodging")),
        ],
        region="부산", seed="t5", reuse_accommodation=False,
    )
    lodging = [b for b in r["blocks"] if b["blockCategory"] == "ACCOMMODATION"]
    assert lodging[0]["placeId"] != lodging[1]["placeId"]


def test_region_unresolved_early_return():
    r = generate_itinerary(
        day_plans=[_day("2026-10-11", _slot("12:00", "13:00", "restaurant"))],
        region="우리 동네", seed="t6",
    )
    assert r == {"blocks": [], "notes": {"region": "unresolved"}}


def test_empty_skeleton():
    assert generate_itinerary(day_plans=[], region="부산", seed="t7") == {"blocks": [], "notes": {}}
    assert generate_itinerary(
        day_plans=[_day("2026-10-11")], region="부산", seed="t7",
    ) == {"blocks": [], "notes": {}}


def test_over_constrained_slot_relaxes_and_notes():
    # lodging + 말도 안 되는 예산 → 완화가 max_budget 떨궈 채움 + notes.relaxed 기록 (§8-7)
    r = generate_itinerary(
        day_plans=[_day("2026-10-11", _slot("20:00", "21:00", "lodging", max_budget=1))],
        region="부산", seed="t8",
    )
    assert len(r["blocks"]) == 1
    assert r["blocks"][0]["blockCategory"] == "ACCOMMODATION"
    assert "max_budget" in r["notes"]["2026-10-11.slot0"]["relaxed"]


def test_notes_keyed_by_slot_ref(monkeypatch):
    # 못 맞추는 subtype → search_places 가 notes.subtype_unmatched → 슬롯 ref 로 집계
    r = generate_itinerary(
        day_plans=[_day("2026-10-11", _slot("12:00", "13:00", "restaurant", cuisine="마라탕"))],
        region="부산", seed="t9",
    )
    assert "2026-10-11.slot0" in r["notes"]
    assert r["notes"]["2026-10-11.slot0"].get("subtype_unmatched") == "마라탕"


def test_signgu_scopes_all_picks():
    from app.db import query as dbq
    r = generate_itinerary(
        day_plans=[_day("2026-10-11",
                        _slot("09:30", "12:00", "attraction"),
                        _slot("12:30", "13:30", "restaurant"),
                        _slot("18:00", "19:30", "restaurant"))],
        region="부산", signgu=["710"], seed="sg1",
    )
    assert len(r["blocks"]) == 3
    assert r["notes"] == {}                       # 710 은 다 있어서 완화 없음
    ids = [b["placeId"] for b in r["blocks"]]
    rows = dbq("SELECT content_id FROM ai_place_v WHERE content_id = ANY(%(ids)s) AND ldong_signgu_cd='710'",
               {"ids": ids})
    assert len(rows) == 3                         # 전부 710 안


def test_geo_widen_relaxation():
    # 없는 시군구 코드 → 슬롯마다 시도(부산)로 넓힘 + relaxed 에 "area"
    r = generate_itinerary(
        day_plans=[_day("2026-10-11", _slot("12:30", "13:30", "restaurant"))],
        region="부산", signgu=["999"], seed="sg2",
    )
    assert len(r["blocks"]) == 1                  # 넓혀서 채워짐
    assert "area" in r["notes"]["2026-10-11.slot0"]["relaxed"]


def test_query_slot_mocked(monkeypatch):
    from app.db import query as dbq

    row = dbq("SELECT e.embedding::text AS vec FROM ai_place_v l "
              "JOIN place_embedding e USING (content_id) "
              "WHERE l.ldong_regn_cd='26' AND l.content_type_id='39' LIMIT 1")[0]
    vec = [float(x) for x in row["vec"].strip("[]").split(",")]
    monkeypatch.setattr(candidates, "embed_query", lambda _t: vec)

    r = generate_itinerary(
        day_plans=[_day("2026-10-11", _slot("18:00", "19:30", "restaurant", query="분위기 좋은"))],
        region="부산", seed="t10",
    )
    assert len(r["blocks"]) == 1
    assert r["blocks"][0]["placeId"]
