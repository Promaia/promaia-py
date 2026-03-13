"""Perplexity integration — API key only."""

import httpx

from promaia.auth.base import AuthMode, Integration


class PerplexityIntegration(Integration):

    def __init__(self):
        super().__init__(
            name="perplexity",
            display_name="Perplexity",
            auth_modes=[AuthMode.API_KEY],
            env_key="PERPLEXITY_API_KEY",
            key_prefix="pplx-",
            key_url="https://docs.perplexity.ai/guides/getting-started",
            help_lines=[
                "To get your API key:",
                "  1. Go to https://www.perplexity.ai/settings/api",
                "  2. Generate an API key",
                "  3. Copy the key (starts with pplx-)",
            ],
        )

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        url = "https://api.perplexity.ai/chat/completions"
        headers = {
            "Authorization": f"Bearer {value}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "sonar",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return True, "Key validated successfully"
            elif resp.status_code == 401:
                return False, "Invalid API key (authentication failed)"
            elif resp.status_code == 429:
                return True, "Key is valid (rate-limited, but authenticated)"
            else:
                return False, f"Unexpected response: HTTP {resp.status_code}"
        except httpx.TimeoutException:
            return False, "Connection timed out — check your internet connection"
        except httpx.ConnectError:
            return False, "Could not connect to Perplexity API — check your internet"
        except Exception as e:
            return False, f"Validation error: {e}"
