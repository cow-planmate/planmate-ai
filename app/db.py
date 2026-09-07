"""AI 캐시 DB(tourdata + pgvector) 읽기전용 커넥션 풀 + 조회 헬퍼.

ORM 없음 — 생 SQL. v2 가 tourdata 에 JPA 안 쓰고 JdbcTemplate 쓴 것과 같은 판단
(wide TEXT 테이블, 관계 없음, 읽기전용 프로젝션).
"""

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

# open=False: 앱 lifespan(app/__init__.py)에서 명시적으로 pool.open()/close().
# max_size=5 = v2 의 tourdata Hikari pool 크기와 동일.
pool = ConnectionPool(settings.ai_db_dsn, min_size=1, max_size=5, open=False)


def query(sql: str, params: dict | None = None) -> list[dict]:
    """읽기전용 SELECT 실행. 결과를 dict 리스트로 반환."""
    with pool.connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()
