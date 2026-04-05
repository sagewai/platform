# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Convenience factory functions for common LLM inference providers.

Each function returns a ``dict`` of keyword arguments to pass directly to
``UniversalAgent`` (or any ``BaseAgent`` subclass). API keys are read from
environment variables with optional override.

Usage::

    from sagewai.engines.universal import UniversalAgent
    from sagewai import providers

    # Local runtimes
    agent = UniversalAgent("bot", **providers.ollama("llama3.1:8b"))
    agent = UniversalAgent("bot", **providers.lm_studio("Mistral-7B-Instruct"))
    agent = UniversalAgent("bot", **providers.llama_cpp())

    # Managed inference
    agent = UniversalAgent("bot", **providers.groq("llama-3.1-8b-instant"))
    agent = UniversalAgent("bot", **providers.together("meta-llama/Llama-3-8b-hf"))
    agent = UniversalAgent("bot", **providers.huggingface("mistralai/Mistral-7B-Instruct-v0.2"))

    # Cloud providers
    agent = UniversalAgent("bot", **providers.openai("gpt-4o"))
    agent = UniversalAgent("bot", **providers.anthropic("claude-sonnet-4-6"))
    agent = UniversalAgent("bot", **providers.gemini("gemini-2.0-flash"))
"""

from __future__ import annotations

import os


def ollama(model: str = "llama3.1:8b", host: str | None = None) -> dict:
    """Ollama local inference (OpenAI-compatible REST API).

    Args:
        model: Ollama model name (e.g. ``"llama3.1:8b"``, ``"mistral:7b"``).
        host: Ollama server URL. Reads ``OLLAMA_HOST`` env var; defaults to
            ``http://localhost:11434``.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    base = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return {"model": f"ollama/{model}", "api_base": base}


def lm_studio(model: str, host: str | None = None) -> dict:
    """LM Studio local inference (OpenAI-compatible server).

    Args:
        model: Model identifier as shown in LM Studio.
        host: LM Studio server URL. Reads ``LM_STUDIO_HOST`` env var; defaults
            to ``http://localhost:1234``.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    base = host or os.environ.get("LM_STUDIO_HOST", "http://localhost:1234")
    return {"model": f"openai/{model}", "api_base": f"{base}/v1", "api_key": "lm-studio"}


def llama_cpp(model: str = "local", host: str | None = None) -> dict:
    """llama.cpp server inference (OpenAI-compatible server).

    Start the server with: ``llama-server -m model.gguf --port 8080``

    Args:
        model: Model identifier (any string; llama.cpp ignores it).
        host: Server URL. Reads ``LLAMA_CPP_HOST`` env var; defaults to
            ``http://localhost:8080``.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    base = host or os.environ.get("LLAMA_CPP_HOST", "http://localhost:8080")
    return {"model": f"openai/{model}", "api_base": f"{base}/v1", "api_key": "llama-cpp"}


def groq(model: str = "llama-3.1-8b-instant", api_key: str | None = None) -> dict:
    """Groq ultra-fast LLM inference.

    Args:
        model: Groq model ID (e.g. ``"llama-3.1-8b-instant"``,
            ``"deepseek-r1-distill-llama-70b"``).
        api_key: Groq API key. Reads ``GROQ_API_KEY`` env var if not provided.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    return {
        "model": f"groq/{model}",
        "api_key": api_key or os.environ.get("GROQ_API_KEY"),
    }


def together(model: str, api_key: str | None = None) -> dict:
    """Together AI open-source model hosting.

    Args:
        model: Together AI model ID (e.g. ``"meta-llama/Llama-3-8b-hf"``).
        api_key: Together AI API key. Reads ``TOGETHER_API_KEY`` env var.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    return {
        "model": f"together_ai/{model}",
        "api_key": api_key or os.environ.get("TOGETHER_API_KEY"),
    }


def fireworks(model: str, api_key: str | None = None) -> dict:
    """Fireworks AI fast scalable inference.

    Args:
        model: Fireworks model ID (e.g. ``"llama-v3p1-8b-instruct"``).
        api_key: Fireworks API key. Reads ``FIREWORKS_API_KEY`` env var.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    return {
        "model": f"fireworks_ai/{model}",
        "api_key": api_key or os.environ.get("FIREWORKS_API_KEY"),
    }


def huggingface(model: str, api_key: str | None = None) -> dict:
    """HuggingFace Inference API (serverless).

    Args:
        model: HuggingFace model repo ID (e.g.
            ``"mistralai/Mistral-7B-Instruct-v0.2"``).
        api_key: HuggingFace token. Reads ``HF_TOKEN`` env var.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    return {
        "model": f"huggingface/{model}",
        "api_key": api_key or os.environ.get("HF_TOKEN"),
    }


def cerebras(model: str, api_key: str | None = None) -> dict:
    """Cerebras high-performance LLM inference.

    Args:
        model: Cerebras model ID.
        api_key: Cerebras API key. Reads ``CEREBRAS_API_KEY`` env var.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    return {
        "model": f"cerebras/{model}",
        "api_key": api_key or os.environ.get("CEREBRAS_API_KEY"),
    }


def openai(model: str = "gpt-4o", api_key: str | None = None) -> dict:
    """OpenAI models.

    Args:
        model: OpenAI model ID (e.g. ``"gpt-4o"``, ``"o3-mini"``).
        api_key: OpenAI API key. Reads ``OPENAI_API_KEY`` env var.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    result: dict = {"model": model}
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if key:
        result["api_key"] = key
    return result


def anthropic(model: str = "claude-sonnet-4-6", api_key: str | None = None) -> dict:
    """Anthropic Claude models.

    Args:
        model: Claude model ID (e.g. ``"claude-sonnet-4-6"``, ``"claude-opus-4-6"``).
        api_key: Anthropic API key. Reads ``ANTHROPIC_API_KEY`` env var.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    result: dict = {"model": f"anthropic/{model}"}
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if key:
        result["api_key"] = key
    return result


def gemini(model: str = "gemini-2.0-flash", api_key: str | None = None) -> dict:
    """Google Gemini models.

    Args:
        model: Gemini model ID (e.g. ``"gemini-2.0-flash"``, ``"gemini-1.5-pro"``).
        api_key: Gemini API key. Reads ``GEMINI_API_KEY`` env var.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    result: dict = {"model": f"gemini/{model}"}
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if key:
        result["api_key"] = key
    return result


def custom(
    model: str,
    api_base: str,
    api_key: str | None = None,
    custom_llm_provider: str | None = None,
) -> dict:
    """Any custom OpenAI-compatible inference endpoint.

    Args:
        model: Model identifier string.
        api_base: Base URL of the inference endpoint.
        api_key: API key if required.
        custom_llm_provider: Explicit LiteLLM provider name override.

    Returns:
        kwargs dict for ``UniversalAgent``.
    """
    result: dict = {"model": model, "api_base": api_base}
    if api_key:
        result["api_key"] = api_key
    if custom_llm_provider:
        result["custom_llm_provider"] = custom_llm_provider
    return result
