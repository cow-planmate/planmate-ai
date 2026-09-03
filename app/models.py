"""요청/응답 DTO (v2 ↔ AI wire) + LLM 내부 출력 스키마.

계약 문서:
- 요청/응답 필드      = project_ai/12
- 케이스 전수          = project_ai/13
- 전체상태 plan 피벗   = project_ai/14  (응답 = actions[] 아니라 완성 plan)
- op 스키마            = project_ai/10 §4  (LLM 내부 출력, wire 안 나감. E-1 이 plan 으로 해석)

규칙:
- wire 키 = camelCase (v2 DTO 그대로). Python 은 snake_case + alias, populate_by_name.
- 요청/컨텍스트는 관대하게(extra="ignore"). 응답/op 는 정의된 필드만.
- 시각 = "HH:MM:SS" 문자열, 날짜 = "YYYY-MM-DD" 문자열 (12 §1 정책).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── 공용 config ──────────────────────────────────────────────────────────────
_LENIENT = ConfigDict(populate_by_name=True, extra="ignore")   # 요청/컨텍스트 (v2 가 필드 더 실어도 무시)
_MODEL = ConfigDict(populate_by_name=True)                     # 응답/내부

# blockCategory: 요청은 v2 의 5값 전부 수용, AI 출력은 3값만 (12 §3-3 / 14 §3)
BlockCategoryIn = Literal["ATTRACTION", "ACCOMMODATION", "RESTAURANT", "FREE", "SEARCH"]
BlockCategoryOut = Literal["ATTRACTION", "ACCOMMODATION", "RESTAURANT"]
PlaceCategory = Literal["attraction", "lodging", "restaurant"]


# ═══════════════════════════════════════════════════════════════════════════════
#  planContext / pendingContext / 응답 plan  — 셰이프 동일 (12 §3, 14 §3)
#  = v2 GetCompletePlanResponse 미러
# ═══════════════════════════════════════════════════════════════════════════════
class PlanFrame(BaseModel):
    model_config = _LENIENT
    plan_id: str = Field(alias="planId")
    plan_name: str | None = Field(default=None, alias="planName")
    destination_id: int | None = Field(default=None, alias="destinationId")
    destination_name: str | None = Field(default=None, alias="destinationName")
    adult_count: int = Field(default=0, alias="adultCount")
    child_count: int = Field(default=0, alias="childCount")


class Timetable(BaseModel):
    model_config = _LENIENT
    time_table_id: int | None = Field(default=None, alias="timeTableId")  # 새 날 = None
    date: str
    time_table_start_time: str = Field(alias="timeTableStartTime")
    time_table_end_time: str = Field(alias="timeTableEndTime")


class PlaceBlock(BaseModel):
    model_config = _LENIENT
    # planContext = 실 blockId / pendingContext·응답의 새 블록 = None (14 §4 자연키 지칭)
    block_id: int | None = Field(default=None, alias="blockId")
    time_table_id: int | None = Field(default=None, alias="timeTableId")
    # v2 wire 엔 없음 → E-1 정규화가 timeTableId↔timetables[].date 조인해 채움 (12 §8)
    date: str | None = None
    place_id: str | None = Field(default=None, alias="placeId")
    place_name: str | None = Field(default=None, alias="placeName")
    place_content_type_id: str | None = Field(default=None, alias="placeContentTypeId")
    place_address: str | None = Field(default=None, alias="placeAddress")
    place_thumbnail_url: str | None = Field(default=None, alias="placeThumbnailUrl")
    place_copyright_div_cd: str | None = Field(default=None, alias="placeCopyrightDivCd")
    latitude: float | None = None
    longitude: float | None = None
    block_start_time: str = Field(alias="blockStartTime")
    block_end_time: str = Field(alias="blockEndTime")
    # 요청 5값 / 응답 3값. str 로 받고 방향별 검증은 조립 레이어가.
    block_category: str = Field(alias="blockCategory")
    memo: str | None = None


class PlanState(BaseModel):
    """planContext / pendingContext / 응답 plan 공통."""
    model_config = _LENIENT
    plan_frame: PlanFrame = Field(alias="planFrame")
    timetables: list[Timetable] = Field(default_factory=list)
    place_blocks: list[PlaceBlock] = Field(default_factory=list, alias="placeBlocks")


# ═══════════════════════════════════════════════════════════════════════════════
#  userContext  — v2 에 대응 DTO 없음, planmate-ai 확정 (12 §4)
# ═══════════════════════════════════════════════════════════════════════════════
class PreferredTheme(BaseModel):
    model_config = _LENIENT
    level: int
    code: str


class RegionCode(BaseModel):
    model_config = _LENIENT
    ldong_regn_cd: str = Field(alias="ldongRegnCd")
    ldong_signgu_cd: str | None = Field(default=None, alias="ldongSignguCd")


class UserContext(BaseModel):
    model_config = _LENIENT
    preferred_themes: list[PreferredTheme] = Field(default_factory=list, alias="preferredThemes")
    region_codes: list[RegionCode] = Field(default_factory=list, alias="regionCodes")
    signgu_codes: list[str] = Field(default_factory=list, alias="signguCodes")


# ═══════════════════════════════════════════════════════════════════════════════
#  shownPlaces  — 비대칭: 요청 echo = lean 3필드 / 응답 = rich (12 §7-2, 13 §3-2)
# ═══════════════════════════════════════════════════════════════════════════════
class ShownPlaceRef(BaseModel):
    """요청 echo — 프론트가 rich 를 3필드로 축약. LLM 인식/리졸브용."""
    model_config = _LENIENT
    content_id: str = Field(alias="contentId")
    title: str
    category: str


class ShownPlace(BaseModel):
    """응답 — PlaceSummaryDto 상위집합 + 미절삭 overview + 카테고리 헤드라인."""
    model_config = _MODEL
    content_id: str = Field(alias="contentId")
    content_type_id: str | None = Field(default=None, alias="contentTypeId")
    category: str
    title: str
    addr1: str | None = None
    thumbnail_url: str | None = Field(default=None, alias="thumbnailUrl")
    copyright_div_cd: str | None = Field(default=None, alias="copyrightDivCd")
    latitude: float | None = None
    longitude: float | None = None
    overview: str | None = None                       # 미절삭 전문 (place_common 재조회)
    distance_km: float | None = Field(default=None, alias="distanceKm")
    # 헤드라인 (search_places D필드에서 — 있는 것만):
    first_menu: str | None = Field(default=None, alias="firstMenu")
    open_time: str | None = Field(default=None, alias="openTime")
    use_time: str | None = Field(default=None, alias="useTime")
    check_in_time: str | None = Field(default=None, alias="checkInTime")
    room_count: str | None = Field(default=None, alias="roomCount")


# ═══════════════════════════════════════════════════════════════════════════════
#  wire 요청 / 응답
# ═══════════════════════════════════════════════════════════════════════════════
class ChatbotRequest(BaseModel):
    model_config = _LENIENT
    plan_id: str = Field(alias="planId")                         # UUID (Plan.planId)
    message: str
    plan_context: PlanState = Field(alias="planContext")          # 항상 = 저장본(DB 진실)
    pending_context: PlanState | None = Field(default=None, alias="pendingContext")  # 프론트 working copy
    shown_places: list[ShownPlaceRef] | None = Field(default=None, alias="shownPlaces")
    user_context: UserContext = Field(alias="userContext")


class ChatbotResponse(BaseModel):
    model_config = _MODEL
    user_message: str = Field(alias="userMessage")
    plan: PlanState | None = None                                 # 완성 상태. 변경 없으면 None (14)
    shown_places: list[ShownPlace] = Field(default_factory=list, alias="shownPlaces")


# ═══════════════════════════════════════════════════════════════════════════════
#  LLM 내부 출력 스키마  — Gemini structured output. wire 안 나감.
#  E-1 assembly 가 이 op 리스트를 planContext 위에 적용해 응답 plan 을 만든다 (14 §5).
#  op 어휘 = 10 §4. 블록 지칭 = blockId(저장) 또는 (date, blockStartTime) 자연키(새 블록, 14 §4).
# ═══════════════════════════════════════════════════════════════════════════════
OpName = Literal[
    "createBlock", "updateBlock", "deleteBlock",
    "createTimeTable", "updateTimeTable", "deleteTimeTable",
    "replaceDayBlocks", "replacePlanBlocks", "resequenceBlocks",
]


class BlockTarget(BaseModel):
    """편집 대상 블록 지칭. block_id(저장 블록) 또는 (date + block_start_time)(새 블록 자연키). (14 §4)"""
    model_config = _MODEL
    block_id: int | None = Field(default=None, alias="blockId")
    date: str | None = None
    block_start_time: str | None = Field(default=None, alias="blockStartTime")
    block_category: str | None = Field(default=None, alias="blockCategory")   # fuzzy 매칭 필터 (선택)


class Op(BaseModel):
    """LLM 이 내는 단일 op. **평평한 스키마** — Gemini response_schema 가 discriminated union(oneOf)
    을 거부해서(17 C-1) 9종을 한 모델로 폈다. `op` 로 종류를 알고, 필요한 필드만 채운다.
    per-op 유효성(예: createBlock 은 placeId 필수)은 조립 레이어가 검사한다.

    - createBlock  : date, blockStartTime, blockEndTime, blockCategory, placeId
    - updateBlock  : target, + 바뀐 필드만 (placeId / blockStartTime(새 시각) / blockEndTime / date(이동) / blockCategory)
    - deleteBlock  : target
    - createTimeTable : date, timeTableStartTime, timeTableEndTime
    - updateTimeTable : timeTableId, + timeTableStartTime? / timeTableEndTime?
    - deleteTimeTable : timeTableId
    - replaceDayBlocks : date, from
    - replacePlanBlocks : from
    - resequenceBlocks : scope ("plan" 또는 "YYYY-MM-DD")
    """
    model_config = _MODEL
    op: OpName

    # 블록 create/update 공용
    date: str | None = None
    block_start_time: str | None = Field(default=None, alias="blockStartTime")
    block_end_time: str | None = Field(default=None, alias="blockEndTime")
    block_category: BlockCategoryOut | None = Field(default=None, alias="blockCategory")
    place_id: str | None = Field(default=None, alias="placeId")

    # update/delete 대상
    target: BlockTarget | None = None

    # 타임테이블
    time_table_id: int | None = Field(default=None, alias="timeTableId")
    time_table_start_time: str | None = Field(default=None, alias="timeTableStartTime")
    time_table_end_time: str | None = Field(default=None, alias="timeTableEndTime")

    # replace* — 빌더 호출 핸들 "gen_itin:call_N"
    from_: str | None = Field(default=None, alias="from")

    # resequence
    scope: str | None = None


class LlmOutput(BaseModel):
    """에이전트 루프 종료 시 LLM 이 내는 최종 구조화 출력.

    - actions   : 플랜 변경 op 리스트 (없으면 [] = 추천/되물음/불가안내 턴)
    - shown_place_ids : 이번 턴이 추천이면 userMessage 가 가리키는 후보 content_id 들.
                        E-1 이 이 id 로 place_common 재조회해 응답 shownPlaces(rich) 를 만든다.
    """
    model_config = _MODEL
    user_message: str = Field(alias="userMessage")
    actions: list[Op] = Field(default_factory=list)
    shown_place_ids: list[str] = Field(default_factory=list, alias="shownPlaceIds")
