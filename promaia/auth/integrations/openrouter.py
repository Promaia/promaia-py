"""OpenRouter integration — OAuth via proxy (primary) or manual API key."""

import httpx

from promaia.auth.base import AuthMode, Integration


class OpenRouterIntegration(Integration):

    def __init__(self):
        super().__init__(
            name="openrouter",
            display_name="OpenRouter",
            auth_modes=[AuthMode.OAUTH, AuthMode.API_KEY],
            env_key="OPENROUTER_API_KEY",
            oauth_provider="openrouter",
            key_prefix="sk-or-",
            key_url="https://openrouter.ai/keys",
            help_lines=[
                "To get your API key:",
                "  1. Go to https://openrouter.ai/keys",
                "  2. Click 'Create Key'",
                "  3. Copy the key (starts with sk-or-)",
            ],
            recommended=False,
        )

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {value}",
            "Content-Type": "application/json",
        }
        body = {
            "model": "anthropic/claude-sonnet-4-5",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=body, headers=headers)
            if resp.status_code == 200:
                return True, "Key validated successfully"
            elif resp.status_code == 401:
                return False, "Invalid API key (authentication failed)"
            elif resp.status_code == 403:
                return False, "API key lacks required permissions"
            elif resp.status_code == 429:
                return True, "Key is valid (rate-limited, but authenticated)"
            else:
                return False, f"Unexpected response: HTTP {resp.status_code}"
        except httpx.TimeoutException:
            return False, "Connection timed out — check your internet connection"
        except httpx.ConnectError:
            return False, "Could not connect to OpenRouter API — check your internet"
        except Exception as e:
            return False, f"Validation error: {e}"
