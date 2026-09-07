"""`generate_itinerary` 빌더 + `resequence` 동선 재정렬 헬퍼.

설계: `project_ai/08` (D1~D10). LLM 없음 — 순수 코드.

generate_itinerary:
- 입력: day_plans 스켈레톤(§3-2) + region + signgu + exclude_ids + reuse_accommodation + seed
- 슬롯마다 search_places(D9 파라미터) → 1위 픽(2-A) → v2 블록 형태로 hydrate
- 스코프: signgu 있으면 시군구까지 좁힘. 슬롯 0건이면 완화 사다리 마지막에 시도로 넓힘("area")
- near 체이닝: 하루 안 = 직전 슬롯 pick content_id / 하루 첫 슬롯 = 숙소 content_id / 슬롯 near? override
- exclude_ids: 에이전트가 채운 것(planContext + 직전 제안) + 빌더 슬롯 픽 누적
- 슬롯 0건 → 점진 완화(query 뺌 → 전부 뺌 → 지리 넓힘) → 채우면 notes[ref].relaxed=[떨군 키], 그래도 0건이면 {empty:true} (D8-B)
- 반환: { "blocks": [...hydrate 완료...], "notes": { "<date>.slot<i>": {relaxed?, empty?, + search_places 신호} } }
  op 래핑(replacePlanBlocks 등)은 조립 레이어 — LLM 이 지목 (`10` §6)

resequence (D10):
- 기존 블록(planContext, v2 형)을 받아 **슬롯 시각 고정 + 같은 blockCategory 안에서만** 장소 재배치해 하루 동선 최소화
- 저장 블록엔 스켈레톤 태그(theme/cuisine/query)가 없음 → category 가 유일한 보존 정보
- 시각 바뀐 블록만 caller 가 updateBlock op 으로
"""

from __future__ import annotations

import itertools
import math

from app.data.regions import resolve_region
from app.services import gemini
from app.services.candidates import search_places

# slot category = search_places 반환 category = v2 BlockCategory (ATTRACTION/ACCOMMODATION/RESTAURANT).
# 어휘가 하나라 매핑 테이블 없음 (app/data/categories.py).

_SEARCH_LIMIT = 5   # 2-A: 1위 픽. 좌표 null·필드 결손 스킵 여유분 (D9)


def _km(a: dict, b: dict) -> float:
    """두 블록 간 haversine km. 한쪽이라도 좌표 없으면 0 (측정 불가 → 최적화 대상 아님)."""
    lat1, lon1, lat2, lon2 = a.get("latitude"), a.get("longitude"), b.get("latitude"), b.get("longitude")
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _block(pick: dict, slot: dict, date: str) -> dict:
    """search_places 픽 → v2 TimetablePlaceBlockDto 형 (blockId·memo 없음, place 필드 hydrate 완료)."""
    return {
        "date": date,
        "blockStartTime": slot["start"],
        "blockEndTime": slot["end"],
        "blockCategory": pick["category"],
        "placeId": pick["content_id"],
        "placeName": pick["title"],
        "placeContentTypeId": pick["content_type_id"],
        "placeAddress": pick["addr1"],
        "placeThumbnailUrl": pick["thumbnail"],
        "placeCopyrightDivCd": pick["copyright_div_cd"],
        "latitude": pick["mapy"],
        "longitude": pick["mapx"],
    }


def _search_slot(
    slot: dict, region, signgu: list[str] | None, near: str | None, exclude_ids: list[str], seed: str,
    query_vec: list[float] | None = None,
) -> tuple[dict, list[str]]:
    """슬롯 1개 검색 + 0건이면 점진 완화 (D8-B).

    완화 사다리: 전체 → query 뺌 → category+scope 만 → (signgu 있으면) 지리 넓히기(signgu 뺌).
    지리를 맨 아래 두는 이유: "다른 도시" 가 "원한 분위기 아님" 보다 큰 불만.
    (`near` 는 WHERE 아닌 ORDER BY 라 0건 여부에 영향 없음 → 끝까지 유지.)
    query_vec: 호출측이 미리 배치로 임베딩해 둔 이 슬롯 query 의 벡터(D12) — 있으면 search_places
    가 또 embed_query 를 부르지 않는다. query 를 쓰는 시도(첫 번째)에만 의미가 있다.
    반환: (search_places 결과, 성공한 시도에서 떨군 슬롯 키 리스트. 지리 넓혔으면 "area" 포함).
    """
    subtype = slot.get("theme") or slot.get("cuisine")
    budget = slot.get("max_budget")
    constrained = [k for k in ("theme", "cuisine", "query", "amenities", "max_budget") if slot.get(k)]
    # (search_places 파라미터, 이 시도에서 떨군 슬롯 키)
    attempts: list[tuple[dict, list[str]]] = [
        (dict(subtype=subtype, query=slot.get("query"), query_vec=query_vec, amenities=slot.get("amenities"),
              max_budget=budget, signgu=signgu), []),
        (dict(subtype=subtype, query=None, amenities=slot.get("amenities"),
              max_budget=budget, signgu=signgu), ["query"] if slot.get("query") else []),
        (dict(subtype=None, query=None, amenities=None, max_budget=None, signgu=signgu), constrained),
    ]
    if signgu:
        attempts.append(
            (dict(subtype=None, query=None, amenities=None, max_budget=None, signgu=None), constrained + ["area"])
        )

    result: dict = {"places": [], "total": 0, "notes": {}}
    dropped: list[str] = []
    for params, drop in attempts:
        dropped = drop
        result = search_places(
            category=slot["category"],
            region=region,
            near=near,
            exclude_ids=exclude_ids,
            strict=False,
            sort_by="relevance",
            limit=_SEARCH_LIMIT,
            seed=seed,
            **params,
        )
        if result["places"]:
            break
    return result, dropped


def _embed_slot_queries(day_plans: list[dict]) -> dict[str, list[float]]:
    """day_plans 전체 슬롯의 query 텍스트를 한 번의 배치 호출로 미리 임베딩(D12).

    슬롯마다(최대 십수 개) embed_query 를 따로 부르면 분당 요청수 쿼터에 몰려 429
    RESOURCE_EXHAUSTED 가 나고, 재시도가 반복 실패하면 그 슬롯은 semantic 랭킹 없이
    조용히 진행된다(08_통합_E2E_테스트_기록.md 발견#4 실측). 호출 수를 슬롯 수 → 1회로
    줄여 쿼터 자체를 피한다. 실패하면 빈 dict — 슬롯별 search_places 의 기존 폴백
    (embed 없이 진행 + notes 헤지)이 그대로 처리한다.
    """
    texts = list(dict.fromkeys(
        slot["query"] for day in day_plans for slot in day.get("slots", []) if slot.get("query")
    ))
    if not texts:
        return {}
    try:
        vecs = gemini.embed_queries(texts)
    except Exception:  # noqa: BLE001
        return {}
    return dict(zip(texts, vecs))


def generate_itinerary(
    *,
    day_plans: list[dict],
    region: str | list[str],
    signgu: list[str] | None = None,   # userContext 목적지 시군구 코드 (있으면 그 안으로 스코프, 0건이면 시도로 완화)
    exclude_ids: list[str] | None = None,
    reuse_accommodation: bool = True,
    seed: str = "",
) -> dict:
    """LLM 스켈레톤 → 슬롯 채운 블록 리스트 + 슬롯별 notes."""
    # region 리졸브 실패 → 전 슬롯 돌리지 말고 early-return (에이전트 되물음이 정상 경로, 방어)
    _, rnote = resolve_region(region)
    if rnote == "unresolved":
        return {"blocks": [], "notes": {"region": "unresolved"}}

    query_vecs = _embed_slot_queries(day_plans)
    used: list[str] = list(exclude_ids or [])
    blocks: list[dict] = []
    notes: dict = {}
    accommodation: dict | None = None   # 첫 lodging 픽 캐시 (content_id 로 near 앵커 + 재사용)

    for day in day_plans:
        date = day["date"]
        prev_id: str | None = None                      # 하루 안 near 체이닝
        day_anchor = accommodation["content_id"] if accommodation else None

        for i, slot in enumerate(day.get("slots", [])):
            ref = f"{date}.slot{i}"
            is_lodging = slot["category"] == "ACCOMMODATION"

            # 숙소 재사용 (검색 안 함)
            if is_lodging and reuse_accommodation and accommodation is not None:
                blocks.append(_block(accommodation, slot, date))
                continue

            near = slot.get("near") or prev_id or day_anchor
            result, dropped = _search_slot(slot, region, signgu, near, used, seed,
                                            query_vec=query_vecs.get(slot.get("query")))

            if not result["places"]:
                notes[ref] = {"empty": True}
                continue

            pick = result["places"][0]
            slot_notes = dict(result["notes"])              # search_places 자체 신뢰도 신호
            if dropped:
                slot_notes["relaxed"] = dropped             # 완화로 떨군 조건 (§8-7)
            if slot_notes:
                notes[ref] = slot_notes

            used.append(pick["content_id"])
            prev_id = pick["content_id"]
            blocks.append(_block(pick, slot, date))

            if is_lodging and reuse_accommodation and accommodation is None:
                accommodation = pick

    return {"blocks": blocks, "notes": notes}


def _resequence_day(day_blocks: list[dict], anchor: dict | None) -> list[dict]:
    """하루치 블록 — 시각 위치 고정, 같은 category 안에서 장소만 재배치해 이동거리 최소.

    day_blocks: 이미 blockStartTime 순 정렬. anchor: 하루 시작 기준점(전날 마지막 블록) or None.
    반환: 같은 길이, 각 위치에 최적 장소가 온 리스트 (해당 위치의 시각을 그 장소가 가짐).
    """
    n = len(day_blocks)
    if n < 2:
        return day_blocks

    # category 별로 "이 category 가 앉는 위치 인덱스들"
    cat_positions: dict[str, list[int]] = {}
    for i, b in enumerate(day_blocks):
        cat_positions.setdefault(b["blockCategory"], []).append(i)

    movable = {c: pos for c, pos in cat_positions.items() if len(pos) >= 2}
    if not movable:
        return day_blocks

    # 각 category 의 장소 순열들 → 전체 조합
    per_cat_perms = {c: list(itertools.permutations(pos)) for c, pos in movable.items()}
    cats = list(per_cat_perms)

    best_order = list(range(n))
    best_cost = math.inf
    for combo in itertools.product(*(per_cat_perms[c] for c in cats)):
        # 위치 i 에 올 원본 블록 인덱스
        order = list(range(n))
        for c, perm in zip(cats, combo):
            src = movable[c]                       # 원본 위치들 (정렬됨)
            for dst_pos, src_pos in zip(src, perm):
                order[dst_pos] = src_pos
        seq = [day_blocks[j] for j in order]
        cost = sum(_km(seq[k], seq[k + 1]) for k in range(n - 1))
        if anchor is not None:
            cost += _km(anchor, seq[0])
        if cost < best_cost:
            best_cost, best_order = cost, order

    out = []
    for pos, src_idx in enumerate(best_order):
        b = dict(day_blocks[src_idx])             # 장소는 src, 시각은 pos
        b["blockStartTime"] = day_blocks[pos]["blockStartTime"]
        b["blockEndTime"] = day_blocks[pos]["blockEndTime"]
        out.append(b)
    return out


def resequence(blocks: list[dict]) -> list[dict]:
    """기존 블록(v2 형) → 슬롯 시각 고정 + category 내 장소 재배치로 동선 최소화 (D10).

    시각이 바뀐 블록만 caller 가 `updateBlock{blockId, blockStartTime, blockEndTime}` op 으로.
    """
    by_date: dict[str, list[dict]] = {}
    for b in blocks:
        by_date.setdefault(b["date"], []).append(b)

    out: list[dict] = []
    prev_last: dict | None = None
    for date in sorted(by_date):
        day = sorted(by_date[date], key=lambda b: b["blockStartTime"])
        reordered = _resequence_day(day, prev_last)
        out.extend(reordered)
        prev_last = reordered[-1] if reordered else prev_last
    return out
