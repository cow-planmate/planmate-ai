"""Gemini 클라이언트 래퍼 — `google-genai` 를 감싸는 유일한 모듈.

다른 코드는 `embed_documents` / `embed_query` / `generate` 만 부른다.
모델명·차원·재시도·task_type 은 전부 여기 캡슐화 (Java 의 인프라 어댑터 자리).
"""

from __future__ import annotations

import math

from google import genai
from google.genai import types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.google_cloud_project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT 미설정 (.env) — Vertex ADC 인증 필요")
        _client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
    return _client


def _is_retryable(exc: BaseException) -> bool:
    # Vertex AI (`18` 전환): 429 = RPM/TPM 스로틀(몇 초면 회복) → 짧게 재시도.
    # 5xx = 일시 서버 오류 → 재시도. 400/404 = 즉시 실패.
    # (AI Studio 무료의 일일쿼터 429 fast-fail = 폐기, `17` C-9)
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code in (429, 500, 502, 503, 504)


_retry = retry(
    retry=retry_if_exception(_is_retryable),
    # 짧은 백오프: 1→2→4초, 재시도 2회. 총 대기 ~3초 << 에이전트 데드라인 30초.
    # 지속 스로틀(2회로 안 풀림)이면 상위로 던져 502 → v2 판단. 정밀값은 E-5 튜닝(`17` #3).
    wait=wait_exponential(multiplier=1, min=1, max=4),
    stop=stop_after_attempt(3),
    reraise=True,
)


def _l2_normalize(vec: list[float]) -> list[float]:
    # gemini-embedding-001 은 3072 미만 차원이면 자동 정규화 안 됨 → 직접 L2 정규화 (Google 권장)
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


@_retry
def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    resp = _get_client().models.embed_content(
        model=settings.gemini_embed_model,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=settings.embed_dim,
        ),
    )
    return [_l2_normalize(list(e.values)) for e in resp.embeddings]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """색인용 임베딩 (장소 텍스트). 배치."""
    if not texts:
        return []
    return _embed(texts, "RETRIEVAL_DOCUMENT")


def embed_query(text: str) -> list[float]:
    """검색용 임베딩 (사용자 요청문). 단건."""
    return _embed([text], "RETRIEVAL_QUERY")[0]


@_retry
def generate(contents, *, config: types.GenerateContentConfig | None = None):
    """LLM 호출 (Phase E). 얇은 패스스루."""
    return _get_client().models.generate_content(
        model=settings.gemini_llm_model,
        contents=contents,
        config=config,
    )
