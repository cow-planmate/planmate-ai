"""로컬 개발 기동 진입점: python main.py

운영에서는 uvicorn/gunicorn 으로 직접 app:app 을 띄운다 (배포 단계에서 확정).
"""

import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=settings.port, reload=True)
