#!/usr/bin/env python3

"""ExternalDNS webhook provider for Verizon Fios Router (CR1000A/CR1000B).

This service implements the ExternalDNS webhook provider API and translates
ExternalDNS record operations into DNS entries managed by a Verizon Fios router
(using the `verizon_router_client` library).
"""

import argparse
import functools
import logging
import os
from collections.abc import Callable
from typing import Any, cast

import uvicorn
from asgiref.wsgi import WsgiToAsgi
from flask import (
    Flask,
    Request,
    Response,
    abort,
    current_app,
    jsonify,
    make_response,
    request,
)
from verizon_router_client.cr1000a import VerizonRouterClient

WEBHOOK_MEDIA_TYPE = "application/external.dns.webhook+json;version=1"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8888

LOGGER = logging.getLogger("verizon-externaldns")


def _validate_media_type(req: Request) -> None:
    accept = req.headers.get("Accept", "")
    content_type = req.headers.get("Content-Type", "")
    if WEBHOOK_MEDIA_TYPE not in accept and WEBHOOK_MEDIA_TYPE not in content_type:
        abort(
            make_response(
                f"Accept or Content-Type header must include {WEBHOOK_MEDIA_TYPE}", 406
            )
        )


def ensure_media_type(func: Callable[..., Response]) -> Callable[..., Response]:
    """Ensure the decorated request has the correct media type.

    Args:
        func: The function to decorate.

    Returns:
        The decorated function.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _validate_media_type(request)
        return func(*args, **kwargs)

    return wrapper


def _make_response(body: Any, status: int = 200) -> Response:
    resp = make_response(jsonify(body), status)
    resp.headers["Content-Type"] = WEBHOOK_MEDIA_TYPE
    return resp


def _ensure_suffix(name: str, suffix: str) -> str:
    if name.endswith(suffix):
        return name
    return f"{name}{suffix}"


def _normalize_dns_suffix(suffix: str) -> str:
    return f".{suffix.strip().strip('.')}"


def _normalize_domain_filter(value: str) -> str:
    return value.strip().strip(".")


def _normalize_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def _resolve_setting(
    args: argparse.Namespace,
    arg_name: str,
    env_name: str,
    default: Any = None,
    parser: Callable[[Any], Any] | None = None,
) -> Any:
    arg_value = getattr(args, arg_name, None)
    value = arg_value if arg_value is not None else os.getenv(env_name, default)
    if parser is not None and value is not None:
        return parser(value)
    return value


def _resolve_bool_setting(
    args: argparse.Namespace,
    arg_name: str,
    env_name: str,
    default: bool = False,
) -> bool:
    if getattr(args, arg_name):
        return True
    return _normalize_bool(os.getenv(env_name, str(default)))


class VerizonExternalDNS:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        tls_hostname: str | None,
        dns_suffix: str,
        domain_filters: list[str],
    ) -> None:
        """A simple ExternalDNS provider implementation that manages DNS entries on a
        Verizon Fios (CR1000A/CR1000B) router.

        Args:
            base_url: The base URL of the Verizon Fios router.
            username: The username for authentication.
            password: The password for authentication.
            tls_hostname: The TLS hostname for secure connections.
            dns_suffix: The DNS suffix to append to record names.
            domain_filters: A list of domain filters to apply.
        """
        self.dns_suffix = dns_suffix
        self.domain_filters = domain_filters
        self._client = VerizonRouterClient(
            base_url=base_url,
            tls_hostname=tls_hostname,
        )
        self._username = username
        self._password = password
        self._logged_in = False

    def ensure_logged_in(func: Callable[..., Any]) -> Callable[..., Any]:
        """Ensure the router client is logged in before executing the decorated
        function. If not logged in, it will attempt to log in using the provided
        credentials.

        Args:
            func: The function to decorate.

        Returns:
            The decorated function.
        """

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            if not self._logged_in:
                self._client.login(self._username, self._password)
                self._logged_in = True
            return func(self, *args, **kwargs)

        return wrapper

    @ensure_logged_in
    def list_records(self) -> list[dict[str, Any]]:
        """List all DNS endpoints managed by the Verizon Fios router.

        Returns:
            A list of dictionaries representing the DNS endpoints. Each dictionary has
            the format:

            {
                "dnsName": "<fully-qualified-domain-name>",
                "recordType": "A",
                "targets": ["<ip-address>"]
            }
        """
        entries = self._client.get_dns_entries_v4()
        LOGGER.debug("Router returned %d DNS entries", len(entries))
        out = []
        for r in entries:
            name = r.get("name")
            ip = r.get("ip")
            if not name or not ip:
                continue
            out.append(
                {
                    "dnsName": _ensure_suffix(name, self.dns_suffix),
                    "recordType": "A",
                    "targets": [ip],
                }
            )
        return out

    @ensure_logged_in
    def create_record(self, dns_name: str, ip: str) -> None:
        """Create a DNS endpoint on the Verizon Fios router.

        Args:
            dns_name: The fully-qualified domain name of the DNS record.
            ip: The IP address to associate with the DNS record.
        """
        LOGGER.info("Creating DNS record %s -> %s", dns_name, ip)
        self._client.add_dns_ipv4(dns_name, ip)

    @ensure_logged_in
    def delete_record(self, dns_name: str) -> None:
        """Delete a DNS endpoint on the Verizon Fios router.

        Args:
            dns_name: The fully-qualified domain name of the DNS record.
        """
        LOGGER.info("Deleting DNS record %s", dns_name)
        self._client.remove_dns_ipv4_by_hostname(dns_name)


flask_app = Flask(__name__)


@flask_app.route("/healthz", methods=["GET"])
def health() -> Response:
    return make_response("ok", 200)


@flask_app.route("/", methods=["GET"])
@ensure_media_type
def negotiate() -> Response:
    domain_filters = cast(list[str], current_app.config["VERIZON_DOMAIN_FILTERS"])
    return _make_response({"filters": domain_filters})


@flask_app.route("/records", methods=["GET"])
@ensure_media_type
def list_records() -> Response:
    provider = cast(VerizonExternalDNS, current_app.config["VERIZON_PROVIDER"])
    try:
        endpoints = provider.list_records()
    except Exception as e:
        LOGGER.exception("Failed to list records")
        abort(make_response(str(e), 500))
    return _make_response(endpoints)


@flask_app.route("/records", methods=["POST"])
@ensure_media_type
def apply_records() -> Response:
    if not request.is_json:
        abort(make_response("JSON request expected", 400))

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(make_response("Invalid JSON payload", 400))

    create = payload.get("create", []) or []
    delete = payload.get("delete", []) or []
    update_old = payload.get("updateOld", []) or []
    update_new = payload.get("updateNew", []) or []

    LOGGER.debug(
        "Changes received: create=%d delete=%d updateOld=%d updateNew=%d",
        len(create),
        len(delete),
        len(update_old),
        len(update_new),
    )

    config = current_app.config
    provider = cast(VerizonExternalDNS, config["VERIZON_PROVIDER"])
    delete_names = []
    create_updates = []

    try:
        for rec in delete:
            dns_name = rec.get("dnsName") if isinstance(rec, dict) else None
            if isinstance(dns_name, str):
                delete_names.append(dns_name)

        for rec in update_old:
            dns_name = rec.get("dnsName") if isinstance(rec, dict) else None
            if isinstance(dns_name, str):
                delete_names.append(dns_name)

        for rec in create + update_new:
            if not isinstance(rec, dict):
                continue
            dns_name = rec.get("dnsName")
            targets = rec.get("targets") or []
            if isinstance(dns_name, str) and isinstance(targets, list) and targets:
                create_updates.append((dns_name, targets[0]))

        for dns_name in delete_names:
            provider.delete_record(dns_name)

        for dns_name, target in create_updates:
            provider.create_record(dns_name, target)
    except Exception as e:
        LOGGER.exception("Failed to apply record changes")
        abort(make_response(str(e), 500))

    return make_response(("", 204))


@flask_app.route("/adjustendpoints", methods=["POST"])
@ensure_media_type
def adjust_records() -> Response:
    if not request.is_json:
        abort(make_response("JSON request expected", 400))

    endpoints = request.get_json(silent=True)
    if not isinstance(endpoints, list):
        abort(make_response("Expected list of endpoints", 400))

    dns_suffix = cast(str, current_app.config["VERIZON_DNS_SUFFIX"])

    normalized = []
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        dns_name = ep.get("dnsName")
        if not isinstance(dns_name, str):
            continue
        ep["dnsName"] = _ensure_suffix(dns_name.rstrip("."), dns_suffix)
        normalized.append(ep)

    return _make_response(normalized)


app = WsgiToAsgi(flask_app)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ExternalDNS Webhook Provider for Verizon Fios Router"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging (can also be enabled via VERIZON_EXTERNALDNS_DEBUG=1/true/yes)",
    )
    parser.add_argument("--base-url")
    parser.add_argument("--username")
    parser.add_argument("--tls-hostname")
    parser.add_argument("--dns-suffix")
    parser.add_argument("--domain-filters")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()

    debug = _resolve_bool_setting(
        args,
        "debug",
        "VERIZON_EXTERNALDNS_DEBUG",
        default=False,
    )

    base_url = _resolve_setting(
        args,
        "base_url",
        "VERIZON_BASE_URL",
        default="https://192.168.1.1",
    )
    username = _resolve_setting(
        args,
        "username",
        "VERIZON_USERNAME",
        default="admin",
    )
    password = _resolve_setting(
        args,
        "password",
        "VERIZON_PASSWORD",
    )
    tls_hostname = _resolve_setting(
        args,
        "tls_hostname",
        "VERIZON_TLS_HOSTNAME",
    )
    dns_suffix = _resolve_setting(
        args,
        "dns_suffix",
        "EXTERNAL_DNS_DNS_SUFFIX",
        parser=_normalize_dns_suffix,
    )
    domain_filters_raw = _resolve_setting(
        args,
        "domain_filters",
        "EXTERNAL_DNS_DOMAIN_FILTERS",
    )
    host = _resolve_setting(
        args,
        "host",
        "WEBHOOK_HOST",
        default=DEFAULT_BIND_HOST,
    )
    port = _resolve_setting(
        args,
        "port",
        "WEBHOOK_PORT",
        default=str(DEFAULT_BIND_PORT),
        parser=int,
    )

    if not username or not password:
        parser.error(
            "--username/VERIZON_USERNAME and --password/VERIZON_PASSWORD are required"
        )

    if not dns_suffix:
        parser.error("--dns-suffix/EXTERNAL_DNS_DNS_SUFFIX is required")

    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    LOGGER.setLevel(level)

    domain_filters = [
        normalized
        for p in (domain_filters_raw or dns_suffix).split(",")
        for normalized in [_normalize_domain_filter(p)]
        if normalized
    ]

    LOGGER.info(
        "Starting Verizon ExternalDNS Webhook Provider with: base_url=%s tls_hostname=%s dns_suffix=%s domain_filters=%s",
        base_url,
        tls_hostname,
        dns_suffix,
        domain_filters,
    )

    provider = VerizonExternalDNS(
        base_url=base_url,
        username=username,
        password=password,
        tls_hostname=tls_hostname,
        dns_suffix=dns_suffix,
        domain_filters=domain_filters,
    )

    flask_app.config["VERIZON_PROVIDER"] = provider
    flask_app.config["VERIZON_DNS_SUFFIX"] = dns_suffix
    flask_app.config["VERIZON_DOMAIN_FILTERS"] = domain_filters

    uvicorn.run(app, host=host, port=port, log_level="debug" if debug else "info")


if __name__ == "__main__":
    main()
