"""장소 카테고리 = 단일 어휘 (v2 와 동일).

v2 `PlaceCategory` / `BlockCategory` enum 이름을 그대로 쓴다: ATTRACTION / ACCOMMODATION / RESTAURANT.
content_type_id 숫자("12"/"14"/"32"/"39")가 더 깊은 진실이고, 문자열 표현은 이 3개로 통일.

- 검색 툴 파라미터 `category`, `generate_itinerary` 슬롯 `category`
- `search_places` 반환 `category`, 응답 `shownPlaces[].category`
- 블록 `blockCategory` (= 이 값과 동일)

이전엔 내부에서 소문자 attraction/lodging/restaurant 를 쓰고 경계마다 대문자로 매핑했는데,
그 split 이 shownPlaces category 버그의 원인이라 어휘를 하나로 합쳤다.
"""

from __future__ import annotations

CATEGORIES: tuple[str, ...] = ("ATTRACTION", "ACCOMMODATION", "RESTAURANT")

# content_type_id → 카테고리
CTYPE_TO_CATEGORY: dict[str, str] = {
    "12": "ATTRACTION",
    "14": "ATTRACTION",
    "32": "ACCOMMODATION",
    "39": "RESTAURANT",
}

# 카테고리 → content_type_id 들 (검색 WHERE)
CATEGORY_TO_CTYPES: dict[str, tuple[str, ...]] = {
    "ATTRACTION": ("12", "14"),
    "ACCOMMODATION": ("32",),
    "RESTAURANT": ("39",),
}
