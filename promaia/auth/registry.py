"""Integration registry — single source of truth for all integrations."""

from __future__ import annotations

from promaia.auth.base import Integration

_INTEGRATIONS: dict[str, Integration] = {}

# Tag integrations as AI providers for setup selector
_AI_NAMES = {"anthropic", "openai", "google_ai", "openrouter"}


def register(integration: Integration) -> None:
    """Register an integration instance."""
    _INTEGRATIONS[integration.name] = integration


def get_integration(name: str) -> Integration:
    """Look up an integration by name. Raises KeyError if unknown."""
    _ensure_loaded()
    return _INTEGRATIONS[name]


def list_integrations() -> list[Integration]:
    """Return all registered integrations in registration order."""
    _ensure_loaded()
    return list(_INTEGRATIONS.values())


def get_ai_integrations() -> list[Integration]:
    """Return AI-provider integrations (for the setup selector)."""
    _ensure_loaded()
    return [i for i in _INTEGRATIONS.values() if i.name in _AI_NAMES]


# ── auto-load ───────────────────────────────────────────────────────

_loaded = False


def _ensure_loaded() -> None:
    """Import all integration modules so they self-register."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    from promaia.auth.integrations.anthropic import AnthropicIntegration
    from promaia.auth.integrations.openai import OpenAIIntegration
    from promaia.auth.integrations.google_ai import GoogleAIIntegration
    from promaia.auth.integrations.google import GoogleIntegration
    from promaia.auth.integrations.notion import NotionIntegration
    from promaia.auth.integrations.discord import DiscordIntegration
    from promaia.auth.integrations.perplexity import PerplexityIntegration
    from promaia.auth.integrations.openrouter import OpenRouterIntegration

    register(OpenRouterIntegration())
    register(AnthropicIntegration())
    register(OpenAIIntegration())
    register(GoogleAIIntegration())
    register(GoogleIntegration())
    register(NotionIntegration())
    register(DiscordIntegration())
    register(PerplexityIntegration())
