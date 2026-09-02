"""search_places 통합 테스트 — 로컬 DB + 부산(regn 26) subset.

임베딩 947건이 부산에 있고 전량 아님 → 단언은 "범위/불변식"만 (정확 건수 X).
`query` 경로는 embed_query 를 목킹 (API 콜 없음).
"""

import pytest

from app.services import candidates
from app.services.candidates import search_places

pytestmark = pytest.mark.usefixtures("db")


def test_structural_restaurant_region():
    r = search_places(category="restaurant", region="부산", limit=10)
    assert r["notes"] == {}
    assert 1 <= len(r["places"]) <= 10
    assert r["total"] > 100  # 부산 음식점 519
    for p in r["places"]:
        assert p["category"] == "restaurant"
        assert "부산" in p["addr1"]


def test_subtype_rank_puts_cafes_first():
    r = search_places(category="restaurant", region="부산", subtype="카페", limit=8)
    # 비-strict → 필터 아님, total 은 부산 음식점 전체
    assert r["total"] > 100
    # 상위권은 카페 성격 (firstmenu 에 디저트/음료 키워드가 자주)
    assert len(r["places"]) == 8


def test_subtype_strict_filters():
    loose = search_places(category="restaurant", region="부산", subtype="중식", limit=50)
    strict = search_places(category="restaurant", region="부산", subtype="중식",
                           strict=True, limit=50)
    assert strict["total"] < loose["total"]
    assert strict["total"] >= 1


def test_subtype_synonym_and_unmatched():
    a = search_places(category="restaurant", region="부산", subtype="중국집",
                      strict=True, limit=5)
    b = search_places(category="restaurant", region="부산", subtype="중식",
                      strict=True, limit=5)
    assert a["total"] == b["total"]
    u = search_places(category="restaurant", region="부산", subtype="마라탕", limit=5)
    assert u["notes"].get("subtype_unmatched") == "마라탕"


def test_amenities_hard_filter_and():
    one = search_places(category="lodging", region="부산", amenities=["바베큐"], limit=50)
    two = search_places(category="lodging", region="부산",
                        amenities=["바베큐", "사우나"], limit=50)
    assert one["total"] >= two["total"] >= 1


def test_amenities_soft_note():
    r = search_places(category="lodging", region="부산", amenities=["수영장"], limit=5)
    assert r["notes"].get("soft_matched") == ["수영장"]
    assert r["notes"].get("confidence") == "low"


def test_amenities_unmatched_note():
    r = search_places(category="lodging", region="부산", amenities=["헬리패드"], limit=5)
    assert "헬리패드" in r["notes"].get("unmatched_amenities", [])


def test_region_unresolved_returns_empty_with_note():
    r = search_places(category="restaurant", region="우리 동네")
    assert r["places"] == []
    assert r["notes"] == {"region": "unresolved"}


def test_name_mode():
    r = search_places(name="광안리해수욕장", limit=3)
    assert r["total"] >= 1
    assert any("광안리" in p["title"] for p in r["places"])


def test_name_not_found_note():
    r = search_places(name="존재하지않는가게이름xyz", limit=3)
    assert r["places"] == []
    assert r["notes"].get("name_not_found")


def test_near_distance_sort():
    r = search_places(category="restaurant", region="부산", near="광안리해수욕장",
                      sort_by="distance", limit=8)
    dists = [p["distance_km"] for p in r["places"] if "distance_km" in p]
    assert dists == sorted(dists)
    assert dists[0] < 5  # 광안리 근처


def test_near_unresolved():
    r = search_places(category="restaurant", region="부산",
                      near="없는장소이름qwerty", sort_by="distance", limit=5)
    assert r["notes"].get("anchor") == "unresolved"


def test_distance_no_anchor_skipped():
    r = search_places(category="restaurant", region="부산", sort_by="distance", limit=5)
    assert r["notes"].get("distance_sort") == "skipped"


def test_subtype_survives_sort_by_distance():
    # subtype 랭크가 거리보다 앞 → 결과 상위는 카페, 그 안에서 거리 오름차순
    r = search_places(category="restaurant", region="부산", subtype="카페", strict=True,
                      near="광안리해수욕장", sort_by="distance", limit=8)
    dists = [p["distance_km"] for p in r["places"] if "distance_km" in p]
    assert dists == sorted(dists)  # strict 라 전부 카페, 거리순
    assert r["total"] < 519  # 부산 음식점 전체보다 적음(카페만)


def test_relevance_default_not_distance_first(monkeypatch):
    # sort_by 미지정 + query → 의미가 거리보다 앞 (거리 오름차순이 아님)
    from app.db import query as dbq

    row = dbq("SELECT e.embedding::text AS vec FROM ai_place_v l "
              "JOIN place_embedding e USING (content_id) "
              "WHERE l.ldong_regn_cd='26' AND l.content_type_id='39' LIMIT 1")[0]
    vec = [float(x) for x in row["vec"].strip("[]").split(",")]
    monkeypatch.setattr(candidates, "embed_query", lambda _t: vec)
    r = search_places(category="restaurant", region="부산", query="x",
                      near="광안리해수욕장", limit=10)  # sort_by 기본 relevance
    dists = [p["distance_km"] for p in r["places"] if "distance_km" in p]
    assert dists != sorted(dists)  # 거리순 아님


def test_max_budget_note():
    r = search_places(category="lodging", region="부산", max_budget=80000, limit=10)
    assert r["notes"].get("price_filter", {}).get("approx") is True
    assert r["total"] >= 1


def test_exclude_ids():
    base = search_places(category="restaurant", region="부산", limit=5)
    first = base["places"][0]["content_id"]
    ex = search_places(category="restaurant", region="부산",
                       exclude_ids=[first], limit=5)
    assert all(p["content_id"] != first for p in ex["places"])


def test_query_vector_path_mocked(monkeypatch):
    # 부산의 어떤 임베딩된 장소 벡터를 그대로 query 벡터로 사용 → 자기 자신이 최상위
    from app.db import query as dbq

    row = dbq("SELECT l.content_id, e.embedding::text AS vec FROM ai_place_v l "
              "JOIN place_embedding e USING (content_id) "
              "WHERE l.ldong_regn_cd='26' AND l.content_type_id='39' LIMIT 1")[0]
    vec = [float(x) for x in row["vec"].strip("[]").split(",")]
    monkeypatch.setattr(candidates, "embed_query", lambda _t: vec)

    r = search_places(category="restaurant", region="부산", query="아무거나", limit=5)
    assert r["notes"].get("query_embed") != "failed"
    assert r["places"][0]["content_id"] == row["content_id"]


def test_overview_truncated():
    r = search_places(category="attraction", region="부산", limit=10)
    assert all(len(p["overview"]) <= 300 for p in r["places"])
