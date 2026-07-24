import json
import pytest

from report_autopilot.client_config import (
    load_client_config, default_config, ClientConfigError, DEFAULT_BRAND_COLOR,
)


def test_default_config_has_sane_fallbacks():
    config = default_config("Acme Corp")
    assert config.client_name == "Acme Corp"
    assert config.brand_color == DEFAULT_BRAND_COLOR
    assert config.delivery.email_to == []
    assert config.delivery.slack_webhook_url is None


def test_load_full_config(tmp_path):
    config_path = tmp_path / "acme.json"
    config_path.write_text(json.dumps({
        "client_name": "Acme Corp",
        "agency_name": "Single Grain",
        "brand_color": "#123456",
        "delivery": {
            "email": {"to": ["a@example.com", "b@example.com"]},
            "slack_webhook_url": "https://hooks.slack.com/fake",
        },
    }))
    config = load_client_config(str(config_path))
    assert config.client_name == "Acme Corp"
    assert config.brand_color == "#123456"
    assert config.delivery.email_to == ["a@example.com", "b@example.com"]
    assert config.delivery.slack_webhook_url == "https://hooks.slack.com/fake"


def test_load_minimal_config_falls_back_to_defaults(tmp_path):
    config_path = tmp_path / "minimal.json"
    config_path.write_text(json.dumps({"client_name": "Bare Client"}))
    config = load_client_config(str(config_path))
    assert config.client_name == "Bare Client"
    assert config.brand_color == DEFAULT_BRAND_COLOR
    assert config.delivery.email_to == []


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(ClientConfigError, match="not found"):
        load_client_config(str(tmp_path / "nope.json"))


def test_load_invalid_json_raises(tmp_path):
    config_path = tmp_path / "broken.json"
    config_path.write_text("{not valid json")
    with pytest.raises(ClientConfigError, match="Invalid JSON"):
        load_client_config(str(config_path))


def test_load_missing_client_name_raises(tmp_path):
    config_path = tmp_path / "no_name.json"
    config_path.write_text(json.dumps({"agency_name": "Single Grain"}))
    with pytest.raises(ClientConfigError, match="client_name"):
        load_client_config(str(config_path))
