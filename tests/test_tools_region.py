"""search_places 지역 범위 결정 (`22` §6-3). 경로 A·구버전 v2 경로는 기존 동작 그대로인지가 핵심."""

import pytest

from app.data import region_index_client
from app.data.region_index import RegionIndexStore
from app.services import candidates, tools
from app.services.tools import ToolContext
from tests.test_region_index import INDEX, PAJU, SEOUL


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        return {"places": [], "total": 0, "notes": {}}

    monkeypatch.setattr(candidates, "search_places", fake_search)
    return calls


@pytest.fixture
def loaded_store(monkeypatch):
    store = RegionIndexStore(lambda known: INDEX)
    store.prefetch()
    monkeypatch.setattr(region_index_client, "store", store)
    return store


def paju_ctx(version="v1"):
    return ToolContext(region=["41"], signgu=["480"], destination_id=PAJU, region_index_version=version)


# ── 기존 동작 유지 ────────────────────────────────────────────────────────────
def test_no_region_arg_uses_destination_codes(captured, loaded_store):
    tools.run_tool("search_places", {"category": "RESTAURANT"}, paju_ctx())
    assert captured[0]["region"] == ["41"]
    assert captured[0]["signgu"] == ["480"]


def test_old_v2_without_version_keeps_legacy_behavior(captured, loaded_store):
    tools.run_tool("search_places", {"region": "부산"}, paju_ctx(version=None))
    # 구버전 v2: 기존처럼 regions.py 문자열 해석 + 목적지 signgu
    assert captured[0]["region"] == "부산"
    assert captured[0]["signgu"] == ["480"]


# ── v2 지역 색인 경로 ─────────────────────────────────────────────────────────
def test_other_region_does_not_inherit_destination_signgu(captured, loaded_store):
    tools.run_tool("search_places", {"region": "부산"}, paju_ctx())
    assert captured[0]["region"] == ["26"]
    assert captured[0]["signgu"] is None


def test_sido_containing_destination_searches_whole_sido(captured, loaded_store):
    tools.run_tool("search_places", {"region": "경기"}, paju_ctx())
    assert captured[0]["region"] == ["41"]
    assert captured[0]["signgu"] is None


def test_jeonnam_resolves_to_12(captured, loaded_store):
    tools.run_tool("search_places", {"region": "전남"}, paju_ctx())
    assert captured[0]["region"] == ["12"]


def test_sigungu_name_scopes_to_sigungu(captured, loaded_store):
    tools.run_tool("search_places", {"region": "여수"}, paju_ctx())
    assert captured[0]["region"] == ["12"]
    assert captured[0]["signgu"] == ["130"]


def test_ambiguous_returns_candidates_without_search(captured, loaded_store):
    ctx = ToolContext(region=["11"], destination_id=SEOUL, region_index_version="v1")
    out = tools.run_tool("search_places", {"region": "고성"}, ctx)
    assert captured == []
    assert out["notes"] == {"region": "ambiguous", "candidates": ["강원 고성", "경남 고성"]}


def test_unresolved_returns_note_without_search(captured, loaded_store):
    out = tools.run_tool("search_places", {"region": "우리 동네"}, paju_ctx())
    assert captured == []
    assert out["notes"] == {"region": "unresolved"}


def test_index_never_loaded_is_index_unavailable(captured, monkeypatch):
    monkeypatch.setattr(region_index_client, "store", RegionIndexStore(None))
    out = tools.run_tool("search_places", {"region": "부산"}, paju_ctx())
    assert captured == []
    assert out["notes"] == {"region": "index_unavailable"}
