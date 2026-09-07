# syntax=docker/dockerfile:1

FROM python:3.12-slim
WORKDIR /app

# curl: 헬스체크용, 비루트 유저로 실행
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY main.py ./

RUN chown -R app:app /app
USER app

EXPOSE 8010
ENV PORT=8010
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fs http://localhost:${PORT}/health | grep -q '"status":"ok"' || exit 1

# uvicorn 직접 기동 — main.py 의 reload=True 는 로컬 전용
ENTRYPOINT ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
