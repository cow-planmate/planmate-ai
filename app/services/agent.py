"""E-4 — 에이전트 루프.

계약: project_ai/04 §3 (루프·가드), 08 D8 (스켈레톤 검증), 11 §2 E-4, 14 (조립), 17 #3.

흐름:
  1. 요청 → ToolContext(region/signgu/exclude_ids/seed) + 프롬프트(SYSTEM + 컨텍스트)
  2. 루프: gemini.generate(tools) → function_call 있으면 run_tool → 되먹임 → 반복
           (가드: MAX_ITERS / 툴별 예산 / (툴,인자) dedup / 데드라인)
  3. 툴콜 없음 or 캡 도달 → _final_output() 으로 최종 op 리스트 (모드 = settings.final_output_mode,
     기본 json = 자유 JSON + 우리 검증. `17` E5-1 실측)
  4. assemble(llm_output, req, caches) → ChatbotResponse
"""

from __future__ import annotations

import json
import logging
import time

from google.genai import types

from app.config import settings
from app.data import region_index_client
from app.models import ChatbotRequest, ChatbotResponse, LlmOutput
from app.prompts import SYSTEM, build_context
from app.services import gemini
from app.services.assembly import assemble
from app.services.tools import GEMINI_TOOLS, ToolContext, run_tool

log = logging.getLogger(__name__)

MAX_ITERS = 6
BUDGET = {"search_places": 4, "generate_itinerary": 2}
DEADLINE_S = 30.0

_FINAL_NUDGE = (
    "이제 최종 결과를 낸다. 더 이상 search_places / generate_itinerary 는 부르지 마라.\n"
    "- search_places 후보를 받았으면 createBlock/updateBlock 의 placeId 에 그 content_id 를 반드시 채워라.\n"
    "- userMessage 는 actions 가 실제로 한 것만 말해라 (안 바꿨으면 바꿨다고 하지 마라)."
)

# ── function 모드: 최종 출력을 받는 submit_plan 선언 (LlmOutput 미러) ──────────
_OP_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["op"],
    properties={
        "op": types.Schema(type=types.Type.STRING, enum=[
            "createBlock", "updateBlock", "deleteBlock", "createTimeTable",
            "updateTimeTable", "deleteTimeTable", "replaceDayBlocks",
            "replacePlanBlocks", "resequenceBlocks"]),
        "date": types.Schema(type=types.Type.STRING),
        "blockStartTime": types.Schema(type=types.Type.STRING),
        "blockEndTime": types.Schema(type=types.Type.STRING),
        "blockCategory": types.Schema(type=types.Type.STRING,
                                      enum=["ATTRACTION", "ACCOMMODATION", "RESTAURANT"]),
        "placeId": types.Schema(type=types.Type.STRING,
                                description="search_places/generate_itinerary 결과의 content_id. 장소를 넣거나 바꾸면 필수."),
        "target": types.Schema(type=types.Type.OBJECT, description="편집 대상 블록 지칭", properties={
            "blockId": types.Schema(type=types.Type.INTEGER),
            "date": types.Schema(type=types.Type.STRING),
            "blockStartTime": types.Schema(type=types.Type.STRING),
            "blockCategory": types.Schema(type=types.Type.STRING),
        }),
        "timeTableId": types.Schema(type=types.Type.INTEGER),
        "timeTableStartTime": types.Schema(type=types.Type.STRING),
        "timeTableEndTime": types.Schema(type=types.Type.STRING),
        "from": types.Schema(type=types.Type.STRING, description="generate_itinerary 호출 핸들 gen_itin:call_N"),
        "scope": types.Schema(type=types.Type.STRING),
    },
)
SUBMIT_PLAN = types.FunctionDeclaration(
    name="submit_plan",
    description="플랜 변경 op 리스트 + 사용자 메시지를 제출하고 턴을 마친다.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        required=["userMessage", "actions", "shownPlaceIds"],
        properties={
            "userMessage": types.Schema(type=types.Type.STRING),
            "actions": types.Schema(type=types.Type.ARRAY, items=_OP_SCHEMA,
                                    description="플랜을 안 바꾸면 빈 배열"),
            "shownPlaceIds": types.Schema(type=types.Type.ARRAY,
                                          items=types.Schema(type=types.Type.STRING),
                                          description="이번에 추천/제시한 장소 content_id. 편집만 했으면 빈 배열"),
        },
    ),
)


def _seed() -> str:
    return str(int(time.time() * 1000))


def _collect_exclude_ids(req: ChatbotRequest) -> list[str]:
    ids: list[str] = []
    src = req.pending_context or req.plan_context
    for b in src.place_blocks:
        if b.place_id:
            ids.append(b.place_id)
    for s in req.shown_places or []:
        ids.append(s.content_id)
    return list(dict.fromkeys(ids))


def _fn_calls(resp) -> list:
    try:
        parts = resp.candidates[0].content.parts or []
    except (AttributeError, IndexError):
        return []
    return [p.function_call for p in parts if getattr(p, "function_call", None)]


def run(req: ChatbotRequest) -> ChatbotResponse:
    # 요청이 가져온 지역 색인 버전과 캐시가 다르면 v2 에서 다시 받는다(하한 간격 안에서는 생략).
    # 실패해도 예외를 내지 않는다 — 채팅 지역명 해석만 index_unavailable 이 된다(`22` §6-1).
    region_index_client.store.sync(req.user_context.region_index_version)
    ctx = ToolContext(
        region=[rc.ldong_regn_cd for rc in req.user_context.region_codes],
        signgu=list(req.user_context.signgu_codes),
        destination_id=req.plan_context.plan_frame.destination_id,
        region_index_version=req.user_context.region_index_version,
        exclude_ids=_collect_exclude_ids(req),
        seed=_seed(),
    )

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=build_context(req))]),
    ]
    tool_cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        tools=[GEMINI_TOOLS],
        temperature=0.7,
        # AFC 끔 — 우리가 루프에서 직접 function_call 파싱/디스패치 (17 C-5)
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    used: dict[str, int] = {}
    seen_calls: set[str] = set()
    t0 = time.monotonic()

    for _ in range(MAX_ITERS):
        if time.monotonic() - t0 > DEADLINE_S:
            log.warning("[agent] 데드라인 초과 — 강제 종료")
            break
        resp = gemini.generate(contents, config=tool_cfg)
        calls = _fn_calls(resp)
        if not calls:
            break  # 모델이 최종 답으로 감

        contents.append(resp.candidates[0].content)
        # Gemini 는 한 턴의 function_call N개에 대한 응답을 Content 1개 안에 part N개로
        # 묶어서 받아야 한다("function response parts 개수가 function call parts 개수와
        # 안 맞는다" 400) — LLM 이 병렬로 여러 툴을 부르면(예: 빈 슬롯 여러 개를 한 번에
        # 채우려 search_places 를 동시에 여러 번) 호출별로 Content 를 따로 append 하던
        # 기존 코드가 이 계약을 깨서 재현됨(P2 "3일차 오후가 비었는데 채워줘" 실측).
        response_parts: list[types.Part] = []
        for fc in calls:
            name = fc.name
            args = dict(fc.args or {})
            key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            if used.get(name, 0) >= BUDGET.get(name, 99):
                out = {"error": f"{name} 호출 예산 초과 — 지금까지 결과로 마무리하라"}
            elif key in seen_calls:
                out = {"error": "같은 인자로 이미 호출함 — 다른 조건으로 하거나 마무리하라"}
            else:
                used[name] = used.get(name, 0) + 1
                seen_calls.add(key)
                try:
                    out = run_tool(name, args, ctx)
                except Exception as e:  # noqa: BLE001
                    log.exception("[agent] 툴 실행 실패 %s", name)
                    out = {"error": f"툴 실행 오류: {e}"}
            response_parts.append(types.Part.from_function_response(name=name, response=out))
        contents.append(types.Content(role="user", parts=response_parts))

    contents.append(types.Content(role="user", parts=[types.Part(text=_FINAL_NUDGE)]))
    llm_out = _final_output(contents)

    return assemble(
        llm_out, req,
        gen_itin_cache=ctx.gen_itin_cache,
        search_cache=ctx.search_cache,
    )


def _final_output(contents: list) -> LlmOutput:
    """최종 op 리스트를 받는다. 모드 = settings.final_output_mode (E-5 실험, `17` E5-1)."""
    mode = settings.final_output_mode

    if mode == "function":
        cfg = types.GenerateContentConfig(
            system_instruction=SYSTEM, temperature=0.3,
            tools=[types.Tool(function_declarations=[SUBMIT_PLAN])],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="ANY", allowed_function_names=["submit_plan"])),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = gemini.generate(contents + [types.Content(
            role="user", parts=[types.Part(text="submit_plan 을 호출해 최종 결과를 제출해라.")])], config=cfg)
        fcs = _fn_calls(resp)
        if not fcs:
            raise RuntimeError("function 모드: submit_plan 호출 없음")
        return LlmOutput.model_validate(dict(fcs[0].args or {}))

    if mode == "json":
        cfg = types.GenerateContentConfig(
            system_instruction=SYSTEM, temperature=0.3,
            response_mime_type="application/json",
        )
        conv = list(contents)
        last_err = None
        for attempt in range(2):
            resp = gemini.generate(conv, config=cfg)
            try:
                return LlmOutput.model_validate_json(resp.text)
            except Exception as e:  # noqa: BLE001
                last_err = e
                conv = conv + [
                    resp.candidates[0].content,
                    types.Content(role="user", parts=[types.Part(text=(
                        f"방금 출력이 스키마와 안 맞음: {e}. "
                        "userMessage(str) / actions(op 배열) / shownPlaceIds(str 배열) 로만, JSON 으로 다시 내라."))]),
                ]
        raise RuntimeError(f"json 모드: 2회 파싱 실패 ({last_err})")

    # mode == "schema" (기본)
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM, temperature=0.3,
        response_mime_type="application/json", response_schema=LlmOutput,
    )
    resp = gemini.generate(contents, config=cfg)
    return resp.parsed if getattr(resp, "parsed", None) else LlmOutput.model_validate_json(resp.text)
