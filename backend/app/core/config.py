from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "CapEval"
    env: str = "dev"

    # MySQL, e.g. mysql+pymysql://root:password@127.0.0.1:3306/capeval?charset=utf8mb4
    database_url: str = "mysql+pymysql://root:password@127.0.0.1:3306/capeval?charset=utf8mb4"

    # 凭证加解密密钥(Fernet),生产环境必须通过环境变量注入
    credential_key: str = ""

    # 冒烟校验超时(秒)
    smoke_timeout_seconds: int = 30

    # 数据保留(PRD 7.4):原始轨迹事件保留天数,到期清理时保留证据链片段;报告永久保留
    trace_retention_days: int = 180

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
