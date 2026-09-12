from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    redis_url: str = "redis://localhost:6379/0"
    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    embedding_base_url: str = "http://localhost:1234/v1"
    embedding_api_key: str = ""
    embedding_model: str = ""
    logging_enabled: bool = True
    log_level: str = "INFO"
    log_file_path: str = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
