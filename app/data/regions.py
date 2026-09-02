"""지역명 → `ldong_regn_cd` 리졸브.

v1 = **시도 레벨만** (17개). 시군구("해운대구" 등)는 §9 백로그.
"부산 해운대" 처럼 시도명을 포함하면 시도 레벨로 스코프됨.
테이블 출처: 로컬 tourdata `place_list.ldong_regn_cd` 실측(16개 코드) + 표준 시도명.
"""

from __future__ import annotations

# (code, 정식명, 별칭들)
_SIDO: list[tuple[str, str, list[str]]] = [
    ("11", "서울특별시", ["서울", "서울시", "seoul"]),
    ("26", "부산광역시", ["부산", "부산시", "busan"]),
    ("27", "대구광역시", ["대구", "대구시", "daegu"]),
    ("28", "인천광역시", ["인천", "인천시", "incheon"]),
    ("12", "광주광역시", ["광주", "광주시", "gwangju"]),
    ("30", "대전광역시", ["대전", "대전시", "daejeon"]),
    ("31", "울산광역시", ["울산", "울산시", "ulsan"]),
    ("36110", "세종특별자치시", ["세종", "세종시", "sejong"]),
    ("41", "경기도", ["경기", "gyeonggi"]),
    ("51", "강원특별자치도", ["강원", "강원도", "gangwon"]),
    ("43", "충청북도", ["충북", "충청북도", "chungbuk"]),
    ("44", "충청남도", ["충남", "충청남도", "chungnam"]),
    ("52", "전북특별자치도", ["전북", "전라북도", "jeonbuk"]),
    ("46", "전라남도", ["전남", "전라남도", "jeonnam"]),  # 데이터엔 아직 없음(수집분 밖)
    ("47", "경상북도", ["경북", "경상북도", "gyeongbuk"]),
    ("48", "경상남도", ["경남", "경상남도", "gyeongnam"]),
    ("50", "제주특별자치도", ["제주", "제주도", "jeju"]),
]

_SUFFIXES = ("특별자치시", "특별자치도", "특별시", "광역시", "자치도", "자치시")

# 정규화 키(별칭·정식명 접미사 스트립) → code
_LOOKUP: dict[str, str] = {}
for _code, _name, _aliases in _SIDO:
    for _k in [_name, *_aliases]:
        _LOOKUP[_k.strip().lower()] = _code
    _stripped = _name
    for _s in _SUFFIXES:
        _stripped = _stripped.replace(_s, "")
    _LOOKUP.setdefault(_stripped.strip().lower(), _code)  # "서울", "경기" 등

# 별칭·정식명 목록(길이 내림차순 — "경상북도"가 "경북"보다 먼저 매칭되게)
_NAMES = sorted({k for k in _LOOKUP}, key=len, reverse=True)


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


def resolve_region(value: str | list[str] | None) -> tuple[list[str], str | None]:
    """지역명(str) 또는 코드 리스트(list) → (regn 코드 리스트, note).

    note: None(성공) / "unresolved"(못 찾음) — 둘 다 되물음 케이스는 호출측 판단.
    """
    if not value:
        return [], None

    # 이미 코드 리스트 (userContext.regionCodes)
    if isinstance(value, list):
        codes = [str(v).strip() for v in value if str(v).strip()]
        return (codes, None) if codes else ([], "unresolved")

    raw = value.strip()
    n = _norm(raw)

    # 1. 정확
    if n in _LOOKUP:
        return [_LOOKUP[n]], None

    # 2. 접미사 스트립 후
    stripped = raw
    for suf in _SUFFIXES:
        stripped = stripped.replace(suf, "")
    if _norm(stripped) in _LOOKUP:
        return [_LOOKUP[_norm(stripped)]], None

    # 3. 부분 문자열 — "부산 해운대" ⊃ "부산"
    for name in _NAMES:
        if name in n:
            return [_LOOKUP[name]], None

    return [], "unresolved"
