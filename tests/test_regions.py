from app.data.regions import resolve_region


def test_exact_and_alias():
    assert resolve_region("부산") == (["26"], None)
    assert resolve_region("부산광역시") == (["26"], None)
    assert resolve_region("서울시") == (["11"], None)
    assert resolve_region("seoul") == (["11"], None)


def test_suffix_strip_and_longest_first():
    assert resolve_region("경상북도") == (["47"], None)
    assert resolve_region("경북") == (["47"], None)
    assert resolve_region("제주특별자치도") == (["50"], None)
    assert resolve_region("강원") == (["51"], None)


def test_substring_falls_back_to_sido():
    # 시군구는 v1 미지원 → 포함된 시도로 스코프
    assert resolve_region("부산 해운대") == (["26"], None)
    assert resolve_region("서울 강남") == (["11"], None)


def test_code_list_passthrough():
    assert resolve_region(["26", "11"]) == (["26", "11"], None)
    assert resolve_region([26, 11]) == (["26", "11"], None)


def test_unresolved_and_empty():
    assert resolve_region("우리 동네") == ([], "unresolved")
    assert resolve_region("") == ([], None)
    assert resolve_region(None) == ([], None)
