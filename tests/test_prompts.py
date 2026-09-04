"""build_context — 요청 컨텍스트 텍스트 조립 (DB·Gemini 무관, 순수)."""

from app.models import ChatbotRequest
from app.prompts import SYSTEM, build_context


def _req(**over):
    body = {
        "planId": "p1",
        "message": "이거 말고 몇 개 더",
        "planContext": {
            "planFrame": {"planId": "p1", "planName": "부산", "destinationName": "부산",
                          "adultCount": 2, "childCount": 0},
            "timetables": [], "placeBlocks": [],
        },
        "userContext": {"preferredThemes": [], "regionCodes": [], "signguCodes": []},
    }
    body.update(over)
    return ChatbotRequest.model_validate(body)


# ── recentMessages ────────────────────────────────────────────────────────
def test_recent_messages_rendered_oldest_to_newest():
    ctx = build_context(_req(recentMessages=[
        "부산 바다 보이는 조용한 카페 추천해줘",
        "너무 멀어, 시내 쪽으로",
    ]))
    assert "# 최근 대화" in ctx
    i_old = ctx.index("부산 바다 보이는 조용한 카페")
    i_new = ctx.index("시내 쪽으로")
    assert i_old < i_new                          # 오래된 → 최신
    assert ctx.index("# 최근 대화") < ctx.index("# 사용자 메시지")   # 현재 메시지가 뒤
    assert ctx.rstrip().endswith("이거 말고 몇 개 더")


def test_recent_messages_absent_no_section():
    assert "# 최근 대화" not in build_context(_req())
    assert "# 최근 대화" not in build_context(_req(recentMessages=[]))


def test_recent_messages_bounded_to_last_3():
    ctx = build_context(_req(recentMessages=["m1", "m2", "m3", "m4", "m5"]))
    assert "m1" not in ctx and "m2" not in ctx
    assert "m3" in ctx and "m4" in ctx and "m5" in ctx


def test_recent_messages_truncated_per_item():
    long = "가" * 900
    ctx = build_context(_req(recentMessages=[long]))
    assert "가" * 500 in ctx
    assert "가" * 501 not in ctx


def test_recent_messages_blank_entries_dropped():
    ctx = build_context(_req(recentMessages=["  ", "", "실제 발화"]))
    assert "실제 발화" in ctx
    assert ctx.count("  - ") == 1


# ── 하위호환 / 인젝션 규칙 ────────────────────────────────────────────────
def test_request_accepts_missing_recent_messages():
    assert _req().recent_messages == []


def test_system_prompt_covers_recent_messages_injection():
    assert "recentMessages" in SYSTEM
