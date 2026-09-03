"""조립 레이어 (E-1) — LLM 내부 op 출력 → 완성된 플랜 상태 `plan`.

계약: project_ai/14 (전체상태 모델), 11 §2 E-1, 10 §4 (op), 12 §7 (shownPlaces).

루프 끝난 뒤 실행. **추론 없음** — op 를 planContext/pendingContext 위에 적용해
완성 상태를 만들고, place 필드는 캐시 DB 에서 hydrate.

- 편집 대상 = `pendingContext`(있으면) / `planContext`. 항상 planContext 는 저장본(안 바뀜).
- 유지 블록 = 편집 대상에서 통째 복사 (blockId·memo·필드). op 가 안 건드린 블록은 **반드시 결과에 포함**
  (빠뜨리면 v2 reconcile 이 삭제, 14 §1).
- 새 블록 = blockId 없음. LLM 이 `(date, blockStartTime)` 자연키로 지칭 (14 §4).
- 시각 이동 + 장소 교체 동시로 자연키가 옛 위치를 가리키면 → `(date, blockCategory, 가장 가까운 시각)` fuzzy (14 §4).
"""

from __future__ import annotations

import logging

from app.db import query as db_query
from app.models import (
    ChatbotRequest,
    ChatbotResponse,
    LlmOutput,
    Op,
    PlaceBlock,
    PlanState,
    ShownPlace,
    Timetable,
)
from app.services.itinerary import resequence as _resequence

log = logging.getLogger(__name__)

_CTYPE_CAT = {"12": "attraction", "14": "attraction", "32": "lodging", "39": "restaurant"}
# content_type_id → v2 BlockCategory. LLM 값 대신 이걸로 확정 (v2 PlaceSummaryDto "역유도" 방식, 17 C-6).
_CTYPE_BLOCKCAT = {"12": "ATTRACTION", "14": "ATTRACTION", "32": "ACCOMMODATION", "39": "RESTAURANT"}

# search_places D필드 key → ShownPlace 헤드라인 필드
_HEADLINE = {
    "firstmenu": "first_menu",
    "opentimefood": "open_time",
    "usetime": "use_time",
    "checkintime": "check_in_time",
    "roomcount": "room_count",
}


# ── 캐시 DB hydrate ──────────────────────────────────────────────────────────
def hydrate_places(content_ids: list[str]) -> dict[str, dict]:
    """content_id → 블록 place 필드 (ai_place_v). 없는 id 는 결과에서 빠짐."""
    ids = [str(c) for c in dict.fromkeys(content_ids) if c]
    if not ids:
        return {}
    rows = db_query(
        "SELECT content_id, title, addr1, mapx, mapy, content_type_id, "
        "       cpyrht_div_cd, thumbnail, overview "
        "FROM ai_place_v WHERE content_id = ANY(%(ids)s)",
        {"ids": ids},
    )
    return {
        r["content_id"]: {
            "place_name": r["title"],
            "place_address": r["addr1"] or None,
            "place_content_type_id": r["content_type_id"],
            "place_copyright_div_cd": r["cpyrht_div_cd"] or "",
            "place_thumbnail_url": r["thumbnail"] or "",
            "latitude": float(r["mapy"]) if r["mapy"] is not None else None,
            "longitude": float(r["mapx"]) if r["mapx"] is not None else None,
            "overview": (r["overview"] or "").strip() or None,
            "category": _CTYPE_CAT.get(r["content_type_id"]),
        }
        for r in rows
    }


# ── 블록 지칭 해석 (14 §4) ──────────────────────────────────────────────────
def _to_min(hms: str | None) -> int:
    if not hms:
        return 0
    p = hms.split(":")
    return int(p[0]) * 60 + int(p[1])


def _resolve_target(target, blocks: list[PlaceBlock], notes: dict) -> PlaceBlock | None:
    if target.block_id is not None:
        for b in blocks:
            if b.block_id == target.block_id:
                return b
        notes.setdefault("dropped_ops", []).append(f"blockId {target.block_id} 없음")
        return None

    # 자연키 — 새 블록 (14 §4)
    day = [b for b in blocks if b.date == target.date]
    if target.block_category:
        day = [b for b in day if b.block_category == target.block_category]
    if not day:
        notes.setdefault("dropped_ops", []).append(
            f"({target.date}, {target.block_start_time}) 매칭 블록 없음"
        )
        return None
    # 정확 시각
    for b in day:
        if b.block_start_time == target.block_start_time:
            return b
    # fuzzy — 같은 날(+category) 안에서 시각 최근접
    t = _to_min(target.block_start_time)
    return min(day, key=lambda b: abs(_to_min(b.block_start_time) - t))


# ── from 핸들 → 빌더 블록 ────────────────────────────────────────────────────
def _builder_blocks(handle: str, cache: dict[str, dict], notes: dict) -> list[PlaceBlock]:
    entry = cache.get(handle)
    if not entry:
        notes.setdefault("dropped_ops", []).append(f"from 핸들 '{handle}' 없음")
        return []
    for k, v in entry.get("notes", {}).items():   # 빌더 슬롯 notes → 조립 notes 로 승계
        notes.setdefault("itinerary", {})[k] = v
    return [PlaceBlock.model_validate(b) for b in entry.get("blocks", [])]


# ── op 적용 ─────────────────────────────────────────────────────────────────
def _drop(notes: dict, msg: str) -> None:
    notes.setdefault("dropped_ops", []).append(msg)
    log.warning("[assembly] op drop: %s", msg)


def _apply_op(op: Op, blocks: list[PlaceBlock], timetables: list[Timetable],
              notes: dict, gen_cache: dict[str, dict]) -> None:
    kind = op.op

    if kind == "createBlock":
        if not (op.place_id and op.date and op.block_start_time and op.block_end_time and op.block_category):
            return _drop(notes, f"createBlock: 필수 필드 누락 {op.model_dump(exclude_none=True)}")
        h = hydrate_places([op.place_id]).get(op.place_id)
        if not h:
            return _drop(notes, f"createBlock: contentId {op.place_id} 캐시에 없음")
        cat = _CTYPE_BLOCKCAT.get(h["place_content_type_id"], op.block_category)
        blocks.append(PlaceBlock(
            block_id=None, date=op.date,
            block_start_time=op.block_start_time, block_end_time=op.block_end_time,
            block_category=cat, place_id=op.place_id,
            place_name=h["place_name"], place_address=h["place_address"],
            place_content_type_id=h["place_content_type_id"],
            place_copyright_div_cd=h["place_copyright_div_cd"],
            place_thumbnail_url=h["place_thumbnail_url"],
            latitude=h["latitude"], longitude=h["longitude"],
        ))

    elif kind == "updateBlock":
        if op.target is None:
            return _drop(notes, "updateBlock: target 없음")
        b = _resolve_target(op.target, blocks, notes)
        if b is None:
            return
        if op.place_id and op.place_id != b.place_id:
            h = hydrate_places([op.place_id]).get(op.place_id)
            if not h:
                return _drop(notes, f"updateBlock: contentId {op.place_id} 캐시에 없음")
            b.place_id = op.place_id
            b.place_name = h["place_name"]
            b.place_address = h["place_address"]
            b.place_content_type_id = h["place_content_type_id"]
            b.place_copyright_div_cd = h["place_copyright_div_cd"]
            b.place_thumbnail_url = h["place_thumbnail_url"]
            b.latitude = h["latitude"]
            b.longitude = h["longitude"]
            b.block_category = _CTYPE_BLOCKCAT.get(h["place_content_type_id"], b.block_category)
        if op.block_start_time:
            b.block_start_time = op.block_start_time
        if op.block_end_time:
            b.block_end_time = op.block_end_time
        if op.date:
            b.date = op.date
            b.time_table_id = None   # 다른 날 → v2 가 date 로 재연결
        if op.block_category:
            b.block_category = op.block_category

    elif kind == "deleteBlock":
        if op.target is None:
            return _drop(notes, "deleteBlock: target 없음")
        b = _resolve_target(op.target, blocks, notes)
        if b is not None:
            blocks.remove(b)

    elif kind == "createTimeTable":
        if not (op.date and op.time_table_start_time and op.time_table_end_time):
            return _drop(notes, "createTimeTable: 필수 필드 누락")
        timetables.append(Timetable(
            time_table_id=None, date=op.date,
            time_table_start_time=op.time_table_start_time,
            time_table_end_time=op.time_table_end_time,
        ))

    elif kind == "updateTimeTable":
        if op.time_table_id is None:
            return _drop(notes, "updateTimeTable: timeTableId 없음")
        for t in timetables:
            if t.time_table_id == op.time_table_id:
                if op.time_table_start_time:
                    t.time_table_start_time = op.time_table_start_time
                if op.time_table_end_time:
                    t.time_table_end_time = op.time_table_end_time
                return
        _drop(notes, f"updateTimeTable: timeTableId {op.time_table_id} 없음")

    elif kind == "deleteTimeTable":
        if op.time_table_id is None:
            return _drop(notes, "deleteTimeTable: timeTableId 없음")
        tt = next((t for t in timetables if t.time_table_id == op.time_table_id), None)
        if tt is None:
            return _drop(notes, f"deleteTimeTable: timeTableId {op.time_table_id} 없음")
        timetables.remove(tt)
        blocks[:] = [b for b in blocks if b.date != tt.date]   # cascade

    elif kind == "replaceDayBlocks":
        if not (op.date and op.from_):
            return _drop(notes, "replaceDayBlocks: date/from 누락")
        new = _builder_blocks(op.from_, gen_cache, notes)
        blocks[:] = [b for b in blocks if b.date != op.date]
        blocks.extend(b for b in new if b.date == op.date)

    elif kind == "replacePlanBlocks":
        if not op.from_:
            return _drop(notes, "replacePlanBlocks: from 누락")
        new = _builder_blocks(op.from_, gen_cache, notes)
        blocks[:] = list(new)

    elif kind == "resequenceBlocks":
        scope = op.scope or "plan"
        scoped = blocks if scope == "plan" else [b for b in blocks if b.date == scope]
        if len(scoped) < 2:
            return
        reordered = _resequence([b.model_dump(by_alias=True) for b in scoped])
        by_key = {(b["date"], b.get("blockId")): b for b in reordered}
        for b in scoped:
            r = by_key.get((b.date, b.block_id))
            if r:
                b.block_start_time = r["blockStartTime"]
                b.block_end_time = r["blockEndTime"]
                b.place_id = r.get("placeId")
                b.place_name = r.get("placeName")
                b.place_address = r.get("placeAddress")
                b.latitude = r.get("latitude")
                b.longitude = r.get("longitude")


# ── shownPlaces (rich) ──────────────────────────────────────────────────────
def _build_shown_places(ids: list[str], search_cache: dict[str, dict]) -> list[ShownPlace]:
    ids = list(dict.fromkeys(str(i) for i in ids if i))
    if not ids:
        return []
    # 미절삭 overview 재조회 (12 §7-2)
    ov_rows = db_query(
        "SELECT content_id, overview FROM ai_place_v WHERE content_id = ANY(%(ids)s)",
        {"ids": ids},
    )
    full_overview = {r["content_id"]: (r["overview"] or "").strip() or None for r in ov_rows}
    fallback = hydrate_places([i for i in ids if i not in search_cache])

    out: list[ShownPlace] = []
    for cid in ids:
        src = search_cache.get(cid)
        if src:
            sp = ShownPlace(
                content_id=cid, content_type_id=src.get("content_type_id"),
                category=src.get("category"), title=src.get("title", ""),
                addr1=src.get("addr1") or None,
                thumbnail_url=src.get("thumbnail") or None,
                copyright_div_cd=src.get("copyright_div_cd") or None,
                latitude=src.get("mapy"), longitude=src.get("mapx"),
                overview=full_overview.get(cid) or (src.get("overview") or None),
                distance_km=src.get("distance_km"),
            )
            for dkey, fname in _HEADLINE.items():
                if src.get(dkey):
                    setattr(sp, fname, src[dkey])
            out.append(sp)
        elif cid in fallback:
            f = fallback[cid]
            out.append(ShownPlace(
                content_id=cid, content_type_id=f["place_content_type_id"],
                category=f["category"], title=f["place_name"] or "",
                addr1=f["place_address"], thumbnail_url=f["place_thumbnail_url"] or None,
                copyright_div_cd=f["place_copyright_div_cd"] or None,
                latitude=f["latitude"], longitude=f["longitude"],
                overview=full_overview.get(cid) or f["overview"],
            ))
        # 캐시에도 DB 에도 없으면 조용히 스킵
    return out


# ── 진입점 ─────────────────────────────────────────────────────────────────
def assemble(
    llm: LlmOutput,
    req: ChatbotRequest,
    *,
    gen_itin_cache: dict[str, dict] | None = None,
    search_cache: dict[str, dict] | None = None,
) -> ChatbotResponse:
    gen_itin_cache = gen_itin_cache or {}
    search_cache = search_cache or {}
    notes: dict = {}

    # 편집 대상 + 정규화 (12 §8 — 블록에 date 채움)
    target: PlanState = req.pending_context or req.plan_context
    timetables = [t.model_copy(deep=True) for t in target.timetables]
    date_by_ttid = {t.time_table_id: t.date for t in timetables if t.time_table_id is not None}
    blocks: list[PlaceBlock] = []
    for b in target.place_blocks:
        nb = b.model_copy(deep=True)
        if nb.date is None and nb.time_table_id is not None:
            nb.date = date_by_ttid.get(nb.time_table_id)
        blocks.append(nb)

    shown = _build_shown_places(llm.shown_place_ids, search_cache) if llm.shown_place_ids else []

    if not llm.actions:
        _log_notes(notes)
        return ChatbotResponse(user_message=llm.user_message, plan=None, shown_places=shown)

    for op in llm.actions:
        _apply_op(op, blocks, timetables, notes, gen_itin_cache)

    plan = PlanState(
        plan_frame=target.plan_frame.model_copy(deep=True),
        timetables=timetables,
        place_blocks=blocks,
    )
    _log_notes(notes)
    return ChatbotResponse(user_message=llm.user_message, plan=plan, shown_places=shown)


def _log_notes(notes: dict) -> None:
    if notes:
        log.info("[assembly] notes=%s", notes)
