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

    # hh.ru API (https://dev.hh.ru) — токен приложения для легального парсинга
    hh_user_agent: str = "SkillVerse/1.0 (contact@example.com)"
    hh_client_id: str = ""
    hh_client_secret: str = ""
    hh_access_token: str = ""  # опционально: готовый app token из кабинета
    hh_area: int = 113
    hh_request_delay_sec: float = 0.6
    hh_max_pages_per_query: int = 20  # API: max ~2000 = 20×100
    hh_per_page: int = 100  # API max 100
    hh_date_from: str = ""  # ISO date, напр. 2025-01-01

    groq_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    # auto | groq | ollama | openai — auto: Groq → при 403 локальная Ollama
    llm_provider: str = "auto"
    # OpenAI-compatible (OpenRouter и т.п.)
    llm_api_key: str = ""
    llm_base_url: str = ""
    # локальная Llama через Ollama (без VPN)
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"


settings = Settings()
