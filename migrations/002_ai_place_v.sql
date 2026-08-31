-- LLM 프롬프트 / 후보 검색이 실제로 쓰는 필드만 조인해 denormalize 한 편의 뷰.
-- 기본 테이블 스키마를 안 건드리고 뷰만 고쳐 진화시킨다.
-- 컬럼명 출처: 관광데이터 활용 공모전/project_ai/02_캐시_테이블_DDL.md (place_list §1, place_common §2)

CREATE OR REPLACE VIEW ai_place_v AS
SELECT
    l.content_id,
    l.title,
    l.addr1,
    l.mapx,
    l.mapy,
    l.ldong_regn_cd,
    l.ldong_signgu_cd,
    l.content_type_id,                 -- VARCHAR(2): '12'/'14' 관광, '32' 숙박, '39' 음식점
    l.lcls_systm1,
    l.lcls_systm2,
    l.lcls_systm3,
    c.overview,
    l.firstimage2 AS thumbnail,
    e.embedding
FROM place_list l
JOIN place_common c USING (content_id)
LEFT JOIN place_embedding e USING (content_id);   -- 임베딩 없는 장소도 노출 (구조 필터만 하는 쿼리용)
