"""pytest 공용 픽스처.

DB 테스트는 로컬 `ai-tourdata-postgres` 컨테이너 + 부산(regn 26) 임베딩 subset 전제.
컨테이너 없으면 db 픽스처가 자동 skip.
"""

import os

import pytest


@pytest.fixture(scope="session")
def live(db):
    """실 Gemini(Vertex) 호출 테스트 게이트. `PLANMATE_LIVE=1` 없으면 skip."""
    if os.environ.get("PLANMATE_LIVE") != "1":
        pytest.skip("PLANMATE_LIVE=1 아님 — 실 Gemini 테스트 skip")
    from app.config import settings

    if not settings.google_cloud_project:
        pytest.skip("GOOGLE_CLOUD_PROJECT 없음 — Vertex ADC 설정 필요 (18 §2-3)")
    try:
        import google.auth

        google.auth.default()   # ADC 자격증명 존재/유효 확인
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ADC 없음 — gcloud auth application-default login 필요 ({e})")


@pytest.fixture(scope="session")
def db():
    from app.db import pool

    try:
        pool.open()
        from app.db import query

        query("SELECT 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"AI 캐시 DB 없음: {e}")
    yield
    pool.close()
