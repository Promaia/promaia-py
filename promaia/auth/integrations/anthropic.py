"""Claude (Anthropic) integration — API key only."""

import httpx

from promaia.auth.base import AuthMode, Integration


class AnthropicIntegration(Integration):

    def __init__(self):
        super().__init__(
            name="anthropic",
            display_name="Claude (Anthropic)",
            auth_modes=[AuthMode.API_KEY],
            env_key="ANTHROPIC_API_KEY",
            key_prefix="sk-ant-",
            key_url="https://console.anthropic.com/settings/keys",
            help_lines=[
                "To get your API key:",
                "  1. Go to https://console.anthropic.com/settings/keys",
                "  2. Click 'Create Key'",
                "  3. Copy the key (starts with sk-ant-)",
            ],
            recommended=False,
        )

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": value,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": "claude-sonnet-4-5-20250929",
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
            return False, "Could not connect to Anthropic API — check your internet"
        except Exception as e:
            return False, f"Validation error: {e}"
