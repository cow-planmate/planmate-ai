# migrations — AI 캐시 DB 로컬 셋업 런북

AI 캐시 DB = `tourdata`(공공데이터 캐시, 15테이블) + AI 전용 additive(`place_embedding`,
`ai_place_v`, 서빙 인덱스). 기본 15테이블은 덤프 복원이 만들고, additive 는 이 폴더의
`*.sql` 을 `apply.py` 가 얹는다.

`docker-compose.local.yml` 은 gitignore(개인 로컬). 아래는 그 파일이 있다는 전제.

## 1. 컨테이너 기동

```bash
docker compose -f docker-compose.local.yml up -d
# healthy 될 때까지 대기
docker compose -f docker-compose.local.yml ps
```

빈 `tourdata` DB 가 host `localhost:5434` 에 뜬다.

## 2. 덤프 복원 (1회성 수동)

덤프 원본: `../관광데이터 활용 공모전/데이터/tourdata.dump` (약 23MB, `pg_dump 17.5` custom 포맷).
저장소에는 넣지 않는다(데이터, 코드 아님).

```bash
docker cp "../관광데이터 활용 공모전/데이터/tourdata.dump" ai-tourdata-postgres:/tmp/t.dump
docker exec ai-tourdata-postgres \
  pg_restore --no-owner --no-privileges -U tourdata -d tourdata /tmp/t.dump
```

- `--no-owner --no-privileges`: 덤프 안의 롤/권한 무시. "role ... does not exist" 경고는 무해.
- 복원 데이터는 도커 볼륨 `ai-tourdata-pgdata` 에 남아 컨테이너 재시작해도 유지.

> 대안(자동): `docker-compose.local.yml` 에서 덤프 + 복원 스크립트를 컨테이너의
> `/docker-entrypoint-initdb.d/` 로 마운트하면 `up` 시 자동 복원(빈 볼륨 최초 1회만).
> 덤프가 저장소 밖이라 어차피 파일을 구해와야 해서 여기선 수동 2줄을 기본으로 둔다.

### 복원 검증

```bash
docker exec ai-tourdata-postgres psql -U tourdata -d tourdata -c \
  "SELECT count(*) FROM place_list;"                     # 49,824 기대
docker exec ai-tourdata-postgres psql -U tourdata -d tourdata -c \
  "SELECT count(*) FROM place_collect_status;"           # 49,824, _ok 컬럼 확인
```

## 3. AI 전용 마이그레이션 적용

```bash
# 프로젝트 루트에서, venv 활성화 상태
python migrations/apply.py
```

`001_place_embedding.sql` → `002_ai_place_v.sql` → `003_serving_indexes_ai.sql` 순서로 적용.
멱등이라 여러 번 돌려도 됨.

### 검증

```bash
docker exec ai-tourdata-postgres psql -U tourdata -d tourdata -c "\d place_embedding"
docker exec ai-tourdata-postgres psql -U tourdata -d tourdata -c "\dv ai_place_v"
docker exec ai-tourdata-postgres psql -U tourdata -d tourdata -c "\di ix_place_* idx_place_embedding_hnsw"
docker exec ai-tourdata-postgres psql -U tourdata -d tourdata -c "SELECT count(*) FROM ai_place_v;"   # 49,824 (LEFT JOIN 이라 임베딩 0이어도 전건)
```

## 4. 앱에서 확인

```bash
python main.py
curl http://localhost:8010/health      # {"status":"ok","db":"up"}
```

## 파일

| 파일 | 내용 | 커밋 |
|---|---|---|
| `001_place_embedding.sql` | `vector(768)` 테이블 + HNSW 인덱스 (빈 채로 시작, Phase C 백필) | ✅ |
| `002_ai_place_v.sql` | `place_list` + `place_common` + `place_embedding` 조인 뷰 | ✅ |
| `003_serving_indexes_ai.sql` | 구조 pre-filter 인덱스 3개 | ✅ |
| `apply.py` | 위 `*.sql` 순서 적용 러너 | ✅ |
| `../docker-compose.local.yml` | 로컬 pgvector 컨테이너 | ✗ gitignore |
