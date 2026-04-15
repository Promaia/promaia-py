"""
Wrapper for agentic NL processor to integrate with existing CLI/Chat interface.
Provides backward-compatible API while using the new agentic system.
"""
from typing import Dict, List, Optional, Any
import os

from promaia.nlq.nl_orchestrator import AgenticNLQueryProcessor
from promaia.utils.display import print_text


# Global processor instance
_processor = None

def get_nl_processor(verbose: bool = False, query_mode: str = "sql") -> AgenticNLQueryProcessor:
    """Get or create the global agentic NL processor."""
    global _processor
    # Note: We don't cache the processor because verbose mode may change between calls
    # This ensures the correct verbose setting is always used
    _processor = AgenticNLQueryProcessor(
        query_mode=query_mode,
        debug=os.getenv("MAIA_DEBUG") == "1",
        verbose=verbose
    )
    return _processor


def process_natural_language_to_content(
    nl_prompt: str,
    workspace: str = None,
    database_names: List[str] = None,
    verbose: bool = False,
    skip_confirmation: bool = False,
    return_metadata: bool = False
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Process natural language queries using the new agentic system.

    This is the main integration point that replaces the old LangGraph system.
    It provides a backward-compatible interface for the CLI and Chat.

    Args:
        nl_prompt: The natural language query
        workspace: Optional workspace filter
        database_names: Optional list of databases to search
        verbose: Show detailed processing steps
        skip_confirmation: Skip user confirmation prompts (for parallel query execution)
        return_metadata: If True, returns (content, metadata) tuple with generated SQL

    Returns:
        Dict mapping database_name -> list of content entries
        OR tuple of (content_dict, metadata_dict) if return_metadata=True
    """
    try:
        processor = get_nl_processor(verbose=verbose)

        # Process the query with the agentic system (includes modification support)
        result = processor.process_query_with_modification(
            nl_prompt,
            workspace=workspace,
            max_retries=2,
            skip_confirmation=skip_confirmation
        )
        
        # Extract metadata for visibility
        metadata = {
            'generated_query': result.get('query'),  # SQL query or vector params
            'query_mode': result.get('query_mode'),
            'intent': result.get('intent')
        }
        
        # Check if user chose to quit (exit to terminal)
        if result.get("action") == "quit":
            return ({}, metadata) if return_metadata else {}
        
        if result["success"] and result["results"]:
            # Direct-data results (e.g. Shopify) — raw SQL rows, no page loading
            if result.get("direct_data"):
                content = {"shopify": result["direct_data"]}
                metadata['direct_data'] = True
                return (content, metadata) if return_metadata else content

            # Extract page IDs from the results
            page_ids = []
            for db_name, entries in result["results"].items():
                for entry in entries:
                    if entry.get('page_id'):
                        page_ids.append(entry['page_id'])

            if not page_ids:
                if verbose:
                    print_text("⚠️  No page IDs found in query results", style="yellow")
                return ({}, metadata) if return_metadata else {}

            # Use the universal adapter to load full content
            if verbose:
                print_text(f"📄 Loading full content for {len(page_ids)} pages...", style="dim")

            from promaia.storage.files import load_content_by_page_ids

            full_content = load_content_by_page_ids(
                page_ids=page_ids,
                expand_gmail_threads=True
            )

            # Return the full content in the expected format
            content = full_content if full_content else {}
            return (content, metadata) if return_metadata else content
        
        else:
            # Query failed after retries
            error_msg = result.get("error", "Unknown error")
            print_text(f"⚠️  Natural language query failed: {error_msg}", style="yellow")
            return ({}, metadata) if return_metadata else {}
    
    except Exception as e:
        print_text(f"❌ Error in natural language processing: {e}", style="red")
        if os.getenv("MAIA_DEBUG") == "1":
            import traceback
            traceback.print_exc()
        return ({}, {}) if return_metadata else {}


def process_vector_search_to_content(
    vs_prompt: str,
    workspace: str = None,
    database_names: List[str] = None,
    verbose: bool = False,
    n_results: int = 20,
    min_similarity: float = 0.2,
    skip_confirmation: bool = False
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Process vector search queries using semantic similarity.

    This uses the same agentic system as NL queries but in vector mode.

    Args:
        vs_prompt: The vector search query
        workspace: Optional workspace filter
        database_names: Optional list of databases to search
        verbose: Show detailed processing steps
        n_results: Maximum number of results to return (default: 20)
        min_similarity: Minimum similarity threshold 0-1 (default: 0.2)
        skip_confirmation: Skip user confirmation prompts (for parallel query execution)
    
    Returns:
        Dict mapping database_name -> list of content entries
    """
    try:
        processor = get_nl_processor(verbose=verbose, query_mode="vector")

        # Process the query with the agentic system (includes modification support)
        # Note: Vector search is deterministic, so retries are pointless - set max_retries=0
        result = processor.process_query_with_modification(
            vs_prompt,
            workspace=workspace,
            max_retries=0,  # No auto-retry for vector search (deterministic)
            n_results=n_results,
            min_similarity=min_similarity,
            skip_confirmation=skip_confirmation
        )
        
        # Check if user chose to quit (exit to terminal)
        if result.get("action") == "quit":
            return {}  # Return empty results to prevent chat from loading
        
        if result.get("success") and not result.get("results"):
            # Valid search, just no results — return empty without error
            return {}

        if result.get("success") and result.get("results"):
            # Extract page IDs from the results (handling both chunks and full pages)
            page_ids = []
            chunk_matches = {}  # Map page_id -> list of matched chunk indices

            # Defensive: ensure results is a dict
            results = result.get("results")
            if not isinstance(results, dict):
                print_text("⚠️  Invalid results format from vector search", style="yellow")
                return {}

            for db_name, entries in results.items():
                for entry in entries:
                    # Get page_id (already base page_id from query strategy)
                    page_id = entry.get('page_id')
                    chunk_id = entry.get('chunk_id')  # Present if result is from a chunk

                    if page_id:
                        if page_id not in page_ids:
                            page_ids.append(page_id)

                        # Track chunk matches - extract chunk index from chunk_id
                        if chunk_id and '_chunk_' in chunk_id:
                            try:
                                chunk_index = int(chunk_id.rsplit('_chunk_', 1)[1])
                                if page_id not in chunk_matches:
                                    chunk_matches[page_id] = []
                                if chunk_index not in chunk_matches[page_id]:
                                    chunk_matches[page_id].append(chunk_index)
                            except (ValueError, IndexError):
                                pass  # Skip if chunk_id format is unexpected
            
            # Defensive: check page_ids is valid
            if not page_ids or page_ids is None:
                if verbose:
                    print_text("⚠️  No page IDs found in search results", style="yellow")
                return {}

            # Use the universal adapter to load full content
            if verbose:
                chunks_info = f" (with {len(chunk_matches)} chunked pages)" if chunk_matches else ""
                print_text(f"📄 Loading full content for {len(page_ids)} pages{chunks_info}...", style="dim")

            from promaia.storage.files import load_content_by_page_ids

            try:
                full_content = load_content_by_page_ids(
                    page_ids=page_ids,
                        expand_gmail_threads=True
                )
            except Exception as e:
                print_text(f"⚠️  Error loading content: {e}", style="yellow")
                return {}

            # Defensive: ensure full_content is never None
            if full_content is None:
                full_content = {}

            # Enhance results with chunk match information
            if chunk_matches and full_content:
                from promaia.storage.hybrid_storage import get_hybrid_registry
                registry = get_hybrid_registry()
                
                for db_name, pages in full_content.items():
                    for page in pages:
                        page_id = page.get('page_id')
                        if page_id in chunk_matches:
                            # Add chunk match metadata
                            page['matched_chunks'] = sorted(chunk_matches[page_id])
                            
                            # Get chunk boundaries for reference
                            chunks_data = registry.get_chunks_for_page(page_id)
                            if chunks_data:
                                page['chunk_boundaries'] = [
                                    (c['char_start'], c['char_end']) 
                                    for c in chunks_data
                                ]
                                page['total_chunks'] = len(chunks_data)
            
            if full_content:
                total_loaded = sum(len(pages) for pages in full_content.values() if pages is not None)
                if verbose:
                    print(f"Vector search processed: {total_loaded} results loaded")
                    print(f"Goal: {result.get('intent', {}).get('goal', 'Unknown')}")
                    print(f"Mode: Semantic search (vector)")
                    print(f"Sources: {list(full_content.keys())}")
                    if total_loaded > len(page_ids):
                        print(f"   (expanded {total_loaded - len(page_ids)} Gmail thread messages)")
            
            # Return the full content in the expected format
            # Defensive: ensure we never return None
            return full_content if full_content else {}
        
        else:
            # Query failed after retries
            error_msg = result.get("error", "Unknown error")
            print_text(f"⚠️  Vector search failed: {error_msg}", style="yellow")
            return {}
    
    except Exception as e:
        print_text(f"❌ Error in vector search processing: {e}", style="red")
        if os.getenv("MAIA_DEBUG") == "1":
            import traceback
            traceback.print_exc()
        return {}


# Alias for backward compatibility
process_nl_query = process_natural_language_to_content

