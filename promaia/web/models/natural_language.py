"""
Pydantic models for natural language processing API endpoints.

These models define the request/response schemas for intelligent
query processing using LangGraph.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel


class NaturalLanguageRequest(BaseModel):
    """Request for natural language processing."""
    query: str
    workspace: Optional[str] = None
    scope_databases: Optional[List[str]] = None


class QueryIntent(BaseModel):
    """Parsed intent from natural language query."""
    goal: str
    databases: List[str]
    search_terms: List[str]
    time_range: Optional[str] = None
    complexity_level: str
    user_goal: str


class NaturalLanguageResponse(BaseModel):
    """Response for natural language processing."""
    success: bool
    query: str
    intent: Optional[QueryIntent] = None
    results: Dict[str, List[Dict[str, Any]]]  # database -> items
    sql_generated: Optional[str] = None
    total_items: int
    processing_time: float
    errors: List[str] = []


class SimpleNLRequest(BaseModel):
    """Simple request for basic NL to sources conversion."""
    query: str
    workspace: Optional[str] = None


class SimpleNLResponse(BaseModel):
    """Simple response with extracted sources."""
    success: bool
    query: str
    suggested_sources: List[str]
    confidence: float
    reasoning: str


