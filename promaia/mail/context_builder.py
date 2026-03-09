"""
Response Context Builder - Builds context using vector search.

Searches across all databases in the workspace to find relevant context
for generating email responses.
"""
import json
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ResponseContext:
    """Container for response generation context."""
    thread_history: str
    relevant_docs: List[Dict[str, Any]]
    relevant_docs_text: str
    workspace: str
    total_sources: int


class ResponseContextBuilder:
    """Builds context using vector search across databases."""
    
    def __init__(self):
        """Initialize context builder."""
        self.vector_db = None
    
    def _get_vector_db(self):
        """Lazy load vector DB manager."""
        if self.vector_db is None:
            from promaia.storage.vector_db import VectorDBManager
            self.vector_db = VectorDBManager()
        return self.vector_db
    
    async def build_context(
        self,
        email_thread: Dict[str, Any],
        workspace: str,
        n_results: int = 20,
        min_similarity: float = 0.3
    ) -> ResponseContext:
        """
        Build response context using vector search.
        
        Args:
            email_thread: Email thread data
            workspace: Workspace to search in
            n_results: Maximum number of vector search results
            min_similarity: Minimum similarity threshold
            
        Returns:
            ResponseContext with thread history and relevant documents
        """
        try:
            # Extract thread history
            thread_history = self._extract_thread_history(email_thread)
            
            # Build search query from email content
            search_query = self._build_search_query(email_thread)
            
            # Dual search strategy: search Gmail and non-Gmail separately
            # This prevents emails from dominating results and crowding out project context
            vector_db = self._get_vector_db()
            
            logger.info(f"🔍 Performing dual search in {workspace} workspace...")
            logger.debug(f"Search query: {search_query[:200]}...")
            
            # Strategy: TWO SEPARATE vector searches to ensure balanced results
            # This is critical because Gmail emails would dominate ALL top results otherwise
            
            # Search 1: Gmail ONLY (filtered at query time)
            gmail_db_names = ["gmail", f"{workspace}.gmail"]
            gmail_filters = {
                "$and": [
                    {"workspace": {"$eq": workspace}},
                    {"database_name": {"$in": gmail_db_names}}
                ]
            }
            
            logger.debug(f"Gmail filters: {gmail_filters}")
            
            gmail_results = vector_db.search(
                query_text=search_query,
                filters=gmail_filters,
                n_results=10,
                min_similarity=min_similarity
            )
            
            # Search 2: NON-Gmail ONLY (filtered at query time)
            non_gmail_filters = {
                "$and": [
                    {"workspace": {"$eq": workspace}},
                    {"database_name": {"$ne": "gmail"}},
                    {"database_name": {"$ne": f"{workspace}.gmail"}}
                ]
            }
            
            logger.debug(f"Non-Gmail filters: {non_gmail_filters}")
            
            non_gmail_results = vector_db.search(
                query_text=search_query,
                filters=non_gmail_filters,
                n_results=5,
                min_similarity=0.0  # No threshold - always include top 5 non-Gmail
            )
            
            logger.info(f"📧 Gmail: {len(gmail_results)} results (threshold: {min_similarity})")
            logger.info(f"📚 Non-Gmail: {len(non_gmail_results)} results (guaranteed top 5)")
            
            if non_gmail_results:
                for idx, result in enumerate(non_gmail_results[:3]):  # Log first 3
                    db = result.get('metadata', {}).get('database_name', 'unknown')
                    score = result.get('similarity_score', 0)
                    title = result.get('metadata', {}).get('title', 'untitled')[:40]
                    logger.info(f"  📄 {db}: {title} ({score:.2f})")
            
            # Combine results by interleaving to ensure balanced representation
            # This prevents one source type from dominating the top 10
            results = []
            max_len = max(len(gmail_results), len(non_gmail_results))
            for i in range(max_len):
                if i < len(gmail_results):
                    results.append(gmail_results[i])
                if i < len(non_gmail_results):
                    results.append(non_gmail_results[i])
            
            logger.info(f"📊 Combined: {len(results)} total results ({len(gmail_results)} Gmail + {len(non_gmail_results)} non-Gmail)")
            if results:
                logger.debug(f"Sample result: {results[0]}")
            
            # Load full content for all results (interleaved, so we get balanced mix in top 10)
            # With 10 Gmail + 5 non-Gmail interleaved, top 10 = 5 Gmail + 5 non-Gmail
            relevant_docs = self._load_document_content(results[:10])  # Top 10 after interleaving
            
            # Format documents as text for prompt
            relevant_docs_text = self._format_docs_for_prompt(relevant_docs)
            
            logger.info(f"✅ Found {len(relevant_docs)} relevant documents for context")
            
            return ResponseContext(
                thread_history=thread_history,
                relevant_docs=relevant_docs,
                relevant_docs_text=relevant_docs_text,
                workspace=workspace,
                total_sources=len(results)
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to build response context: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            # Return minimal context
            return ResponseContext(
                thread_history=self._extract_thread_history(email_thread),
                relevant_docs=[],
                relevant_docs_text="No additional context available.",
                workspace=workspace,
                total_sources=0
            )
    
    def _extract_thread_history(self, email_thread: Dict[str, Any]) -> str:
        """Extract formatted thread history."""
        # Get conversation body (which includes full thread for full_thread mode)
        conversation = email_thread.get('conversation_body', '')
        
        if not conversation:
            # Fallback to basic info
            from_addr = email_thread.get('from', 'Unknown')
            subject = email_thread.get('subject', 'No Subject')
            date = email_thread.get('date', 'Unknown')
            body = email_thread.get('body', '')
            
            return f"""From: {from_addr}
Subject: {subject}
Date: {date}

{body}"""
        
        return conversation
    
    def _build_search_query(self, email_thread: Dict[str, Any]) -> str:
        """
        Build search query from email content.
        Combines subject and body for comprehensive search.
        """
        subject = email_thread.get('subject', '')
        body = email_thread.get('conversation_body', '') or email_thread.get('body', '')
        
        # Combine subject and body, truncate if too long
        query = f"{subject}\n\n{body}"
        
        if len(query) > 2000:
            query = query[:2000]
        
        return query
    
    def _load_document_content(self, search_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Load full content for search results.
        
        Args:
            search_results: Results from vector search
            
        Returns:
            List of documents with full content
        """
        docs = []
        
        for result in search_results:
            try:
                page_id = result.get('page_id')
                metadata = result.get('metadata', {})
                
                # Try to read the markdown file
                from promaia.storage.hybrid_storage import get_hybrid_registry
                import sqlite3
                registry = get_hybrid_registry()
                
                # Get file path from registry
                with sqlite3.connect(registry.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT file_path, title, database_name FROM unified_content WHERE page_id = ?",
                        (page_id,)
                    )
                    row = cursor.fetchone()
                    
                    if row:
                        file_path, title, database_name = row
                        
                        # Read markdown content
                        import os
                        if os.path.exists(file_path):
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.read()

                            # Keep full content (no truncation)
                            docs.append({
                                'page_id': page_id,
                                'title': title,
                                'database': database_name,
                                'content_snippet': content,
                                'similarity': result.get('similarity_score', 0),
                                'metadata': metadata
                            })
            
            except Exception as e:
                logger.warning(f"⚠️  Could not load content for {page_id}: {e}")
                continue
        
        return docs
    
    def _format_docs_for_prompt(self, docs: List[Dict[str, Any]]) -> str:
        """Format documents as text for AI prompt, separated by source type."""
        if not docs:
            return "No relevant documents found in knowledge base."
        
        # Separate Gmail and non-Gmail documents
        gmail_docs = []
        non_gmail_docs = []
        
        for doc in docs:
            db_name = doc.get('database', '')
            if db_name == 'gmail' or db_name.endswith('.gmail'):
                gmail_docs.append(doc)
            else:
                non_gmail_docs.append(doc)
        
        formatted = []
        
        # Gmail section
        if gmail_docs:
            formatted.append(f"=== EMAIL HISTORY ({len(gmail_docs)} relevant threads) ===\n")
            for i, doc in enumerate(gmail_docs, 1):
                formatted.append(f"[{i}] {doc['title']}")
                formatted.append(f"    Database: {doc['database']} | Relevance: {doc['similarity']:.0%}")
                formatted.append(f"    {doc['content_snippet']}")
                formatted.append("")
        
        # Non-Gmail section
        if non_gmail_docs:
            formatted.append(f"=== PROJECT CONTEXT ({len(non_gmail_docs)} relevant documents) ===\n")
            for i, doc in enumerate(non_gmail_docs, 1):
                formatted.append(f"[{i}] {doc['title']}")
                formatted.append(f"    Database: {doc['database']} | Relevance: {doc['similarity']:.0%}")
                formatted.append(f"    {doc['content_snippet']}")
                formatted.append("")
        
        return '\n'.join(formatted)
    
    def serialize_context_for_storage(self, context: ResponseContext) -> str:
        """
        Serialize context for storage in database.
        
        Args:
            context: ResponseContext object
            
        Returns:
            JSON string
        """
        return json.dumps({
            'total_sources': context.total_sources,
            'relevant_docs_text': context.relevant_docs_text,  # Store formatted text for AI prompt
            'workspace': context.workspace,
            'documents': [
                {
                    'page_id': doc['page_id'],
                    'title': doc['title'],
                    'database': doc['database'],
                    'similarity': doc['similarity'],
                    'snippet': doc['content_snippet'][:200]  # Store short snippet
                }
                for doc in context.relevant_docs
            ]
        })
    
    def deserialize_context_from_storage(self, context_json: str, thread_history: str = "") -> Optional[ResponseContext]:
        """
        Deserialize context from storage.
        
        Args:
            context_json: JSON string from database
            thread_history: Thread history text
            
        Returns:
            ResponseContext object or None if deserialization fails
        """
        try:
            data = json.loads(context_json)
            return ResponseContext(
                thread_history=thread_history,
                relevant_docs=[],  # Don't need full docs for refinement
                relevant_docs_text=data.get('relevant_docs_text', ''),
                workspace=data.get('workspace', ''),
                total_sources=data.get('total_sources', 0)
            )
        except Exception as e:
            logger.warning(f"Failed to deserialize context: {e}")
            return None

