"""E-2 — Gemini function-calling 툴 등록 + 디스패치.

계약: project_ai/16 (툴별 요청/응답), 05 §8-6 (LLM 노출 파라미터), 08 D9 (LLM 축약 반환).

- LLM 이 채우는 파라미터만 스키마에 노출. `signgu`/`anchor_xy`/`seed`/`exclude_ids`/`region`(일정 흐름)은
  에이전트가 `ToolContext` 로 주입.
- 반환은 LLM 컨텍스트용 축약 (좌표·thumbnail·raw type 제거). 전체 레코드는 `ToolContext.search_cache` 에.
- `subtype` 유효값 = codes.py SUBTYPE_VOCAB (17 #1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from google.genai import types

from app.data.codes import SUBTYPE_VOCAB
from app.services import candidates, itinerary

# ── LLM 축약 반환에서 뺄 필드 (블록 hydration 전용) ─────────────────────────
_DROP_FROM_PLACE = {"content_type_id", "copyright_div_cd", "mapx", "mapy", "thumbnail"}

_ALL_SUBTYPES = sorted({v for vs in SUBTYPE_VOCAB.values() for v in vs})


# ── Gemini function 선언 ────────────────────────────────────────────────────
SEARCH_PLACES = types.FunctionDeclaration(
    name="search_places",
    description=(
        "tourdata 캐시에서 장소를 검색한다. 장소 추천·치환·'must-have' 후보 조회에 쓴다. "
        "자연어 취향은 query 로, 분류는 subtype/category 로, 편의시설은 amenities 로 넘긴다."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "category": types.Schema(
                type=types.Type.STRING, enum=["attraction", "lodging", "restaurant"],
                description="장소 대분류. 관광지/문화시설=attraction, 숙소=lodging, 식당·카페=restaurant.",
            ),
            "region": types.Schema(
                type=types.Type.STRING,
                description="지역명(예: '부산', '부산 해운대'). 문장에서 추출. 일정 생성 흐름에선 비워둔다(자동 주입).",
            ),
            "subtype": types.Schema(
                type=types.Type.STRING,
                description="세부 종류. 유효값: " + ", ".join(_ALL_SUBTYPES)
                + ". 동의어('중국집'→'중식')도 인식. 목록에 없으면 query 로.",
            ),
            "query": types.Schema(
                type=types.Type.STRING,
                description="완전 자유 자연어 취향('바다 보이는 조용한', '가성비'). 분류·지역은 넣지 말 것.",
            ),
            "amenities": types.Schema(
                type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING),
                description="편의시설 키워드(['바베큐','사우나','주차','수영장']). 있으면 필터/헤지.",
            ),
            "name": types.Schema(
                type=types.Type.STRING,
                description="특정 장소를 이름으로 지목할 때만('경복궁 넣어줘'). discovery 필터는 무시된다.",
            ),
            "near": types.Schema(
                type=types.Type.STRING,
                description="'~근처' 기준점 장소명. 일정 생성 흐름에선 비워둔다(직전 슬롯 자동 체이닝).",
            ),
            "sort_by": types.Schema(
                type=types.Type.STRING, enum=["relevance", "distance"],
                description="정렬. 기본 relevance. '가까운 순'이면 distance.",
            ),
            "strict": types.Schema(
                type=types.Type.BOOLEAN,
                description="'X만'이면 true(하드 필터), 'X 위주로'면 false(랭크). 기본 false.",
            ),
            "max_budget": types.Schema(
                type=types.Type.INTEGER,
                description="숙소 1박 가격 상한(원). 'N원 이하 숙소'일 때만.",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER, description="최대 결과 수. 기본 20.",
            ),
        },
    ),
)

GENERATE_ITINERARY = types.FunctionDeclaration(
    name="generate_itinerary",
    description=(
        "여행 전체 일정을 통짜로 편성한다('부산 2박3일 짜줘'). "
        "day_plans 로 날짜별 슬롯 스켈레톤(시각·category·취향)만 넘기면, 빌더가 각 슬롯을 장소로 채운다. "
        "지역·일수·날짜는 자동 주입되므로 day_plans 의 date 는 플랜의 실제 날짜를 쓴다."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        required=["day_plans"],
        properties={
            "day_plans": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["date", "slots"],
                    properties={
                        "date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD, 플랜의 실제 날짜"),
                        "slots": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                required=["start", "end", "category"],
                                properties={
                                    "start": types.Schema(type=types.Type.STRING, description="HH:MM"),
                                    "end": types.Schema(type=types.Type.STRING, description="HH:MM"),
                                    "category": types.Schema(
                                        type=types.Type.STRING,
                                        enum=["attraction", "lodging", "restaurant"],
                                    ),
                                    "theme": types.Schema(type=types.Type.STRING, description="관광 테마(역사/자연 등)"),
                                    "cuisine": types.Schema(type=types.Type.STRING, description="음식 종류(한식/중식 등)"),
                                    "query": types.Schema(type=types.Type.STRING, description="자유 취향"),
                                    "amenities": types.Schema(
                                        type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING),
                                    ),
                                    "max_budget": types.Schema(type=types.Type.INTEGER),
                                    "near": types.Schema(type=types.Type.STRING, description="그 슬롯만 특정 지역 앵커"),
                                },
                            ),
                        ),
                    },
                ),
            ),
            "reuse_accommodation": types.Schema(
                type=types.Type.BOOLEAN,
                description="숙소 1회 검색 후 전 일자 동일 사용. 기본 true. '숙소는 내가 정할게'면 false.",
            ),
        },
    ),
)

GEMINI_TOOLS = types.Tool(function_declarations=[SEARCH_PLACES, GENERATE_ITINERARY])


# ── 에이전트 주입 컨텍스트 ──────────────────────────────────────────────────
@dataclass
class ToolContext:
    region: list[str] = field(default_factory=list)      # userContext.regionCodes → 코드
    signgu: list[str] = field(default_factory=list)
    exclude_ids: list[str] = field(default_factory=list)  # 누적
    seed: str = ""
    search_cache: dict[str, dict] = field(default_factory=dict)   # content_id -> 전체 레코드
    gen_itin_cache: dict[str, dict] = field(default_factory=dict)  # handle -> {blocks, notes}
    _gen_calls: int = 0


# ── 디스패치 ────────────────────────────────────────────────────────────────
def run_tool(name: str, args: dict, ctx: ToolContext) -> dict:
    if name == "search_places":
        return _search_places(args, ctx)
    if name == "generate_itinerary":
        return _generate_itinerary(args, ctx)
    return {"error": f"unknown tool: {name}"}


def _abridge_place(p: dict) -> dict:
    """LLM 컨텍스트용 축약 (08 D9). 좌표·thumbnail·raw type 제거, 나머지+D필드 유지."""
    return {k: v for k, v in p.items() if k not in _DROP_FROM_PLACE}


def _search_places(args: dict, ctx: ToolContext) -> dict:
    res = candidates.search_places(
        name=args.get("name"),
        category=args.get("category"),
        region=args.get("region") or (ctx.region or None),
        signgu=ctx.signgu or None,
        subtype=args.get("subtype"),
        amenities=args.get("amenities"),
        query=args.get("query"),
        strict=bool(args.get("strict", False)),
        sort_by=args.get("sort_by", "relevance"),
        near=args.get("near"),
        max_budget=args.get("max_budget"),
        limit=int(args.get("limit") or 20),
        exclude_ids=ctx.exclude_ids or None,
        seed=ctx.seed,
    )
    for p in res["places"]:
        ctx.search_cache[p["content_id"]] = p   # 전체 레코드 캐시 (조립·shownPlaces 용)
    return {
        "places": [_abridge_place(p) for p in res["places"]],
        "total": res["total"],
        "notes": res["notes"],
    }


def _generate_itinerary(args: dict, ctx: ToolContext) -> dict:
    ctx._gen_calls += 1
    handle = f"gen_itin:call_{ctx._gen_calls}"
    res = itinerary.generate_itinerary(
        day_plans=args["day_plans"],
        region=ctx.region or "",
        signgu=ctx.signgu or None,
        exclude_ids=list(ctx.exclude_ids),
        reuse_accommodation=bool(args.get("reuse_accommodation", True)),
        seed=ctx.seed,
    )
    ctx.gen_itin_cache[handle] = res
    for b in res["blocks"]:
        if b.get("placeId"):
            ctx.exclude_ids.append(b["placeId"])   # 재생성 시 중복 방지

    # LLM 축약 — 블록 목록 통째로 안 줌. 요약 + notes(헤지용) + 핸들.
    by_date: dict[str, int] = {}
    for b in res["blocks"]:
        by_date[b["date"]] = by_date.get(b["date"], 0) + 1
    return {
        "from": handle,
        "block_count": len(res["blocks"]),
        "days": [{"date": d, "count": c} for d, c in sorted(by_date.items())],
        "notes": res["notes"],
    }
