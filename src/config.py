from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            str(PROJECT_ROOT / ".env.docker"),
            str(PROJECT_ROOT / ".env"),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg2://competence:competence@localhost:5433/competence_platform"

    hh_user_agent: str = "CompetencePlatform/1.0 (zyuzyunda@gmail.com)"
    hh_area: int = 113
    hh_request_delay_sec: float = 0.6
    hh_max_pages_per_query: int = 25
    hh_per_page: int = 50

    groq_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"


settings = Settings()
