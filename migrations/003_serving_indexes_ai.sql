-- AI 후보 검색용 서빙 인덱스 (v2 serving_indexes_v2.sql 의 AI 타깃 버전).
-- 기본 15테이블 DDL 은 인덱스가 없다(외부 관리). 소비 패턴에 맞춰 additive 로 얹는다.
-- 멱등: 최신화 잡이 매 실행 재적용해 self-healing.

-- 구조 pre-filter: 지역 + 콘텐츠 타입 (후보 검색 WHERE 절)
CREATE INDEX IF NOT EXISTS ix_place_list_regn_type
    ON place_list (ldong_regn_cd, ldong_signgu_cd, content_type_id);

-- 선호테마 랭킹 (lclsSystm 매치 여부)
CREATE INDEX IF NOT EXISTS ix_place_list_lcls
    ON place_list (lcls_systm1, lcls_systm2, lcls_systm3);

-- 1:N 조인 (Phase E 에서 상세 필드 필요 시)
CREATE INDEX IF NOT EXISTS ix_place_image_cid
    ON place_image (content_id);
