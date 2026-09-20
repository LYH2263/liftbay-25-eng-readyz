from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    database_url: str = "postgresql+psycopg2://liftbay:liftbay@localhost:5443/liftbay"
    seed_on_empty: bool = True
    # readyz 探测整体超时（秒）：超过即返回 not_ready。
    db_ready_timeout: float = 2.0
    # 建立数据库连接阶段的超时（秒），驱动层参数。
    db_connect_timeout: int = 2


settings = Settings()
