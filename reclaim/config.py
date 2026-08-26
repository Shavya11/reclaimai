"""Single settings object. Loads .env if present; every field has a default so a
fresh clone with no .env still runs in DRY_RUN."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# Not a secret and not pretending to be one. It exists so the webhook
# signature path is real in DRY_RUN rather than bypassed.
LOCAL_WEBHOOK_SECRET = "reclaim_local_dev_secret"


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

    # A hosted container starts with an empty disk, and an empty disk serves an
    # empty dashboard. On means: if the database has no records at boot, run the
    # batch once so the deployment is never a blank page. Off is correct
    # locally, where `cli demo` owns that decision.
    seed_on_boot: bool = False

    @property
    def webhook_secret(self) -> str:
        """The secret webhook signatures are verified against.

        Live secret when one is configured. Otherwise — and ONLY in DRY_RUN —
        a documented local constant, so a clone with no Razorpay account still
        exercises the real signing and verification path instead of a branch
        that skips it. Outside DRY_RUN a missing secret returns "", and every
        delivery fails verification. Fail closed.
        """
        if self.razorpay_webhook_secret:
            return self.razorpay_webhook_secret
        return LOCAL_WEBHOOK_SECRET if self.dry_run else ""

    @property
    def has_razorpay(self) -> bool:
        return self.razorpay_key_id.startswith("rzp_test_") and bool(
            self.razorpay_key_secret
        )

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()
