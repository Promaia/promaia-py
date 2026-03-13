"""
Pydantic models for context editing API endpoints.

These models define the request/response schemas for the /e command
and other context manipulation functionality.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel


class ContextEditRequest(BaseModel):
    """Request for context editing operations."""
    action: str  # "show_current", "set_sources", "set_filters", "set_workspace", "set_nl_prompt"
    sources: Optional[List[str]] = None
    filters: Optional[List[str]] = None 
    workspace: Optional[str] = None
    natural_language_prompt: Optional[str] = None


class ContextInfo(BaseModel):
    """Information about current chat context."""
    sources: List[str]
    filters: List[str]
    workspace: Optional[str] = None
    natural_language_prompt: Optional[str] = None
    total_items: Optional[int] = None
    sources_breakdown: Optional[Dict[str, int]] = None


class ContextEditResponse(BaseModel):
    """Response for context editing operations."""
    success: bool
    message: str
    context: Optional[ContextInfo] = None


class SyncRequest(BaseModel):
    """Request for database sync operations."""
    databases: List[str]
    force: Optional[bool] = False


class SyncResult(BaseModel):
    """Result of a database sync operation."""
    database: str
    success: bool
    items_synced: int
    duration_seconds: float
    error: Optional[str] = None


class SyncResponse(BaseModel):
    """Response for sync operations."""
    success: bool
    results: List[SyncResult]
    total_synced: int
    total_duration: float


class ModelSwitchRequest(BaseModel):
    """Request to switch AI model."""
    model: str  # "claude", "gpt-4o", "gemini", "llama"


class ModelSwitchResponse(BaseModel):
    """Response for model switching."""
    success: bool
    message: str
    current_model: str
    available_models: List[str]


class SaveConversationRequest(BaseModel):
    """Request to save conversation."""
    title: Optional[str] = None
    tags: Optional[List[str]] = None


class SaveConversationResponse(BaseModel):
    """Response for save conversation."""
    success: bool
    message: str
    saved_id: Optional[str] = None
    saved_location: Optional[str] = None


class PushNotionResponse(BaseModel):
    """Response for push to Notion."""
    success: bool
    message: str
    notion_page_id: Optional[str] = None
    notion_page_url: Optional[str] = None


