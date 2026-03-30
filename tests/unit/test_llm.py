"""Tests for investagent.llm."""

from unittest.mock import MagicMock, patch

from investagent.llm import LLMClient


def test_llm_client_default_model():
    client = LLMClient(client=MagicMock())
    assert client.model == "claude-sonnet-4-20250514"


def test_llm_client_custom_model():
    client = LLMClient(model="claude-haiku-4-5-20251001", client=MagicMock())
    assert client.model == "claude-haiku-4-5-20251001"


def test_llm_client_passes_base_url_and_api_key():
    """When no client is injected, base_url and api_key reach AsyncAnthropic."""
    with patch("investagent.llm.anthropic.AsyncAnthropic") as mock_cls:
        with patch("investagent.llm.httpx.AsyncClient"):
            LLMClient(
                model="MiniMax-M2.7",
                base_url="https://api.minimaxi.com/anthropic",
                api_key="test-key-123",
            )
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["base_url"] == "https://api.minimaxi.com/anthropic"
            assert call_kwargs["api_key"] == "test-key-123"
            assert "http_client" in call_kwargs


def test_llm_client_defaults_none_without_base_url():
    """Without base_url/api_key, AsyncAnthropic gets defaults."""
    with patch("investagent.llm.anthropic.AsyncAnthropic") as mock_cls:
        LLMClient(model="claude-sonnet-4-20250514")
        mock_cls.assert_called_once_with()


def test_llm_client_injected_client_ignores_base_url():
    """When client is injected, base_url and api_key are not used."""
    mock_client = MagicMock()
    llm = LLMClient(
        model="MiniMax-M2.7",
        base_url="https://should.be.ignored",
        api_key="ignored-key",
        client=mock_client,
    )
    assert llm._client is mock_client
