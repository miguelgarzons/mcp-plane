from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


class CredentialStore:
    def __init__(self, file_path: Path, encryption_key: str) -> None:
        self.file_path = file_path
        self.fernet = Fernet(encryption_key.encode("utf-8"))

    def _ensure_storage(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.file_path.write_text("{}", encoding="utf-8")

    def _load_raw(self) -> dict[str, Any]:
        self._ensure_storage()
        raw = self.file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("credentials store must contain an object")
        return data

    def _save_raw(self, data: dict[str, Any]) -> None:
        self._ensure_storage()
        self.file_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")

    def upsert_plane_credentials(
        self,
        user_id: str,
        base_url: str,
        workspace_slug: str,
        project_id: str,
        api_token: str,
    ) -> dict[str, str]:
        cleaned_user_id = user_id.strip()
        if not cleaned_user_id:
            raise ValueError("user_id is required")

        data = self._load_raw()
        encrypted = self.fernet.encrypt(api_token.strip().encode("utf-8")).decode("utf-8")
        data[cleaned_user_id] = {
            "base_url": base_url.strip(),
            "workspace_slug": workspace_slug.strip(),
            "project_id": project_id.strip(),
            "api_token_encrypted": encrypted,
        }
        self._save_raw(data)

        return {
            "user_id": cleaned_user_id,
            "base_url": base_url.strip(),
            "workspace_slug": workspace_slug.strip(),
            "project_id": project_id.strip(),
        }

    def get_plane_credentials(self, user_id: str) -> dict[str, str] | None:
        cleaned_user_id = user_id.strip()
        data = self._load_raw()
        entry = data.get(cleaned_user_id)
        if not isinstance(entry, dict):
            return None

        encrypted = str(entry.get("api_token_encrypted", ""))
        if not encrypted:
            return None

        decrypted = self.fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")
        return {
            "base_url": str(entry.get("base_url", "")).strip(),
            "workspace_slug": str(entry.get("workspace_slug", "")).strip(),
            "project_id": str(entry.get("project_id", "")).strip(),
            "api_token": decrypted,
        }

    def delete_plane_credentials(self, user_id: str) -> bool:
        cleaned_user_id = user_id.strip()
        data = self._load_raw()
        existed = cleaned_user_id in data
        if existed:
            del data[cleaned_user_id]
            self._save_raw(data)
        return existed

    def list_users(self) -> list[str]:
        data = self._load_raw()
        return sorted(data.keys())
