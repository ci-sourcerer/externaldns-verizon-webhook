from typing import Any

from externaldns_verizon_webhook.app import WEBHOOK_MEDIA_TYPE, flask_app


class FakeProvider:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.created: list[tuple[str, str]] = []
        self.listed: list[dict[str, Any]] = [
            {"dnsName": "example.com", "recordType": "A", "targets": ["203.0.113.10"]}
        ]

    def list_records(self) -> list[dict[str, Any]]:
        return self.listed

    def create_record(self, dns_name: str, ip: str) -> None:
        self.created.append((dns_name, ip))

    def delete_record(self, dns_name: str) -> None:
        self.deleted.append(dns_name)


def _client_with_provider() -> tuple[Any, FakeProvider]:
    provider = FakeProvider()
    original_provider = flask_app.config.get("VERIZON_PROVIDER")
    original_dns_suffix = flask_app.config.get("VERIZON_DNS_SUFFIX")
    original_filters = flask_app.config.get("VERIZON_DOMAIN_FILTERS")

    flask_app.config["TESTING"] = True
    flask_app.config["VERIZON_PROVIDER"] = provider
    flask_app.config["VERIZON_DNS_SUFFIX"] = ".example.com"
    flask_app.config["VERIZON_DOMAIN_FILTERS"] = ["example.com"]

    try:
        client = flask_app.test_client()
    finally:
        flask_app.config["VERIZON_PROVIDER"] = original_provider
        flask_app.config["VERIZON_DNS_SUFFIX"] = original_dns_suffix
        flask_app.config["VERIZON_DOMAIN_FILTERS"] = original_filters

    flask_app.config["VERIZON_PROVIDER"] = provider
    flask_app.config["VERIZON_DNS_SUFFIX"] = ".example.com"
    flask_app.config["VERIZON_DOMAIN_FILTERS"] = ["example.com"]

    return client, provider


def test_negotiate_response_includes_domain_filters() -> None:
    client, _ = _client_with_provider()

    response = client.get(
        "/",
        headers={
            "Accept": WEBHOOK_MEDIA_TYPE,
            "Content-Type": WEBHOOK_MEDIA_TYPE,
        },
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == WEBHOOK_MEDIA_TYPE
    assert response.get_json() == {"filters": ["example.com"]}


def test_list_records_returns_provider_data() -> None:
    client, provider = _client_with_provider()

    response = client.get(
        "/records",
        headers={
            "Accept": WEBHOOK_MEDIA_TYPE,
            "Content-Type": WEBHOOK_MEDIA_TYPE,
        },
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == WEBHOOK_MEDIA_TYPE
    assert response.get_json() == provider.listed


def test_adjust_records_adds_dns_suffix() -> None:
    client, _ = _client_with_provider()

    response = client.post(
        "/adjustendpoints",
        json=[
            {"dnsName": "api"},
            {"dnsName": "db.example.com"},
            {"dnsName": "ignored"},
        ],
        headers={
            "Accept": WEBHOOK_MEDIA_TYPE,
            "Content-Type": WEBHOOK_MEDIA_TYPE,
        },
    )

    assert response.status_code == 200
    assert response.get_json() == [
        {"dnsName": "api.example.com"},
        {"dnsName": "db.example.com"},
        {"dnsName": "ignored.example.com"},
    ]


def test_apply_records_calls_provider_for_create_delete_and_update() -> None:
    client, provider = _client_with_provider()

    response = client.post(
        "/records",
        json={
            "create": [{"dnsName": "new", "targets": ["198.51.100.3"]}],
            "delete": [{"dnsName": "old.example.com"}],
            "updateOld": [{"dnsName": "old-update.example.com"}],
            "updateNew": [{"dnsName": "updated", "targets": ["198.51.100.9"]}],
        },
        headers={
            "Accept": WEBHOOK_MEDIA_TYPE,
            "Content-Type": WEBHOOK_MEDIA_TYPE,
        },
    )

    assert response.status_code == 204
    assert provider.deleted == ["old.example.com", "old-update.example.com"]
    assert provider.created == [
        ("new", "198.51.100.3"),
        ("updated", "198.51.100.9"),
    ]
