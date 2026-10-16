"""v2 내부 gRPC `RegionIndexService.GetRegionIndex` 클라이언트.

설계: project_ai/22_지역_참조데이터_동기화_설계.md §5-3·§6-1.

- 주소 `settings.v2_grpc_addr`(테스트 서버 docker 네트워크·운영 k3s 모두 `planmate-app:9090`).
- 인증 metadata `x-internal-token` = AI 전용 `INTERNAL_AI_TOKEN` — v2 는 이 토큰에 지역 색인
  서비스만 허용한다(공유 토큰을 주면 사용자 정보 API 까지 열리므로 쓰지 않는다).
- 생성 코드(BSR `planmate-proto-*-python`)는 호출 시점에 import 한다 — 패키지가 없거나 토큰이
  비어 있어도 앱 기동과 다른 기능은 영향받지 않게.
"""

from __future__ import annotations

import logging
import threading

from app.config import settings
from app.data.region_index import FetchError, Node, RegionIndex, RegionIndexStore

log = logging.getLogger(__name__)

# 설정·배포 실수라 다시 시도해도 계속 실패할 원인 — ERROR 로그로 드러낸다(22 §6-1)
_PERMANENT_CODES = {"UNAUTHENTICATED", "PERMISSION_DENIED", "UNIMPLEMENTED", "INVALID_ARGUMENT", "NOT_FOUND"}


def make_fetcher():
    """토큰이 없으면 None — 색인을 받을 방법이 없으므로 시도하지 않는다(기동 시 한 번 ERROR)."""
    if not settings.internal_ai_token:
        return None
    return fetch


def fetch(known_version: str) -> RegionIndex | None:
    try:
        import grpc
        from planmate.internal.v1 import region_index_service_pb2 as pb
        from planmate.internal.v1 import region_index_service_pb2_grpc as pb_grpc
    except ImportError as e:
        raise FetchError(f"gRPC/BSR 패키지 없음: {e}", permanent=True) from e

    with grpc.insecure_channel(settings.v2_grpc_addr) as channel:
        stub = pb_grpc.RegionIndexServiceStub(channel)
        try:
            resp = stub.GetRegionIndex(
                pb.GetRegionIndexRequest(known_version=known_version),
                metadata=(("x-internal-token", settings.internal_ai_token),),
                timeout=settings.region_index_deadline_s,
            )
        except grpc.RpcError as e:
            code = e.code().name if e.code() is not None else "UNKNOWN"
            raise FetchError(f"{code} {e.details()}", permanent=code in _PERMANENT_CODES) from e

    if resp.unchanged:
        return None
    return RegionIndex.build(resp.version, [_to_node(n) for n in resp.nodes])


def _to_node(n) -> Node:
    return Node(
        destination_id=n.destination_id,
        name=n.name,
        parent_id=n.parent_id if n.HasField("parent_id") else None,
        aliases=tuple(n.aliases),
        codes=tuple((c.regn, c.signgu if c.HasField("signgu") else None) for c in n.codes),
    )


store = RegionIndexStore(make_fetcher(), min_interval_s=settings.region_index_min_refresh_s)


def prefetch_in_background() -> None:
    """기동 직후 1회. 실패해도 기동·readiness 를 막지 않는다(AI 배포가 v2 상태에 묶이지 않게)."""
    if not settings.internal_ai_token:
        log.error("INTERNAL_AI_TOKEN 미설정 — v2 지역 색인을 받을 수 없어 채팅 지역명 해석이 "
                  "index_unavailable 로 실패한다 (22 §5-3)")
        return
    threading.Thread(target=store.prefetch, name="region-index-prefetch", daemon=True).start()
