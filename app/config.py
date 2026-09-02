"""환경변수 → 타입 있는 설정 객체. Spring 의 application.yml + @ConfigurationProperties 자리."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ai_db_dsn: str
    port: int = 8010
    internal_shared_secret: str = "dev-secret"
    gemini_api_key: str = ""

    # Gemini 모델 (2026-09-02: gemini-2.5-flash 는 신규 사용자 404 → 3.6-flash 권장)
    gemini_llm_model: str = "gemini-3.6-flash"
    gemini_embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768


settings = Settings()
