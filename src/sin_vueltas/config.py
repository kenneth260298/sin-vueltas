from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql://app:app@localhost:5432/sinvueltas"
    # Where downloaded originals are stored locally (ignored by Git).
    raw_storage_dir: Path = Path("data/raw")


settings = Settings()