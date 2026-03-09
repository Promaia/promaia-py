"""
Pydantic models for browser mode API endpoints.

These models define the request/response schemas for interactive data source
selection and preview functionality.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel


class SourceInfo(BaseModel):
    """Information about an available data source."""
    name: str
    display_name: str
    count: int
    date_range: Optional[str] = None
    last_updated: Optional[str] = None
    description: Optional[str] = None


class DiscordServer(BaseModel):
    """Information about a Discord server."""
    server_id: str
    server_name: str
    channels: List[Dict[str, Any]]


class BrowserSourcesResponse(BaseModel):
    """Response for GET /api/browser/sources."""
    sources: List[SourceInfo]
    workspace: str
    total_items: int


class BrowserDiscordResponse(BaseModel):
    """Response for GET /api/browser/discord."""
    servers: List[DiscordServer]
    workspace: str


class BrowserPreviewRequest(BaseModel):
    """Request for POST /api/browser/preview."""
    sources: List[str]
    workspace: Optional[str] = None
    limit: Optional[int] = 100


class PreviewItem(BaseModel):
    """Preview of a content item."""
    title: str
    content_preview: str
    source_name: str
    created_date: Optional[str] = None
    item_type: Optional[str] = None


class BrowserPreviewResponse(BaseModel):
    """Response for POST /api/browser/preview."""
    preview_items: List[PreviewItem]
    total_items: int
    sources_breakdown: Dict[str, int]
    date_range: Optional[str] = None
    estimated_tokens: Optional[int] = None


