"""Base classes for the unified credential management system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class AuthMode(Enum):
    API_KEY = "api_key"
    OAUTH = "oauth"
    USER_OAUTH = "user_oauth"


@dataclass
class Integration:
    """Base class for all credential integrations.

    Subclasses override ``validate_credential`` (and optionally the
    storage methods) to implement provider-specific logic.  The default
    ``get_default_credential`` / ``store_credential`` / ``clear_credential``
    implementations read and write the key specified by ``env_key`` in the
    project ``.env`` file via ``promaia.utils.env_writer``.
    """

    name: str
    display_name: str
    auth_modes: list[AuthMode]
    env_key: str | None = None
    oauth_provider: str | None = None
    key_url: str | None = None
    key_prefix: str | None = None
    help_lines: list[str] = field(default_factory=list)
    recommended: bool = False

    # ── credential storage (default: .env) ──────────────────────────

    def get_default_credential(self) -> str | None:
        """Return the default stored credential, or *None*."""
        if self.env_key is None:
            return None
        from promaia.utils.env_writer import read_env_value
        return read_env_value(self.env_key)

    def store_credential(self, value: str, **kwargs) -> None:
        """Persist *value* to the appropriate store."""
        if self.env_key is None:
            raise NotImplementedError(
                f"{self.name}: no env_key set and store_credential not overridden"
            )
        from promaia.utils.env_writer import ensure_env_file, update_env_value
        ensure_env_file()
        update_env_value(self.env_key, value)

    def clear_credential(self) -> None:
        """Remove the stored credential."""
        if self.env_key is None:
            raise NotImplementedError(
                f"{self.name}: no env_key set and clear_credential not overridden"
            )
        from promaia.utils.env_writer import ensure_env_file, update_env_value
        ensure_env_file()
        update_env_value(self.env_key, "")

    # ── validation ──────────────────────────────────────────────────

    async def validate_credential(self, value: str) -> tuple[bool, str]:
        """Validate a credential value.

        Returns ``(success, message)`` where *message* is a human-readable
        description of the result (e.g. ``"Connected as ..."`` or an error).
        """
        raise NotImplementedError(
            f"{self.name}: validate_credential not implemented"
        )
