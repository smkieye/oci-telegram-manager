from app.services.cloudflare_service import CloudflareDNSRecord, CloudflareService


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")


class FakeSession:
    def __init__(self):
        self.calls = []

    def put(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("PUT", url, headers, json, timeout))
        return FakeResponse({"success": True, "result": {"id": json["id"], "content": json["content"]}})

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, headers, params, timeout))
        return FakeResponse({"success": True, "result": []})

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, headers, json, timeout))
        return FakeResponse({"success": True, "result": {"id": "new-id", "content": json["content"]}})


def test_cloudflare_upsert_updates_existing_record():
    session = FakeSession()
    service = CloudflareService("token", "zone", session=session)
    existing = CloudflareDNSRecord(id="record-id", name="node.example.com", type="A", content="1.1.1.1")

    result = service.upsert_record("node.example.com", "8.8.8.8", "A", existing=existing)

    assert result["id"] == "record-id"
    method, url, headers, payload, timeout = session.calls[0]
    assert method == "PUT"
    assert url.endswith("/zones/zone/dns_records/record-id")
    assert headers["Authorization"] == "Bearer token"
    assert payload["content"] == "8.8.8.8"
    assert payload["proxied"] is False


def test_cloudflare_upsert_creates_missing_record():
    session = FakeSession()
    service = CloudflareService("token", "zone", session=session)

    result = service.upsert_record("node.example.com", "8.8.4.4", "A")

    assert result["id"] == "new-id"
    assert session.calls[0][0] == "GET"
    assert session.calls[1][0] == "POST"
    assert session.calls[1][3]["name"] == "node.example.com"
