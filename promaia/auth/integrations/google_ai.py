"""Gemini (Google AI) integration — API key only."""

import httpx

from promaia.auth.base import AuthMode, Integration


class GoogleAIIntegration(Integration):

    def __init__(self):
        super().__init__(
            name="google_ai",
            display_name="Gemini (Google)",
            auth_modes=[AuthMode.API_KEY],
            env_key="GOOGLE_API_KEY",
            key_prefix="AIza",
            key_url="https://aistudio.google.com/apikey",
            help_lines=[
                "To get your API key:",
                "  1. Go to https://aistudio.google.com/apikey",
                "  2. Click 'Create API Key'",
                "  3. Copy the key (starts with AIza)",
            ],
        )

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={value}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                count = len(data.get("models", []))
                return True, f"Key validated ({count} models available)"
            elif resp.status_code in (400, 403):
                return False, "Invalid API key"
            else:
                return False, f"Unexpected response: HTTP {resp.status_code}"
        except httpx.TimeoutException:
            return False, "Connection timed out — check your internet connection"
        except httpx.ConnectError:
            return False, "Could not connect to Google API — check your internet"
        except Exception as e:
            return False, f"Validation error: {e}"
