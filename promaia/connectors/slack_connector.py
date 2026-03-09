"""
Slack connector implementation for Promaia.

This module provides a Slack bot API connector that integrates with the existing
Promaia architecture for message synchronization and storage.

Mirrors the Discord connector pattern for consistency.
"""
from __future__ import annotations

import os
import json
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Union
from pathlib import Path

from .base import BaseConnector, QueryFilter, DateRangeFilter, SyncResult

logger = logging.getLogger(__name__)

# Lazy import for slack_sdk - only loaded when SlackConnector is actually instantiated
slack_sdk = None
SlackApiError = None


def _ensure_slack_imported():
    """Ensure slack_sdk is imported. Raises ImportError if not available."""
    global slack_sdk, SlackApiError
    if slack_sdk is None:
        try:
            import slack_sdk
            from slack_sdk.errors import SlackApiError as error_class
            SlackApiError = error_class
        except ImportError:
            raise ImportError(
                "Slack integration requires slack-sdk\n"
                "Install with: pip install slack-sdk"
            )


class SlackConnector(BaseConnector):
    """Slack bot API connector for message synchronization."""
    
    # Slack rate limits (configurable per connector)
    RATE_LIMIT_REQUESTS_PER_MINUTE = 50  # Tier 3 methods
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # Ensure slack_sdk is available before proceeding
        _ensure_slack_imported()
        
        self.workspace_id = config.get("database_id")  # Slack workspace ID
        self.workspace = config.get("workspace")
        
        # Bot configuration
        self.bot_token = config.get("bot_token")
        if not self.bot_token:
            self.bot_token = os.environ.get("SLACK_BOT_TOKEN")
        
        self.client = None
        self._connected = False
        
        # Rate limiting for API compliance
        self._last_request_time = 0
        self._rate_limit_delay = 60.0 / self.RATE_LIMIT_REQUESTS_PER_MINUTE  # ~1.2 seconds
        self._efficient_rate_limit_delay = 0.1  # 100ms for pagination
    
    async def _rate_limit(self):
        """Ensure we don't exceed Slack's rate limits."""
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - self._last_request_time
        
        if time_since_last < self._rate_limit_delay:
            sleep_time = self._rate_limit_delay - time_since_last
            await asyncio.sleep(sleep_time)
        
        self._last_request_time = asyncio.get_event_loop().time()
    
    async def _rate_limit_efficient(self):
        """Apply efficient rate limiting for pagination."""
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - self._last_request_time
        
        if time_since_last < self._efficient_rate_limit_delay:
            sleep_time = self._efficient_rate_limit_delay - time_since_last
            await asyncio.sleep(sleep_time)
        
        self._last_request_time = asyncio.get_event_loop().time()
    
    async def connect(self) -> bool:
        """Establish connection to Slack API using bot token."""
        try:
            if not self.bot_token:
                raise ValueError("Slack bot token not provided in config or environment")
            
            # Create Slack client
            self.client = slack_sdk.WebClient(token=self.bot_token)
            
            # Test authentication
            response = self.client.auth_test()
            
            self._connected = True
            self.logger.info(f"Slack connector initialized for workspace {response['team']}")
            
            return True
        
        except Exception as e:
            self.logger.error(f"Failed to connect to Slack: {e}")
            return False
    
    async def test_connection(self) -> bool:
        """Test if the Slack connection is working."""
        if not self._connected:
            await self.connect()
        
        try:
            response = self.client.auth_test()
            self.logger.info(f"Connected to Slack workspace: {response['team']}")
            return True
        except Exception as e:
            self.logger.error(f"Slack connection test failed: {e}")
            return False
    
    async def get_database_schema(self) -> Dict[str, Any]:
        """Get the schema/properties for Slack messages."""
        return {
            "ts": {"type": "text", "description": "Message timestamp (unique ID)"},
            "user": {"type": "text", "description": "User ID who sent the message"},
            "username": {"type": "text", "description": "Username"},
            "channel": {"type": "text", "description": "Channel ID"},
            "channel_name": {"type": "text", "description": "Channel name"},
            "text": {"type": "text", "description": "Message text content"},
            "thread_ts": {"type": "text", "description": "Thread timestamp"},
            "reply_count": {"type": "number", "description": "Number of replies"},
            "reactions": {"type": "text", "description": "Reactions (JSON)"},
            "files": {"type": "text", "description": "Attached files (JSON)"},
            "edited": {"type": "checkbox", "description": "Was message edited"},
        }
    
    async def query_pages(
        self,
        filters: Optional[List[QueryFilter]] = None,
        date_filter: Optional[DateRangeFilter] = None,
        sort_by: Optional[str] = None,
        sort_direction: str = "desc",
        limit: Optional[int] = None,
        complex_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Query messages from Slack channels."""
        if not self._connected:
            await self.connect()
        
        max_retries = 3
        retry_delay = 2.0
        
        for attempt in range(max_retries):
            try:
                return await self._query_pages_impl(
                    filters, date_filter, sort_by, sort_direction, limit, complex_filter
                )
            except Exception as e:
                error_msg = str(e).lower()
                if attempt < max_retries - 1 and any(phrase in error_msg for phrase in [
                    "connection", "timeout", "network"
                ]):
                    self.logger.warning(f"Slack connection attempt {attempt + 1} failed: {e}")
                    self.logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 1.5
                    continue
                else:
                    self.logger.error(f"Failed to query Slack messages: {e}")
                    return []
        
        return []
    
    async def _query_pages_impl(
        self,
        filters: Optional[List[QueryFilter]] = None,
        date_filter: Optional[DateRangeFilter] = None,
        sort_by: Optional[str] = None,
        sort_direction: str = "desc",
        limit: Optional[int] = None,
        complex_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Internal implementation of query_pages with actual Slack API calls."""
        try:
            # Get channels to sync
            channel_ids = self._extract_channel_filters(filters, complex_filter)
            
            if not channel_ids:
                # No specific channels - sync all accessible channels
                self.logger.info("No specific channel filter provided - syncing all accessible channels")
                return await self._query_all_accessible_channels(date_filter, sort_direction, limit)
            
            self.logger.info(f"Querying {len(channel_ids)} Slack channels: {channel_ids}")
            
            # Calculate date range
            oldest = None
            latest = None
            if date_filter:
                if date_filter.start_date:
                    oldest = str(date_filter.start_date.timestamp())
                if date_filter.end_date:
                    latest = str(date_filter.end_date.timestamp())
            
            # Query each channel
            all_messages = []
            
            per_channel_limit = limit // len(channel_ids) if limit and len(channel_ids) > 1 else limit
            if per_channel_limit and per_channel_limit < 10:
                per_channel_limit = 10
            
            for channel_id in channel_ids:
                try:
                    # Apply rate limiting
                    await self._rate_limit()
                    
                    # Fetch messages from channel
                    channel_messages = await self._fetch_channel_messages(
                        channel_id=channel_id,
                        oldest=oldest,
                        latest=latest,
                        limit=per_channel_limit
                    )
                    
                    if channel_messages:
                        self.logger.info(f"Found {len(channel_messages)} messages in channel {channel_id}")
                        all_messages.extend(channel_messages)
                    
                except SlackApiError as e:
                    self.logger.warning(f"Error querying channel {channel_id}: {e.response['error']}")
                    continue
            
            self.logger.info(f"Total messages found across all channels: {len(all_messages)}")
            return all_messages
        
        except Exception as e:
            self.logger.error(f"Error in _query_pages_impl: {e}", exc_info=True)
            raise
    
    async def _fetch_channel_messages(
        self,
        channel_id: str,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch messages from a single Slack channel."""
        messages = []
        cursor = None
        message_count = 0
        
        while True:
            # Apply rate limiting
            if message_count > 0:
                await self._rate_limit_efficient()
            
            # Fetch a page of messages
            response = self.client.conversations_history(
                channel=channel_id,
                oldest=oldest,
                latest=latest,
                limit=100,  # Slack's max per request
                cursor=cursor
            )
            
            if not response['ok']:
                break
            
            # Convert messages to our format
            for msg in response.get('messages', []):
                message_data = await self._convert_message_to_data(msg, channel_id)
                messages.append(message_data)
                message_count += 1
                
                if limit and message_count >= limit:
                    return messages
            
            # Check for more pages
            cursor = response.get('response_metadata', {}).get('next_cursor')
            if not cursor:
                break
        
        return messages
    
    async def _convert_message_to_data(
        self,
        message: Dict[str, Any],
        channel_id: str
    ) -> Dict[str, Any]:
        """Convert a Slack message to our data format."""
        # Get channel info
        channel_name = await self._get_channel_name(channel_id)
        
        # Get user info
        user_id = message.get('user', '')
        username = await self._get_username(user_id)
        
        return {
            'id': f"msg_{message.get('ts', '')}",
            'ts': message.get('ts', ''),
            'user': user_id,
            'username': username,
            'channel': channel_id,
            'channel_name': channel_name,
            'text': message.get('text', ''),
            'thread_ts': message.get('thread_ts'),
            'reply_count': message.get('reply_count', 0),
            'reactions': json.dumps(message.get('reactions', [])),
            'files': json.dumps(message.get('files', [])),
            'edited': 'edited' in message,
            'timestamp': datetime.fromtimestamp(float(message.get('ts', '0')), tz=timezone.utc).isoformat(),
        }
    
    async def _get_channel_name(self, channel_id: str) -> str:
        """Get channel name from ID."""
        try:
            response = self.client.conversations_info(channel=channel_id)
            if response['ok']:
                return response['channel']['name']
        except:
            pass
        return channel_id
    
    async def _get_username(self, user_id: str) -> str:
        """Get username from user ID."""
        if not user_id:
            return "Unknown"
        
        try:
            response = self.client.users_info(user=user_id)
            if response['ok']:
                return response['user'].get('name', user_id)
        except:
            pass
        return user_id
    
    def _extract_channel_filters(
        self,
        filters: Optional[List[QueryFilter]],
        complex_filter: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """Extract channel IDs from filters."""
        channel_ids = []
        
        if filters:
            for f in filters:
                if f.property_name in ['channel', 'channel_id']:
                    if f.operator == 'eq':
                        channel_ids.append(f.value)
                    elif f.operator == 'in' and isinstance(f.value, list):
                        channel_ids.extend(f.value)
        
        if complex_filter and complex_filter.get('type') == 'complex':
            or_clauses = complex_filter.get('or_clauses', [])
            for and_conditions in or_clauses:
                for condition in and_conditions:
                    if condition.get('property') in ['channel', 'channel_id']:
                        if condition.get('operator') == '=':
                            channel_ids.append(condition.get('value'))
        
        return channel_ids
    
    async def _query_all_accessible_channels(
        self,
        date_filter: Optional[DateRangeFilter] = None,
        sort_direction: str = "desc",
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Query messages from all accessible channels."""
        try:
            # Get all channels
            response = self.client.conversations_list(types="public_channel,private_channel")
            channels = response['channels']
            
            self.logger.info(f"Found {len(channels)} channels to sync")
            
            oldest = None
            latest = None
            if date_filter:
                if date_filter.start_date:
                    oldest = str(date_filter.start_date.timestamp())
                if date_filter.end_date:
                    latest = str(date_filter.end_date.timestamp())
            
            all_messages = []
            
            for channel in channels:
                channel_id = channel['id']
                
                try:
                    await self._rate_limit()
                    
                    channel_limit = limit // len(channels) if limit else 100
                    if channel_limit < 10:
                        channel_limit = 10
                    
                    messages = await self._fetch_channel_messages(
                        channel_id=channel_id,
                        oldest=oldest,
                        latest=latest,
                        limit=channel_limit
                    )
                    
                    if messages:
                        self.logger.info(f"Found {len(messages)} messages in #{channel['name']}")
                        all_messages.extend(messages)
                
                except SlackApiError as e:
                    if e.response['error'] == 'not_in_channel':
                        self.logger.debug(f"Bot not in channel #{channel['name']}")
                    else:
                        self.logger.warning(f"Error fetching from #{channel['name']}: {e}")
                    continue
            
            # Sort
            if sort_direction == "desc":
                all_messages.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            else:
                all_messages.sort(key=lambda x: x.get('timestamp', ''))
            
            # Apply limit
            if limit and len(all_messages) > limit:
                all_messages = all_messages[:limit]
            
            return all_messages
        
        except Exception as e:
            self.logger.error(f"Failed to query all Slack channels: {e}", exc_info=True)
            return []
    
    async def discover_accessible_channels(self) -> Dict[str, Any]:
        """Discover all channels the bot has access to."""
        if not self._connected:
            await self.connect()
        
        try:
            self.logger.info("Discovering accessible Slack channels...")
            
            response = self.client.conversations_list(
                types="public_channel,private_channel",
                exclude_archived=True
            )
            
            channels = response['channels']
            accessible_channels = []
            
            for channel in channels:
                accessible_channels.append({
                    'id': channel['id'],
                    'name': channel['name'],
                    'is_private': channel.get('is_private', False),
                    'is_member': channel.get('is_member', False),
                    'discovered_at': datetime.now().isoformat()
                })
            
            self.logger.info(f"Discovery complete: {len(accessible_channels)} channels accessible")
            
            return {
                'workspace_id': self.workspace_id,
                'channels': accessible_channels,
                'discovered_at': datetime.now().isoformat(),
                'total_channels': len(accessible_channels)
            }
        
        except Exception as e:
            self.logger.error(f"Error discovering Slack channels: {e}", exc_info=True)
            return {'workspace_id': self.workspace_id, 'channels': []}
    
    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]:
        """Get full content of a specific Slack message."""
        # page_id format: "msg_{ts}"
        ts = page_id.replace('msg_', '')
        
        # We need channel context to fetch a specific message
        # This is a limitation - return empty for now
        self.logger.warning(f"get_page_content not fully implemented for Slack messages")
        return {}
    
    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        """Get properties of a specific Slack message."""
        content = await self.get_page_content(page_id, include_properties=True)
        return content
    
    async def sync_to_local(self, *args, **kwargs) -> SyncResult:
        """Sync Slack messages to local storage - placeholder for backwards compatibility."""
        raise NotImplementedError("Use sync_to_local_unified for Slack connector")
    
    async def sync_to_local_unified(
        self,
        storage,
        db_config,
        filters: Optional[List[QueryFilter]] = None,
        date_filter: Optional[DateRangeFilter] = None,
        include_properties: bool = True,
        force_update: bool = False,
        excluded_properties: List[str] = None,
        complex_filter: Optional[Dict[str, Any]] = None
    ) -> SyncResult:
        """Sync Slack messages to local storage using unified storage system."""
        result = SyncResult()
        result.start_time = datetime.now()
        
        try:
            # Query Slack for messages
            limit = None if date_filter else self.config.get("sync_limit", 100)
            
            self.logger.info(f"Querying Slack with date_filter: {date_filter}")
            messages = await self.query_pages(
                filters=filters,
                date_filter=date_filter,
                limit=limit,
                complex_filter=complex_filter
            )
            
            if not messages:
                self.logger.info("No new Slack messages found from query.")
                return result
            
            self.logger.info(f"Found {len(messages)} Slack messages from query.")
            result.pages_fetched = len(messages)
            
            # Prepare pages for storage
            pages_to_save = []
            for message in messages:
                page_data = self._prepare_page_for_storage(message, db_config, excluded_properties)
                pages_to_save.append(page_data)
            
            # Save to storage
            saved_count = 0
            skipped_count = 0
            
            for page_data in pages_to_save:
                try:
                    # Create channel-specific subdirectories
                    channel_name = page_data.get("channel_name", "unknown")
                    safe_channel_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in channel_name)
                    safe_channel_name = safe_channel_name.strip("_").replace(" ", "_")
                    
                    import copy
                    from promaia.storage.unified_storage import UnifiedStorage
                    slack_db_config = copy.deepcopy(db_config)
                    resolved_md_dir = UnifiedStorage._resolve_md_dir(slack_db_config)
                    channel_md_dir = os.path.join(resolved_md_dir, safe_channel_name)
                    slack_db_config.markdown_directory = channel_md_dir

                    os.makedirs(channel_md_dir, exist_ok=True)
                    
                    saved_files = storage.save_content(
                        page_id=page_data["page_id"],
                        title=page_data["metadata"]["title"],
                        content_data=page_data["metadata"],
                        database_config=slack_db_config,
                        markdown_content=page_data["content"]
                    )
                    
                    if saved_files:
                        result.add_success(saved_files.get('markdown', ''))
                        saved_count += 1
                    else:
                        result.add_skip()
                        skipped_count += 1
                
                except Exception as e:
                    self.logger.error(f"Error saving Slack message {page_data['page_id']}: {e}")
                    result.add_error(f"Failed to save message {page_data['page_id']}: {e}")
            
            self.logger.info(f"Slack sync completed: {saved_count} saved, {skipped_count} skipped")
            result.end_time = datetime.now()
            
            return result
        
        except Exception as e:
            self.logger.error(f"Slack sync failed: {e}", exc_info=True)
            result.add_error(f"Slack sync failed: {e}")
            result.end_time = datetime.now()
            return result
    
    def _prepare_page_for_storage(
        self,
        message: Dict[str, Any],
        db_config,
        excluded_properties: List[str] = None
    ) -> Dict[str, Any]:
        """Prepare message data for the unified storage format."""
        page_id = message['id']
        markdown_content = self._message_to_markdown(message)
        
        channel_name = message.get('channel_name', 'unknown')
        timestamp_str = message.get('ts', 'unknown')
        username = message.get('username', 'Unknown')
        
        # Create filename-safe title
        text_snippet = message.get('text', '')[:50]
        clean_snippet = "".join(c if c.isalnum() or c in " -_" else "_" for c in text_snippet)
        filename_title = f"{timestamp_str}_{username}_{clean_snippet}"
        
        metadata = {
            "page_id": page_id,
            "title": filename_title,
            "created_time": message.get('timestamp'),
            "last_edited_time": message.get('timestamp'),
            "synced_time": datetime.now(timezone.utc).isoformat(),
            "source_id": message.get('ts'),
            "data_source": "slack",
            "content_type": "message",
            "properties": {
                "username": username,
                "channel_name": channel_name,
                "text": message.get('text', ''),
                "thread_ts": message.get('thread_ts'),
            },
            "slack_channel_name": channel_name,
            "slack_channel_id": message.get('channel'),
        }
        
        return {
            "page_id": page_id,
            "content": markdown_content,
            "metadata": metadata,
            "channel_name": channel_name,
        }
    
    def _message_to_markdown(self, message: Dict[str, Any]) -> str:
        """Convert a Slack message to markdown."""
        text = message.get('text', '*[No text content]*')
        
        # Add reactions
        reactions_section = ""
        if message.get('reactions'):
            try:
                reactions = json.loads(message['reactions'])
                if reactions:
                    reactions_section = "\n\n## Reactions\n\n"
                    for reaction in reactions:
                        reactions_section += f":{reaction.get('name', '?')}: x{reaction.get('count', 0)}  "
            except:
                pass
        
        # Add files
        files_section = ""
        if message.get('files'):
            try:
                files = json.loads(message['files'])
                if files:
                    files_section = "\n\n## Files\n\n"
                    for file in files:
                        files_section += f"- {file.get('name', 'Unknown')} ({file.get('size', 0)} bytes)\n"
            except:
                pass
        
        return text + reactions_section + files_section
    
    async def cleanup(self):
        """Clean up Slack connector."""
        # No persistent connections to clean up
        pass
