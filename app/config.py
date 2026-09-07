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

    # 최종 op 출력 모드 (`17` E5-1 실측): json = 자유 JSON + 우리 검증 (기본, 9/9) /
    #   schema = response_schema 제약 (updateBlock optional 필드 스킵 버그, 7/9) /
    #   function = submit_plan 함수호출 (9/9, SUBMIT_PLAN 미러 유지비용). schema/function 은 폴백 스위치.
    final_output_mode: str = "json"


settings = Settings()
