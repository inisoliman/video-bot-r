from pydantic_settings import BaseSettings
from pydantic import AnyUrl, Field

class Settings(BaseSettings):
    BOT_TOKEN: str
    DATABASE_URL: AnyUrl
    CHANNEL_ID: int
    ADMIN_IDS: str
    APP_URL: AnyUrl
    WEBHOOK_SECRET: str = Field(default="")
    PORT: int = Field(default=10000)
    DB_POOL_MIN: int = Field(default=2)
    DB_POOL_MAX: int = Field(default=20)
    LOG_LEVEL: str = Field(default="INFO")

    @property
    def admin_ids(self):
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip()]

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
