"""`subtype` 자연어 → lclsSystm 코드, `amenities` 자연어 → 정규화 컬럼.

출처: `project_ai/05 §B-1`(lclsSystm 라벨), `§B-2`(subfacility 12종).
매핑 없는 값은 티어에 따라: subtype→`notes.subtype_unmatched`, amenities→소프트/`notes`.
"""

from __future__ import annotations

CATEGORY_CTYPE: dict[str, tuple[str, ...]] = {
    "attraction": ("12", "14"),
    "lodging": ("32",),
    "restaurant": ("39",),
}

# subtype → (lclsSystm 레벨 컬럼, 코드). category 별.
SUBTYPE_CODE: dict[str, dict[str, tuple[str, str]]] = {
    "restaurant": {
        "한식": ("lcls_systm2", "FD01"),
        "중식": ("lcls_systm3", "FD020100"), "중국집": ("lcls_systm3", "FD020100"),
        "중화요리": ("lcls_systm3", "FD020100"),
        "일식": ("lcls_systm3", "FD020200"), "일본식": ("lcls_systm3", "FD020200"),
        "양식": ("lcls_systm3", "FD020300"), "서양식": ("lcls_systm3", "FD020300"),
        "카페": ("lcls_systm2", "FD05"), "커피": ("lcls_systm2", "FD05"),
        "찻집": ("lcls_systm2", "FD05"),
        "분식": ("lcls_systm3", "FD030400"), "김밥": ("lcls_systm3", "FD030400"),
        "제과": ("lcls_systm3", "FD030100"), "베이커리": ("lcls_systm3", "FD030100"),
        "빵집": ("lcls_systm3", "FD030100"),
        "퓨전": ("lcls_systm3", "FD020500"),
        "주점": ("lcls_systm2", "FD04"), "술집": ("lcls_systm2", "FD04"),
    },
    "attraction": {
        "역사": ("lcls_systm1", "HS"), "유적": ("lcls_systm1", "HS"),
        "유적지": ("lcls_systm1", "HS"), "전통": ("lcls_systm1", "HS"),
        "사찰": ("lcls_systm1", "HS"), "절": ("lcls_systm1", "HS"),
        "자연": ("lcls_systm1", "NA"), "산": ("lcls_systm1", "NA"),
        "도시관광": ("lcls_systm1", "VE"), "체험": ("lcls_systm1", "EX"),
        "해수욕장": ("lcls_systm3", "NA020900"), "해변": ("lcls_systm3", "NA020900"),
        "바다": ("lcls_systm3", "NA020900"),
        "호수": ("lcls_systm3", "NA020200"),
        "공원": ("lcls_systm1", "NA"),
        "박물관": ("lcls_systm3", "VE070100"),
        "미술관": ("lcls_systm3", "VE070600"), "화랑": ("lcls_systm3", "VE070600"),
        "전시관": ("lcls_systm3", "VE070300"),
        "공연장": ("lcls_systm3", "VE060100"),
        "전망대": ("lcls_systm3", "VE010200"), "타워": ("lcls_systm3", "VE010200"),
        "테마파크": ("lcls_systm3", "VE020100"), "놀이공원": ("lcls_systm3", "VE020100"),
    },
    "lodging": {
        "호텔": ("lcls_systm3", "AC010100"),
        "모텔": ("lcls_systm3", "AC040100"),
        "펜션": ("lcls_systm3", "AC030100"),
        "한옥": ("lcls_systm3", "AC030200"), "한옥스테이": ("lcls_systm3", "AC030200"),
        "게스트하우스": ("lcls_systm3", "AC060200"), "게하": ("lcls_systm3", "AC060200"),
        "유스호스텔": ("lcls_systm3", "AC060100"),
        "리조트": ("lcls_systm3", "VE050200"),
        "야영장": ("lcls_systm2", "AC05"), "캠핑장": ("lcls_systm2", "AC05"),
        "캠핑": ("lcls_systm2", "AC05"),
        "글램핑": ("lcls_systm3", "AC050400"),
    },
}

# subtype description 에 노출할 유효값(별칭 제외 대표값)
SUBTYPE_VOCAB = {
    "restaurant": ["한식", "중식", "일식", "양식", "카페", "분식", "제과", "퓨전", "주점"],
    "attraction": ["역사", "자연", "도시관광", "체험", "해수욕장", "호수", "공원",
                   "박물관", "미술관", "전시관", "공연장", "전망대", "테마파크"],
    "lodging": ["호텔", "모텔", "펜션", "한옥스테이", "게스트하우스", "유스호스텔",
                "리조트", "야영장", "글램핑"],
}

# amenities → (테이블, 컬럼, 매칭방식). '1' = subfacility 불린 / 'kids' / 'parking'(근사)
#   테이블: lodging=place_intro_lodging, food=place_intro_food
AMENITY_COLUMN: dict[str, tuple[str, str, str]] = {
    "바베큐": ("lodging", "barbecue", "1"), "바비큐": ("lodging", "barbecue", "1"),
    "bbq": ("lodging", "barbecue", "1"),
    "사우나": ("lodging", "sauna", "1"),
    "대중탕": ("lodging", "publicbath", "1"), "목욕탕": ("lodging", "publicbath", "1"),
    "헬스": ("lodging", "fitness", "1"), "헬스장": ("lodging", "fitness", "1"),
    "피트니스": ("lodging", "fitness", "1"),
    "노래방": ("lodging", "karaoke", "1"),
    "세미나실": ("lodging", "seminar", "1"), "회의실": ("lodging", "seminar", "1"),
    "자전거": ("lodging", "bicycle", "1"),
    "매점": ("lodging", "beverage", "1"), "편의점": ("lodging", "beverage", "1"),
    "캠프파이어": ("lodging", "campfire", "1"),
    "공용pc": ("lodging", "publicpc", "1"),
    "스포츠시설": ("lodging", "sports", "1"),
    "뷰티": ("lodging", "beauty", "1"),
    "키즈존": ("food", "kidsfacility", "kids"), "키즈": ("food", "kidsfacility", "kids"),
    "주차": ("*", "parking", "parking"),
}

# 컬럼 없는(=티어 3 소프트, subfacility 자유텍스트 ILIKE) 키워드
AMENITY_SOFT = {"수영장", "풀장", "조식", "와이파이", "wifi", "반려동물", "애견"}


def resolve_subtype(category: str | None, value: str | None):
    """(레벨 컬럼, 코드) 또는 None."""
    if not category or not value:
        return None
    return SUBTYPE_CODE.get(category, {}).get(value.strip())


# lclsSystm 코드 → 한글 라벨 (userContext.preferredThemes 를 프롬프트 문장으로 변환용, 17 #6).
# SUBTYPE_CODE 역인덱스(대표 라벨) + L1 대분류.
_L1_LABEL = {"AC": "숙박", "EX": "체험", "FD": "음식", "HS": "역사", "LS": "레저",
             "NA": "자연", "VE": "도시관광", "EV": "축제", "SH": "쇼핑"}
_CODE_LABEL: dict[str, str] = dict(_L1_LABEL)
for _cat, _m in SUBTYPE_CODE.items():
    for _label, (_col, _code) in _m.items():
        _CODE_LABEL.setdefault(_code, _label)   # 첫(대표) 라벨 우선


def label_for_code(code: str | None) -> str | None:
    """lclsSystm 코드 → 한글 라벨. 모르면 None."""
    if not code:
        return None
    return _CODE_LABEL.get(code.strip()) or _CODE_LABEL.get(code.strip()[:2])
