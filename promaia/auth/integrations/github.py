"""GitHub integration — personal access token only.

Used by the static-site CMS target (``promaia.cms_site``) to clone, pull and
push the site repo over HTTPS. A fine-grained token scoped to that one
repository with "Contents: Read and write" is enough.
"""

import httpx

from promaia.auth.base import AuthMode, Integration


class GitHubIntegration(Integration):

    def __init__(self):
        super().__init__(
            name="github",
            display_name="GitHub",
            auth_modes=[AuthMode.API_KEY],
            env_key="GITHUB_TOKEN",
            key_url="https://github.com/settings/personal-access-tokens/new",
            help_lines=[
                "To create a token for the website repo:",
                "  1. Go to https://github.com/settings/personal-access-tokens/new",
                "  2. Repository access: only the site repo (e.g. koiib-website)",
                "  3. Permissions: Contents → Read and write",
                "  4. Generate and copy the token (starts with github_pat_)",
            ],
        )

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        headers = {
            "Authorization": f"Bearer {value}",
            "Accept": "application/vnd.github+json",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get("https://api.github.com/user", headers=headers)
            if resp.status_code == 200:
                login = resp.json().get("login", "unknown")
                return True, f"Connected as {login}"
            if resp.status_code == 401:
                return False, "Invalid token (authentication failed)"
            if resp.status_code == 403:
                # Fine-grained tokens without account permissions can't read /user
                # but still work for repo contents.
                return True, "Token accepted (no access to /user; repo access not checked)"
            return False, f"Unexpected response: HTTP {resp.status_code}"
        except httpx.TimeoutException:
            return False, "Connection timed out — check your internet connection"
        except httpx.ConnectError:
            return False, "Could not connect to GitHub — check your internet"
        except Exception as e:
            return False, f"Validation error: {e}"
