"""AI 캐시 DB 에 additive 마이그레이션(001~) 을 순서대로 적용.

    python migrations/apply.py

Flyway/Alembic 는 안 쓴다 — 기본 15테이블 스키마는 tourdata.dump + 최신화 잡이
소유하고, 여기서 관리하는 건 AI 전용 파일 몇 개뿐. 모든 파일은 멱등(IF NOT EXISTS /
CREATE OR REPLACE)이라 이력 테이블 없이 매번 통째로 재적용해도 안전하다.
"""

import pathlib
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent


def main() -> None:
    files = sorted(MIGRATIONS_DIR.glob("[0-9]*.sql"))
    if not files:
        sys.exit("적용할 .sql 파일이 없습니다.")

    with psycopg.connect(settings.ai_db_dsn, autocommit=True) as conn:
        for f in files:
            print(f"applying {f.name} ...")
            conn.execute(f.read_text(encoding="utf-8"))
    print(f"done ({len(files)} files)")


if __name__ == "__main__":
    main()
