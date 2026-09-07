"""E-4 에이전트 루프 — 병렬 function call 회귀 테스트 (Gemini 콜 목, DB/네트워크 불필요).

한 턴에 function_call 이 여러 개(LLM 이 빈 슬롯 여러 개를 한 번에 채우려 search_places 를
동시에 여러 번 부르는 경우 등)일 때, 그 응답들을 Content 하나에 part 여러 개로 몰아서
보내야 한다 — 호출별로 Content 를 따로 쪼개면 Gemini 가 400("function response parts 개수가
function call parts 개수와 안 맞는다")을 던진다. 실측: P2 "3일차 오후가 비었는데 채워줘"
(08_통합_E2E_테스트_기록.md).
"""

from types import SimpleNamespace
from unittest.mock import patch

from app.models import ChatbotRequest, LlmOutput
from app.services import agent


def _req():
    return ChatbotRequest.model_validate({
        "planId": "p1", "message": "테스트",
        "planContext": {
            "planFrame": {"planId": "p1", "planName": "부산", "destinationName": "부산",
                          "adultCount": 2, "childCount": 0},
            "timetables": [], "placeBlocks": [],
        },
        "userContext": {"preferredThemes": [], "regionCodes": [], "signguCodes": []},
    })


def _fake_response(calls):
    """calls: [(name, args), ...] 나 [] (function_call 없는 최종 턴)."""
    parts = [SimpleNamespace(function_call=SimpleNamespace(name=n, args=a)) for n, a in calls]
    content = SimpleNamespace(parts=parts, role="model")
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def test_parallel_function_calls_bundled_into_single_content():
    call_turn = _fake_response([
        ("search_places", {"category": "restaurant"}),
        ("search_places", {"category": "attraction"}),
    ])
    final_turn = _fake_response([])   # function_call 없음 → 루프 탈출

    captured_contents: list[list] = []

    def fake_generate(contents, config=None):
        captured_contents.append(list(contents))
        return call_turn if len(captured_contents) == 1 else final_turn

    with patch.object(agent.gemini, "generate", side_effect=fake_generate), \
         patch.object(agent, "run_tool", return_value={"places": []}) as mock_run_tool, \
         patch.object(agent, "_final_output", return_value=LlmOutput.model_validate(
             {"userMessage": "됐어요", "actions": [], "shownPlaceIds": []})):
        resp = agent.run(_req())

    assert resp.user_message == "됐어요"
    assert mock_run_tool.call_count == 2   # 툴 호출 자체는 개별로 실행됨(디스패치는 정상)

    # 두 번째 gemini.generate() 호출에 실린 contents 의 마지막 원소가 = 첫 턴의 두 function_call
    # 에 대한 응답. 버그 있던 코드는 이걸 Content 2개로 쪼개서 보냈다 — 여기선 하나로 뭉쳐야 한다.
    second_call_contents = captured_contents[1]
    response_content = second_call_contents[-1]
    assert response_content.role == "user"
    assert len(response_content.parts) == 2, (
        "function_call 2개에 대한 응답이 Content 1개에 part 2개로 안 뭉쳐짐 — "
        "쪼개서 보내면 Gemini 가 400 을 던진다(실측 P2)"
    )


def test_single_function_call_still_works():
    """회귀 방지 — 병렬 아닌 단일 호출 경로는 그대로 동작해야 함."""
    call_turn = _fake_response([("search_places", {"category": "restaurant"})])
    final_turn = _fake_response([])

    captured_contents: list[list] = []

    def fake_generate(contents, config=None):
        captured_contents.append(list(contents))
        return call_turn if len(captured_contents) == 1 else final_turn

    with patch.object(agent.gemini, "generate", side_effect=fake_generate), \
         patch.object(agent, "run_tool", return_value={"places": []}), \
         patch.object(agent, "_final_output", return_value=LlmOutput.model_validate(
             {"userMessage": "됐어요", "actions": [], "shownPlaceIds": []})):
        resp = agent.run(_req())

    assert resp.user_message == "됐어요"
    response_content = captured_contents[1][-1]
    assert len(response_content.parts) == 1
