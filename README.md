# planmate-ai

PlanMate 의 AI 일정 챗봇 서버. 사용자가 채팅으로 "부산 2박3일 짜줘", "2일차 저녁 딴 데로 바꿔줘"처럼 말하면,
관광공사 tourdata 캐시 DB 에서 후보 장소를 찾아 일정을 만들거나 고친 결과를 Backend-v2 에 돌려준다.
결과를 실제 일정에 저장하는 일([반영])은 Backend-v2 가 맡는다.

```
프론트 ─ Backend-v2 ──REST(POST /api/chatbot)──▶ planmate-ai ─┬─ Gemini (Vertex AI): LLM · 임베딩
                                                              └─ AI 캐시 DB (PostgreSQL + pgvector, 읽기 전용)
```

## 처리하는 요청

| 유형 | 예시 |
|---|---|
| 일정 생성 | "부산 2박3일 알아서 짜줘" |
| 부분 재생성 | "2일차만 다시 짜줘", "3일차 오후가 비었는데 채워줘" |
| 검색 기반 편집 | "2일차 저녁을 딴 데로 바꿔줘" |
| 장소 추천 | "부산 카페 3곳 추천해줘" |
| 동선 재편성 | "동선 좋게 다시 짜줘" |
| 구조 조작 | "3일차 마지막 일정 빼줘", "2일차 점심을 1시간 당겨줘" |
| 되물음 | 지역·날짜를 알 수 없으면 되묻는다 ("우리 동네 근처로 다시 짜줘") |
| 불가·안내 | 데이터로 보장할 수 없는 조건(영업 중, 평점, 웨이팅)은 확인 안내를 붙인다 |

## 동작 방식

1. Backend-v2 가 사용자 메시지, 현재 일정(`planContext`), 사용자 정보를 보낸다.
2. 에이전트 루프(`agent.py`)가 Gemini function calling 으로 필요한 도구를 고른다.
   - `search_places` — 지역·카테고리·세부 유형 코드 필터와 pgvector 의미 검색을 한 SQL 로 실행 (`candidates.py`)
   - `generate_itinerary` — 날짜별 슬롯을 채우는 일정 생성. 슬롯마다의 취향 문장은 한 번에 배치 임베딩한다 (`itinerary.py`)
3. LLM 은 완성된 일정이 아니라 편집 명령(op: `createBlock`·`updateBlock`·`deleteBlock` 등)을 낸다.
   조립 레이어(`assembly.py`)가 명령을 적용해 최종 일정을 만들고, 시간 겹침 조정·빠진 날짜 보완처럼
   **최종 상태를 한 번에 검증**한다.
4. 응답은 `userMessage`(안내 문장) + `plan`(바뀐 일정 전체, 바뀌지 않으면 `null`) + `shownPlaces`(추천 카드).

## 구조

| 경로 | 역할 |
|---|---|
| `main.py` | 로컬 기동 진입점 (`uvicorn`) |
| `app/__init__.py` | FastAPI 앱 + DB 풀 생명주기 |
| `app/config.py` | 환경변수 로딩 (`pydantic-settings`) |
| `app/api/routes.py` | REST 엔드포인트 (`/health`, `/api/chatbot`) |
| `app/models.py` | 요청·응답 DTO, LLM 출력 스키마 |
| `app/prompts.py` | system 프롬프트와 요청 컨텍스트 조립 |
| `app/services/agent.py` | 에이전트 루프 (도구 호출 반복, 시간 제한) |
| `app/services/tools.py` | function calling 도구 등록과 디스패치 |
| `app/services/candidates.py` | `search_places` — 후보 장소 동적 SQL |
| `app/services/itinerary.py` | `generate_itinerary` 와 동선 재정렬 (LLM 없음) |
| `app/services/assembly.py` | op 적용, 최종 일정 조립·검증 |
| `app/services/gemini.py` | Gemini 클라이언트 래퍼 (LLM·임베딩·재시도) |
| `app/data/` | 카테고리·지역·세부 유형 코드 매핑 |
| `app/db.py` | AI 캐시 DB 읽기 전용 커넥션 풀 + `query()` 헬퍼 (ORM 없음) |
| `jobs/backfill_embeddings.py` | 장소 임베딩 전량 백필 |
| `migrations/` | AI 캐시 DB 추가 스키마(임베딩 테이블·뷰·인덱스)와 적용 스크립트 |

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

`/health` 의 `db: up` 은 AI 캐시 DB 가 떠 있어야 나온다. DB 없이도 앱은 기동된다.
DB 준비(덤프 복원, 추가 스키마 적용)는 [`migrations/README.md`](migrations/README.md) 참고.

## 테스트

```bash
python -m pytest                                    # 단위 테스트
PLANMATE_LIVE=1 python -m pytest tests/test_agent_live.py   # 실제 Gemini + DB (기본 skip)
```

## 배포

`.github/workflows/deploy.yml` — push 하면 테스트 후 Docker 이미지를 빌드한다.

- `test` 브랜치 → `:test` 이미지, 테스트 서버가 자동으로 따라간다.
- `main` 브랜치 → `sha-xxxxxxx` 이미지, deploy 저장소 태그를 갱신해 ArgoCD 가 운영(k3s)에 반영한다.
