"""resequence (동선 재정렬 헬퍼) — 순수 기하, DB 불필요."""

from app.services.itinerary import resequence


def _b(date, start, cat, lat, lon, bid=None):
    return {
        "blockId": bid, "date": date, "blockStartTime": start, "blockEndTime": start,
        "blockCategory": cat, "latitude": lat, "longitude": lon, "placeName": f"{cat}@{lat},{lon}",
    }


def _names_in_time_order(blocks):
    return [b["placeName"] for b in sorted(blocks, key=lambda x: x["blockStartTime"])]


def test_swaps_same_category_to_shorten_route():
    # 시간순: 식당(0,0) · 관광A(10,0) · 식당(0.1,0) · 관광B(0.2,0)
    # 식당 두 개를 바꾸면 0→10→0.1→0.2 (지그재그) 가 0.1→10→0→0.2 로 …
    # 실제로는 "가까운 식당을 관광A 다음(3번째)로" 가 이득
    blocks = [
        _b("2026-10-11", "12:00", "RESTAURANT", 0.0, 0.0),
        _b("2026-10-11", "14:00", "ATTRACTION", 10.0, 0.0),
        _b("2026-10-11", "18:00", "RESTAURANT", 9.9, 0.0),
        _b("2026-10-11", "20:00", "ACCOMMODATION", 10.0, 0.0),
    ]
    out = resequence(blocks)
    order = _names_in_time_order(out)
    # 관광A(10,0) 바로 뒤(18:00 저녁)에 가까운 식당(9.9,0)이 와야 동선 최소
    assert order[1] == "ATTRACTION@10.0,0.0"
    assert order[2] == "RESTAURANT@9.9,0.0"
    assert order[0] == "RESTAURANT@0.0,0.0"
    # 시각은 위치 고정 — 저녁 자리는 여전히 18:00
    ev = [b for b in out if b["blockStartTime"] == "18:00"][0]
    assert ev["blockCategory"] == "RESTAURANT"


def test_slot_times_and_categories_unchanged():
    blocks = [
        _b("2026-10-11", "09:00", "ATTRACTION", 1.0, 1.0),
        _b("2026-10-11", "12:00", "RESTAURANT", 5.0, 5.0),
        _b("2026-10-11", "15:00", "ATTRACTION", 1.1, 1.0),
    ]
    out = resequence(blocks)
    slots = [(b["blockStartTime"], b["blockCategory"]) for b in sorted(out, key=lambda x: x["blockStartTime"])]
    assert slots == [("09:00", "ATTRACTION"), ("12:00", "RESTAURANT"), ("15:00", "ATTRACTION")]


def test_single_per_category_noop():
    blocks = [
        _b("2026-10-11", "09:00", "ATTRACTION", 1.0, 1.0, bid=1),
        _b("2026-10-11", "12:00", "RESTAURANT", 2.0, 2.0, bid=2),
        _b("2026-10-11", "20:00", "ACCOMMODATION", 3.0, 3.0, bid=3),
    ]
    out = resequence(blocks)
    assert [b["placeName"] for b in out] == [b["placeName"] for b in blocks]


def test_per_day_independent_with_carryover_anchor():
    blocks = [
        _b("2026-10-11", "18:00", "RESTAURANT", 0.0, 0.0),
        _b("2026-10-11", "20:00", "ACCOMMODATION", 0.0, 0.0),
        # 다음날: 관광 두 개 — 전날 끝(0,0) 에서 가까운 걸 먼저
        _b("2026-10-12", "10:00", "ATTRACTION", 20.0, 0.0),
        _b("2026-10-12", "14:00", "ATTRACTION", 0.5, 0.0),
    ]
    out = resequence(blocks)
    d2 = _names_in_time_order([b for b in out if b["date"] == "2026-10-12"])
    assert d2[0] == "ATTRACTION@0.5,0.0"     # 전날 앵커(0,0)에 가까운 것 먼저
    assert d2[1] == "ATTRACTION@20.0,0.0"


def test_missing_coords_no_crash():
    blocks = [
        _b("2026-10-11", "09:00", "ATTRACTION", None, None),
        _b("2026-10-11", "12:00", "RESTAURANT", 1.0, 1.0),
        _b("2026-10-11", "15:00", "ATTRACTION", 2.0, 2.0),
    ]
    out = resequence(blocks)
    assert len(out) == 3
