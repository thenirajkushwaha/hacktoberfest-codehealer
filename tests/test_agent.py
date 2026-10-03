"""
Unit tests for heal.agent reasoning core and model resolution.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from heal.agent import (
    DEFAULT_MULTIMODAL_MODEL,
    DEFAULT_TEXT_MODEL,
    HealingAgent,
    MissingAPIKeyError,
)


def test_missing_api_key():
    agent = HealingAgent(api_key="")
    with pytest.raises(MissingAPIKeyError):
        agent._get_client()


def test_model_resolution_defaults():
    agent = HealingAgent(api_key="test_key")
    # Text only
    assert agent.resolve_model(has_image=False) == DEFAULT_TEXT_MODEL
    # Multimodal image input
    assert agent.resolve_model(has_image=True) == DEFAULT_MULTIMODAL_MODEL


def test_model_resolution_custom_override():
    agent = HealingAgent(api_key="test_key", model="gemini-2.0-flash")
    assert agent.resolve_model(has_image=False) == "gemini-2.0-flash"
    assert agent.resolve_model(has_image=True) == "gemini-2.0-flash"


def test_diagnose_and_patch_prompt_dispatch():
    agent = HealingAgent(api_key="test_key")
    
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "```python\nprint('fixed')\n```"
    mock_client.models.generate_content.return_value = mock_response

    with patch.object(agent, "_get_client", return_value=mock_client):
        script_path = Path("fake_script.py")
        result = agent.diagnose_and_patch(
            script_path=script_path,
            script_code="print('broken'",
            stderr="SyntaxError: unexpected EOF while parsing",
            stdout="",
            returncode=1,
            error_category="SYNTAX_ERROR",
            attempt=1,
            max_retries=3,
        )

        assert "print('fixed')" in result
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == DEFAULT_TEXT_MODEL
        assert "SyntaxError" in call_kwargs["contents"][0]
