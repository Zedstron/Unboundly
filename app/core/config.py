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
    local_embedding_generator: bool = False
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    logging_enabled: bool = True
    log_level: str = "INFO"
    log_file_path: str = None
    mcp_server_urls: str = ""
    identity_mode: str = "local"
    reply_unknown_contacts: bool = False

    @property
    def mcp_server_url_list(self) -> list[str]:
        return [ u.strip() for u in self.mcp_server_urls.split(",") if u.strip() ]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
