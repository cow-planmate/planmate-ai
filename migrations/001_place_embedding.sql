-- AI 전용 additive 스키마: 장소 임베딩 저장소.
-- 기본 15개 테이블(place_list 등)은 tourdata.dump 복원이 만든다. 이 파일은 그 위에 얹는다.
-- 멱등: 여러 번 실행해도 안전 (최신화 잡도 매 실행 재적용 예정).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS place_embedding (
    content_id   VARCHAR(20) PRIMARY KEY,        -- place_list.content_id (FK 제약 안 검 — 기본 DDL 원칙)
    embedding    vector(768),                    -- gemini-embedding-001, 768차원 고정 (D3/D4)
    model        TEXT        NOT NULL,           -- 예: 'gemini-embedding-001' (모델 교체 추적)
    source_hash  TEXT        NOT NULL,           -- hash(임베딩 입력 텍스트) — 입력 구성은 Q3(미정)
    updated_at   TIMESTAMP   NOT NULL DEFAULT now()
);

-- 유사도 검색용 ANN 인덱스 (cosine). 빈 테이블에 걸면 즉시.
-- 백필 대량 insert 속도가 문제되면 Phase C 에서 "백필 후 생성"으로 옮길 수 있음.
CREATE INDEX IF NOT EXISTS idx_place_embedding_hnsw
    ON place_embedding USING hnsw (embedding vector_cosine_ops);
