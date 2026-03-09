"""
Natural Language Processing API endpoints.

These endpoints handle intelligent query processing using LangGraph
for the -nl flag functionality.
"""

import logging
import time
from typing import Optional, List
from fastapi import APIRouter, HTTPException
from promaia.web.models.natural_language import (
    NaturalLanguageRequest, 
    NaturalLanguageResponse,
    SimpleNLRequest,
    SimpleNLResponse,
    QueryIntent
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/natural-language", tags=["natural-language"])


@router.post("/process", response_model=NaturalLanguageResponse)
async def process_natural_language(request: NaturalLanguageRequest):
    """Process natural language query using LangGraph intelligent system."""
    start_time = time.time()
    
    try:
        logger.info(f"🧠 Processing natural language query: {request.query[:100]}...")
        
        # Import here to avoid circular imports
        from ...ai.intelligent_nl_processor import IntelligentNaturalLanguageProcessor
        
        # Create processor instance
        processor = IntelligentNaturalLanguageProcessor()
        
        if not processor.enabled:
            raise HTTPException(
                status_code=503, 
                detail="Natural language processor not available - API credits insufficient or no working LLM client"
            )
        
        # Process the query
        result = processor.process_query(
            user_query=request.query,
            scope_databases=request.scope_databases
        )
        
        processing_time = time.time() - start_time
        
        if result["success"]:
            # Convert intent to Pydantic model if available
            intent_data = result.get("intent", {})
            intent = None
            
            if intent_data:
                intent = QueryIntent(
                    goal=intent_data.get("goal", "Unknown"),
                    databases=intent_data.get("databases", []),
                    search_terms=intent_data.get("search_terms", []),
                    time_range=intent_data.get("time_range"),
                    complexity_level=intent_data.get("complexity_level", "Unknown"),
                    user_goal=intent_data.get("user_goal", "Unknown")
                )
            
            total_items = sum(len(items) for items in result["results"].values())
            
            logger.info(f"✅ NL processing completed: {total_items} items in {processing_time:.2f}s")
            
            return NaturalLanguageResponse(
                success=True,
                query=request.query,
                intent=intent,
                results=result["results"],
                sql_generated=result.get("sql_generated"),
                total_items=total_items,
                processing_time=processing_time,
                errors=result.get("errors", [])
            )
        else:
            logger.warning(f"⚠️ NL processing failed: {result.get('errors', ['Unknown error'])}")
            
            return NaturalLanguageResponse(
                success=False,
                query=request.query,
                intent=None,
                results={},
                total_items=0,
                processing_time=processing_time,
                errors=result.get("errors", ["Processing failed"])
            )
            
    except Exception as e:
        processing_time = time.time() - start_time
        logger.error(f"❌ NL processing error: {e}")
        
        return NaturalLanguageResponse(
            success=False,
            query=request.query,
            intent=None,
            results={},
            total_items=0,
            processing_time=processing_time,
            errors=[str(e)]
        )


@router.post("/simple", response_model=SimpleNLResponse)
async def simple_natural_language(request: SimpleNLRequest):
    """Simple natural language to source suggestions conversion."""
    try:
        logger.info(f"🔍 Simple NL processing: {request.query[:100]}...")
        
        # Basic pattern matching for common queries
        query_lower = request.query.lower()
        suggested_sources = []
        confidence = 0.0
        reasoning = "Pattern-based analysis: "
        
        # Email patterns
        if any(term in query_lower for term in ['email', 'gmail', 'mail', 'inbox']):
            if request.workspace:
                suggested_sources.append(f"{request.workspace}.gmail")
            else:
                suggested_sources.append("gmail")
            confidence += 0.3
            reasoning += "detected email keywords; "
        
        # Journal patterns
        if any(term in query_lower for term in ['journal', 'diary', 'note', 'entry', 'thought']):
            if request.workspace:
                suggested_sources.append(f"{request.workspace}.journal")
            else:
                suggested_sources.append("journal")
            confidence += 0.3
            reasoning += "detected journal keywords; "
        
        # Discord patterns
        if any(term in query_lower for term in ['discord', 'chat', 'message', 'conversation']):
            if request.workspace:
                suggested_sources.append(f"{request.workspace}.discord")
            else:
                suggested_sources.append("discord")
            confidence += 0.2
            reasoning += "detected chat keywords; "
        
        # Time-based patterns
        time_patterns = {
            'today': ':1',
            'yesterday': ':2', 
            'week': ':7',
            'month': ':30',
            'recent': ':7'
        }
        
        for time_word, suffix in time_patterns.items():
            if time_word in query_lower and suggested_sources:
                # Add time suffix to first source
                suggested_sources[0] = suggested_sources[0] + suffix
                confidence += 0.1
                reasoning += f"detected time keyword '{time_word}'; "
                break
        
        # Default fallback
        if not suggested_sources:
            if request.workspace:
                suggested_sources = [f"{request.workspace}.journal", f"{request.workspace}.gmail"]
            else:
                suggested_sources = ["journal", "gmail"]
            confidence = 0.1
            reasoning += "no specific patterns detected, using default sources"
        
        logger.info(f"✅ Simple NL suggestions: {suggested_sources} (confidence: {confidence:.2f})")
        
        return SimpleNLResponse(
            success=True,
            query=request.query,
            suggested_sources=suggested_sources,
            confidence=min(confidence, 1.0),
            reasoning=reasoning.strip()
        )
        
    except Exception as e:
        logger.error(f"❌ Simple NL processing error: {e}")
        raise HTTPException(status_code=500, detail=f"Simple NL processing failed: {str(e)}")
