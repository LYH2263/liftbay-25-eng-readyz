from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    database_url: str = "postgresql+psycopg2://liftbay:liftbay@localhost:5443/liftbay"
    seed_on_empty: bool = True
    # readyz 只读数据库探测的墙钟超时（秒），超时即视为未就绪
    ready_probe_timeout: float = 2.0


settings = Settings()
