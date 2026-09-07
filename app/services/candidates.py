"""`search_places` — 후보 장소 검색 (그룹 A/B/C/E/F를 한 SQL로).

설계: `project_ai/05 §8`. LLM 은 파라미터만, 여기서 동적 SQL 조립.
- B 코드필터  : category → content_type_id, region → ldong_regn_cd, signgu → ldong_signgu_cd(선택, 단계적 스코프), subtype → lclsSystm
- C 플래그    : amenities → place_intro_* 컬럼
- A 의미검색  : query → gemini.embed_query → ORDER BY embedding <=> qvec
- E 거리      : near/anchor_xy → ORDER BY 좌표거리
- F 숙박요금  : max_budget → place_info_room 최저가 근사 (D식, notes 헤지)
- D 자유텍스트: 필터 안 함, 카테고리별 원문 필드를 반환에 포함 → LLM 이 다음 턴 프루닝
"""

from __future__ import annotations

import math
import re

from app.data.categories import CATEGORY_TO_CTYPES, CTYPE_TO_CATEGORY
from app.data.codes import AMENITY_COLUMN, AMENITY_SOFT, resolve_subtype
from app.data.regions import resolve_region
from app.db import query as db_query
from app.services.gemini import embed_query

_PARK_NEG = ("불가", "없음", "불가능", "불가눙", "불간으", "x", "-")

# 카테고리별 반환 D 필드 (SELECT 표현식, alias)
_D_FIELDS = {
    "RESTAURANT": [
        ("pf.opentimefood", "opentimefood"), ("pf.firstmenu", "firstmenu"),
        ("pf.treatmenu", "treatmenu"), ("pf.restdatefood", "restdate"),
        ("pf.reservationfood", "reservationfood"), ("pf.packing", "packing"),
        ("pf.chkcreditcardfood", "chkcreditcard"),
    ],
    "ACCOMMODATION": [
        ("pl.roomtype", "roomtype"), ("pl.roomcount", "roomcount"),
        ("pl.checkintime", "checkintime"), ("pl.checkouttime", "checkouttime"),
        ("pl.subfacility", "subfacility"), ("pl.foodplace", "foodplace"),
        ("pl.pickup", "pickup"), ("pl.chkcooking", "chkcooking"),
    ],
    "ATTRACTION": [
        ("COALESCE(pa.usetime, pc.usetimeculture)", "usetime"),
        ("COALESCE(pa.restdate, pc.restdateculture)", "restdate"),
        ("pa.expguide", "expguide"),
    ],
}
_D_JOINS = {
    "RESTAURANT": ["LEFT JOIN place_intro_food pf USING (content_id)"],
    "ACCOMMODATION": ["LEFT JOIN place_intro_lodging pl USING (content_id)"],
    "ATTRACTION": [
        "LEFT JOIN place_intro_attraction pa USING (content_id)",
        "LEFT JOIN place_intro_culture pc USING (content_id)",
    ],
}


def _haversine_km(x1, y1, x2, y2):
    r = 6371.0
    p1, p2 = math.radians(y1), math.radians(y2)
    dp = math.radians(y2 - y1)
    dl = math.radians(x2 - x1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 2)


def _resolve_near(near: str, regn_codes: list[str]) -> tuple[float, float] | None:
    near = near.strip()
    if re.fullmatch(r"\d{2,20}", near):  # content_id
        rows = db_query(
            "SELECT mapx, mapy FROM place_list WHERE content_id = %(id)s", {"id": near}
        )
    else:
        sql = ("SELECT mapx, mapy FROM place_list WHERE title ILIKE %(t)s "
               "AND mapx IS NOT NULL")
        params: dict = {"t": f"%{near}%"}
        if regn_codes:
            sql += " AND ldong_regn_cd = ANY(%(r)s)"
            params["r"] = regn_codes
        sql += " ORDER BY length(title) LIMIT 1"
        rows = db_query(sql, params)
    if rows and rows[0]["mapx"] is not None:
        return float(rows[0]["mapx"]), float(rows[0]["mapy"])
    return None


def _vec_literal(vec) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def search_places(
    *,
    name: str | None = None,
    category: str | None = None,
    region: str | list[str] | None = None,
    signgu: list[str] | None = None,   # 시군구 코드 (ai_place_v.ldong_signgu_cd, 3자리). 있으면 그 안으로 좁힘
    subtype: str | None = None,
    amenities: list[str] | None = None,
    query: str | None = None,
    query_vec: list[float] | None = None,
    strict: bool = False,
    sort_by: str = "relevance",
    near: str | None = None,
    anchor_xy: tuple[float, float] | None = None,
    max_budget: int | None = None,
    limit: int = 20,
    exclude_ids: list[str] | None = None,
    seed: str = "",
) -> dict:
    notes: dict = {}
    limit = max(1, min(int(limit or 20), 40))
    p: dict = {"seed": str(seed or "")}
    where: list[str] = ["TRUE"]
    joins: dict[str, str] = {}
    order: list[str] = []          # name 모드 전용 (일치도순)
    ord_subtype = ord_embed = ord_dist = None

    # --- category → content_type ---
    ctypes = CATEGORY_TO_CTYPES.get(category or "")
    if ctypes:
        where.append("l.content_type_id = ANY(%(ctypes)s)")
        p["ctypes"] = list(ctypes)

    # --- region (티어 1: 실패 = 되물음, 전국검색 안 함) ---
    regn_codes, rnote = resolve_region(region)
    if regn_codes:
        where.append("l.ldong_regn_cd = ANY(%(regns)s)")
        p["regns"] = regn_codes
    elif rnote == "unresolved" and region:
        return {"places": [], "total": 0, "notes": {"region": "unresolved"}}

    # --- signgu (선택): 이미 코드라 리졸브 없음. 단계적 스코프 — 있으면 시군구까지 좁힘 ---
    if signgu:
        where.append("l.ldong_signgu_cd = ANY(%(signgu)s)")
        p["signgu"] = [str(s) for s in signgu]

    if exclude_ids:
        where.append("l.content_id <> ALL(%(excl)s)")
        p["excl"] = [str(x) for x in exclude_ids]

    # --- name 모드: discovery 필터 무시 ---
    if name:
        bare = name.strip()
        where.append("l.title ILIKE %(nm)s")
        p["nm"] = f"%{bare}%"
        if not ctypes:  # name 모드도 추천 스코프 4종으로 제한
            where.append("l.content_type_id IN ('12','14','32','39')")
        p["bare"] = bare.lower()
        order.append("position(%(bare)s in lower(l.title))")
        order.append("length(l.title)")
        rows = _run(where, joins, order, p, limit, category)
        if not rows:
            notes["name_not_found"] = bare
        return _pack(rows, notes, category, anchor_xy=None)

    # --- subtype (티어 1) ---
    st = resolve_subtype(category, subtype)
    if subtype and not st:
        notes["subtype_unmatched"] = subtype
    if st:
        col, code = st
        p["stcode"] = code
        if strict:
            where.append(f"l.{col} = %(stcode)s")
        else:
            ord_subtype = f"CASE WHEN l.{col} = %(stcode)s THEN 0 ELSE 1 END"

    # --- amenities ---
    if amenities:
        soft: list[str] = []
        for i, a in enumerate(amenities):
            a = a.strip()
            hit = AMENITY_COLUMN.get(a) or AMENITY_COLUMN.get(a.lower())
            if hit:
                colname, mode = hit
                if mode == "1":  # subfacility 불린
                    joins["pl"] = "LEFT JOIN place_intro_lodging pl USING (content_id)"
                    where.append(f"pl.{colname} = '1'")
                elif mode == "kids":
                    joins["pf"] = "LEFT JOIN place_intro_food pf USING (content_id)"
                    where.append("pf.kidsfacility = '1'")
                elif mode == "parking":  # 근사 (티어 2), 카테고리별 컬럼
                    _pk = {"RESTAURANT": ("pf", "place_intro_food", "parkingfood"),
                           "ACCOMMODATION": ("pl", "place_intro_lodging", "parkinglodging"),
                           "ATTRACTION": ("pa", "place_intro_attraction", "parking")}.get(category)
                    if _pk:
                        al, t, cn = _pk
                        joins[al] = f"LEFT JOIN {t} {al} USING (content_id)"
                        p[f"pkneg{i}"] = list(_PARK_NEG)
                        where.append(
                            f"({al}.{cn} <> '' AND lower(trim({al}.{cn})) <> ALL(%(pkneg{i})s))"
                        )
            elif a in AMENITY_SOFT or a.lower() in AMENITY_SOFT:
                soft.append(a)
            else:
                notes.setdefault("unmatched_amenities", []).append(a)
        if soft:  # 티어 3: subfacility 자유텍스트 ILIKE
            joins["pl"] = "LEFT JOIN place_intro_lodging pl USING (content_id)"
            for j, s in enumerate(soft):
                p[f"soft{j}"] = f"%{s}%"
                where.append(f"pl.subfacility ILIKE %(soft{j})s")
            notes["soft_matched"] = soft
            notes["confidence"] = "low"

    # --- query → 임베딩 (그룹 A) ---
    if query:
        if query_vec is not None:
            # 호출측(generate_itinerary)이 슬롯 전체를 배치로 미리 임베딩해 넘긴 경우 —
            # 여기서 또 embed_query 를 부르면 배치로 줄인 의미가 없다.
            p["qvec"] = _vec_literal(query_vec)
            ord_embed = "l.embedding <=> %(qvec)s::vector"
        else:
            try:
                qvec = embed_query(query)
                p["qvec"] = _vec_literal(qvec)
                ord_embed = "l.embedding <=> %(qvec)s::vector"
            except Exception:  # noqa: BLE001
                notes["query_embed"] = "failed"

    # --- 거리 (그룹 E) ---
    axy = None
    if near:
        axy = _resolve_near(near, regn_codes)
        if axy is None:
            notes["anchor"] = "unresolved"
    elif sort_by == "distance" and anchor_xy:
        axy = anchor_xy
    if sort_by == "distance" and axy is None and "anchor" not in notes:
        notes["distance_sort"] = "skipped"
    if axy:
        p["ax"], p["ay"] = axy
        # 경도는 위도에 따라 짧아짐 → cos(lat) 가중해야 haversine 순서와 일치
        ord_dist = ("(((l.mapx - %(ax)s) * cos(radians(%(ay)s)))^2 "
                    "+ (l.mapy - %(ay)s)^2)")

    # --- max_budget (그룹 F, 티어 3 근사) ---
    if max_budget and category == "ACCOMMODATION":
        joins["fee"] = (
            "LEFT JOIN (SELECT content_id, "
            "MIN(NULLIF(regexp_replace(roomoffseasonminfee1, '\\D', '', 'g'), '')::bigint) AS min_fee "
            "FROM place_info_room WHERE roomoffseasonminfee1 ~ '^[1-9]' GROUP BY content_id) fee "
            "USING (content_id)"
        )
        where.append("fee.min_fee <= %(budget)s")
        p["budget"] = int(max_budget)
        notes["price_filter"] = {"approx": True, "basis": "비수기 최저 객실료"}

    # --- ORDER BY 조립 --- (name 모드는 위에서 return 하므로 여기 안 옴)
    # subtype 랭크 항상 최우선. 그다음 sort_by:
    #   distance  : subtype → 거리 → 의미
    #   relevance : subtype → 의미 → 거리(tiebreak)
    if sort_by == "distance":
        order = [x for x in (ord_subtype, ord_dist, ord_embed) if x]
    else:
        order = [x for x in (ord_subtype, ord_embed, ord_dist) if x]

    rows = _run(where, joins, order, p, limit, category)
    return _pack(rows, notes, category, anchor_xy=axy)


def _run(where, joins, order, p, limit, category):
    for j in _D_JOINS.get(category or "", []):
        al = j.split()[3]  # "LEFT JOIN <table> <alias> ..."
        joins.setdefault(al, j)

    dsel = ", ".join(f"{expr} AS {al}" for expr, al in _D_FIELDS.get(category or "", []))
    dsel = (", " + dsel) if dsel else ""

    order_sql = ", ".join(order + ["md5(l.content_id || %(seed)s)"])
    sql = f"""
        SELECT l.content_id, l.title, l.content_type_id, l.cpyrht_div_cd,
               l.addr1, l.mapx, l.mapy,
               left(l.overview, 300) AS overview, l.thumbnail,
               count(*) OVER() AS _total{dsel}
        FROM ai_place_v l
        {" ".join(joins.values())}
        WHERE {" AND ".join(where)}
        ORDER BY {order_sql}
        LIMIT {limit}
    """
    return db_query(sql, p)


def _pack(rows, notes, category, anchor_xy):
    d_aliases = [al for _, al in _D_FIELDS.get(category or "", [])]
    places = []
    for r in rows:
        item = {
            "content_id": r["content_id"],
            "title": r["title"],
            "category": CTYPE_TO_CATEGORY.get(r["content_type_id"]),
            "content_type_id": r["content_type_id"],          # raw — v2 placeContentTypeId (attraction 12/14 구분)
            "copyright_div_cd": r["cpyrht_div_cd"] or "",      # v2 placeCopyrightDivCd
            "addr1": r["addr1"] or "",
            "mapx": float(r["mapx"]) if r["mapx"] is not None else None,
            "mapy": float(r["mapy"]) if r["mapy"] is not None else None,
            "overview": (r["overview"] or "").strip(),
            "thumbnail": r["thumbnail"] or "",
        }
        if anchor_xy and r["mapx"] is not None:
            item["distance_km"] = _haversine_km(
                anchor_xy[0], anchor_xy[1], float(r["mapx"]), float(r["mapy"])
            )
        for al in d_aliases:
            v = r.get(al)
            if v:
                item[al] = v
        places.append(item)
    total = rows[0]["_total"] if rows else 0
    return {"places": places, "total": total, "notes": notes}
