"""환경변수 → 타입 있는 설정 객체. Spring 의 application.yml + @ConfigurationProperties 자리."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ai_db_dsn: str
    port: int = 8010
    internal_shared_secret: str = "dev-secret"

    # Vertex AI (google-genai, ADC 인증 — 로컬 `gcloud auth application-default login` / VM 서비스계정).
    # AI Studio API 키 방식 폐기 (무료 20 req/day 병목, `18` 전환). project 미설정 시 기동 실패.
    google_cloud_project: str = ""
    google_cloud_location: str = "global"

    # Gemini 모델 (`18` §3: gemini-3.6-flash / gemini-embedding-001 Vertex 200 확인)
    gemini_llm_model: str = "gemini-3.6-flash"
    gemini_embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768


settings = Settings()
