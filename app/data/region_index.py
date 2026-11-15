"""지역 색인 — 채팅 문장의 지역명을 v2 여행지 노드로 해석한다.

설계: project_ai/22_지역_참조데이터_동기화_설계.md §6-1·§6-2.

원본은 Backend-v2(destination 노드·지역코드·별칭)이고, 이 모듈은 받아 둔 사본을 메모리에 들고
해석만 한다. 예전 `regions.py` 처럼 AI 저장소에 이름표를 두지 않는다 — 그 사본이 v2 행정구역
개편(전남·광주 → `12`)을 놓쳐 없는 코드(`46`)로 검색하는 일이 있었다.

- `RegionIndexStore` : 색인 1벌 캐시. 요청이 가져온 버전과 다를 때만 v2 에서 다시 받는다.
- `resolve()`       : 이름 → 노드 하나(또는 ambiguous / unresolved). 플랜 목적지로 겹침을 좁힌다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)

CodePair = tuple[str, str | None]   # (ldong_regn_cd, ldong_signgu_cd | None = 시도 전체)

# 끝에서 떼는 행정 접미사. 긴 것부터 — "특별자치시"를 "시"로 먼저 떼면 "세종특별자치"가 남는다.
# 구(區)는 떼지 않는다 — 구는 노드가 아니다("해운대구" → "해운대" 로 만들어도 찾을 노드가 없다).
_SUFFIXES = ("특별자치시", "특별자치도", "특별시", "광역시", "시", "군")


@dataclass(frozen=True)
class Node:
    destination_id: int
    name: str
    parent_id: int | None
    aliases: tuple[str, ...]
    codes: tuple[CodePair, ...]

    @property
    def is_sido(self) -> bool:
        return self.parent_id is None


@dataclass(frozen=True)
class RegionIndex:
    version: str
    nodes: dict[int, Node]
    # 정규화 키(노드 이름·별칭) → 노드 id 들. 같은 별칭이 여러 노드에 걸릴 수 있다("고성").
    lookup: dict[str, frozenset[int]] = field(default_factory=dict)
    # 시도 노드의 이름·별칭 키, 긴 것부터(같은 길이는 사전순 — 실행마다 결과가 같도록)
    sido_keys: tuple[str, ...] = ()

    @classmethod
    def build(cls, version: str, nodes: list[Node]) -> "RegionIndex":
        by_id = {n.destination_id: n for n in nodes}
        lookup: dict[str, set[int]] = {}
        for n in nodes:
            for key in {_norm(n.name), *(_norm(a) for a in n.aliases)}:
                if key:
                    lookup.setdefault(key, set()).add(n.destination_id)
        sido_keys = sorted(
            {k for k, ids in lookup.items() if any(by_id[i].is_sido for i in ids)},
            key=lambda k: (-len(k), k),
        )
        return cls(
            version=version,
            nodes=by_id,
            lookup={k: frozenset(v) for k, v in lookup.items()},
            sido_keys=tuple(sido_keys),
        )

    def sido_of(self, node_id: int) -> int:
        n = self.nodes[node_id]
        return n.destination_id if n.is_sido else n.parent_id   # type: ignore[return-value]


@dataclass(frozen=True)
class Resolution:
    node: Node | None = None
    note: str | None = None                 # None | "ambiguous" | "unresolved"
    candidates: tuple[str, ...] = ()        # ambiguous 일 때 되물음에 쓸 노드 이름


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


def _strip_suffix(s: str) -> str:
    for suf in _SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s


# ── 해석 ─────────────────────────────────────────────────────────────────────
def resolve(value: str, index: RegionIndex, destination_id: int | None = None) -> Resolution:
    """지역명 → 노드 하나. §6-2 순서: 정확 일치 → 단어 단위 → (시도만) 부분 문자열 → 좁히기."""
    if not value or not value.strip():
        return Resolution(note="unresolved")

    candidates = _exact(_norm(value), index, destination_id)
    if not candidates:
        candidates = _by_tokens(value, index, destination_id)
    if not candidates:
        candidates = _sido_substring(_norm(value), index)
    if not candidates:
        return Resolution(note="unresolved")
    return _narrow(candidates, index, destination_id)


def _exact(key: str, index: RegionIndex, destination_id: int | None) -> set[int]:
    """원형이 맞으면 그 결과, 원형으로 아무것도 안 맞을 때만 접미사를 뗀 형태로 찾는다.
    "제주특별자치도"는 원형이 제주도 별칭과 맞으므로, 정규화형 "제주"의 제주시 후보는 보지 않는다."""
    ids = index.lookup.get(key)
    if not ids:
        stripped = _strip_suffix(key)
        ids = index.lookup.get(stripped) if stripped != key else None
    if not ids:
        return set()
    return _collapse_same_word(set(ids), index, destination_id)


def _collapse_same_word(ids: set[int], index: RegionIndex, destination_id: int | None) -> set[int]:
    """한 단어가 시도와 그 하위 시군에 동시에 맞으면(예: "제주" = 제주도 별칭이자 제주시 이름)
    목적지가 그 시군 자체일 때만 시군, 그 외엔 시도 전체 — 넓은 쪽은 원하는 곳이 빠지지 않는다."""
    sidos = {i for i in ids if index.nodes[i].is_sido}
    for s in sidos:
        children = {i for i in ids if index.nodes[i].parent_id == s}
        if not children:
            continue
        keep = destination_id if destination_id in children else s
        ids = (ids - children - {s}) | {keep}
    return ids


def _by_tokens(value: str, index: RegionIndex, destination_id: int | None) -> set[int]:
    """띄어 쓴 단어마다 정확 일치. 시도 단어와 시군 단어가 같이 있으면 그 시도 아래 시군으로 좁힌다
    ("전남 여수" → 여수, "경남 고성" → 경남 고성). 못 찾은 단어는 무시한다("부산 해운대" → 부산)."""
    hits: set[int] = set()
    for token in value.split():
        hits |= _exact(_norm(token), index, destination_id)
    if not hits:
        return set()
    sidos = {i for i in hits if index.nodes[i].is_sido}
    children = {i for i in hits if not index.nodes[i].is_sido}
    if sidos and children:
        under = {c for c in children if index.nodes[c].parent_id in sidos}
        return under or hits
    return hits


def _sido_substring(key: str, index: RegionIndex) -> set[int]:
    """시도 이름·별칭만 부분 문자열로 찾는다(시군까지 넓히면 "고양이" → 고양 같은 오인식).
    시도가 잡히면 나머지 글자가 그 시도 아래 시군과 정확히 맞는지 한 번 더 본다("전남여수" → 여수)."""
    for sk in index.sido_keys:
        pos = key.find(sk)
        if pos < 0:
            continue
        sido_ids = {i for i in index.lookup[sk] if index.nodes[i].is_sido}
        rest = key[:pos] + key[pos + len(sk):]
        if rest:
            for rk in (rest, _strip_suffix(rest)):
                under = {i for i in index.lookup.get(rk, ()) if index.nodes[i].parent_id in sido_ids}
                if under:
                    return under
        return sido_ids
    return set()


def _narrow(candidates: set[int], index: RegionIndex, destination_id: int | None) -> Resolution:
    """§6-2-1 목적지 기준 좁히기. 후보가 하나면 목적지와 달라도 그대로 쓴다(다른 지역 질문도 정상)."""
    if len(candidates) > 1 and destination_id in index.nodes:
        if destination_id in candidates:
            candidates = {destination_id}
        else:
            dest_sido = index.sido_of(destination_id)
            same_sido = {c for c in candidates if index.sido_of(c) == dest_sido}
            if len(same_sido) == 1:
                candidates = same_sido
    if len(candidates) == 1:
        return Resolution(node=index.nodes[next(iter(candidates))])
    names = tuple(sorted(index.nodes[c].name for c in candidates))
    return Resolution(note="ambiguous", candidates=names)


def scope_of(node: Node) -> tuple[list[str], list[str]]:
    """노드 코드 → search_places 의 (region 코드 리스트, signgu 리스트).

    지금 노드는 모두 시도 하나에 속하고 (시도, NULL)과 (시도, 시군구)를 섞어 갖지 않으므로
    두 리스트로 펼쳐도 코드 쌍과 같은 범위다(22 §11-1 점검). 섞인 노드가 생기면 시군구를 빼고
    시도로 넓힌다 — 좁혀서 빠뜨리는 것보다 넓게 찾는 쪽이 안전하다.
    """
    regns = sorted({r for r, _ in node.codes})
    signgus = sorted({s for _, s in node.codes if s})
    mixed = len(regns) > 1 or (signgus and any(s is None for _, s in node.codes))
    if mixed:
        log.warning("region node %s(%d) has mixed codes %s — searching whole sido",
                    node.name, node.destination_id, node.codes)
        return regns, []
    return regns, signgus


# ── 캐시 ─────────────────────────────────────────────────────────────────────
class FetchError(Exception):
    """색인 조회 실패. `permanent` = 설정·배포 실수라 다시 시도해도 계속 실패할 원인(ERROR 로그)."""

    def __init__(self, message: str, *, permanent: bool):
        super().__init__(message)
        self.permanent = permanent


# known_version → 새 색인, 바뀐 게 없으면 None. 실패는 FetchError.
Fetcher = Callable[[str], "RegionIndex | None"]


class RegionIndexStore:
    """프로세스 메모리의 색인 1벌. 교체는 참조를 통째로 바꾸므로 읽는 쪽은 락이 필요 없다."""

    def __init__(self, fetch: Fetcher | None, *, min_interval_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self._fetch = fetch
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._lock = threading.Lock()
        self._last_attempt: float | None = None
        self._index: RegionIndex | None = None

    def get(self) -> RegionIndex | None:
        return self._index

    def sync(self, wanted_version: str | None) -> None:
        """요청이 가져온 버전과 다를 때만 재조회. 실패·불일치가 반복돼도 하한 간격 안에서는 다시 안 부른다."""
        if wanted_version is None:
            return
        current = self._index
        if current is not None and current.version == wanted_version:
            return
        self._refresh()

    def prefetch(self) -> None:
        """기동 직후 1회 — 첫 챗봇 요청을 기다리지 않고 받아 둔다. 실패해도 예외를 내지 않는다."""
        self._refresh()

    def _refresh(self) -> None:
        if self._fetch is None:
            return
        with self._lock:   # 같은 순간 여러 요청이 와도 조회는 한 번(single-flight)
            now = self._clock()
            if self._last_attempt is not None and now - self._last_attempt < self._min_interval_s:
                return
            self._last_attempt = now
            known = self._index.version if self._index is not None else ""
            try:
                fetched = self._fetch(known)
            except FetchError as e:
                (log.error if e.permanent else log.warning)("region index fetch failed: %s", e)
                return
            except Exception:  # noqa: BLE001 — 색인 실패가 챗봇 요청을 깨면 안 된다
                log.exception("region index fetch failed unexpectedly")
                return
            if fetched is not None:
                self._index = fetched
                log.info("region index loaded: version=%s nodes=%d", fetched.version, len(fetched.nodes))
