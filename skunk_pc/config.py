from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sí", "si"}


@dataclass(frozen=True)
class Settings:
    app_name: str
    host: str
    port: int
    data_dir: Path
    upload_dir: Path
    output_dir: Path
    database_path: Path
    session_secret: str
    password_hash: str
    dev_auth_bypass: bool
    max_upload_mb: int
    job_retention_hours: int
    admin_socket: Path

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("SKUNK_DATA_DIR", "/var/lib/skunk-pc"))
        return cls(
            app_name="Skunk PC Print Server",
            host=os.getenv("SKUNK_HOST", "0.0.0.0"),
            port=int(os.getenv("SKUNK_PORT", "8081")),
            data_dir=data_dir,
            upload_dir=data_dir / "uploads",
            output_dir=data_dir / "output",
            database_path=data_dir / "skunk.db",
            session_secret=os.getenv("SKUNK_SESSION_SECRET", ""),
            password_hash=os.getenv("SKUNK_PASSWORD_HASH", ""),
            dev_auth_bypass=_as_bool(os.getenv("SKUNK_DEV_AUTH_BYPASS")),
            max_upload_mb=int(os.getenv("SKUNK_MAX_UPLOAD_MB", "30")),
            job_retention_hours=int(os.getenv("SKUNK_JOB_RETENTION_HOURS", "24")),
            admin_socket=Path(
                os.getenv("SKUNK_ADMIN_SOCKET", "/run/skunk-pc/admin.sock")
            ),
        )

    def prepare_directories(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.output_dir):
            path.mkdir(parents=True, exist_ok=True)

    def validate_runtime(self) -> None:
        if self.dev_auth_bypass:
            return
        if len(self.session_secret) < 32:
            raise RuntimeError("SKUNK_SESSION_SECRET no está configurado correctamente")
        if not self.password_hash:
            raise RuntimeError("SKUNK_PASSWORD_HASH no está configurado")


settings = Settings.from_env()
