"""
Browser mode API endpoints.

These endpoints provide data source discovery and preview functionality
for the interactive browser mode (-b flag).
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Query, Depends
from promaia.web.models.browser import (
    BrowserSourcesResponse, 
    BrowserDiscordResponse, 
    BrowserPreviewRequest,
    BrowserPreviewResponse,
    SourceInfo,
    PreviewItem,
    DiscordServer
)
from ...storage.unified_query import get_query_interface
from ...config.workspaces import get_workspace_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/browser", tags=["browser"])


@router.get("/sources", response_model=BrowserSourcesResponse)
async def get_available_sources(
    workspace: str = Query(..., description="Workspace name"),
    days: Optional[int] = Query(None, description="Filter sources by activity in last N days")
):
    """Get available data sources for browser selection."""
    try:
        query_interface = get_query_interface()
        logger.info(f"🔍 Getting available sources for workspace: {workspace}")
        
        # Get statistics for all sources
        stats = query_interface.get_database_stats()
        
        # Convert to SourceInfo objects
        sources = []
        total_items = 0
        
        for db_name, db_stats in stats.get('databases', {}).items():
            if workspace and workspace != "" and not db_name.startswith(workspace):
                # Skip sources not in the requested workspace
                continue
                
            count = db_stats.get('count', 0)
            if count == 0:
                continue  # Skip empty sources
                
            # Extract display name (remove workspace prefix)
            display_name = db_name
            if '.' in db_name:
                display_name = db_name.split('.', 1)[1]
            
            source_info = SourceInfo(
                name=db_name,
                display_name=display_name,
                count=count,
                date_range=db_stats.get('date_range'),
                last_updated=db_stats.get('latest_date'),
                description=f"{count} items"
            )
            
            sources.append(source_info)
            total_items += count
        
        # Sort by count (most items first)
        sources.sort(key=lambda x: x.count, reverse=True)
        
        logger.info(f"✅ Found {len(sources)} sources with {total_items} total items")
        
        return BrowserSourcesResponse(
            sources=sources,
            workspace=workspace,
            total_items=total_items
        )
        
    except Exception as e:
        logger.error(f"❌ Error getting sources: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get sources: {str(e)}")


@router.get("/discord", response_model=BrowserDiscordResponse)
async def get_discord_channels(
    workspace: str = Query(..., description="Workspace name")
):
    """Get Discord servers and channels for selection."""
    try:
        logger.info(f"🔍 Getting Discord channels for workspace: {workspace}")
        
        # Get workspace manager and Discord configs
        workspace_manager = get_workspace_manager()
        
        if not workspace_manager.validate_workspace(workspace):
            raise HTTPException(status_code=404, detail=f"Workspace '{workspace}' not found")
        
        # Get Discord database configs from workspace
        workspace_config = workspace_manager.get_workspace_config(workspace)
        discord_databases = []
        
        if hasattr(workspace_config, 'databases'):
            for db_name, db_config in workspace_config.databases.items():
                if 'discord' in db_name.lower() and hasattr(db_config, 'database_id'):
                    discord_databases.append((db_name, db_config))
        
        servers = []
        
        # For each Discord database, get channel info
        for db_name, db_config in discord_databases:
            try:
                # Get synced channels from query interface
                query_interface = get_query_interface()
                
                # Query for available channels in this Discord source
                channels_data = query_interface.query_content_for_chat(
                    sources=[db_name],
                    limit=1000  # Get many to see channel diversity
                )
                
                # Extract unique channels
                channels = []
                seen_channels = set()
                
                for item in channels_data.get('content', []):
                    channel_info = {
                        'channel_id': item.get('metadata', {}).get('channel_id', 'unknown'),
                        'channel_name': item.get('metadata', {}).get('channel_name', item.get('title', 'Unknown')),
                        'message_count': 1  # We'll aggregate this
                    }
                    
                    channel_key = channel_info['channel_id']
                    if channel_key not in seen_channels:
                        channels.append(channel_info)
                        seen_channels.add(channel_key)
                
                server = DiscordServer(
                    server_id=getattr(db_config, 'database_id', 'unknown'),
                    server_name=db_name.replace('.discord', '').replace('discord', '').strip(),
                    channels=channels
                )
                servers.append(server)
                
            except Exception as e:
                logger.warning(f"⚠️ Error getting channels for {db_name}: {e}")
                # Add empty server to show it exists but has issues
                servers.append(DiscordServer(
                    server_id=getattr(db_config, 'database_id', 'unknown'),
                    server_name=db_name,
                    channels=[]
                ))
        
        logger.info(f"✅ Found {len(servers)} Discord servers")
        
        return BrowserDiscordResponse(
            servers=servers,
            workspace=workspace
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting Discord channels: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get Discord channels: {str(e)}")


@router.post("/preview", response_model=BrowserPreviewResponse)
async def preview_sources(request: BrowserPreviewRequest):
    """Preview what data would be loaded for selected sources."""
    try:
        logger.info(f"🔍 Previewing sources: {request.sources}")
        
        query_interface = get_query_interface()
        
        # Get content preview for selected sources
        content_data = query_interface.query_content_for_chat(
            sources=request.sources,
            workspace=request.workspace,
            limit=min(request.limit, 100)  # Cap preview at 100 items
        )
        
        content_items = content_data.get('content', [])
        
        # Convert to preview items
        preview_items = []
        sources_breakdown = {}
        earliest_date = None
        latest_date = None
        
        for item in content_items:
            # Extract preview text (first 200 chars)
            content = item.get('content', '') or item.get('message_content', '')
            if isinstance(content, str):
                preview_text = content[:200] + "..." if len(content) > 200 else content
            else:
                preview_text = str(content)[:200] + "..."
            
            source_name = item.get('source_name', 'unknown')
            
            # Track source counts
            if source_name not in sources_breakdown:
                sources_breakdown[source_name] = 0
            sources_breakdown[source_name] += 1
            
            # Track date range
            item_date = item.get('created_date') or item.get('message_date')
            if item_date:
                if earliest_date is None or item_date < earliest_date:
                    earliest_date = item_date
                if latest_date is None or item_date > latest_date:
                    latest_date = item_date
            
            preview_item = PreviewItem(
                title=item.get('title', 'Untitled')[:100],
                content_preview=preview_text,
                source_name=source_name,
                created_date=item_date,
                item_type=item.get('metadata', {}).get('type', 'content')
            )
            preview_items.append(preview_item)
        
        # Calculate date range string
        date_range = None
        if earliest_date and latest_date:
            if earliest_date == latest_date:
                date_range = earliest_date
            else:
                date_range = f"{earliest_date} to {latest_date}"
        
        # Estimate token count (rough approximation)
        total_content_length = sum(len(item.content_preview) for item in preview_items)
        estimated_tokens = total_content_length // 4  # Rough estimate: 4 chars per token
        
        logger.info(f"✅ Preview generated: {len(preview_items)} items from {len(sources_breakdown)} sources")
        
        return BrowserPreviewResponse(
            preview_items=preview_items,
            total_items=len(preview_items),
            sources_breakdown=sources_breakdown,
            date_range=date_range,
            estimated_tokens=estimated_tokens
        )
        
    except Exception as e:
        logger.error(f"❌ Error generating preview: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate preview: {str(e)}")
