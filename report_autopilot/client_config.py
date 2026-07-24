"""
client_config.py
-----------------
Per-client settings (brand color, agency name shown on the report,
optional logo) loaded from a small JSON file instead of hardcoded
constants. This is what makes the tool usable across more than one
client without editing source code for each new one.

Config file format (see sample_data/client_configs/acme_corp.json):
{
  "client_name": "Acme Corp",
  "agency_name": "Single Grain",
  "brand_color": "#f27038",
  "delivery": {
    "email": {"to": ["client@example.com"]},
    "slack_webhook_url": null
  }
}

Any field not present falls back to a sensible default, so a minimal
config file (even just {"client_name": "..."}) is valid.
"""

import json
import os
from dataclasses import dataclass, field


DEFAULT_BRAND_COLOR = "#f27038"
DEFAULT_AGENCY_NAME = "Single Grain"


class ClientConfigError(Exception):
    pass


@dataclass
class DeliveryConfig:
    email_to: list = field(default_factory=list)
    slack_webhook_url: str = None


@dataclass
class ClientConfig:
    client_name: str
    agency_name: str = DEFAULT_AGENCY_NAME
    brand_color: str = DEFAULT_BRAND_COLOR
    logo_path: str = None
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)


def load_client_config(path: str) -> ClientConfig:
    if not os.path.exists(path):
        raise ClientConfigError(f"Client config file not found: {path}")

    with open(path) as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ClientConfigError(f"Invalid JSON in {path}: {e}")

    if "client_name" not in raw:
        raise ClientConfigError(f"{path} is missing required field 'client_name'")

    delivery_raw = raw.get("delivery", {}) or {}
    email_raw = delivery_raw.get("email", {}) or {}

    return ClientConfig(
        client_name=raw["client_name"],
        agency_name=raw.get("agency_name", DEFAULT_AGENCY_NAME),
        brand_color=raw.get("brand_color", DEFAULT_BRAND_COLOR),
        logo_path=raw.get("logo_path"),
        delivery=DeliveryConfig(
            email_to=email_raw.get("to", []),
            slack_webhook_url=delivery_raw.get("slack_webhook_url"),
        ),
    )


def default_config(client_name: str, agency_name: str = DEFAULT_AGENCY_NAME) -> ClientConfig:
    """Used when no config file is given -- e.g. quick CLI runs with just --client."""
    return ClientConfig(client_name=client_name, agency_name=agency_name)
