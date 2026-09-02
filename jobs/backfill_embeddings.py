"""place_embedding 최초 전량 백필.

    python -m jobs.backfill_embeddings [--batch-size N] [--limit N] [--sleep S] [--dry-run]

- `place_embedding` 테이블 자체가 체크포인트 — 재실행 시 이미 임베딩된(동일 source_hash) 행 skip.
- 배치마다 커밋 → 크래시 시 마지막 배치만 손실.
- 증분(Phase H)도 이 로직 재사용 (대상 SELECT 만 "modifiedtime 변경분" 으로 좁힘).

Q3: 임베딩 입력 = title + "\\n" + overview(HTML 스트립). overview 없으면 title 만.
"""

from __future__ import annotations

import argparse
import hashlib
import time

import psycopg
from pgvector.psycopg import register_vector

from app.config import settings
from app.services import gemini
from app.support.text import strip_html

UPSERT = """
INSERT INTO place_embedding (content_id, embedding, model, source_hash, updated_at)
VALUES (%s, %s, %s, %s, now())
ON CONFLICT (content_id) DO UPDATE
  SET embedding = EXCLUDED.embedding,
      model = EXCLUDED.model,
      source_hash = EXCLUDED.source_hash,
      updated_at = now()
"""


def build_input(title: str | None, overview: str | None) -> str:
    ov = strip_html(overview)
    t = (title or "").strip()
    return f"{t}\n{ov}".strip() if ov else t


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _flush(conn, buf: list[tuple[str, str, str]], dry_run: bool) -> int:
    if not buf:
        return 0
    vecs = gemini.embed_documents([b[1] for b in buf])
    if not dry_run:
        with conn.cursor() as cur:
            cur.executemany(
                UPSERT,
                [(cid, vec, settings.gemini_embed_model, sh)
                 for (cid, _txt, sh), vec in zip(buf, vecs)],
            )
        conn.commit()
    n = len(buf)
    buf.clear()
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--limit", type=int, default=0, help="N건만 스캔 (테스트)")
    ap.add_argument("--regn", default="", help="ldong_regn_cd 쉼표목록으로 지역 한정 (예: 26)")
    ap.add_argument("--sleep", type=float, default=0.0, help="배치 사이 sleep(초)")
    ap.add_argument("--dry-run", action="store_true", help="임베딩 호출만, DB write 안 함")
    args = ap.parse_args()

    conn = psycopg.connect(settings.ai_db_dsn, autocommit=False)
    register_vector(conn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT content_id, source_hash FROM place_embedding")
            done = {cid: sh for cid, sh in cur.fetchall()}
        print(f"기존 임베딩 {len(done):,}건")

        # 추천 스코프 타입만 임베딩 (관광지 12·14 / 숙박 32 / 음식점 39).
        # search_places 가 조회하는 대상 = 이 4종뿐 → 나머지(쇼핑·축제·코스…)는 벡터 불필요.
        q = ("SELECT l.content_id, l.title, c.overview "
             "FROM place_list l JOIN place_common c USING (content_id) "
             "WHERE l.content_type_id IN ('12','14','32','39')")
        params: list = []
        if args.regn:
            regns = [r.strip() for r in args.regn.split(",") if r.strip()]
            q += " AND l.ldong_regn_cd = ANY(%s)"
            params.append(regns)
        q += " ORDER BY l.content_id"
        if args.limit:
            q += f" LIMIT {int(args.limit)}"
        with conn.cursor() as cur:
            cur.execute(q, params or None)
            rows = cur.fetchall()
        print(f"스캔 대상 {len(rows):,}건")

        buf: list[tuple[str, str, str]] = []
        embedded = skipped_done = skipped_empty = 0
        t0 = time.monotonic()

        for cid, title, overview in rows:
            text = build_input(title, overview)
            if not text:
                skipped_empty += 1
                continue
            h = source_hash(text)
            if done.get(cid) == h:
                skipped_done += 1
                continue
            buf.append((cid, text, h))
            if len(buf) >= args.batch_size:
                embedded += _flush(conn, buf, args.dry_run)
                rate = embedded / max(time.monotonic() - t0, 1e-9)
                print(f"  embedded {embedded:,} | skip(done {skipped_done:,}/empty {skipped_empty:,}) | {rate:.1f}/s")
                if args.sleep:
                    time.sleep(args.sleep)

        embedded += _flush(conn, buf, args.dry_run)
        dt = time.monotonic() - t0
        print(f"완료: embedded={embedded:,} skipped_done={skipped_done:,} "
              f"skipped_empty={skipped_empty:,} scanned={len(rows):,} ({dt:.0f}s)")
        if not args.dry_run:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM place_embedding")
                print(f"place_embedding 총 {cur.fetchone()[0]:,}행")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
