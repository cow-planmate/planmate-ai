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
from datetime import datetime

from app.data.categories import CTYPE_TO_CATEGORY
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

# content_type_id → 카테고리(= v2 BlockCategory). 카테고리 어휘 단일 (app/data/categories.py).
# 블록 category 는 LLM 값 대신 이걸로 확정 (v2 PlaceSummaryDto "역유도" 방식, 17 C-6).


# ── 포맷 정규화 (v2 wire 형태로) ────────────────────────────────────────────
def _hm(t: str | None) -> str | None:
    """시각 → canonical HH:MM (v2 LocalTime ISO 직렬화 형태). 초 잘라내고 시·분 zero-pad."""
    if not t:
        return t
    p = t.split(":")
    if len(p) >= 2 and p[0].strip().isdigit() and p[1].strip().isdigit():
        return f"{int(p[0]):02d}:{int(p[1]):02d}"
    return t


def _norm_date(d: str | None) -> str | None:
    """YYYY-MM-DD 로 정규화·검증 (zero-pad 포함). 파싱 실패면 None."""
    if not d:
        return d
    try:
        return datetime.strptime(d.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


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
            "category": CTYPE_TO_CATEGORY.get(r["content_type_id"]),
        }
        for r in rows
    }


# ── 블록 지칭 해석 (14 §4) ──────────────────────────────────────────────────
def _to_min(hms: str | None) -> int:
    if not hms:
        return 0
    p = hms.split(":")
    return int(p[0]) * 60 + int(p[1])


def _from_min(m: int) -> str:
    m = max(0, min(m, 24 * 60 - 1))
    return f"{m // 60:02d}:{m % 60:02d}"


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _gaps(day_start: int, ranges: list[tuple[int, int]]) -> list[tuple[int, int | None]]:
    """day_start 이후, 정렬된 ranges(자기 자신 제외) 사이의 빈 구간들. 마지막 원소는
    (x, None) = 그날 마지막 블록 뒤 무제한 구간 — 창 확장으로 항상 자리를 만들 수 있다."""
    gaps: list[tuple[int, int | None]] = []
    cursor = day_start
    for bs, be in ranges:
        if bs > cursor:
            gaps.append((cursor, bs))
        cursor = max(cursor, be)
    gaps.append((cursor, None))
    return gaps


def _nearest_fit(desired_start: int, duration: int, gaps: list[tuple[int, int | None]]) -> int:
    """desired_start 에 가장 가까우면서 duration 이 들어갈 수 있는 지점. 동률이면 더 늦은 쪽(§5-8-1)."""
    best_start: int | None = None
    best_dist: int | None = None
    for gap_start, gap_end in gaps:
        if gap_end is not None and gap_end - gap_start < duration:
            continue
        upper = (gap_end - duration) if gap_end is not None else max(desired_start, gap_start)
        candidate = min(max(desired_start, gap_start), upper)
        dist = abs(candidate - desired_start)
        if best_start is None or dist < best_dist or (dist == best_dist and candidate > best_start):
            best_start, best_dist = candidate, dist
    return best_start if best_start is not None else desired_start


def _resolve_overlap(desired_start: str, desired_end: str, day_start: str,
                      siblings: list[PlaceBlock]) -> tuple[str, str] | None:
    """[desired_start,desired_end) 가 같은 날 siblings 와 겹치면, 겹치지 않는 가장 가까운
    자리로 옮긴 (start, end) 를 반환. 안 겹치면 None(조정 불필요).

    임계값 없음 — 하루가 꽉 차 있으면 마지막 블록 뒤(창 확장 대상)까지 밀려도 그대로 배치한다.
    호출자가 이 결과로 timetable 끝을 늘려야 할 수도 있다(§5-8-3). 08_generate_itinerary_설계.md
    §5 "겹침 회피 버림"은 generate_itinerary 빌더(배치 생성) 얘기 — 이건 그것과 별개인 단일
    블록 편집(createBlock/updateBlock) 경로의 겹침 회피다. 일괄 이동(T5류)은 스코프 밖.
    """
    s, e = _to_min(desired_start), _to_min(desired_end)
    duration = e - s
    ranges = sorted((_to_min(b.block_start_time), _to_min(b.block_end_time)) for b in siblings)
    if not any(_overlaps(s, e, bs, be) for bs, be in ranges):
        return None
    new_start = _nearest_fit(s, duration, _gaps(_to_min(day_start), ranges))
    if new_start == s:
        # 방어적 가드 — 겹침은 감지됐는데 재배치 결과가 원래 자리와 같으면(관측된 엣지 케이스,
        # 08_통합_E2E_테스트_기록.md 발견#7 인접 증상: "20:30에서 20:30로 조정했어요"처럼
        # 의미 없는 헤지가 나가는 걸 막는다) 조정 안 함으로 취급.
        return None
    return _from_min(new_start), _from_min(new_start + duration)


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
    out: list[PlaceBlock] = []
    for b in entry.get("blocks", []):
        pb = PlaceBlock.model_validate(b)
        pb.block_start_time = _hm(pb.block_start_time)
        pb.block_end_time = _hm(pb.block_end_time)
        pb.date = _norm_date(pb.date) or pb.date   # 빌더 date(LLM day_plans) 도 정규화 → replaceDayBlocks 필터 정합
        out.append(pb)
    return out


# ── op 적용 ─────────────────────────────────────────────────────────────────
def _drop(notes: dict, msg: str) -> None:
    notes.setdefault("dropped_ops", []).append(msg)
    log.warning("[assembly] op drop: %s", msg)


_OP_TIME_ATTRS = ("block_start_time", "block_end_time",
                  "time_table_start_time", "time_table_end_time")


def _normalize_ops(ops: list[Op], notes: dict) -> list[Op]:
    """LLM op 의 시각 → HH:MM, 날짜 → YYYY-MM-DD 검증 (프롬프트만으로는 안 새는 것 방어).
    날짜 형식 오류 op 은 drop. Op/BlockTarget 는 validate_assignment 아님 → setattr 직접."""
    out: list[Op] = []
    for op in ops:
        for a in _OP_TIME_ATTRS:
            v = getattr(op, a, None)
            if v is not None:
                setattr(op, a, _hm(v))
        if op.target is not None and op.target.block_start_time is not None:
            op.target.block_start_time = _hm(op.target.block_start_time)
        bad_date = False
        for holder in (op, op.target):
            if holder is None:
                continue
            d = getattr(holder, "date", None)
            if not d:
                continue
            nd = _norm_date(d)
            if nd is None:
                _drop(notes, f"{op.op}: 날짜 형식 오류 '{d}'")
                bad_date = True
            else:
                holder.date = nd
        if not bad_date:
            out.append(op)
    return out


def _batch_dates(ops: list[Op]) -> set[str]:
    """이 턴에 같은 날짜를 2번 이상 건드리는(=배치) 날짜들. 겹침 자동회피(D11)는 스코프가
    단일 블록 편집(createBlock/updateBlock 각 1건)이지 배치가 아니다(설계 §5-8-1) — 배치인
    날짜는 여기서 걸러 회피 로직을 끈다. 안 그러면 순차 처리 중 겹침→재배치→다음 op 와
    또 겹침이 도미노로 이어져 여러 블록이 자정 근처로 쓸려가는 사고가 난다(실측: T1
    "1일차랑 2일차 순서 바꿔줘", 08_통합_E2E_테스트_기록.md 발견#8).
    """
    counts: dict[str, int] = {}
    for op in ops:
        d = None
        if op.op == "createBlock" and op.date:
            d = op.date
        elif op.op == "updateBlock" and (op.block_start_time or op.block_end_time or op.date):
            d = op.date or (op.target.date if op.target else None)
        if d:
            counts[d] = counts.get(d, 0) + 1
    return {d for d, c in counts.items() if c > 1}


def _apply_op(op: Op, blocks: list[PlaceBlock], timetables: list[Timetable],
              notes: dict, gen_cache: dict[str, dict], batch_dates: set[str]) -> None:
    kind = op.op

    if kind == "createBlock":
        if not (op.place_id and op.date and op.block_start_time and op.block_end_time and op.block_category):
            return _drop(notes, f"createBlock: 필수 필드 누락 {op.model_dump(exclude_none=True)}")
        h = hydrate_places([op.place_id]).get(op.place_id)
        if not h:
            return _drop(notes, f"createBlock: contentId {op.place_id} 캐시에 없음")
        cat = CTYPE_TO_CATEGORY.get(h["place_content_type_id"], op.block_category)
        start, end = op.block_start_time, op.block_end_time
        tt = next((t for t in timetables if t.date == op.date), None)
        siblings = [b for b in blocks if b.date == op.date]
        resolved = (None if op.date in batch_dates else
                    _resolve_overlap(start, end, tt.time_table_start_time if tt else "00:00", siblings))
        if resolved:
            start, end = resolved
            if tt is not None and _to_min(end) > _to_min(tt.time_table_end_time):
                tt.time_table_end_time = end
            notes.setdefault("overlap_adjusted", []).append(
                f"'{h['place_name']}' 일정이 기존 일정과 겹쳐 {op.block_start_time}에서 {start}로 조정했어요.")
        blocks.append(PlaceBlock(
            block_id=None, date=op.date,
            block_start_time=start, block_end_time=end,
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
        if not (op.place_id or op.block_start_time or op.block_end_time or op.date or op.block_category):
            # target 만 있고 바꿀 필드 0 → no-op. LLM 이 검색만 하고 placeId 안 실은 경우 (E5-1).
            # 조용히 통과시키면 userMessage 가 "바꿨다"고 거짓말 → drop + note 로 드러냄.
            return _drop(notes, "updateBlock: 바꿀 필드 없음 (target 만) — 무시")
        b = _resolve_target(op.target, blocks, notes)
        if b is None:
            return
        cat_from_place = False
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
            b.block_category = CTYPE_TO_CATEGORY.get(h["place_content_type_id"], b.block_category)
            cat_from_place = True   # content_type 역유도(C-6) 가 우선 — 아래 op.block_category 로 안 덮음
        time_or_date_changed = bool(op.block_start_time or op.block_end_time or op.date)
        original_start = b.block_start_time
        if op.block_start_time:
            b.block_start_time = op.block_start_time
        if op.block_end_time:
            b.block_end_time = op.block_end_time
        if op.date:
            b.date = op.date
            b.time_table_id = None   # 다른 날 → v2 가 date 로 재연결
        if time_or_date_changed and b.date not in batch_dates:
            tt = next((t for t in timetables if t.date == b.date), None)
            siblings = [x for x in blocks if x.date == b.date and x is not b]
            resolved = _resolve_overlap(b.block_start_time, b.block_end_time,
                                         tt.time_table_start_time if tt else "00:00", siblings)
            if resolved:
                b.block_start_time, b.block_end_time = resolved
                if tt is not None and _to_min(b.block_end_time) > _to_min(tt.time_table_end_time):
                    tt.time_table_end_time = b.block_end_time
                notes.setdefault("overlap_adjusted", []).append(
                    f"'{b.place_name}' 일정이 기존 일정과 겹쳐 {original_start}에서 {b.block_start_time}로 조정했어요.")
        if op.block_category and not cat_from_place:
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
        if any(t.date == op.date for t in timetables):
            # 그 날 타임테이블 이미 존재 — v2 TimeTable.date 유니크. 창 조정은 updateTimeTable.
            return _drop(notes, f"createTimeTable: {op.date} 이미 있음 — 무시 (창 조정은 updateTimeTable)")
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
        scoped = [b for b in blocks if b.date and (scope == "plan" or b.date == scope)]
        if len(scoped) < 2:   # date 없는 블록은 재배치서 제외 (resequence 가 date 로 그룹핑)
            return
        reordered = _resequence([b.model_dump(by_alias=True) for b in scoped])
        # 매칭 키 = (date, blockStartTime). blockId 로 하면 프리뷰 새 블록(전부 None)이
        # 한 키로 뭉개짐. 하루 안 시각은 유니크(v2 제약)라 안전. resequence 는 시각 자리
        # 고정 + 장소만 재배치 → 각 자리(시각)에 온 장소 필드를 원본 블록에 복사.
        by_key = {(b["date"], b["blockStartTime"]): b for b in reordered}
        for b in scoped:
            r = by_key.get((b.date, b.block_start_time))
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
            cat = src.get("category")
            if cat is None:   # 추천스코프(12/14/32/39) 밖 → 스킵 (category = v2 enum 강제)
                continue
            sp = ShownPlace(
                content_id=cid, content_type_id=src.get("content_type_id"),
                category=cat, title=src.get("title", ""),
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
            if f["category"] is None:
                continue
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

    # 편집 대상 + 정규화 (12 §8 — 블록에 date 채움 / 시각 → HH:MM)
    target: PlanState = req.pending_context or req.plan_context
    timetables: list[Timetable] = []
    for t in target.timetables:
        nt = t.model_copy(deep=True)
        nt.time_table_start_time = _hm(nt.time_table_start_time)
        nt.time_table_end_time = _hm(nt.time_table_end_time)
        timetables.append(nt)
    date_by_ttid = {t.time_table_id: t.date for t in timetables if t.time_table_id is not None}
    blocks: list[PlaceBlock] = []
    for b in target.place_blocks:
        nb = b.model_copy(deep=True)
        nb.block_start_time = _hm(nb.block_start_time)
        nb.block_end_time = _hm(nb.block_end_time)
        if nb.date is None and nb.time_table_id is not None:
            nb.date = date_by_ttid.get(nb.time_table_id)
        blocks.append(nb)

    shown = _build_shown_places(llm.shown_place_ids, search_cache) if llm.shown_place_ids else []

    if not llm.actions:
        _log_notes(notes)
        return ChatbotResponse(user_message=llm.user_message, plan=None, shown_places=shown)

    normalized_ops = _normalize_ops(llm.actions, notes)
    batch_dates = _batch_dates(normalized_ops)
    for op in normalized_ops:
        _apply_op(op, blocks, timetables, notes, gen_itin_cache, batch_dates)

    # 겹침 자동 회피(§5-8-1) 안내 — LLM 최종 출력 이후에 조립 레이어가 결정하는 일이라
    # LLM 은 이걸 모른다. userMessage 에 직접 덧붙인다(새 wire 필드 없음).
    message = llm.user_message
    if notes.get("overlap_adjusted"):
        message = (message.rstrip() + " " + " ".join(notes["overlap_adjusted"])).strip()

    plan = PlanState(
        plan_frame=target.plan_frame.model_copy(deep=True),
        timetables=timetables,
        place_blocks=blocks,
    )
    _log_notes(notes)
    return ChatbotResponse(user_message=message, plan=plan, shown_places=shown)


def _log_notes(notes: dict) -> None:
    if notes:
        log.info("[assembly] notes=%s", notes)
