"""
Team/User management for Promaia.

Stores information about team members scraped from Slack/Discord,
allowing the orchestrator to know who users are and how to reach them.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field

from promaia.utils.env_writer import get_data_dir

logger = logging.getLogger(__name__)


def _default_team_config_path() -> Path:
    return get_data_dir() / "team.json"


@dataclass
class TeamMember:
    """
    A team member that Promaia knows about.

    Attributes:
        id: Unique identifier (usually the primary platform user ID)
        name: Display name (how to refer to them in goals)
        aliases: Alternative names/nicknames
        slack_id: Slack user ID (if connected)
        discord_id: Discord user ID (if connected)
        email: Email address (if available)
        timezone: Timezone string (e.g., "America/New_York")
        role: Role/title (e.g., "Engineer", "Manager")
        notes: Any notes about this person
        last_synced: When this record was last updated
    """
    id: str
    name: str
    aliases: List[str] = field(default_factory=list)
    slack_id: Optional[str] = None
    slack_username: Optional[str] = None
    discord_id: Optional[str] = None
    discord_username: Optional[str] = None
    email: Optional[str] = None
    timezone: Optional[str] = None
    role: Optional[str] = None
    notes: Optional[str] = None
    avatar_url: Optional[str] = None
    is_bot: bool = False
    last_synced: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TeamMember':
        return cls(**data)

    def matches_name(self, query: str) -> bool:
        """Check if this member matches a name query (case-insensitive)."""
        query_lower = query.lower().strip()

        # Check main name
        if query_lower in self.name.lower():
            return True

        # Check aliases
        for alias in self.aliases:
            if query_lower in alias.lower():
                return True

        # Check usernames
        if self.slack_username and query_lower in self.slack_username.lower():
            return True
        if self.discord_username and query_lower in self.discord_username.lower():
            return True

        return False


@dataclass
class SlackChannel:
    """A Slack channel that Promaia knows about."""
    id: str                          # "C01234567"
    name: str                        # "engineering" (no #)
    is_private: bool = False
    is_member: bool = False          # Bot can post here
    topic: Optional[str] = None
    purpose: Optional[str] = None
    last_synced: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SlackChannel':
        return cls(**data)


class TeamManager:
    """
    Manages team member data for Promaia.

    Usage:
        team = TeamManager()
        team.sync_from_slack(bot_token)

        # Later, find a user
        member = team.find_member("Alice")
        if member:
            slack_id = member.slack_id
    """

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize team manager.

        Args:
            config_path: Path to team config file (default: maia-data/team.json)
        """
        self.config_path = config_path or _default_team_config_path()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        self.members: Dict[str, TeamMember] = {}
        self.channels: Dict[str, SlackChannel] = {}
        self._last_updated: Optional[str] = None
        self._load()

    def _load(self):
        """Load team data from config file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)
                    self.members = {
                        k: TeamMember.from_dict(v)
                        for k, v in data.get('members', {}).items()
                    }
                    self.channels = {
                        k: SlackChannel.from_dict(v)
                        for k, v in data.get('channels', {}).items()
                    }
                    self._last_updated = data.get('last_updated')
                logger.info(f"Loaded {len(self.members)} team members, {len(self.channels)} channels from {self.config_path}")
            except Exception as e:
                logger.error(f"Error loading team config: {e}")
                self.members = {}
                self.channels = {}
        else:
            self.members = {}
            self.channels = {}

    def _save(self):
        """Save team data to config file."""
        try:
            self._last_updated = datetime.now(timezone.utc).isoformat()
            data = {
                'members': {k: v.to_dict() for k, v in self.members.items()},
                'channels': {k: v.to_dict() for k, v in self.channels.items()},
                'last_updated': self._last_updated
            }
            with open(self.config_path, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved {len(self.members)} team members, {len(self.channels)} channels to {self.config_path}")
        except Exception as e:
            logger.error(f"Error saving team config: {e}")

    def add_member(self, member: TeamMember):
        """Add or update a team member."""
        self.members[member.id] = member
        self._save()

    def remove_member(self, member_id: str):
        """Remove a team member."""
        if member_id in self.members:
            del self.members[member_id]
            self._save()

    def get_member(self, member_id: str) -> Optional[TeamMember]:
        """Get a team member by ID."""
        return self.members.get(member_id)

    def find_member(self, name: str) -> Optional[TeamMember]:
        """
        Find a team member by name, alias, or username.

        Args:
            name: Name to search for (case-insensitive, partial match)

        Returns:
            TeamMember if found, None otherwise
        """
        name_lower = name.lower().strip()

        # First, try exact match on name
        for member in self.members.values():
            if member.name.lower() == name_lower:
                return member

        # Then try partial match
        for member in self.members.values():
            if member.matches_name(name):
                return member

        return None

    def find_by_slack_id(self, slack_id: str) -> Optional[TeamMember]:
        """Find a team member by Slack user ID."""
        for member in self.members.values():
            if member.slack_id == slack_id:
                return member
        return None

    def find_by_discord_id(self, discord_id: str) -> Optional[TeamMember]:
        """Find a team member by Discord user ID."""
        for member in self.members.values():
            if member.discord_id == discord_id:
                return member
        return None

    def list_members(self, include_bots: bool = False) -> List[TeamMember]:
        """List all team members."""
        members = list(self.members.values())
        if not include_bots:
            members = [m for m in members if not m.is_bot]
        return sorted(members, key=lambda m: m.name.lower())

    def is_stale(self, max_age_hours: int = 24) -> bool:
        """Return True if team data is empty or older than max_age_hours."""
        if not self.members and not self.channels:
            return True
        if not self._last_updated:
            return True
        try:
            last = datetime.fromisoformat(self._last_updated)
            age = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            return age > max_age_hours
        except (ValueError, TypeError):
            return True

    def find_channel(self, name: str) -> Optional[SlackChannel]:
        """Find a channel by name (case-insensitive, strips leading #)."""
        name = name.lstrip('#').lower().strip()
        for channel in self.channels.values():
            if channel.name.lower() == name:
                return channel
        return None

    def list_channels(self, member_only: bool = False) -> List[SlackChannel]:
        """List all channels, sorted by name."""
        channels = list(self.channels.values())
        if member_only:
            channels = [c for c in channels if c.is_member]
        return sorted(channels, key=lambda c: c.name.lower())

    def get_roster_summary(self, platform: str = "slack") -> str:
        """Get a compact roster string for AI prompts."""
        parts = []

        # Team members
        members = self.list_members(include_bots=False)
        if members:
            member_strs = []
            for m in members:
                if platform == "slack" and m.slack_username:
                    member_strs.append(f"{m.name} (@{m.slack_username})")
                elif platform == "discord" and m.discord_username:
                    member_strs.append(f"{m.name} (@{m.discord_username})")
                else:
                    member_strs.append(m.name)
            parts.append(f"Team members: {', '.join(member_strs)}")

        # Channels
        channels = self.list_channels(member_only=True)
        if channels:
            channel_strs = [f"#{c.name}" for c in channels]
            parts.append(f"Channels: {', '.join(channel_strs)}")

        return "\n".join(parts)

    async def sync_channels_from_slack(self, bot_token: str) -> Dict[str, int]:
        """
        Sync channels from Slack workspace.

        Args:
            bot_token: Slack bot token (xoxb-...)

        Returns:
            Dict with counts: {'added': N, 'updated': N, 'total': N}
        """
        try:
            from slack_sdk.web.async_client import AsyncWebClient

            client = AsyncWebClient(token=bot_token)

            added = 0
            updated = 0
            now = datetime.now(timezone.utc).isoformat()
            cursor = None

            while True:
                kwargs: Dict[str, Any] = {
                    'types': 'public_channel,private_channel',
                    'exclude_archived': True,
                    'limit': 200,
                }
                if cursor:
                    kwargs['cursor'] = cursor

                result = await client.conversations_list(**kwargs)

                if not result['ok']:
                    raise Exception(f"Slack API error: {result.get('error')}")

                for ch in result['channels']:
                    channel_id = ch['id']
                    existing = self.channels.get(channel_id)

                    channel = SlackChannel(
                        id=channel_id,
                        name=ch.get('name', ''),
                        is_private=ch.get('is_private', False),
                        is_member=ch.get('is_member', False),
                        topic=(ch.get('topic') or {}).get('value') or None,
                        purpose=(ch.get('purpose') or {}).get('value') or None,
                        last_synced=now,
                    )
                    self.channels[channel_id] = channel

                    if existing:
                        updated += 1
                    else:
                        added += 1

                # Pagination
                next_cursor = result.get('response_metadata', {}).get('next_cursor')
                if not next_cursor:
                    break
                cursor = next_cursor

            self._save()

            logger.info(f"Channel sync complete: {added} added, {updated} updated, {len(self.channels)} total")

            return {
                'added': added,
                'updated': updated,
                'total': len(self.channels)
            }

        except ImportError:
            raise ImportError("slack-sdk is required for Slack sync. Install with: pip install slack-sdk")
        except Exception as e:
            logger.error(f"Error syncing channels from Slack: {e}")
            raise

    async def sync_from_slack(self, bot_token: str) -> Dict[str, int]:
        """
        Sync team members from Slack workspace.

        Args:
            bot_token: Slack bot token (xoxb-...)

        Returns:
            Dict with counts: {'added': N, 'updated': N, 'total': N}
        """
        try:
            from slack_sdk.web.async_client import AsyncWebClient

            client = AsyncWebClient(token=bot_token)

            # Get all users
            result = await client.users_list()

            if not result['ok']:
                raise Exception(f"Slack API error: {result.get('error')}")

            added = 0
            updated = 0
            now = datetime.now(timezone.utc).isoformat()

            for user in result['members']:
                # Skip deleted users
                if user.get('deleted'):
                    continue

                slack_id = user['id']
                profile = user.get('profile', {})

                # Check if we already have this user
                existing = self.find_by_slack_id(slack_id)

                # Build member data
                name = (
                    profile.get('display_name') or
                    profile.get('real_name') or
                    user.get('name', 'Unknown')
                )

                if existing:
                    # Update existing member
                    existing.name = name
                    existing.slack_username = user.get('name')
                    existing.email = profile.get('email')
                    existing.timezone = user.get('tz')
                    existing.avatar_url = profile.get('image_72')
                    existing.is_bot = user.get('is_bot', False)
                    existing.last_synced = now
                    updated += 1
                else:
                    # Create new member
                    member = TeamMember(
                        id=f"slack_{slack_id}",
                        name=name,
                        slack_id=slack_id,
                        slack_username=user.get('name'),
                        email=profile.get('email'),
                        timezone=user.get('tz'),
                        avatar_url=profile.get('image_72'),
                        is_bot=user.get('is_bot', False),
                        last_synced=now
                    )
                    self.members[member.id] = member
                    added += 1

            self._save()

            logger.info(f"Slack sync complete: {added} added, {updated} updated, {len(self.members)} total")

            return {
                'added': added,
                'updated': updated,
                'total': len(self.members)
            }

        except ImportError:
            raise ImportError("slack-sdk is required for Slack sync. Install with: pip install slack-sdk")
        except Exception as e:
            logger.error(f"Error syncing from Slack: {e}")
            raise

    async def sync_from_discord(self, bot_token: str, guild_id: str) -> Dict[str, int]:
        """
        Sync team members from Discord server.

        Args:
            bot_token: Discord bot token
            guild_id: Discord server/guild ID

        Returns:
            Dict with counts: {'added': N, 'updated': N, 'total': N}
        """
        try:
            import aiohttp

            headers = {
                'Authorization': f'Bot {bot_token}',
                'Content-Type': 'application/json'
            }

            added = 0
            updated = 0
            now = datetime.now(timezone.utc).isoformat()

            async with aiohttp.ClientSession() as session:
                # Get guild members
                url = f'https://discord.com/api/v10/guilds/{guild_id}/members?limit=1000'
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        raise Exception(f"Discord API error: {resp.status} - {error}")

                    members_data = await resp.json()

            for member in members_data:
                user = member.get('user', {})
                discord_id = user.get('id')

                if not discord_id:
                    continue

                # Check if we already have this user
                existing = self.find_by_discord_id(discord_id)

                # Build member data
                name = member.get('nick') or user.get('global_name') or user.get('username', 'Unknown')

                if existing:
                    # Update existing member
                    existing.name = name
                    existing.discord_username = user.get('username')
                    existing.avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{user.get('avatar')}.png" if user.get('avatar') else None
                    existing.is_bot = user.get('bot', False)
                    existing.last_synced = now
                    updated += 1
                else:
                    # Create new member
                    new_member = TeamMember(
                        id=f"discord_{discord_id}",
                        name=name,
                        discord_id=discord_id,
                        discord_username=user.get('username'),
                        avatar_url=f"https://cdn.discordapp.com/avatars/{discord_id}/{user.get('avatar')}.png" if user.get('avatar') else None,
                        is_bot=user.get('bot', False),
                        last_synced=now
                    )
                    self.members[new_member.id] = new_member
                    added += 1

            self._save()

            logger.info(f"Discord sync complete: {added} added, {updated} updated, {len(self.members)} total")

            return {
                'added': added,
                'updated': updated,
                'total': len(self.members)
            }

        except ImportError:
            raise ImportError("aiohttp is required for Discord sync. Install with: pip install aiohttp")
        except Exception as e:
            logger.error(f"Error syncing from Discord: {e}")
            raise


# Singleton instance
_team_manager: Optional[TeamManager] = None


def get_team_manager() -> TeamManager:
    """Get the global team manager instance."""
    global _team_manager
    if _team_manager is None:
        _team_manager = TeamManager()
    return _team_manager
