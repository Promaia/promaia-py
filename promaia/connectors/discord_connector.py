"""
Discord connector implementation for Maia.

This module provides a Discord bot API connector that integrates with the existing
Maia architecture for message synchronization and storage.
"""
from __future__ import annotations

import os
import json
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Union, TYPE_CHECKING
from pathlib import Path

from .base import BaseConnector, QueryFilter, DateRangeFilter, SyncResult

if TYPE_CHECKING:
    import discord

logger = logging.getLogger(__name__)

# Lazy import for discord - only loaded when DiscordConnector is actually instantiated
discord = None
commands = None

def _ensure_discord_imported():
    """Ensure discord.py is imported. Raises ImportError if not available."""
    global discord, commands
    if discord is None:
        try:
            import discord as discord_module
            from discord.ext import commands as commands_module
            discord = discord_module
            commands = commands_module
        except ImportError:
            raise ImportError(
                "Discord integration requires discord.py\n"
                "Install with: pip install discord.py"
            )

class DiscordConnector(BaseConnector):
    """Discord bot API connector for message synchronization."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        # Ensure discord.py is available before proceeding
        _ensure_discord_imported()

        self.server_id = config.get("database_id")  # Use server_id as database_id for consistency
        self.workspace = config.get("workspace")

        # Bot configuration
        self.bot_token = config.get("bot_token")
        self.intents = discord.Intents.default()
        self.intents.message_content = True  # Required to read message content
        self.intents.guilds = True
        self.intents.guild_messages = True
        
        self.client = None
        self.guild = None
        self._connected = False

        # OCR channel support — channels listed here download images + run OCR
        # instead of syncing text.  Stored in property_filters.ocr_channels.
        pf = config.get("property_filters") or {}
        self.ocr_channels: set = set(pf.get("ocr_channels", []))
        self.annotation_window: int = int(config.get("annotation_window_seconds", 60))

        # Rate limiting for API compliance
        self._last_request_time = 0
        self._rate_limit_delay = 1.0  # 1 second between requests (conservative)
        self._efficient_rate_limit_delay = 0.1  # 100ms for efficient pagination (Discord allows ~50 requests/second)
        
    async def _rate_limit(self):
        """Ensure we don't exceed Discord's rate limits."""
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - self._last_request_time
        
        if time_since_last < self._rate_limit_delay:
            sleep_time = self._rate_limit_delay - time_since_last
            await asyncio.sleep(sleep_time)
        
        self._last_request_time = asyncio.get_event_loop().time()

    async def _rate_limit_efficient(self):
        """Apply efficient rate limiting for pagination (Discord allows ~50 requests/second)."""
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - self._last_request_time
        
        if time_since_last < self._efficient_rate_limit_delay:
            sleep_time = self._efficient_rate_limit_delay - time_since_last
            await asyncio.sleep(sleep_time)
        
        self._last_request_time = asyncio.get_event_loop().time()

    async def connect(self) -> bool:
        """Establish connection to Discord API using bot token."""
        try:
            if not self.bot_token:
                raise ValueError("Discord bot token not provided in config")
            
            # For data access, we'll use a temporary client approach
            # Store connection info for later use
            self._connected = True
            
            self.logger.info(f"Discord connector initialized for server {self.server_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to connect to Discord: {e}")
            return False
    
    async def _get_guild_data(self):
        """Get guild data using a temporary client connection."""
        if not self.bot_token or not self.server_id:
            return None
            
        # Create temporary client for data access
        client = discord.Client(intents=self.intents)
        
        try:
            # Add connection timeout and better error handling
            await asyncio.wait_for(client.login(self.bot_token), timeout=30.0)
            
            # Get guild using HTTP API (no gateway connection needed)
            guild = await asyncio.wait_for(client.fetch_guild(int(self.server_id)), timeout=15.0)
            channels = await asyncio.wait_for(guild.fetch_channels(), timeout=15.0)
            
            # Convert to simple data structure
            guild_data = {
                'id': guild.id,
                'name': guild.name,
                'channels': []
            }
            
            for channel in channels:
                # Only include text-based channels (text=0, news=5)
                # Exclude voice channels (2), categories (4), stage voice (13), etc.
                if hasattr(channel, 'type') and channel.type in [discord.ChannelType.text, discord.ChannelType.news]:
                    guild_data['channels'].append({
                        'id': channel.id,
                        'name': channel.name,
                        'type': 'text'
                    })
            
            return guild_data
            
        finally:
            await client.close()

    async def test_connection(self) -> bool:
        """Test if the Discord connection is working."""
        if not self.client:
            if not await self.connect():
                return False
        
        try:
            # Test basic API access
            if self.guild:
                # Guild channels should be available after connection
                channels = self.guild.channels
                self.logger.info(f"Connected to Discord server: {self.guild.name}")
            return True
        except Exception as e:
            self.logger.error(f"Discord connection test failed: {e}")
            return False

    async def get_database_schema(self) -> Dict[str, Any]:
        """Get the schema/properties for Discord messages."""
        return {
            "author_id": {"type": "text", "description": "Message author user ID"},
            "author_name": {"type": "text", "description": "Message author username"},
            "author_display_name": {"type": "text", "description": "Message author display name"},
            "channel_id": {"type": "text", "description": "Channel ID where message was sent"},
            "channel_name": {"type": "text", "description": "Channel name"},
            "content": {"type": "text", "description": "Message content"},
            "timestamp": {"type": "date", "description": "Message timestamp"},
            "edited_timestamp": {"type": "date", "description": "Last edit timestamp"},
            "message_type": {"type": "select", "description": "Type of message (default, reply, etc.)"},
            "has_attachments": {"type": "checkbox", "description": "Has file attachments"},
            "attachment_count": {"type": "number", "description": "Number of attachments"},
            "has_embeds": {"type": "checkbox", "description": "Has embedded content"},
            "reaction_count": {"type": "number", "description": "Number of reactions"},
            "thread_id": {"type": "text", "description": "Thread ID if message is in a thread"},
            "reference_message_id": {"type": "text", "description": "ID of referenced message (for replies)"},
        }

    async def query_pages(self, 
                         filters: Optional[List[QueryFilter]] = None,
                         date_filter: Optional[DateRangeFilter] = None,
                         sort_by: Optional[str] = None,
                         sort_direction: str = "desc",
                         limit: Optional[int] = None,
                         complex_filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Query messages from Discord channels."""
        if not self._connected:
            await self.connect()
        
        max_retries = 3
        retry_delay = 2.0
        
        for attempt in range(max_retries):
            try:
                return await self._query_pages_impl(filters, date_filter, sort_by, sort_direction, limit, complex_filter)
                
            except Exception as e:
                error_msg = str(e).lower()
                if attempt < max_retries - 1 and any(phrase in error_msg for phrase in [
                    "cannot connect to host discord.com",
                    "nodename nor servname provided",
                    "connection timeout",
                    "ssl",
                    "network",
                    "dns"
                ]):
                    self.logger.warning(f"Discord connection attempt {attempt + 1} failed: {e}")
                    self.logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 1.5  # Exponential backoff
                    continue
                else:
                    self.logger.error(f"Failed to query Discord messages: {e}")
                    return []
        
        self.logger.error(f"Failed to query Discord messages after {max_retries} attempts")
        return []

    async def _query_pages_impl(self, 
                               filters: Optional[List[QueryFilter]] = None,
                               date_filter: Optional[DateRangeFilter] = None,
                               sort_by: Optional[str] = None,
                               sort_direction: str = "desc",
                               limit: Optional[int] = None,
                               complex_filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Internal implementation of query_pages with actual Discord API calls."""
        try:
            # Get channels to sync - support both single and multiple channels
            channel_identifiers = self._extract_multiple_channel_filters(filters, complex_filter)
            if not channel_identifiers:
                # Fallback to old single-channel method for backward compatibility
                single_channel = self._extract_channel_filter(filters)
                if single_channel:
                    channel_identifiers = [single_channel]
                else:
                    # No specific channel filter - sync all accessible channels (original behavior)
                    self.logger.info("No specific channel filter provided - syncing all accessible channels")
                    return await self._query_all_accessible_channels(date_filter, sort_direction, limit)
            
            self.logger.info(f"Querying {len(channel_identifiers)} Discord channels: {channel_identifiers}")
            
            # Create temporary client for message fetching
            client = discord.Client(intents=self.intents)
            
            try:
                # Add connection timeout and better error handling
                self.logger.info("Connecting to Discord API...")
                await asyncio.wait_for(client.login(self.bot_token), timeout=30.0)
                self.logger.info("Successfully authenticated with Discord")
                
                guild = await asyncio.wait_for(client.fetch_guild(int(self.server_id)), timeout=15.0)
                self.logger.info(f"Successfully connected to Discord server: {guild.name}")
                
                # Fetch the channels for this guild
                guild_channels = await asyncio.wait_for(guild.fetch_channels(), timeout=15.0)
                
                # Calculate date range for filtering (common for all channels)
                after_date = None
                before_date = None
                if date_filter:
                    after_date = date_filter.start_date
                    before_date = date_filter.end_date
                
                # Query each channel and collect all messages
                all_messages = []
                
                # For date-based queries, fetch ALL messages per channel within date range
                # For non-date queries, split the limit across channels
                if date_filter:
                    # Date filter active - get ALL messages in range for each channel (no per-channel limit)
                    per_channel_limit = None
                    self.logger.info(f"Date-based query: fetching ALL messages per channel within date range")
                else:
                    # No date filter - split total limit across channels
                    per_channel_limit = limit // len(channel_identifiers) if limit and len(channel_identifiers) > 1 else limit
                    if per_channel_limit and per_channel_limit < 10:  # Ensure minimum per channel
                        per_channel_limit = 10
                    self.logger.info(f"Non-date query: using per-channel limit of {per_channel_limit}")
                
                for channel_identifier in channel_identifiers:
                    try:
                        # Handle both channel ID and channel name
                        channel = None
                        if channel_identifier.startswith("name:"):
                            # Channel name filter - find channel by name (with sanitized name mapping)
                            sanitized_channel_name = channel_identifier[5:]  # Remove "name:" prefix
                            
                            # First try exact match
                            for ch in guild_channels:
                                if hasattr(ch, 'send') and ch.name == sanitized_channel_name:
                                    channel = ch
                                    break
                            
                            # If no exact match, try reverse sanitization lookup
                            if not channel:
                                channel = self._find_channel_by_sanitized_name(guild_channels, sanitized_channel_name)
                            
                            if not channel:
                                self.logger.warning(f"Channel '{sanitized_channel_name}' not found in server")
                                continue
                        else:
                            # Channel ID filter - fetch by ID
                            channel = await guild.fetch_channel(int(channel_identifier))
                        
                        if not channel:
                            self.logger.warning(f"Could not access channel {channel_identifier}")
                            continue
                        
                        # Apply rate limiting between channels
                        await self._rate_limit()
                        
                        # Fetch messages from this channel with proper pagination
                        channel_messages = []
                        message_count = 0
                        
                        if per_channel_limit is None:
                            # Date-based query: Get ALL messages in date range with pagination
                            self.logger.info(f"Fetching ALL messages from #{channel.name} within date range...")
                            
                            # Use chunks to handle large date ranges efficiently
                            chunk_size = 100  # Discord's optimal chunk size
                            last_message = None
                            
                            while True:
                                # Apply rate limiting between chunks
                                if message_count > 0:
                                    await self._rate_limit_efficient()
                                
                                # Fetch chunk with proper before parameter for pagination
                                chunk_messages = []
                                async for message in channel.history(
                                    limit=chunk_size,
                                    after=after_date,
                                    before=last_message.created_at if last_message else before_date,
                                    oldest_first=False  # Always newest first for efficient pagination
                                ):
                                    chunk_messages.append(message)
                                
                                if not chunk_messages:
                                    break  # No more messages in range
                                
                                # Convert messages and add to results
                                for message in chunk_messages:
                                    message_data = await self._convert_message_to_data(message)
                                    channel_messages.append(message_data)
                                    message_count += 1
                                
                                # Update pagination marker
                                last_message = chunk_messages[-1]
                                
                                # If we got fewer than chunk_size, we've reached the end
                                if len(chunk_messages) < chunk_size:
                                    break
                                    
                                # Safety check to prevent infinite loops
                                if message_count > 10000:  # Reasonable safety limit
                                    self.logger.warning(f"Reached safety limit of 10000 messages for channel #{channel.name}")
                                    break
                            
                        else:
                            # Limited query: Use simple approach with specified limit
                            async for message in channel.history(
                                limit=per_channel_limit,
                                after=after_date,
                                before=before_date,
                                oldest_first=(sort_direction == "asc")
                            ):
                                # Apply rate limiting every 50 messages
                                if message_count % 50 == 0 and message_count > 0:
                                    await self._rate_limit_efficient()
                                
                                # Convert Discord message to our format
                                message_data = await self._convert_message_to_data(message)
                                channel_messages.append(message_data)
                                message_count += 1
                        
                        if channel_messages:
                            self.logger.info(f"Found {len(channel_messages)} messages in channel #{channel.name}")
                            all_messages.extend(channel_messages)
                        else:
                            self.logger.info(f"No messages found in channel #{channel.name}")
                            
                    except Exception as e:
                        self.logger.warning(f"Error querying channel {channel_identifier}: {e}")
                        continue
                
                self.logger.info(f"Total messages found across all channels: {len(all_messages)}")
                return all_messages
                
            finally:
                if client and not client.is_closed():
                    await client.close()
                    self.logger.debug("Discord client connection closed")
            
        except asyncio.TimeoutError:
            self.logger.error("Timeout while connecting to Discord API - check your network connection")
            return []
        except Exception as e:
            # This will be caught by the retry logic in the parent method
            raise

    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]:
        """Get full content of a specific Discord message."""
        # page_id format: "msg_{message_id}"
        message_id = page_id.replace('msg_', '')
        
        try:
            # We need channel context to fetch a specific message
            # This is a limitation of Discord API - we need to know which channel
            # For now, search through all accessible channels
            for channel in self.guild.text_channels:
                try:
                    await self._rate_limit()
                    message = await channel.fetch_message(int(message_id))
                    return await self._convert_message_to_data(message)
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    continue
            
            self.logger.warning(f"Message {message_id} not found in any accessible channel")
            return {}
            
        except Exception as e:
            self.logger.error(f"Failed to get Discord message content for {page_id}: {e}")
            return {}

    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        """Get properties of a specific Discord message."""
        content = await self.get_page_content(page_id, include_properties=True)
        
        return {
            "author_id": content.get("author_id"),
            "author_name": content.get("author_name"),
            "channel_id": content.get("channel_id"),
            "channel_name": content.get("channel_name"),
            "timestamp": content.get("timestamp"),
            "content": content.get("content"),
            "has_attachments": content.get("has_attachments", False),
            "has_embeds": content.get("has_embeds", False),
        }

    async def sync_to_local(self, 
                           output_directory: str,
                           filters: Optional[List[QueryFilter]] = None,
                           date_filter: Optional[DateRangeFilter] = None,
                           include_properties: bool = True,
                           force_update: bool = False,
                           excluded_properties: List[str] = None) -> SyncResult:
        """Sync Discord messages to local storage - placeholder for backwards compatibility."""
        # This will be implemented as sync_to_local_unified following the pattern
        raise NotImplementedError("Use sync_to_local_unified for Discord connector")

    async def sync_to_local_unified(self, 
                                   storage,
                                   db_config,
                                   filters: Optional[List[QueryFilter]] = None,
                                   date_filter: Optional[DateRangeFilter] = None,
                                   include_properties: bool = True,
                                   force_update: bool = False,
                                   excluded_properties: List[str] = None,
                                   complex_filter: Optional[Dict[str, Any]] = None) -> SyncResult:
        """Sync Discord messages to local storage using unified storage system."""
        result = SyncResult()
        result.start_time = datetime.now()
        
        try:
            # For date-based queries, don't use arbitrary limits - get ALL messages in date range
            # Only use sync_limit as fallback when no date filter is specified
            limit = None if date_filter else self.config.get("sync_limit", 100)
            
            # Query Discord for recent messages
            self.logger.info(f"Querying Discord with date_filter: {date_filter}")
            if date_filter:
                self.logger.info(f"Date filter active - fetching ALL messages in range (no arbitrary limit)")
            else:
                self.logger.info(f"No date filter - using sync_limit: {limit}")
                
            messages = await self.query_pages(
                filters=filters, 
                date_filter=date_filter,
                limit=limit,
                complex_filter=complex_filter
            )
            
            if not messages:
                self.logger.info("No new Discord messages found from query.")
                return result

            self.logger.info(f"Found {len(messages)} Discord messages from query.")
            result.pages_fetched = len(messages)

            # Partition OCR channel messages from regular ones
            if self.ocr_channels:
                ocr_messages = [m for m in messages if m.get("channel_id") in self.ocr_channels]
                regular_messages = [m for m in messages if m.get("channel_id") not in self.ocr_channels]

                if ocr_messages:
                    self.logger.info(f"Processing {len(ocr_messages)} messages from OCR channels")
                    ocr_result = await self._process_ocr_channel_messages(
                        ocr_messages, db_config, force_update
                    )
                    # Merge OCR results into main result
                    result.pages_saved += ocr_result.pages_saved
                    result.pages_skipped += ocr_result.pages_skipped
                    result.pages_failed += ocr_result.pages_failed
                    result.files_created.extend(ocr_result.files_created)
                    result.errors.extend(ocr_result.errors)

                messages = regular_messages
                if not messages:
                    self.logger.info("No regular (non-OCR) messages to process.")
                    result.end_time = datetime.now()
                    return result

            pages_to_save = []
            for message in messages:
                # Prepare page data for unified storage
                page_data = self._prepare_page_for_storage(message, db_config, excluded_properties)
                pages_to_save.append(page_data)
            
            if not pages_to_save:
                self.logger.info("No new or updated messages to save after filtering.")
                return result
            
            # Process pages with proper skipping logic  
            saved_count = 0
            skipped_count = 0
            
            for page_data in pages_to_save:
                try:
                    # For Discord, create channel-specific subdirectories
                    channel_name = page_data.get("channel_name", "unknown")
                    
                    # Create a safe channel directory name
                    safe_channel_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in channel_name)
                    safe_channel_name = safe_channel_name.strip("_").replace(" ", "_")
                    
                    # Create modified database config with channel-specific directory
                    import copy
                    discord_db_config = copy.deepcopy(db_config)
                    original_md_dir = discord_db_config.markdown_directory
                    channel_md_dir = os.path.join(original_md_dir, safe_channel_name)
                    discord_db_config.markdown_directory = channel_md_dir

                    # Resolve to absolute path before creating directory
                    resolved_channel_md_dir = channel_md_dir
                    if not os.path.isabs(resolved_channel_md_dir):
                        from promaia.utils.env_writer import get_data_dir
                        resolved_channel_md_dir = os.path.join(str(get_data_dir()), resolved_channel_md_dir)
                    os.makedirs(resolved_channel_md_dir, exist_ok=True)
                    
                    # Save using unified storage with channel-specific directory
                    saved_files = storage.save_content(
                        page_id=page_data["page_id"],
                        title=page_data["metadata"]["title"],
                        content_data=page_data["metadata"],
                        database_config=discord_db_config,
                        markdown_content=page_data["content"]
                    )
                    
                    if saved_files:
                        result.add_success(saved_files.get('markdown', ''))
                        saved_count += 1
                    else:
                        result.add_skip()
                        skipped_count += 1
                        
                except Exception as e:
                    self.logger.error(f"Error saving Discord message {page_data['page_id']}: {e}")
                    result.add_error(f"Failed to save message {page_data['page_id']}: {e}")
            
            self.logger.info(f"Discord sync completed: {saved_count} saved, {skipped_count} skipped")

            # Backfill channel_names mapping into config for browser use
            try:
                seen_channels = {}
                for msg in messages:
                    cid = msg.get("channel_id")
                    cname = msg.get("channel_name")
                    if cid and cname and cname != "unknown":
                        seen_channels[str(cid)] = cname
                if seen_channels:
                    existing_names = dict(db_config.property_filters.get("channel_names", {}))
                    updated = False
                    for cid, cname in seen_channels.items():
                        if cid not in existing_names:
                            existing_names[cid] = cname
                            updated = True
                    if updated:
                        db_config.property_filters["channel_names"] = existing_names
                        from promaia.config.databases import get_database_manager
                        get_database_manager().save_database_field(db_config, "property_filters")
                        self.logger.info(f"Backfilled channel_names: {existing_names}")
            except Exception as e:
                self.logger.debug(f"Could not backfill channel_names: {e}")

            result.end_time = datetime.now()

            return result
            
        except Exception as e:
            self.logger.error(f"Discord sync failed: {e}")
            result.add_error(f"Discord sync failed: {e}")
            result.end_time = datetime.now()
            return result

    def _extract_channel_filter(self, filters: Optional[List[QueryFilter]]) -> Optional[str]:
        """Extract channel identifier from filters (supports both channel_id and channel_name)."""
        if not filters:
            return None
        
        # First, try to find channel_id filter (returns actual channel ID)
        for filter_obj in filters:
            if filter_obj.property_name == "channel_id" and filter_obj.operator == "eq":
                return filter_obj.value
        
        # If no channel_id filter, try channel_name filter (return the name, we'll handle it differently)
        for filter_obj in filters:
            if filter_obj.property_name == "channel_name" and filter_obj.operator == "eq":
                # Return the channel name prefixed to indicate it's a name, not ID
                return f"name:{filter_obj.value}"
        
        return None

    def _extract_multiple_channel_filters(self, filters: Optional[List[QueryFilter]] = None, complex_filter: Optional[Dict[str, Any]] = None) -> List[str]:
        """Extract multiple channel identifiers from filters and complex filters."""
        channel_identifiers = []
        
        # Handle simple filters first
        if filters:
            for filter_obj in filters:
                if filter_obj.property_name == "channel_id":
                    if filter_obj.operator == "eq":
                        channel_identifiers.append(filter_obj.value)
                    elif filter_obj.operator == "in" and isinstance(filter_obj.value, list):
                        # Handle array of channel IDs
                        channel_identifiers.extend(filter_obj.value)
                elif filter_obj.property_name == "channel_name":
                    if filter_obj.operator == "eq":
                        channel_identifiers.append(f"name:{filter_obj.value}")
                    elif filter_obj.operator == "in" and isinstance(filter_obj.value, list):
                        # Handle array of channel names
                        channel_identifiers.extend([f"name:{name}" for name in filter_obj.value])
                elif filter_obj.property_name == "discord_channel_name":
                    if filter_obj.operator == "eq":
                        channel_identifiers.append(f"name:{filter_obj.value}")
                    elif filter_obj.operator == "in" and isinstance(filter_obj.value, list):
                        # Handle array of discord channel names
                        channel_identifiers.extend([f"name:{name}" for name in filter_obj.value])
        
        # Handle complex filters (multiple channels with OR logic)
        if complex_filter and complex_filter.get('type') == 'complex':
            or_clauses = complex_filter.get('or_clauses', [])
            for and_conditions in or_clauses:
                for condition in and_conditions:
                    prop_name = condition.get('property', '')
                    operator = condition.get('operator', '=')
                    value = condition.get('value', '')
                    
                    if operator == '=' and prop_name in ['discord_channel_name', 'channel_name']:
                        channel_identifiers.append(f"name:{value}")
                    elif operator == '=' and prop_name == 'channel_id':
                        channel_identifiers.append(value)
        
        return channel_identifiers
    
    async def _query_all_accessible_channels(self, 
                                           date_filter: Optional[DateRangeFilter] = None,
                                           sort_direction: str = "desc", 
                                           limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Query messages from all accessible channels in the Discord server."""
        try:
            # Create temporary client for message fetching
            client = discord.Client(intents=self.intents)
            all_messages = []
            
            try:
                # Add connection timeout and better error handling
                self.logger.info("Connecting to Discord API for full server sync...")
                await asyncio.wait_for(client.login(self.bot_token), timeout=30.0)
                self.logger.info("Successfully authenticated with Discord")
                
                guild = await asyncio.wait_for(client.fetch_guild(int(self.server_id)), timeout=15.0)
                self.logger.info(f"Successfully connected to Discord server: {guild.name}")
                
                # Get all accessible channels
                guild_channels = await guild.fetch_channels()
                text_channels = [ch for ch in guild_channels if hasattr(ch, 'send')]  # Text channels only
                
                self.logger.info(f"Found {len(text_channels)} text channels to sync")
                
                # Calculate date range for filtering
                after_date = None
                before_date = None
                if date_filter:
                    after_date = date_filter.start_date
                    before_date = date_filter.end_date
                
                # Query each channel
                for channel in text_channels:
                    try:
                        # Apply rate limiting between channels
                        await self._rate_limit()
                        
                        # For date-based queries, get ALL messages per channel
                        # For non-date queries, split limit across channels
                        if date_filter:
                            channel_limit = None  # Get all messages in date range
                        else:
                            channel_limit = limit // len(text_channels) if limit else 100
                            if channel_limit < 10:  # Ensure minimum per channel
                                channel_limit = 10
                        
                        channel_messages = []
                        message_count = 0
                        
                        if channel_limit is None:
                            # Date-based query: Get ALL messages in date range with pagination
                            self.logger.info(f"Fetching ALL messages from #{channel.name} within date range...")
                            
                            # Use chunks to handle large date ranges efficiently
                            chunk_size = 100  # Discord's optimal chunk size
                            last_message = None
                            
                            while True:
                                # Apply rate limiting between chunks
                                if message_count > 0:
                                    await self._rate_limit_efficient()
                                
                                # Fetch chunk with proper before parameter for pagination
                                chunk_messages = []
                                async for message in channel.history(
                                    limit=chunk_size,
                                    after=after_date,
                                    before=last_message.created_at if last_message else before_date,
                                    oldest_first=False  # Always newest first for efficient pagination
                                ):
                                    chunk_messages.append(message)
                                
                                if not chunk_messages:
                                    break  # No more messages in range
                                
                                # Convert messages and add to results
                                for message in chunk_messages:
                                    message_data = await self._convert_message_to_data(message)
                                    channel_messages.append(message_data)
                                    message_count += 1
                                
                                # Update pagination marker
                                last_message = chunk_messages[-1]
                                
                                # If we got fewer than chunk_size, we've reached the end
                                if len(chunk_messages) < chunk_size:
                                    break
                                    
                                # Safety check to prevent infinite loops
                                if message_count > 10000:  # Reasonable safety limit
                                    self.logger.warning(f"Reached safety limit of 10000 messages for channel #{channel.name}")
                                    break
                            
                        else:
                            # Limited query: Use simple approach with specified limit
                            async for message in channel.history(
                                limit=channel_limit,
                                after=after_date,
                                before=before_date,
                                oldest_first=(sort_direction == "asc")
                            ):
                                message_data = await self._convert_message_to_data(message)
                                channel_messages.append(message_data)
                                message_count += 1
                                
                                # Apply rate limiting for large channel syncs
                                if message_count % 50 == 0:
                                    await self._rate_limit_efficient()
                        
                        if channel_messages:
                            self.logger.info(f"Found {len(channel_messages)} messages in channel #{channel.name}")
                            all_messages.extend(channel_messages)
                        
                    except discord.Forbidden:
                        self.logger.warning(f"No access to channel #{channel.name}")
                        continue
                    except Exception as e:
                        self.logger.warning(f"Error fetching from channel #{channel.name}: {e}")
                        continue
                
                # Sort all messages by timestamp if needed
                if sort_direction == "desc":
                    all_messages.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
                else:
                    all_messages.sort(key=lambda x: x.get('timestamp', ''))
                
                # Apply global limit after sorting
                if limit and len(all_messages) > limit:
                    all_messages = all_messages[:limit]
                
                self.logger.info(f"Total messages collected from all channels: {len(all_messages)}")
                return all_messages
                
            finally:
                await client.close()
                
        except Exception as e:
            self.logger.error(f"Failed to query all Discord channels: {e}")
            return []
    
    def _find_channel_by_sanitized_name(self, channels, sanitized_name: str):
        """Find a Discord channel by its sanitized name (reverse lookup)."""
        for channel in channels:
            if hasattr(channel, 'send'):  # Text channel
                # Apply the same sanitization logic used when saving files
                safe_channel_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in channel.name)
                safe_channel_name = safe_channel_name.strip("_").replace(" ", "_")
                
                if safe_channel_name == sanitized_name:
                    self.logger.info(f"Mapped sanitized name '{sanitized_name}' to Discord channel '{channel.name}'")
                    return channel
        
        return None

    async def _convert_message_to_data(self, message: discord.Message) -> Dict[str, Any]:
        """Convert a Discord message to our data format."""
        # Handle thread context
        thread_id = None
        if hasattr(message.channel, 'parent_id') and message.channel.parent_id:
            thread_id = str(message.channel.id)
        
        # Handle message references (replies)
        reference_message_id = None
        if message.reference and message.reference.message_id:
            reference_message_id = str(message.reference.message_id)
        
        # Process attachments
        attachments = []
        for attachment in message.attachments:
            attachments.append({
                "id": str(attachment.id),
                "filename": attachment.filename,
                "size": attachment.size,
                "url": attachment.url,
                "content_type": attachment.content_type
            })
        
        # Process embeds
        embeds = []
        for embed in message.embeds:
            embed_data = {
                "title": embed.title,
                "description": embed.description,
                "url": embed.url,
                "color": embed.color.value if embed.color else None,
                "timestamp": embed.timestamp.isoformat() if embed.timestamp else None,
            }
            if embed.author:
                embed_data["author"] = {
                    "name": embed.author.name,
                    "url": embed.author.url,
                    "icon_url": embed.author.icon_url
                }
            embeds.append(embed_data)
        
        # Process reactions
        reactions = []
        for reaction in message.reactions:
            reactions.append({
                "emoji": str(reaction.emoji),
                "count": reaction.count,
                "me": reaction.me
            })
        
        return {
            "id": f"msg_{message.id}",
            "message_id": str(message.id),
            "channel_id": str(message.channel.id),
            "channel_name": message.channel.name,
            "server_id": str(message.guild.id),
            "server_name": message.guild.name,
            "author_id": str(message.author.id),
            "author_name": message.author.name,
            "author_display_name": message.author.display_name,
            "content": message.content,
            "timestamp": message.created_at.isoformat(),
            "edited_timestamp": message.edited_at.isoformat() if message.edited_at else None,
            "message_type": str(message.type),
            "has_attachments": len(message.attachments) > 0,
            "attachment_count": len(message.attachments),
            "attachments": attachments,
            "has_embeds": len(message.embeds) > 0,
            "embeds": embeds,
            "reaction_count": len(message.reactions),
            "reactions": reactions,
            "thread_id": thread_id,
            "reference_message_id": reference_message_id,
            "pinned": message.pinned,
        }

    def _prepare_page_for_storage(self, message: Dict[str, Any], db_config, excluded_properties: List[str] = None) -> Dict[str, Any]:
        """Prepare message data for the unified storage format."""
        
        page_id = message['id']
        markdown_content = self._message_to_markdown(message)
        
        # Extract channel information for Discord-specific organization
        channel_name = message.get('channel_name', 'unknown')
        channel_id = message.get('channel_id', 'unknown')

        # Format timestamp for filename
        timestamp_str = "unknown_time"
        if message.get('timestamp'):
            try:
                dt = datetime.fromisoformat(message['timestamp'].replace('Z', '+00:00'))
                timestamp_str = dt.strftime("%Y-%m-%d_%H-%M-%S")
            except ValueError:
                pass

        # Create a filename-safe title
        author_name = message.get('author_name', 'Unknown')
        
        # Get first ~15 characters of content for filename
        content_snippet = ""
        content = message.get('content', '')
        if content:
            # Clean content for filename use
            clean_content = "".join(c if c.isalnum() or c in " -_" else "_" for c in content)
            content_snippet = clean_content[:15].strip("_").strip()
            if content_snippet:
                content_snippet = f"_{content_snippet}"
        
        # New filename format: YYYY-MM-DD_HH-MM-SS_author_content_msg_id.md
        filename_title = f"{timestamp_str}_{author_name}{content_snippet}_msg_{page_id}"

        # Extract properties from message data
        properties = {
            "title": filename_title,
            "author_id": message.get('author_id'),
            "author_name": message.get('author_name'),
            "channel_name": channel_name,
            "channel_id": channel_id,
            "timestamp": message.get('timestamp'),
            "has_attachments": message.get('has_attachments', False),
            "content": message.get('content', ''),
        }
        
        # Metadata for registry
        metadata = {
            "page_id": page_id,
            "title": properties["title"],
            "created_time": message.get('timestamp'),
            "last_edited_time": message.get('edited_timestamp') or message.get('timestamp'),
            "synced_time": datetime.now(timezone.utc).isoformat(),
            "source_id": message.get('message_id'),
            "data_source": "discord",
            "content_type": "message",
            "properties": properties,
            "raw_message_data": message,
            # Add Discord-specific channel information
            "discord_channel_name": channel_name,
            "discord_channel_id": channel_id,
            "discord_server_id": message.get('server_id'),
            "discord_server_name": message.get('server_name')
        }
        
        return {
            "page_id": page_id,
            "content": markdown_content,
            "metadata": metadata,
            # Add channel info for storage path organization
            "channel_name": channel_name,
            "channel_id": channel_id
        }

    def _message_to_markdown(self, message: Dict[str, Any]) -> str:
        """Convert a Discord message dictionary to a markdown string."""
        content = message.get("content", "")
        
        # Add main content - no verbose header, just the content
        main_content = content if content else "*[No text content]*"
        
        # Add attachment information
        attachments_section = ""
        if message.get("has_attachments", False):
            attachments = message.get("attachments", [])
            attachments_section = "\n\n## Attachments\n\n"
            for attachment in attachments:
                attachments_section += f"- **{attachment.get('filename', 'Unknown')}** ({attachment.get('size', 0)} bytes)\n"
        
        # Add embed information
        embeds_section = ""
        if message.get("has_embeds", False):
            embeds = message.get("embeds", [])
            embeds_section = "\n\n## Embeds\n\n"
            for embed in embeds:
                if embed.get("title"):
                    embeds_section += f"### {embed['title']}\n\n"
                if embed.get("description"):
                    embeds_section += f"{embed['description']}\n\n"
        
        # Add reaction information
        reactions_section = ""
        if message.get("reaction_count", 0) > 0:
            reactions = message.get("reactions", [])
            reactions_section = "\n\n## Reactions\n\n"
            for reaction in reactions:
                reactions_section += f"{reaction.get('emoji', '?')} x{reaction.get('count', 0)}  "
        
        return main_content + attachments_section + embeds_section + reactions_section

    async def list_server_channels(self):
        """Debug method to list all channels in the Discord server."""
        if not self.bot_token or not self.server_id:
            self.logger.error("Bot token or server ID not available")
            return []
            
        # Create temporary client for data access
        client = discord.Client(intents=self.intents)
        
        try:
            # Add connection timeout and better error handling
            self.logger.info("Connecting to Discord API to list channels...")
            await asyncio.wait_for(client.login(self.bot_token), timeout=30.0)
            guild = await asyncio.wait_for(client.fetch_guild(int(self.server_id)), timeout=15.0)
            
            print(f'🎮 Discord Server: {guild.name} (ID: {guild.id})')
            print('📢 Available Channels:')
            
            channels = await guild.fetch_channels()
            text_channels = []
            
            for channel in channels:
                if hasattr(channel, 'send'):  # Text channel
                    print(f'  #{channel.name} (ID: {channel.id})')
                    text_channels.append({
                        'name': channel.name,
                        'id': str(channel.id)
                    })
                    
            return text_channels
            
        except Exception as e:
            self.logger.error(f"Error listing Discord channels: {e}")
            return []
        finally:
            await client.close()

    async def test_channel_access(self, channel, guild) -> bool:
        """Test if the bot has read access to a specific channel using permissions."""
        try:
            # Get the bot's user ID from the client
            bot_user_id = guild._state.self_id if hasattr(guild._state, 'self_id') else None
            if not bot_user_id:
                # Fallback to current user from guild
                bot_user_id = guild._state.user.id if hasattr(guild._state, 'user') else None
            
            if bot_user_id:
                # Get bot member object
                bot_member = guild.get_member(bot_user_id)
                if not bot_member:
                    bot_member = await guild.fetch_member(bot_user_id)
                
                # Check permissions directly (much faster than reading messages)
                permissions = channel.permissions_for(bot_member)
                
                # Check for all required read permissions
                has_read_permission = (
                    permissions.read_messages and 
                    permissions.read_message_history and 
                    permissions.view_channel
                )
                
                return has_read_permission
            else:
                # If we can't get bot ID, fall back to message test
                raise Exception("Could not determine bot user ID")
            
        except Exception as e:
            self.logger.debug(f"Error testing channel permissions for {channel.name}: {e}")
            # Fallback to message reading test if permissions check fails
            try:
                async for _ in channel.history(limit=1):
                    return True
                return True
            except discord.Forbidden:
                return False
            except Exception:
                return False

    async def discover_accessible_channels(self) -> Dict[str, Any]:
        """Discover all channels the bot has read access to."""
        if not self.bot_token or not self.server_id:
            self.logger.error("Bot token or server ID not available")
            return {"server_name": "Unknown", "channels": []}
            
        # Create temporary client for data access
        client = discord.Client(intents=self.intents)
        
        try:
            # Add connection timeout and better error handling
            self.logger.info("Connecting to Discord API to discover accessible channels...")
            await asyncio.wait_for(client.login(self.bot_token), timeout=30.0)
            guild = await asyncio.wait_for(client.fetch_guild(int(self.server_id)), timeout=15.0)
            
            # Get bot member once for efficiency
            bot_user_id = client.user.id
            bot_member = guild.get_member(bot_user_id)
            if not bot_member:
                bot_member = await guild.fetch_member(bot_user_id)
            
            channels = await guild.fetch_channels()
            
            accessible_channels = []
            tested_count = 0
            
            # Filter to only text channels first
            text_channels = [ch for ch in channels if hasattr(ch, 'send')]
            self.logger.info(f"Testing {len(text_channels)} text channels for read permissions...")
            
            for channel in text_channels:
                tested_count += 1
                
                # Fast permission check using pre-fetched bot member
                try:
                    permissions = channel.permissions_for(bot_member)
                    has_read_permission = (
                        permissions.read_messages and 
                        permissions.read_message_history and 
                        permissions.view_channel
                    )
                    
                    if has_read_permission:
                        accessible_channels.append({
                            'name': channel.name,
                            'id': str(channel.id),
                            'discovered_at': datetime.now().isoformat()
                        })
                        self.logger.debug(f"✓ #{channel.name} - readable")
                    else:
                        self.logger.debug(f"✗ #{channel.name} - no read access")
                        
                except Exception as e:
                    self.logger.debug(f"! #{channel.name} - permission check failed: {e}")
                
                # Progress indicator for large servers
                if tested_count % 20 == 0:
                    self.logger.info(f"Tested {tested_count}/{len(text_channels)} channels...")
                    
            self.logger.info(f"Discovery complete: {len(accessible_channels)}/{len(text_channels)} channels accessible")
                    
            return {
                "server_name": guild.name,
                "server_id": str(guild.id),
                "channels": accessible_channels,
                "discovered_at": datetime.now().isoformat(),
                "total_tested": len(text_channels)
            }
            
        except Exception as e:
            self.logger.error(f"Error discovering accessible channels: {e}")
            return {"server_name": "Unknown", "channels": []}
        finally:
            await client.close()

    def get_cache_file_path(self) -> Path:
        """Get the path for the channel cache file."""
        from promaia.utils.env_writer import get_cache_dir
        cache_dir = get_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / f"discord_channels_{self.workspace}_{self.server_id}.json"

    async def get_cached_accessible_channels(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Get cached accessible channels, discovering them if cache doesn't exist."""
        cache_file = self.get_cache_file_path()
        
        # Check if we need to discover/refresh
        if force_refresh or not cache_file.exists():
            self.logger.info(f"Discovering accessible channels for server {self.server_id}")
            channel_data = await self.discover_accessible_channels()
            
            # Cache the results
            try:
                with open(cache_file, 'w') as f:
                    json.dump(channel_data, f, indent=2)
                self.logger.info(f"Cached {len(channel_data.get('channels', []))} accessible channels")
            except Exception as e:
                self.logger.error(f"Error caching channel data: {e}")
            
            return channel_data
        
        # Load from cache
        try:
            with open(cache_file, 'r') as f:
                channel_data = json.load(f)
            self.logger.debug(f"Loaded {len(channel_data.get('channels', []))} channels from cache")
            return channel_data
        except Exception as e:
            self.logger.error(f"Error loading cached channels: {e}")
            # Fall back to discovery
            return await self.discover_accessible_channels()

    async def refresh_channel_cache(self):
        """Refresh the channel access cache."""
        return await self.get_cached_accessible_channels(force_refresh=True)

    # -- OCR channel support ---------------------------------------------------

    IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}

    async def _process_ocr_channel_messages(
        self, messages: List[Dict[str, Any]], db_config, force_update: bool = False
    ) -> SyncResult:
        """Process messages from OCR-enabled channels: download images and run OCR."""
        result = SyncResult()

        from promaia.storage.discord_ocr_tracker import DiscordOCRTracker
        from promaia.storage.hybrid_storage import get_hybrid_registry
        tracker = DiscordOCRTracker()
        registry = get_hybrid_registry()

        # Download directory
        from promaia.utils.env_writer import get_data_subdir
        download_dir = get_data_subdir() / "uploads" / "discord" / self.workspace
        download_dir.mkdir(parents=True, exist_ok=True)

        # Group into annotated image sets + collect reply re-processes
        image_sets = self._group_ocr_messages(messages)
        reply_reprocesses = self._collect_ocr_reply_reprocesses(messages)
        self.logger.info(f"OCR: {len(image_sets)} image sets, {len(reply_reprocesses)} reply re-processes")

        from promaia.ocr.processor import OCRProcessor
        processor = OCRProcessor(workspace=self.workspace)

        # Resolve the base markdown directory for this data source
        from promaia.utils.env_writer import get_data_dir
        base_md_dir = Path(get_data_dir()) / db_config.markdown_directory

        # -- Process new images --
        for img_set in image_sets:
            msg_id = img_set["message_id"]
            annotation = img_set.get("annotation")

            # Per-channel markdown directory (same structure as regular Discord channels)
            channel_name = img_set.get("channel_name", "unknown")
            safe_channel = "".join(c if c.isalnum() or c in " -_" else "_" for c in channel_name)
            safe_channel = safe_channel.strip("_").replace(" ", "_")
            channel_md_dir = base_md_dir / safe_channel

            for att in img_set["attachments"]:
                att_id = att["id"]

                if tracker.is_processed(msg_id, att_id) and not force_update:
                    result.add_skip()
                    continue

                local_path = download_dir / f"{msg_id}_{att_id}_{att['filename']}"
                ok = await self._download_attachment(att["url"], local_path)
                if not ok:
                    result.add_error(f"Download failed: {att['filename']}")
                    continue

                try:
                    doc = await processor.process_image(
                        local_path,
                        annotation=annotation,
                        save_markdown=True,
                        move_to_processed=True,
                        sync_to_notion=False,
                        markdown_dir=channel_md_dir,
                    )
                    tracker.mark_processed(
                        message_id=msg_id,
                        attachment_id=att_id,
                        channel_id=img_set.get("channel_id"),
                        server_id=self.server_id,
                        original_filename=att["filename"],
                        image_path=str(doc.processed_image_path or local_path),
                        annotation=annotation,
                        ocr_status=doc.status,
                        markdown_path=str(doc.markdown_path) if doc.markdown_path else None,
                    )
                    if doc.status in ("completed", "review_needed"):
                        # Register in hybrid registry so content appears in chat/browser
                        if doc.markdown_path and doc.markdown_path.exists():
                            page_id = f"ocr_{msg_id}_{att_id}"
                            try:
                                registry.add_content({
                                    'page_id': page_id,
                                    'workspace': db_config.workspace,
                                    'database_id': db_config.database_id,
                                    'database_name': db_config.nickname,
                                    'file_path': str(doc.markdown_path),
                                    'title': doc.markdown_path.stem,
                                    'created_time': img_set.get("timestamp"),
                                    'last_edited_time': img_set.get("timestamp"),
                                    'synced_time': datetime.now(timezone.utc).isoformat(),
                                    'file_size': doc.markdown_path.stat().st_size,
                                    'checksum': None,
                                    'metadata': {
                                        'source_id': msg_id,
                                        'data_source': 'discord',
                                        'content_type': 'ocr',
                                        'discord_channel_name': channel_name,
                                        'discord_channel_id': img_set.get("channel_id"),
                                        'discord_server_id': self.server_id,
                                        'annotation': annotation,
                                    }
                                })
                            except Exception as e:
                                self.logger.warning(f"Failed to register OCR content in registry: {e}")
                        result.add_success(str(doc.markdown_path or ""))
                        print(f"  OCR: {att['filename']} -> {doc.status}")
                    else:
                        result.add_error(f"OCR failed for {att['filename']}: {doc.error}")
                except Exception as e:
                    self.logger.error(f"OCR error for {att['filename']}: {e}")
                    result.add_error(str(e))

        # -- Handle reply re-processing --
        for rp in reply_reprocesses:
            ref_msg_id = rp["ref_message_id"]
            new_annotation = rp["annotation"]

            tracked = tracker.get_by_message_id(ref_msg_id)
            if not tracked:
                continue

            for record in tracked:
                image_path = Path(record["image_path"])
                if not image_path.exists():
                    self.logger.warning(f"Image {image_path} not found for re-processing")
                    continue
                # Use the same markdown directory as the original processing
                orig_md = record.get("markdown_path")
                reprocess_md_dir = Path(orig_md).parent if orig_md else None
                try:
                    doc = await processor.process_image(
                        image_path,
                        annotation=new_annotation,
                        save_markdown=True,
                        move_to_processed=False,
                        sync_to_notion=False,
                        markdown_dir=reprocess_md_dir,
                    )
                    md_path = str(doc.markdown_path) if doc.markdown_path else None
                    tracker.update_annotation(
                        ref_msg_id, record["attachment_id"],
                        new_annotation, md_path,
                    )
                    # Update registry entry
                    if doc.markdown_path and doc.markdown_path.exists():
                        page_id = f"ocr_{ref_msg_id}_{record['attachment_id']}"
                        try:
                            registry.add_content({
                                'page_id': page_id,
                                'workspace': db_config.workspace,
                                'database_id': db_config.database_id,
                                'database_name': db_config.nickname,
                                'file_path': str(doc.markdown_path),
                                'title': doc.markdown_path.stem,
                                'created_time': rp.get("timestamp"),
                                'last_edited_time': datetime.now(timezone.utc).isoformat(),
                                'synced_time': datetime.now(timezone.utc).isoformat(),
                                'file_size': doc.markdown_path.stat().st_size,
                                'checksum': None,
                                'metadata': {
                                    'source_id': ref_msg_id,
                                    'data_source': 'discord',
                                    'content_type': 'ocr',
                                    'discord_channel_id': record.get("channel_id"),
                                    'annotation': new_annotation,
                                }
                            })
                        except Exception as e:
                            self.logger.warning(f"Failed to update OCR registry entry: {e}")
                    print(f"  Re-OCR: {record['original_filename']} with annotation")
                    result.add_success(md_path or "")
                except Exception as e:
                    self.logger.error(f"Re-processing error: {e}")
                    result.add_error(str(e))

        return result

    def _group_ocr_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Group messages into annotated image sets for OCR processing.

        Rules:
        - Message with images + text -> text is annotation for those images
        - Message with images, no text -> no annotation
        - Text-only message within annotation_window after an image message
          -> annotation for the preceding images
        """
        # Sort by timestamp ascending for proper grouping
        sorted_msgs = sorted(messages, key=lambda m: m.get("timestamp", ""))

        image_sets: List[Dict[str, Any]] = []
        pending: List[Dict[str, Any]] = []  # image sets awaiting annotation

        for msg in sorted_msgs:
            attachments = msg.get("attachments", [])
            image_atts = [
                a for a in attachments
                if a.get("content_type", "").split(";")[0].strip() in self.IMAGE_CONTENT_TYPES
            ]
            text = (msg.get("content") or "").strip()
            is_reply = bool(msg.get("reference_message_id"))

            if image_atts:
                img_set = {
                    "message_id": msg["message_id"],
                    "channel_id": msg.get("channel_id"),
                    "channel_name": msg.get("channel_name", "unknown"),
                    "timestamp": msg.get("timestamp", ""),
                    "attachments": image_atts,
                    "annotation": text if text else None,
                }
                image_sets.append(img_set)
                if not text:
                    pending.append(img_set)
                else:
                    pending = []
            elif text and not is_reply and pending:
                # Text within annotation window of pending images
                try:
                    msg_time = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                    for p in pending:
                        p_time = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
                        if (msg_time - p_time).total_seconds() <= self.annotation_window and p["annotation"] is None:
                            p["annotation"] = text
                except (ValueError, KeyError):
                    pass
                pending = []

        return image_sets

    def _collect_ocr_reply_reprocesses(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Find replies to image messages that should trigger OCR re-processing."""
        image_msg_ids = set()
        for msg in messages:
            attachments = msg.get("attachments", [])
            has_images = any(
                a.get("content_type", "").split(";")[0].strip() in self.IMAGE_CONTENT_TYPES
                for a in attachments
            )
            if has_images:
                image_msg_ids.add(msg["message_id"])

        reprocesses = []
        for msg in messages:
            ref_id = msg.get("reference_message_id")
            text = (msg.get("content") or "").strip()
            if ref_id and ref_id in image_msg_ids and text:
                reprocesses.append({
                    "ref_message_id": ref_id,
                    "annotation": text,
                    "reply_message_id": msg["message_id"],
                })
        return reprocesses

    async def _download_attachment(self, url: str, dest: Path) -> bool:
        """Download a Discord attachment to a local path."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        self.logger.error(f"Download failed ({resp.status}): {url}")
                        return False
                    data = await resp.read()
                    dest.write_bytes(data)
                    self.logger.debug(f"Downloaded {dest.name} ({len(data)} bytes)")
                    return True
        except Exception as e:
            self.logger.error(f"Download error: {e}")
            return False

    async def cleanup(self):
        """Clean up Discord connector."""
        # No persistent connections to clean up with the new approach
        pass