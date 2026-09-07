# planmate-ai

PlanMate AI 서버 — 요청문 임베딩 + tourdata 벡터 유사도 검색 + LLM 장소 선택·일정 구성.
Backend-v2 와 REST/JSON 으로 통신한다.

## 로컬 개발

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (mac/linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env           # 값 확인
python main.py
```

- Swagger: http://localhost:8010/docs
- 헬스체크: http://localhost:8010/health

`/health` 의 `db: up` 은 AI 캐시 DB(`docker-compose.local.yml` 의 `tourdata-postgres`)가
떠 있어야 나온다. DB 없이도 앱은 기동된다.

## 구조

| 경로 | 역할 |
|---|---|
| `main.py` | 로컬 기동 진입점 (`uvicorn`) |
| `app/config.py` | 환경변수 로딩 (`pydantic-settings`) |
| `app/db.py` | AI 캐시 DB 읽기전용 커넥션 풀 + `query()` 헬퍼 (ORM 없음) |
| `app/api/routes.py` | REST 엔드포인트 |
| `app/__init__.py` | FastAPI 앱 + DB 풀 생명주기 |

설계 문서는 `project_ai/` (git 미추적).
