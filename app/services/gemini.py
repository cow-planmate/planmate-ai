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
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY 미설정 (.env)")
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _is_retryable(exc: BaseException) -> bool:
    # ClientError(429) / ServerError(5xx) 만 재시도. 400/404 등은 즉시 실패.
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code not in (429, 500, 502, 503, 504):
        return False
    # 429 중 '일일 무료 쿼터 소진'(PerDay)은 재시도해도 소용없음 → 즉시 실패 (06 §6, 17 #8).
    if code == 429 and ("PerDay" in str(exc) or "RequestsPerDay" in str(exc)):
        return False
    return True


_retry = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=2, min=2, max=120),
    stop=stop_after_attempt(6),
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
