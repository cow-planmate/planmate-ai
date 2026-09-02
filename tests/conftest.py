"""pytest 공용 픽스처.

DB 테스트는 로컬 `ai-tourdata-postgres` 컨테이너 + 부산(regn 26) 임베딩 subset 전제.
컨테이너 없으면 db 픽스처가 자동 skip.
"""

import pytest


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
