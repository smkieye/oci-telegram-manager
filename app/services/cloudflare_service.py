from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class CloudflareDNSRecord:
    id: str
    name: str
    type: str
    content: str


class CloudflareService:
    def __init__(self, api_token: str, zone_id: str, session: requests.Session | None = None):
        self.api_token = api_token
        self.zone_id = zone_id
        self.session = session or requests.Session()
        self.base_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def list_records(self, name: str | None = None, record_type: str | None = None) -> list[CloudflareDNSRecord]:
        params: dict[str, str] = {}
        if name:
            params["name"] = name
        if record_type:
            params["type"] = record_type
        response = self.session.get(
            f"{self.base_url}/dns_records",
            headers=self.headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError(f"Cloudflare API failed: {payload}")
        return [
            CloudflareDNSRecord(
                id=item["id"],
                name=item["name"],
                type=item["type"],
                content=item["content"],
            )
            for item in payload.get("result", [])
        ]

    def upsert_record(
        self,
        name: str,
        content: str,
        record_type: str = "A",
        ttl: int = 120,
        proxied: bool = False,
        existing: CloudflareDNSRecord | None = None,
    ) -> dict[str, Any]:
        record = existing
        if record is None:
            matches = self.list_records(name=name, record_type=record_type)
            record = matches[0] if matches else None

        payload = {
            "id": record.id if record else None,
            "type": record_type,
            "name": name,
            "content": content,
            "ttl": ttl,
            "proxied": proxied,
        }
        if record:
            response = self.session.put(
                f"{self.base_url}/dns_records/{record.id}",
                headers=self.headers,
                json=payload,
                timeout=20,
            )
        else:
            payload.pop("id")
            response = self.session.post(
                f"{self.base_url}/dns_records",
                headers=self.headers,
                json=payload,
                timeout=20,
            )
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare API failed: {data}")
        return data["result"]
