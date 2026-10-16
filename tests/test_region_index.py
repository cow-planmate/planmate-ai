"""지역 색인 해석·캐시 (`22` §6-1·§6-2·§8). DB·네트워크 없이 고정 색인 픽스처로 돈다."""

import pytest

from app.data.region_index import FetchError, Node, RegionIndex, RegionIndexStore, resolve, scope_of

# 실제 v2 시드와 같은 모양의 축소판 (ID 는 임의)
SEOUL, BUSAN, GYEONGGI, GANGWON, GYEONGNAM, JEONNAM, JEJU_DO, SEJONG = 1, 20, 29, 30, 31, 155, 160, 170
PAJU, GG_GWANGJU, GOYANG, YANGPYEONG = 45, 46, 60, 61
GW_GOSEONG, SOKCHO = 47, 50
GN_GOSEONG, TONGYEONG = 48, 49
GWANGJU, YEOSU = 17, 15
JEJU_SI, SEOGWIPO = 28, 27


def _sido(i, name, regn, *aliases):
    return Node(i, name, None, tuple(aliases), ((regn, None),))


def _sigungu(i, name, parent, regn, signgus, *aliases):
    return Node(i, name, parent, tuple(aliases), tuple((regn, s) for s in signgus))


NODES = [
    _sido(SEOUL, "서울", "11", "seoul"),
    _sido(BUSAN, "부산", "26", "busan"),
    _sido(GYEONGGI, "경기", "41", "경기도", "gyeonggi"),
    _sido(GANGWON, "강원", "51", "강원도", "gangwon"),
    _sido(GYEONGNAM, "경남", "48", "경상남도", "gyeongnam"),
    _sido(JEONNAM, "전남광주", "12", "전라남도", "전남", "전남광주통합특별시", "jeonnam"),
    _sido(JEJU_DO, "제주도", "50", "제주특별자치도", "제주", "jeju"),
    _sido(SEJONG, "세종", "36110", "sejong"),
    _sigungu(PAJU, "파주", GYEONGGI, "41", ["480"]),
    _sigungu(GG_GWANGJU, "경기 광주", GYEONGGI, "41", ["610"], "광주"),
    _sigungu(GOYANG, "고양", GYEONGGI, "41", ["280", "281", "285", "287"]),
    _sigungu(YANGPYEONG, "양평", GYEONGGI, "41", ["830"]),
    _sigungu(GW_GOSEONG, "강원 고성", GANGWON, "51", ["820"], "고성"),
    _sigungu(SOKCHO, "속초", GANGWON, "51", ["210"]),
    _sigungu(GN_GOSEONG, "경남 고성", GYEONGNAM, "48", ["820"], "고성"),
    _sigungu(TONGYEONG, "통영", GYEONGNAM, "48", ["220"]),
    _sigungu(GWANGJU, "광주", JEONNAM, "12", ["210", "240", "270", "300", "330"], "광주광역시", "gwangju"),
    _sigungu(YEOSU, "여수", JEONNAM, "12", ["130"]),
    _sigungu(JEJU_SI, "제주", JEJU_DO, "50", ["110"]),
    _sigungu(SEOGWIPO, "서귀포", JEJU_DO, "50", ["130"]),
]
INDEX = RegionIndex.build("v1", NODES)


def node_of(value, destination_id=None):
    res = resolve(value, INDEX, destination_id)
    assert res.note is None, (value, res)
    return res.node.destination_id


# ── 기본 해석 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    ("전남", JEONNAM), ("전라남도", JEONNAM), ("전남광주", JEONNAM), ("전남광주통합특별시", JEONNAM),
    ("jeonnam", JEONNAM), ("Seoul", SEOUL), ("서울특별시", SEOUL), ("서울시", SEOUL),
    ("세종특별자치시", SEJONG), ("세종시", SEJONG), ("경기도", GYEONGGI),
    ("파주", PAJU), ("파주시", PAJU), ("여수", YEOSU), ("경기 광주", GG_GWANGJU), ("경남 고성", GN_GOSEONG),
])
def test_exact_and_suffix(value, expected):
    assert node_of(value) == expected


def test_two_words_sido_then_sigungu_picks_sigungu():
    assert node_of("전남 여수") == YEOSU
    assert node_of("강원도 고성") == GW_GOSEONG


def test_unmatched_word_falls_back_to_sido():
    assert node_of("부산 해운대") == BUSAN


def test_gu_alone_is_unresolved():
    assert resolve("해운대", INDEX).note == "unresolved"


# ── 원형 정확 일치 우선 (§6-2 해석 순서 1) ────────────────────────────────────
def test_full_official_name_beats_stripped_form():
    # 정규화형 "제주"는 제주시와 겹치지만 원형 "제주특별자치도"는 제주도 별칭과 바로 맞는다
    assert node_of("제주특별자치도", destination_id=JEJU_SI) == JEJU_DO
    # 정규화형 "광주"는 경기 광주 별칭과 겹치지만 원형 "광주광역시"는 광주 노드
    assert node_of("광주광역시", destination_id=GYEONGGI) == GWANGJU


# ── 목적지 기준 좁히기 (§6-2-1) ──────────────────────────────────────────────
@pytest.mark.parametrize("destination,value,expected", [
    (GN_GOSEONG, "고성", GN_GOSEONG),     # 규칙 1: 목적지 노드 자체
    (TONGYEONG, "고성", GN_GOSEONG),      # 규칙 2: 같은 시도(경남)
    (SOKCHO, "고성", GW_GOSEONG),         # 규칙 2: 같은 시도(강원)
    (GYEONGGI, "광주 카페", GG_GWANGJU),  # 규칙 2: 목적지가 시도 노드
    (YEOSU, "광주", GWANGJU),             # 규칙 2: 같은 시도(전남광주)
    (GYEONGGI, "광주시", GG_GWANGJU),     # "광주시"는 "광주"와 같은 처리
])
def test_destination_narrows_overlapping_names(destination, value, expected):
    assert node_of(value, destination_id=destination) == expected


def test_unrelated_destination_asks_back():
    res = resolve("고성", INDEX, destination_id=SEOUL)
    assert res.note == "ambiguous"
    assert res.candidates == ("강원 고성", "경남 고성")
    assert resolve("광주", INDEX, destination_id=SEOUL).note == "ambiguous"


def test_single_candidate_ignores_destination():
    # 다른 지역을 묻는 것도 정상 요청 — 목적지가 파주여도 부산은 부산
    assert node_of("부산", destination_id=PAJU) == BUSAN


def test_no_destination_skips_narrowing():
    assert resolve("고성", INDEX, destination_id=None).note == "ambiguous"
    assert resolve("고성", INDEX, destination_id=99999).note == "ambiguous"


# ── 한 단어가 시도·하위 시군에 동시에 (§6-2 결과 정리 1 단서) ─────────────────
@pytest.mark.parametrize("destination,expected", [
    (JEJU_DO, JEJU_DO), (JEJU_SI, JEJU_SI), (SEOGWIPO, JEJU_DO), (SEOUL, JEJU_DO), (None, JEJU_DO),
])
def test_jeju_follows_destination(destination, expected):
    assert node_of("제주", destination_id=destination) == expected


# ── 부분 문자열은 시도만 + 시도 뒤 나머지 (§6-2 해석 순서 3) ───────────────────
def test_sido_then_rest_without_space():
    assert node_of("전남여수") == YEOSU
    assert node_of("부산해운대") == BUSAN


@pytest.mark.parametrize("value", ["고양이 카페", "양평해장국", "강릉역"])
def test_sigungu_not_matched_by_substring(value):
    assert resolve(value, INDEX).note == "unresolved"


def test_known_limit_sido_substring_false_positive():
    # 현행 regions.py 와 같은 한계(회귀 아님) — 문서 §6-2 에 기록
    assert node_of("경기장 근처") == GYEONGGI


def test_deterministic_across_hash_seeds():
    # 같은 길이 키가 여러 개여도 정렬이 (길이, 사전순)이라 결과가 고정된다
    assert INDEX.sido_keys == tuple(sorted(INDEX.sido_keys, key=lambda k: (-len(k), k)))


# ── 검색 범위 ────────────────────────────────────────────────────────────────
def test_scope_of_sido_and_sigungu():
    assert scope_of(INDEX.nodes[JEONNAM]) == (["12"], [])
    assert scope_of(INDEX.nodes[GWANGJU]) == (["12"], ["210", "240", "270", "300", "330"])


def test_scope_of_mixed_codes_widens_to_sido():
    mixed = Node(999, "섞임", None, (), (("41", None), ("41", "480")))
    assert scope_of(mixed) == (["41"], [])


# ── 캐시 (§6-1) ─────────────────────────────────────────────────────────────
class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _store(fetch, clock=None):
    return RegionIndexStore(fetch, min_interval_s=30.0, clock=clock or FakeClock())


def test_store_same_version_does_not_fetch():
    calls = []
    store = _store(lambda known: calls.append(known) or INDEX)
    store.sync("v1")
    store.sync("v1")
    assert calls == [""]
    assert store.get() is INDEX


def test_store_new_version_refetches_after_floor():
    clock = FakeClock()
    v2 = RegionIndex.build("v2", NODES)
    responses = [INDEX, v2]
    calls = []
    store = _store(lambda known: calls.append(known) or responses.pop(0), clock)
    store.sync("v1")
    clock.now = 10
    store.sync("v2")            # 하한(30초) 안 — 재조회 안 함
    assert calls == [""]
    clock.now = 31
    store.sync("v2")
    assert calls == ["", "v1"]
    assert store.get().version == "v2"


def test_store_unchanged_keeps_index():
    clock = FakeClock()
    store = _store(lambda known: INDEX if known == "" else None, clock)
    store.sync("v1")
    clock.now = 100
    store.sync("other")         # 서버가 unchanged 라고 하면 그대로
    assert store.get() is INDEX


def test_store_failure_keeps_previous_index_and_never_raises():
    clock = FakeClock()
    results = [INDEX, FetchError("UNAVAILABLE", permanent=False)]

    def fetch(known):
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    store = _store(fetch, clock)
    store.sync("v1")
    clock.now = 100
    store.sync("v2")
    assert store.get() is INDEX


def test_store_never_loaded_stays_none_on_failure():
    def fetch(known):
        raise FetchError("UNAUTHENTICATED", permanent=True)

    store = _store(fetch)
    store.prefetch()
    store.sync("v1")
    assert store.get() is None


def test_store_without_fetcher_or_version_is_noop():
    _store(None).sync("v1")
    calls = []
    store = _store(lambda known: calls.append(known))
    store.sync(None)            # 구버전 v2 — 버전이 안 오면 조회하지 않는다
    assert calls == []
