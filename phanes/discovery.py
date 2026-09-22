"""Explicit body discovery (Architecture Freeze section 9).

Discovery is not scanning: the only input is a local JSON host config
explicitly passed on the command line. Adapter selection goes through a
fixed factory table; the host package is imported lazily so the generic
runtime starts without it when no body is configured. Any invalid config
fails loudly; there is never a fallback to a privileged body.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phanes.contracts import BodyAdapter

CONFIG_SCHEMA_VERSION = 1

_CONFIG_KEYS = {"schema_version", "body", "allowed_capabilities"}
_BODY_KEYS = {"adapter", "body_id"}


class DiscoveryError(Exception):
    """Invalid host configuration. Startup must fail, not fall back."""


def _create_mock_uav(body_id: str) -> BodyAdapter:
    from phanes_host.mock_uav import MockUAVAdapter  # lazy: host package is optional

    return MockUAVAdapter(body_id)


# Fixed factory table. An adapter name is never a file path, module path
# or URL. New adapters are added here by the host side.
_ADAPTER_FACTORIES = {"mock_uav": _create_mock_uav}


def load_host_config(path: Path | str) -> dict[str, Any]:
    """Read and parse the explicitly given local JSON host config."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiscoveryError(f"cannot load host config {path}: {exc}") from exc
    return raw


def discover_body(config: dict[str, Any]) -> BodyAdapter | None:
    """Create the configured adapter, or None when the config declares no body.

    Strict validation: unknown fields, unknown adapter names, unsupported
    versions, malformed authorization lists and a non-empty authorization
    list without a body are all configuration errors.
    """
    if not isinstance(config, dict):
        raise DiscoveryError("host config must be a JSON object")
    extra = set(config) - _CONFIG_KEYS
    if extra:
        raise DiscoveryError(f"unknown host config field(s): {sorted(extra)}")
    if "schema_version" not in config:
        raise DiscoveryError("host config missing schema_version")
    if type(config["schema_version"]) is not int or config["schema_version"] != CONFIG_SCHEMA_VERSION:
        raise DiscoveryError(f"unsupported host config schema_version: {config['schema_version']!r}")

    allowed = config.get("allowed_capabilities", [])
    if not isinstance(allowed, list) or not all(isinstance(n, str) for n in allowed):
        raise DiscoveryError("allowed_capabilities must be a list of capability names")

    body = config.get("body")
    if body is None:
        if allowed:
            raise DiscoveryError("allowed_capabilities require a configured body")
        return None
    if not isinstance(body, dict) or set(body) != _BODY_KEYS:
        raise DiscoveryError(f"body must be an object with exactly fields {sorted(_BODY_KEYS)}")
    if not isinstance(body["adapter"], str) or not isinstance(body["body_id"], str) or not body["body_id"]:
        raise DiscoveryError("body.adapter and body.body_id must be non-empty strings")

    factory = _ADAPTER_FACTORIES.get(body["adapter"])
    if factory is None:
        raise DiscoveryError(f"unknown adapter: {body['adapter']!r}")
    return factory(body["body_id"])
