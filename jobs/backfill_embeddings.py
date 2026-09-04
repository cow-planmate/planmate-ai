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
from collections import deque

import psycopg
from pgvector.psycopg import register_vector

from app.config import settings
from app.services import gemini
from app.support.text import strip_html

# Vertex `gemini-embedding` 은 base-model 당 분당 입력토큰(TPM) 한도가 있다(기본 ~30K).
# 초과하면 429 RESOURCE_EXHAUSTED. 아래 두 겹으로 방어:
#  1) 선제 페이싱: 최근 60초 전송 토큰이 --tpm 아래로 유지되게 배치 전 sleep
#  2) 반사 대비: 그래도 429 나면 한 창(65초) 자고 같은 배치 재시도 (배치마다 커밋 → 손실 0)
_TPM_WINDOW_S = 60.0
_RETRY_SLEEP_S = 65.0
_MAX_429_RETRY = 20
_sent: deque[tuple[float, int]] = deque()   # (보낸 시각, 추정 토큰수)


def est_tokens(text: str) -> int:
    """한국어+overview 혼합의 러프 토큰 추정 (문자수/2). 과대추정 쪽이라 안전 방향."""
    return max(1, round(len(text) / 2))


def _pace(next_tokens: int, tpm: int) -> None:
    """최근 60초 전송량 + 이번 배치가 tpm 을 넘으면, 창이 빌 때까지 sleep."""
    while True:
        now = time.monotonic()
        while _sent and now - _sent[0][0] > _TPM_WINDOW_S:
            _sent.popleft()
        used = sum(t for _, t in _sent)
        if used + next_tokens <= tpm or not _sent:
            return
        wait = _TPM_WINDOW_S - (now - _sent[0][0]) + 0.5
        print(f"  [pace] 최근60s {used:,}tok + 배치 {next_tokens:,}tok > {tpm:,}TPM → {wait:.0f}s 대기")
        time.sleep(max(wait, 1.0))


def _is_429(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code == 429 or "RESOURCE_EXHAUSTED" in str(exc)

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


def _flush(conn, buf: list[tuple[str, str, str]], dry_run: bool, tpm: int = 10**9) -> int:
    if not buf:
        return 0
    texts = [b[1] for b in buf]
    batch_tok = sum(est_tokens(t) for t in texts)
    for attempt in range(_MAX_429_RETRY):
        _pace(batch_tok, tpm)
        try:
            vecs = gemini.embed_documents(texts)
            break
        except Exception as exc:  # noqa: BLE001
            if not _is_429(exc) or attempt == _MAX_429_RETRY - 1:
                raise
            print(f"  [429] TPM 초과 — {_RETRY_SLEEP_S:.0f}s 후 재시도 ({attempt + 1}/{_MAX_429_RETRY})")
            _sent.clear()
            time.sleep(_RETRY_SLEEP_S)
    _sent.append((time.monotonic(), batch_tok))
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
    ap.add_argument("--tpm", type=int, default=26000,
                    help="선제 페이싱 목표 분당 입력토큰 (Vertex gemini-embedding 기본캡 ~30K, 마진 두고 26K)")
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
                embedded += _flush(conn, buf, args.dry_run, args.tpm)
                rate = embedded / max(time.monotonic() - t0, 1e-9)
                print(f"  embedded {embedded:,} | skip(done {skipped_done:,}/empty {skipped_empty:,}) | {rate:.1f}/s")
                if args.sleep:
                    time.sleep(args.sleep)

        embedded += _flush(conn, buf, args.dry_run, args.tpm)
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
