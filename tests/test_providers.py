"""Tests for sagewai.providers — inference provider factory functions."""
from __future__ import annotations

import os
from unittest.mock import patch

from sagewai import providers


def test_ollama_defaults():
    result = providers.ollama()
    assert result["model"] == "ollama/llama3.1:8b"
    assert result["api_base"] == "http://localhost:11434"


def test_ollama_custom_model_and_host():
    result = providers.ollama("mistral:7b", host="http://192.168.1.10:11434")
    assert result["model"] == "ollama/mistral:7b"
    assert result["api_base"] == "http://192.168.1.10:11434"


def test_ollama_env_var():
    with patch.dict(os.environ, {"OLLAMA_HOST": "http://remote:11434"}):
        result = providers.ollama()
    assert result["api_base"] == "http://remote:11434"


def test_lm_studio_defaults():
    result = providers.lm_studio("Mistral-7B-Instruct")
    assert result["model"] == "openai/Mistral-7B-Instruct"
    assert "localhost:1234" in result["api_base"]
    assert result["api_key"] == "lm-studio"


def test_lm_studio_custom_host():
    result = providers.lm_studio("my-model", host="http://localhost:5678")
    assert result["api_base"] == "http://localhost:5678/v1"


def test_llama_cpp_defaults():
    result = providers.llama_cpp()
    assert result["model"] == "openai/local"
    assert "localhost:8080" in result["api_base"]
    assert result["api_key"] == "llama-cpp"


def test_groq_defaults():
    result = providers.groq()
    assert result["model"] == "groq/llama-3.1-8b-instant"


def test_groq_custom_model():
    result = providers.groq("deepseek-r1-distill-llama-70b", api_key="test-key")
    assert result["model"] == "groq/deepseek-r1-distill-llama-70b"
    assert result["api_key"] == "test-key"


def test_groq_env_key():
    with patch.dict(os.environ, {"GROQ_API_KEY": "env-groq-key"}):
        result = providers.groq()
    assert result["api_key"] == "env-groq-key"


def test_together():
    result = providers.together("meta-llama/Llama-3-8b-hf", api_key="key")
    assert result["model"] == "together_ai/meta-llama/Llama-3-8b-hf"
    assert result["api_key"] == "key"


def test_fireworks():
    result = providers.fireworks("llama-v3p1-8b-instruct", api_key="fw-key")
    assert result["model"] == "fireworks_ai/llama-v3p1-8b-instruct"
    assert result["api_key"] == "fw-key"


def test_huggingface():
    result = providers.huggingface("mistralai/Mistral-7B-Instruct-v0.2", api_key="hf-tok")
    assert result["model"] == "huggingface/mistralai/Mistral-7B-Instruct-v0.2"
    assert result["api_key"] == "hf-tok"


def test_openai_defaults():
    result = providers.openai()
    assert result["model"] == "gpt-4o"


def test_anthropic_defaults():
    result = providers.anthropic()
    assert result["model"] == "anthropic/claude-sonnet-4-6"


def test_gemini_defaults():
    result = providers.gemini()
    assert result["model"] == "gemini/gemini-2.5-flash"


def test_custom():
    result = providers.custom(
        model="openai/my-model",
        api_base="http://myserver:8000/v1",
        api_key="secret",
        custom_llm_provider="openai",
    )
    assert result["model"] == "openai/my-model"
    assert result["api_base"] == "http://myserver:8000/v1"
    assert result["api_key"] == "secret"
    assert result["custom_llm_provider"] == "openai"


def test_custom_no_key():
    result = providers.custom("openai/model", "http://localhost:8000")
    assert "api_key" not in result
    assert "custom_llm_provider" not in result
