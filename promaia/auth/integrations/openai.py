"""ChatGPT (OpenAI) integration — API key only."""

import httpx

from promaia.auth.base import AuthMode, Integration


class OpenAIIntegration(Integration):

    def __init__(self):
        super().__init__(
            name="openai",
            display_name="ChatGPT (OpenAI)",
            auth_modes=[AuthMode.API_KEY],
            env_key="OPENAI_API_KEY",
            key_prefix="sk-",
            key_url="https://platform.openai.com/api-keys",
            help_lines=[
                "To get your API key:",
                "  1. Go to https://platform.openai.com/api-keys",
                "  2. Click 'Create new secret key'",
                "  3. Copy the key (starts with sk-)",
            ],
        )

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {value}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)
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
            return False, "Could not connect to OpenAI API — check your internet"
        except Exception as e:
            return False, f"Validation error: {e}"
