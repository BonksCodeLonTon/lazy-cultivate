from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    discord_token: str
    discord_guild_id: int = 0

    # PostgreSQL connection components (avoids URL special-char issues)
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "isoma"
    db_password: str = "isoma@2026!@#"
    db_name: str = "cultivation_db"

    debug: bool = False
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        password = quote_plus(self.db_password)
        return (
            f"postgresql+asyncpg://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()
