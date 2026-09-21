"""Test validation that token properties have been properly removed from models.

This test suite ensures that our token property removal is comprehensive and
prevents accidental re-introduction of token-related functionality.

Assertions target pydantic's structured ``ValidationError.errors()`` payload
(type=``extra_forbidden``, loc=(field,)) rather than fishing for substrings in
the human-readable message — substring assertions over pydantic's prose break
silently on library upgrades.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from dualeai.models.bridge import BridgeContentDeltaResponse
from dualeai.models.llm_result import LLMResult


def _extra_forbidden_fields(exc: ValidationError) -> set[str]:
    """Return field names rejected as extra/forbidden by a pydantic ValidationError."""
    return {str(err["loc"][0]) for err in exc.errors() if err["type"] == "extra_forbidden" and err.get("loc")}


@pytest.mark.unit
class TestLLMResultTokenRemoval:
    """Test that LLMResult properly rejects token properties."""

    def test_llm_result_rejects_total_tokens(self):
        """Test that LLMResult raises ValidationError when total_tokens is provided."""
        with pytest.raises(ValidationError) as exc_info:
            LLMResult.model_validate({"completion": "Test completion", "total_tokens": 100})

        assert "total_tokens" in _extra_forbidden_fields(exc_info.value)

    def test_llm_result_rejects_prompt_tokens(self):
        """Test that LLMResult raises ValidationError when prompt_tokens is provided."""
        with pytest.raises(ValidationError) as exc_info:
            LLMResult.model_validate({"completion": "Test completion", "prompt_tokens": 50})

        assert "prompt_tokens" in _extra_forbidden_fields(exc_info.value)

    def test_llm_result_rejects_completion_tokens(self):
        """Test that LLMResult raises ValidationError when completion_tokens is provided."""
        with pytest.raises(ValidationError) as exc_info:
            LLMResult.model_validate({"completion": "Test completion", "completion_tokens": 50})

        assert "completion_tokens" in _extra_forbidden_fields(exc_info.value)

    def test_llm_result_rejects_all_token_properties(self):
        """Test that LLMResult raises ValidationError when multiple token properties are provided."""
        with pytest.raises(ValidationError) as exc_info:
            LLMResult.model_validate(
                {"completion": "Test completion", "total_tokens": 100, "prompt_tokens": 50, "completion_tokens": 50}
            )

        # All three token fields must be flagged as extra (not just the first one)
        rejected = _extra_forbidden_fields(exc_info.value)
        assert {"total_tokens", "prompt_tokens", "completion_tokens"} <= rejected

    def test_llm_result_creation_without_tokens_succeeds(self):
        """Test that LLMResult can be created successfully without token properties."""
        # This should work fine
        result = LLMResult(completion="Test completion", cache_hit=False)

        assert result.completion == "Test completion"
        assert not result.cache_hit

    def test_llm_result_model_fields_exclude_tokens(self):
        """Test that LLMResult model fields don't include token properties."""
        model_fields = set(LLMResult.model_fields.keys())

        # Verify token fields are not in the model
        assert "total_tokens" not in model_fields
        assert "prompt_tokens" not in model_fields
        assert "completion_tokens" not in model_fields

        # Verify expected fields are still present
        assert "completion" in model_fields
        assert "cache_hit" in model_fields


@pytest.mark.unit
class TestBridgeContentDeltaTokenRemoval:
    """Test that BridgeContentDeltaResponse properly rejects unknown properties."""

    def test_bridge_content_delta_rejects_delta_tokens(self):
        """Test that BridgeContentDeltaResponse raises ValidationError when delta_tokens is provided."""
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError) as exc_info:
            BridgeContentDeltaResponse.model_validate(
                {
                    "type": "content.delta",
                    "timestamp": now,
                    "delta": "Test chunk content",
                    "delta_tokens": 5,
                }
            )

        assert "delta_tokens" in _extra_forbidden_fields(exc_info.value)

    def test_bridge_content_delta_creation_without_delta_tokens_succeeds(self):
        """Test that BridgeContentDeltaResponse can be created successfully without delta_tokens."""
        now = datetime.now(timezone.utc)
        chunk = BridgeContentDeltaResponse(
            type="content.delta",
            timestamp=now,
            delta="Test chunk content",
        )

        assert chunk.type == "content.delta"
        assert chunk.delta == "Test chunk content"
        assert chunk.timestamp == now

    def test_bridge_content_delta_model_fields_exclude_delta_tokens(self):
        """Test that BridgeContentDeltaResponse model fields don't include delta_tokens."""
        model_fields = set(BridgeContentDeltaResponse.model_fields.keys())

        # Verify delta_tokens is not in the model
        assert "delta_tokens" not in model_fields

        # Verify expected fields are still present
        assert "type" in model_fields
        assert "delta" in model_fields
        assert "timestamp" in model_fields


@pytest.mark.unit
class TestTokenRemovalConsistency:
    """Test overall consistency of token removal across models."""

    def test_no_token_references_in_model_schemas(self):
        """Test that no model schemas contain token-related field references."""
        # Check LLMResult schema
        llm_result_schema = LLMResult.model_json_schema()
        properties = llm_result_schema.get("properties", {})

        token_fields = ["total_tokens", "prompt_tokens", "completion_tokens", "delta_tokens"]
        for field in token_fields:
            assert field not in properties, f"Found unexpected token field '{field}' in LLMResult schema"

        # Check BridgeContentDeltaResponse schema
        chunk_schema = BridgeContentDeltaResponse.model_json_schema()
        chunk_properties = chunk_schema.get("properties", {})

        assert "delta_tokens" not in chunk_properties, (
            "Found unexpected delta_tokens in BridgeContentDeltaResponse schema"
        )

    def test_serialization_excludes_token_fields(self):
        """Test that model serialization doesn't include token fields."""
        # Create valid models
        llm_result = LLMResult(completion="Test")
        now = datetime.now(timezone.utc)
        chunk = BridgeContentDeltaResponse(type="content.delta", timestamp=now, delta="Test content")

        # Check serialized data doesn't contain token fields
        llm_result_dict = llm_result.model_dump()
        assert "total_tokens" not in llm_result_dict
        assert "prompt_tokens" not in llm_result_dict
        assert "completion_tokens" not in llm_result_dict

        chunk_dict = chunk.model_dump()
        assert "delta_tokens" not in chunk_dict
