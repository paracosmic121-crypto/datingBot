"""
Central configuration, loaded from environment variables (.env).
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # --- Telegram ---
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    REQUEST_TIMEOUT: float = float(os.getenv("REQUEST_TIMEOUT", "30.0"))
    PROXY_URL: str | None = os.getenv("PROXY_URL") or os.getenv("TELEGRAM_PROXY_URL") or None

    # --- MongoDB ---
    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "dating_bot")

    # --- FastAPI / Admin ---
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "change-me")

    # --- Bot behaviour & Monetization ---
    MIN_AGE: int = 18
    MAX_AGE: int = 99
    FREE_LIKES_PER_DAY: int = int(os.getenv("FREE_LIKES_PER_DAY", "20"))
    FREE_VIEWS_PER_HOUR: int = int(os.getenv("FREE_VIEWS_PER_HOUR", "10"))
    PREMIUM_15D_STARS: int = int(os.getenv("PREMIUM_15D_STARS", "100"))  # 15 days
    PREMIUM_30D_STARS: int = int(os.getenv("PREMIUM_30D_STARS", "200"))  # 30 days

    def validate(self) -> None:
        missing = []
        if not self.BOT_TOKEN:
            missing.append("BOT_TOKEN")
        if not self.MONGO_URI:
            missing.append("MONGO_URI")
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill them in."
            )


settings = Settings()
