import os
from dataclasses import dataclass, field
from pathlib import Path


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    data_dir: str = os.getenv(
        "SC_DATA_DIR", str(Path(__file__).parent / "data")
    )
    max_upload_mb: int = int(os.getenv("SC_MAX_UPLOAD_MB", "50"))
    # Fetch FX / metal prices from public sources; "off" uses only app/data values.
    live_rates: bool = os.getenv("SC_LIVE_RATES", "on").strip().lower() not in ("off", "0", "false", "no")
    cors_origins: list[str] = field(
        default_factory=lambda: _csv(
            os.getenv(
                "SC_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            )
        )
    )


settings = Settings()
