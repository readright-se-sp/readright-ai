import re

from packaging_helpers import ROOT, compose_services, env_of, read_text, volumes_of


def caddyfile() -> str:
    return read_text(ROOT / "Caddyfile")


def seconds(name: str) -> float:
    match = re.search(rf"\b{name}\s+(\d+(?:\.\d+)?)(ms|s|m)?\b", caddyfile())
    assert match, f"{name} not set"
    value, unit = float(match.group(1)), match.group(2) or "ns"
    factor = {"ms": 0.001, "s": 1, "m": 60}.get(unit)
    assert factor is not None, f"{name} needs a unit"
    return value * factor


def test_site_address_comes_from_env_with_localhost_default():
    """Criterion 14."""
    assert "{$MAI_SITE_ADDRESS:localhost}" in caddyfile()


def test_proxies_to_the_api_container():
    """Criterion 14."""
    assert re.search(r"reverse_proxy\s+api:8000", caddyfile())


def test_upstream_timeouts_outlast_the_request_deadline():
    """Criterion 14."""
    assert seconds("read_timeout") >= 121
    assert seconds("write_timeout") >= 121
    assert seconds("dial_timeout") == 5


def test_request_body_is_limited_to_one_megabyte():
    """Criterion 14."""
    match = re.search(r"request_body\s*\{[^}]*max_size\s+(\S+)", caddyfile(), re.DOTALL)
    assert match, "no request_body max_size"
    assert match.group(1).lower() in {"1mb", "1mib", "1m", "1048576"}


def test_caddy_service_mounts_config_read_only_and_persists_certificates():
    """Criterion 14."""
    caddy = compose_services("compose.yaml")["caddy"]
    mounts = {m["target"]: m for m in volumes_of(caddy)}
    assert mounts["/etc/caddy/Caddyfile"]["read_only"]
    assert mounts["/data"]["source"] == "caddy_data"
    assert mounts["/config"]["source"] == "caddy_config"
    assert "MAI_SITE_ADDRESS" in env_of(caddy)
