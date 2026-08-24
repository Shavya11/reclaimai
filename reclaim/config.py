"""Single settings object. Loads .env if present; every field has a default so a
fresh clone with no .env still runs in DRY_RUN."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    autopilot_enabled: bool = True

    # Defaults to True so a clone with no credentials never makes a live call.
    dry_run: bool = True

    database_url: str = f"sqlite:///{ROOT / 'data' / 'reclaim.db'}"
    timezone: str = "Asia/Kolkata"

    seed: int = 42

    @property
    def has_razorpay(self) -> bool:
        return self.razorpay_key_id.startswith("rzp_test_") and bool(
            self.razorpay_key_secret
        )

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()
