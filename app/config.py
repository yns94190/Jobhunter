from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration globale, chargée depuis .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_version: str = "0.1.0"
    database_url: str = "sqlite:///./data/jobhunter.db"
    log_level: str = "INFO"

    # Connecteurs — remplis au fil des jours
    france_travail_client_id: str | None = None
    france_travail_client_secret: str | None = None
    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None
    imap_host: str | None = None
    imap_user: str | None = None
    imap_password: str | None = None
    anthropic_api_key: str | None = None
    groq_api_key: str | None = None
    llm_provider: str = "groq"  # "groq" ou "anthropic"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None


settings = Settings()