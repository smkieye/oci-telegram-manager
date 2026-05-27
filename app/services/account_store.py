from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.oci_config import save_uploaded_oci_config, save_uploaded_oci_key, validate_uploaded_oci_files


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    path: Path
    region: str | None = None
    created_at: str | None = None

    @property
    def config_path(self) -> Path:
        return self.path / "config"

    @property
    def key_path(self) -> Path:
        return self.path / "oci_api_key.pem"


def slugify_account_name(name: str) -> str:
    value = name.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-_") or "account"
    # Telegram callback_data has a 64-byte limit. Keep account IDs short enough
    # to be embedded in account-management callback buttons, even for CJK names.
    if len(value.encode("utf-8")) > 40:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
        while len(value.encode("utf-8")) > 31:
            value = value[:-1]
        value = f"{value}-{digest}"
    return value


def extract_region(raw_config: str) -> str | None:
    for line in raw_config.splitlines():
        if line.strip().lower().startswith("region="):
            return line.split("=", 1)[1].strip() or None
    return None


class AccountStore:
    def __init__(self, accounts_dir: Path):
        self.accounts_dir = Path(accounts_dir)
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        self.accounts_dir.chmod(0o700)

    @property
    def current_file(self) -> Path:
        return self.accounts_dir / ".current"

    def list_accounts(self) -> list[Account]:
        accounts: list[Account] = []
        for child in sorted(self.accounts_dir.iterdir()):
            if not child.is_dir():
                continue
            meta = self._read_meta(child)
            accounts.append(
                Account(
                    id=child.name,
                    name=str(meta.get("name") or child.name),
                    path=child,
                    region=meta.get("region"),
                    created_at=meta.get("created_at"),
                )
            )
        return accounts

    def get_account(self, account_id: str) -> Account:
        safe_id = Path(account_id).name
        path = self.accounts_dir / safe_id
        if not path.is_dir():
            raise ValueError(f"账号不存在：{account_id}")
        meta = self._read_meta(path)
        return Account(
            id=safe_id,
            name=str(meta.get("name") or safe_id),
            path=path,
            region=meta.get("region"),
            created_at=meta.get("created_at"),
        )

    def create_account(self, name: str, raw_config: str, raw_key: bytes) -> Account:
        base_id = slugify_account_name(name)
        account_id = self._unique_id(base_id)
        account_dir = self.accounts_dir / account_id
        account_dir.mkdir(parents=True, exist_ok=False)
        account_dir.chmod(0o700)

        container_key_path = f"/app/data/accounts/{account_id}/oci_api_key.pem"
        save_uploaded_oci_config(raw_config, account_dir, container_key_path=container_key_path)
        save_uploaded_oci_key(raw_key, account_dir)
        region = extract_region(raw_config)
        self._write_meta(
            account_dir,
            {
                "id": account_id,
                "name": name.strip(),
                "region": region,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if not self.current_file.exists():
            self.set_current(account_id)
        return self.get_account(account_id)

    def delete_account(self, account_id: str) -> None:
        account = self.get_account(account_id)
        was_current = self.get_current_id() == account.id
        for item in account.path.iterdir():
            item.unlink()
        account.path.rmdir()
        if was_current:
            accounts = self.list_accounts()
            if accounts:
                self.set_current(accounts[0].id)
            elif self.current_file.exists():
                self.current_file.unlink()

    def validate_account(self, account_id: str) -> tuple[bool, str]:
        account = self.get_account(account_id)
        return validate_uploaded_oci_files(account.path)

    def get_current_id(self) -> str | None:
        if not self.current_file.exists():
            accounts = self.list_accounts()
            return accounts[0].id if accounts else None
        value = self.current_file.read_text(encoding="utf-8").strip()
        if not value:
            return None
        try:
            self.get_account(value)
        except ValueError:
            return None
        return value

    def get_current(self) -> Account | None:
        account_id = self.get_current_id()
        return self.get_account(account_id) if account_id else None

    def set_current(self, account_id: str) -> Account:
        account = self.get_account(account_id)
        self.current_file.write_text(account.id, encoding="utf-8")
        self.current_file.chmod(0o600)
        return account

    def _unique_id(self, base_id: str) -> str:
        candidate = base_id
        index = 2
        while (self.accounts_dir / candidate).exists():
            candidate = f"{base_id}-{index}"
            index += 1
        return candidate

    def _read_meta(self, account_dir: Path) -> dict[str, object]:
        meta_path = account_dir / "meta.json"
        if not meta_path.exists():
            return {}
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _write_meta(self, account_dir: Path, data: dict[str, object]) -> None:
        meta_path = account_dir / "meta.json"
        meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_path.chmod(0o600)
