"""Tests for configuration management with Pydantic Settings."""

import os
from unittest.mock import patch

import pytest

from dualeai.config import DualeAIConfig


@pytest.mark.unit
class TestObservabilityEndpoints:
    """Per-signal OTLP/HTTP endpoint construction.

    OTLP exporter spec: an endpoint passed explicitly to an exporter is used
    AS-IS (no per-signal path appended) — verified against the installed
    opentelemetry-exporter-otlp-proto-http source (1.41.0 and 1.44.0). The SDK
    must therefore build ``{base}/v1/<signal>`` itself.
    """

    def test_default_observability_endpoint_uses_otlp_http_port(self):
        """Default endpoint targets the OTLP/HTTP port 4318, not gRPC 4317."""
        config = DualeAIConfig(token="dualeai_test_token_12345")
        assert config.observability.endpoint == "http://localhost:4318"

    def test_otel_traces_endpoint_appends_v1_traces(self):
        """Span exporter URL is the base endpoint plus /v1/traces."""
        config = DualeAIConfig(token="dualeai_test_token_12345")
        config.observability.endpoint = "https://telemetry.example.com"
        assert config.otel_traces_endpoint == "https://telemetry.example.com/v1/traces"

    def test_otel_metrics_endpoint_appends_v1_metrics(self):
        """Metric exporter URL is the base endpoint plus /v1/metrics."""
        config = DualeAIConfig(token="dualeai_test_token_12345")
        config.observability.endpoint = "https://telemetry.example.com/"
        # Trailing slash on the base must not produce a double slash.
        assert config.otel_metrics_endpoint == "https://telemetry.example.com/v1/metrics"


@pytest.mark.unit
class TestConfigLoading:
    """Test configuration loading from various sources."""

    def test_default_config_values(self):
        """Test that default config values are set correctly in Field definitions."""
        # Test the class field defaults directly (what would be used without env vars)
        # Check Field defaults from the class definition
        fields = DualeAIConfig.model_fields
        assert fields["endpoint"].default == "https://api.duale.ai"
        assert fields["debug"].default is False
        # token's Field default is None (pydantic-settings fills it from
        # DUALEAI_TOKEN); required-ness is enforced by validate_token_present_and_format
        # at validation time, not by the Field declaration itself.
        assert fields["token"].default is None

    @patch.dict(
        os.environ,
        {
            "DUALEAI_TOKEN": "dualeai_test_dev_token_12345",
            "DUALEAI_ENDPOINT": "https://test-bridge:8080",
            "DUALEAI_DEBUG": "true",
            "DUALEAI_TENANT_ID": "test-tenant",
        },
    )
    def test_environment_variable_loading(self):
        """Test that configuration loads from environment variables."""
        # Create config with mocked environment
        test_config = DualeAIConfig()
        assert test_config.token == "dualeai_test_dev_token_12345"
        assert test_config.endpoint == "https://test-bridge:8080"
        assert test_config.debug is True

    def test_environment_variable_override(self, tmp_path):
        """Test that environment variables override .env file values."""
        # Real .env says debug=true; the env var says false and must win.
        env_file = tmp_path / ".env"
        env_file.write_text("DUALEAI_DEBUG=true\n")
        original_env_file = DualeAIConfig.model_config.get("env_file")
        DualeAIConfig.model_config["env_file"] = str(env_file)
        try:
            with patch.dict(
                os.environ,
                {
                    "DUALEAI_TOKEN": "dualeai_test_token_12345",
                    "DUALEAI_DEBUG": "false",
                    "DUALEAI_TENANT_ID": "test-tenant",
                },
            ):
                override_config = DualeAIConfig()
                assert override_config.debug is False  # Env var beats .env

            # Control: without the env var the .env value is loaded (proves
            # the .env source was active, so the override above was real).
            with patch.dict(
                os.environ,
                {
                    "DUALEAI_TOKEN": "dualeai_test_token_12345",
                    "DUALEAI_TENANT_ID": "test-tenant",
                },
            ):
                dotenv_config = DualeAIConfig()
                assert dotenv_config.debug is True
        finally:
            DualeAIConfig.model_config["env_file"] = original_env_file

    def test_case_insensitive_env_vars(self):
        """Test that environment variables are case-insensitive."""
        with patch.dict(
            os.environ,
            {
                "DUALEAI_TOKEN": "dualeai_test_token_12345",
                # Lowercase var with a NON-default value: only case-insensitive
                # loading can flip debug to True (default is False).
                "dualeai_debug": "true",
                "DUALEAI_TENANT_ID": "test-tenant",
            },
        ):
            test_config = DualeAIConfig()
            assert test_config.debug is True

    def test_nested_delimiter_support(self):
        """Test that nested delimiter works for nested configs (observability)."""
        with patch.dict(
            os.environ,
            {
                "DUALEAI_TOKEN": "dualeai_test_token_12345",
                "DUALEAI_OBSERVABILITY__ENDPOINT": "http://otel:4317",
                "DUALEAI_TENANT_ID": "test-tenant",
            },
        ):
            test_config = DualeAIConfig()
            # Verify nested config works
            assert test_config.model_config.get("env_nested_delimiter") == "__"
            assert test_config.observability.endpoint == "http://otel:4317"


@pytest.mark.unit
class TestConfigValidation:
    """Test configuration validation rules."""

    @pytest.mark.parametrize(("raw_value", "expected"), [("true", True), ("false", False)])
    def test_debug_validation(self, raw_value: str, expected: bool):
        """Test that debug field accepts boolean values."""
        with patch.dict(
            os.environ,
            {
                "DUALEAI_TOKEN": "dualeai_test_token_12345",
                "DUALEAI_DEBUG": raw_value,
                "DUALEAI_TENANT_ID": "test-tenant",
            },
        ):
            config = DualeAIConfig()
            assert config.debug is expected

    def test_token_format_validation(self):
        """Test that token must start with dualeai_."""
        with (
            patch.dict(
                os.environ,
                {
                    "DUALEAI_TOKEN": "invalid-token-format",
                    "DUALEAI_TENANT_ID": "test-tenant",
                },
            ),
            pytest.raises(ValueError, match="token must start with 'dualeai_'"),
        ):
            DualeAIConfig()

    def test_endpoint_format_validation(self):
        """Test that endpoint must be HTTP(S) URL."""
        with (
            patch.dict(
                os.environ,
                {
                    "DUALEAI_TOKEN": "dualeai_test_token_12345",
                    "DUALEAI_ENDPOINT": "ftp://invalid-protocol",
                    "DUALEAI_TENANT_ID": "test-tenant",
                },
            ),
            pytest.raises(ValueError, match="endpoint must be an HTTP"),
        ):
            DualeAIConfig()

    @pytest.mark.parametrize("redis_url", ["redis://cache.example:6379/0", "rediss://cache.example:6380/0"])
    def test_redis_url_accepts_plain_and_tls_schemes(self, redis_url: str):
        """Redis configuration accepts both redis-py connection schemes."""
        config = DualeAIConfig(token="dualeai_test_token_12345", redis_url=redis_url)
        assert config.redis_url == redis_url

    def test_redis_url_rejects_other_schemes(self):
        """Redis configuration rejects URLs that redis-py must not receive."""
        with pytest.raises(ValueError, match="redis:// or rediss://"):
            DualeAIConfig(token="dualeai_test_token_12345", redis_url="http://cache.example:6379/0")
