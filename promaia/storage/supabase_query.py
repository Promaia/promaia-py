"""
Supabase Query Interface for Promaia

This replaces the SQLite-based query system with Supabase PostgreSQL queries.
Designed for fast context loading and scalable multi-user support.
"""

import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, date, timedelta
from supabase import create_client, Client

from promaia.utils.config import load_environment

logger = logging.getLogger(__name__)

class SupabaseQueryInterface:
    """Query interface that uses Supabase PostgreSQL for all data operations."""
    
    def __init__(self, user_id: str = "00000000-0000-0000-0000-000000000001"):
        """Initialize Supabase client."""
        load_environment()
        
        self.supabase_url = os.getenv('SUPABASE_URL')
        self.supabase_key = os.getenv('SUPABASE_ANON_KEY')
        self.user_id = user_id
        
        if not self.supabase_url or not self.supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be set")
        
        self.supabase: Client = create_client(self.supabase_url, self.supabase_key)
        logger.info(f"🌩️ SupabaseQueryInterface initialized for user {user_id[:8]}...")
    
    def query_content_for_chat(self, workspace: str, sources: List[str] = None, 
                              days: int = None, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Query content for chat interface - optimized for fast context loading."""
        try:
            logger.info(f"🔍 Querying Supabase: workspace={workspace}, sources={sources}, days={days}")
            
            # Build base query
            query = self.supabase.table('content_items').select(
                'id, title, content, source_name, content_type, created_date, message_date, metadata'
            ).eq('user_id', self.user_id)
            
            # Filter by sources if specified
            if sources and len(sources) > 0:
                # Handle source filtering
                source_names = []
                for source in sources:
                    if ':' in source:
                        # Handle "journal:30" format - extract just the source name
                        source_name = source.split(':')[0]
                        source_names.append(source_name)
                    else:
                        source_names.append(source)
                
                if len(source_names) == 1:
                    query = query.eq('source_name', source_names[0])
                else:
                    query = query.in_('source_name', source_names)
            
            # Filter by workspace (if it maps to specific sources)
            if workspace and workspace != "":
                # For now, workspace filtering is handled by source filtering
                # In the future, we could add a workspace field to content_items
                pass
            
            # Filter by date range if specified
            # Use PostgreSQL COALESCE to pick first non-null date efficiently
            if days and days > 0:
                cutoff_date = (datetime.now() - timedelta(days=days)).date().isoformat()
                logger.info(f"🕒 Date filtering requested ({days} days, cutoff: {cutoff_date})")

                # Simple, efficient date filtering approach
                # After migration, created_date should be populated for most items
                try:
                    # Use created_date filter - fastest and most reliable after migration
                    query = query.gte('created_date', cutoff_date)
                    logger.info("✅ Applied date filtering using created_date")
                except Exception as e:
                    logger.warning(f"⚠️ Date filtering failed, continuing without date filter: {e}")
                    # Continue without date filtering to ensure some results are returned
            
            # Order by the best-available date; use indexed_date as stable fallback
            query = query.order('indexed_date', desc=True)
            
            # Limit to prevent overwhelming context
            query = query.limit(1000)
            
            # Execute query
            response = query.execute()
            
            if not response.data:
                logger.info("📭 No content found matching criteria")
                return []
            
            # Transform to expected format
            results = []
            for item in response.data:
                transformed = {
                    'id': item['id'],
                    'title': item['title'],
                    'content': item['content'],
                    'source_database': item['source_name'],
                    'database_name': item['source_name'],
                    'content_type': item['content_type'],
                    'timestamp': item.get('created_date'),
                    'date_obj': None,
                    'metadata': item.get('metadata', {})
                }
                
                # Parse date
                if item.get('created_date'):
                    try:
                        transformed['date_obj'] = datetime.fromisoformat(item['created_date']).date()
                    except:
                        pass
                elif item.get('message_date'):
                    try:
                        transformed['date_obj'] = datetime.fromisoformat(item['message_date']).date()
                    except:
                        pass
                
                results.append(transformed)
            
            logger.info(f"✅ Found {len(results)} items from Supabase")
            return results
            
        except Exception as e:
            logger.error(f"❌ Supabase query failed: {e}")
            return []
    
    def natural_language_query(self, nl_prompt: str, workspace: str = None, 
                              database_names: List[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        """Process natural language query using Supabase full-text search."""
        try:
            logger.info(f"🤖 Natural language query: '{nl_prompt[:50]}...'")
            
            # For now, do a simple content search
            # In the future, we could use Supabase's vector search or AI features
            
            # Build search query
            query = self.supabase.table('content_items').select(
                'id, title, content, source_name, content_type, created_date, message_date, metadata'
            ).eq('user_id', self.user_id)
            
            # Simple text search in title and content
            # Supabase supports PostgreSQL full-text search
            search_terms = nl_prompt.lower().split()
            
            # Filter by database names if specified
            if database_names and len(database_names) > 0:
                if len(database_names) == 1:
                    query = query.eq('source_name', database_names[0])
                else:
                    query = query.in_('source_name', database_names)
            
            # Use text search (this is a simplified version)
            # For better results, we'd use PostgreSQL's ts_vector and ts_query
            if search_terms:
                # For now, use ilike for simple text matching
                # textSearch requires full-text search setup which we haven't configured yet
                search_term = '%' + ' '.join(search_terms) + '%'
                query = query.ilike('content', search_term)
            
            # Order by relevance (for now, by date)
            query = query.order('created_date', desc=True).limit(500)
            
            # Execute query
            response = query.execute()
            
            if not response.data:
                logger.info("📭 No content found for natural language query")
                return {}
            
            # Group results by source
            results = {}
            for item in response.data:
                source_name = item['source_name']
                if source_name not in results:
                    results[source_name] = []
                
                transformed = {
                    'id': item['id'],
                    'title': item['title'],
                    'content': item['content'],
                    'source_database': source_name,
                    'database_name': source_name,
                    'content_type': item['content_type'],
                    'timestamp': item.get('created_date'),
                    'date_obj': None,
                    'metadata': item.get('metadata', {})
                }
                
                # Parse date
                if item.get('created_date'):
                    try:
                        transformed['date_obj'] = datetime.fromisoformat(item['created_date']).date()
                    except:
                        pass
                
                results[source_name].append(transformed)
            
            logger.info(f"✅ Natural language query found {sum(len(items) for items in results.values())} items across {len(results)} sources")
            return results
            
        except Exception as e:
            logger.error(f"❌ Natural language query failed: {e}")
            return {}
    
    def get_database_context(self, workspace: str) -> Dict[str, Any]:
        """Get database context information for workspace."""
        try:
            logger.info(f"📊 Getting database context for workspace: {workspace}")
            
            # Get counts by source
            response = self.supabase.table('content_items').select(
                'source_name', count='exact'
            ).eq('user_id', self.user_id).execute()
            
            # Count by source name
            source_counts = {}
            if response.data:
                # Group and count manually since Supabase doesn't return grouped counts directly
                for item in response.data:
                    source = item['source_name']
                    source_counts[source] = source_counts.get(source, 0) + 1
            
            # Get total count
            total_response = self.supabase.table('content_items').select(
                '*', count='exact'
            ).eq('user_id', self.user_id).execute()
            
            total_count = total_response.count if hasattr(total_response, 'count') else len(total_response.data or [])
            
            context = {
                'workspace': workspace,
                'total_items': total_count,
                'sources': source_counts,
                'database_type': 'supabase_postgresql',
                'last_updated': datetime.now().isoformat()
            }
            
            logger.info(f"✅ Database context: {total_count} total items across {len(source_counts)} sources")
            return context
            
        except Exception as e:
            logger.error(f"❌ Database context query failed: {e}")
            return {
                'workspace': workspace,
                'total_items': 0,
                'sources': {},
                'database_type': 'supabase_postgresql',
                'error': str(e)
            }
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics about the data."""
        try:
            logger.info("📈 Getting Supabase statistics...")
            
            # Get total count
            total_response = self.supabase.table('content_items').select(
                '*', count='exact'
            ).eq('user_id', self.user_id).execute()
            
            total_count = total_response.count if hasattr(total_response, 'count') else len(total_response.data or [])
            
            # Get counts by source
            source_counts = {}
            for source in ['gmail', 'journal', 'stories', 'generic']:
                try:
                    response = self.supabase.table('content_items').select(
                        '*', count='exact'
                    ).eq('user_id', self.user_id).eq('source_name', source).execute()
                    
                    count = response.count if hasattr(response, 'count') else len(response.data or [])
                    source_counts[source] = count
                except Exception as e:
                    logger.warning(f"Error getting count for {source}: {e}")
                    source_counts[source] = 0
            
            # Get date range
            date_response = self.supabase.table('content_items').select(
                'created_date'
            ).eq('user_id', self.user_id).order('created_date', desc=False).limit(1).execute()
            
            earliest_date = None
            if date_response.data:
                try:
                    earliest_date = date_response.data[0]['created_date']
                except:
                    pass
            
            stats = {
                'total': total_count,
                'architecture': 'supabase_postgresql',
                'database_url': self.supabase_url,
                'user_id': self.user_id[:8] + '...',
                'earliest_date': earliest_date,
                'last_updated': datetime.now().isoformat(),
                **source_counts
            }
            
            logger.info(f"✅ Statistics: {total_count} total items")
            return stats
            
        except Exception as e:
            logger.error(f"❌ Statistics query failed: {e}")
            return {
                'total': 0,
                'architecture': 'supabase_postgresql',
                'error': str(e)
            }

# Global instance
_supabase_query_interface: Optional[SupabaseQueryInterface] = None

def get_supabase_query_interface(user_id: str = "00000000-0000-0000-0000-000000000001") -> SupabaseQueryInterface:
    """Get the global Supabase query interface instance."""
    global _supabase_query_interface
    if _supabase_query_interface is None:
        _supabase_query_interface = SupabaseQueryInterface(user_id)
    return _supabase_query_interface
