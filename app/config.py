from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def parse_allowed_user_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    values: set[int] = set()
    for part in raw.replace(",", " ").split():
        part = part.strip()
        if not part:
            continue
        values.add(int(part))
    return values


def mask_secret(value: str | None, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}{'*' * 8}{value[-visible:]}"


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_user_ids: set[int]
    data_dir: Path
    database_path: Path
    oci_config_path: Path
    oci_key_path: Path
    accounts_dir: Path
    cloudflare_api_token: str | None = None
    cloudflare_zone_id: str | None = None
    default_cloudflare_record: str | None = None
    web_admin_password: str | None = None
    web_session_secret: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is required")

        allowed_user_ids = parse_allowed_user_ids(os.getenv("ALLOWED_USER_IDS"))
        if not allowed_user_ids:
            raise RuntimeError("ALLOWED_USER_IDS is required")

        data_dir = Path(os.getenv("DATA_DIR", "/app/data")).expanduser()
        oci_dir = data_dir / "oci"
        return cls(
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            data_dir=data_dir,
            database_path=data_dir / "oci_manager.sqlite3",
            oci_config_path=oci_dir / "config",
            oci_key_path=oci_dir / "oci_api_key.pem",
            accounts_dir=data_dir / "accounts",
            cloudflare_api_token=os.getenv("CLOUDFLARE_API_TOKEN") or None,
            cloudflare_zone_id=os.getenv("CLOUDFLARE_ZONE_ID") or None,
            default_cloudflare_record=os.getenv("DEFAULT_CLOUDFLARE_RECORD") or None,
            web_admin_password=os.getenv("WEB_ADMIN_PASSWORD") or None,
            web_session_secret=os.getenv("WEB_SESSION_SECRET") or bot_token,
        )

    @property
    def cloudflare_enabled(self) -> bool:
        return bool(self.cloudflare_api_token and self.cloudflare_zone_id)
