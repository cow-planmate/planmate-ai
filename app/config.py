"""환경변수 → 타입 있는 설정 객체. Spring 의 application.yml + @ConfigurationProperties 자리."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ai_db_dsn: str
    port: int = 8010
    internal_shared_secret: str = "dev-secret"
    gemini_api_key: str = ""


settings = Settings()
