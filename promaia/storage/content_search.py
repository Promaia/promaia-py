"""
Content search utilities for natural language queries.

This module provides functionality to search through the actual content files
stored in the /data/ directory structure, complementing the metadata searches
in the unified_content view.
"""
import os
import re
import sqlite3
import logging
from typing import Dict, List, Optional, Any, Set
from pathlib import Path

logger = logging.getLogger(__name__)

class ContentSearcher:
    """Searches through actual content files for natural language queries."""
    
    def __init__(self, db_path: str = None, data_root: str = None):
        if db_path is None:
            from promaia.utils.env_writer import get_db_path
            db_path = str(get_db_path())
        if data_root is None:
            from promaia.utils.env_writer import get_data_subdir
            data_root = str(get_data_subdir())
        self.db_path = db_path
        self.data_root = data_root
    
    def search_content(self, search_terms: List[str], 
                      workspace: str = None, 
                      database_names: List[str] = None,
                      limit: int = 1000) -> List[Dict[str, Any]]:
        """
        Search through actual content files for given terms.
        
        Args:
            search_terms: List of terms to search for
            workspace: Optional workspace filter
            database_names: Optional database name filters
            limit: Maximum number of results to return
            
        Returns:
            List of matching content items with metadata
        """
        try:
            # Get file paths from database
            file_paths = self._get_file_paths(workspace, database_names, limit)
            
            if not file_paths:
                logger.info("No file paths found for search criteria")
                return []
            
            # Search through files
            matching_results = []
            
            for file_info in file_paths:
                file_path = file_info['file_path']
                
                if self._file_contains_terms(file_path, search_terms):
                    # Add content preview
                    content_preview = self._get_content_preview(file_path, search_terms)
                    
                    result = {
                        **file_info,  # Include all database metadata
                        'content_preview': content_preview,
                        'search_match_type': 'content'
                    }
                    matching_results.append(result)
            
            logger.info(f"Content search found {len(matching_results)} matches for terms: {search_terms}")
            return matching_results
            
        except Exception as e:
            logger.error(f"Error in content search: {e}")
            return []
    
    def _get_file_paths(self, workspace: str = None, 
                       database_names: List[str] = None, 
                       limit: int = 1000) -> List[Dict[str, Any]]:
        """Get file paths and metadata from database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Build query
                where_conditions = ["file_path IS NOT NULL", "file_path != ''"]
                params = []
                
                if workspace:
                    where_conditions.append("workspace = ?")
                    params.append(workspace)
                
                if database_names:
                    db_placeholders = ','.join('?' * len(database_names))
                    where_conditions.append(f"database_name IN ({db_placeholders})")
                    params.extend(database_names)
                
                where_clause = " AND ".join(where_conditions)
                
                query = f"""
                    SELECT page_id, workspace, database_name, content_type, file_path, 
                           title, created_time, last_edited_time, synced_time,
                           sender_email, sender_name, metadata
                    FROM unified_content 
                    WHERE {where_clause}
                    ORDER BY last_edited_time DESC
                    LIMIT ?
                """
                
                params.append(limit)
                cursor.execute(query, params)
                
                results = []
                for row in cursor.fetchall():
                    results.append(dict(row))
                
                return results
                
        except Exception as e:
            logger.error(f"Error getting file paths: {e}")
            return []
    
    def _file_contains_terms(self, file_path: str, search_terms: List[str]) -> bool:
        """Check if file contains any of the search terms."""
        try:
            # Handle file paths - database paths already include data/ prefix
            if os.path.isabs(file_path):
                full_path = file_path
            elif file_path.startswith('data/'):
                # Path already includes data/ prefix
                full_path = file_path
            else:
                # Path is relative to data root
                full_path = os.path.join(self.data_root, file_path)
            
            if not os.path.exists(full_path):
                logger.debug(f"File not found: {full_path}")
                return False
            
            # Read file content
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read().lower()
            
            # Check for any term (case-insensitive)
            for term in search_terms:
                if term.lower() in content:
                    return True
            
            return False
            
        except Exception as e:
            logger.debug(f"Error reading file {file_path}: {e}")
            return False
    
    def _get_content_preview(self, file_path: str, search_terms: List[str], 
                           context_chars: int = 200) -> str:
        """Get a preview of content around the search terms."""
        try:
            # Handle file paths - database paths already include data/ prefix
            if os.path.isabs(file_path):
                full_path = file_path
            elif file_path.startswith('data/'):
                # Path already includes data/ prefix
                full_path = file_path
            else:
                # Path is relative to data root
                full_path = os.path.join(self.data_root, file_path)
            
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            # Find the first occurrence of any search term
            content_lower = content.lower()
            earliest_pos = len(content)
            found_term = None
            
            for term in search_terms:
                pos = content_lower.find(term.lower())
                if pos != -1 and pos < earliest_pos:
                    earliest_pos = pos
                    found_term = term
            
            if found_term is None:
                # Return start of content if no terms found
                return content[:context_chars] + "..." if len(content) > context_chars else content
            
            # Extract context around the found term
            start = max(0, earliest_pos - context_chars // 2)
            end = min(len(content), earliest_pos + len(found_term) + context_chars // 2)
            
            preview = content[start:end]
            
            # Add ellipsis if truncated
            if start > 0:
                preview = "..." + preview
            if end < len(content):
                preview = preview + "..."
            
            return preview
            
        except Exception as e:
            logger.debug(f"Error getting content preview for {file_path}: {e}")
            return ""

    def search_by_content_pattern(self, pattern: str, 
                                workspace: str = None,
                                database_names: List[str] = None) -> List[Dict[str, Any]]:
        """
        Search content using regex patterns.
        
        Args:
            pattern: Regex pattern to search for
            workspace: Optional workspace filter
            database_names: Optional database name filters
            
        Returns:
            List of matching content items
        """
        try:
            compiled_pattern = re.compile(pattern, re.IGNORECASE)
            file_paths = self._get_file_paths(workspace, database_names)
            
            matching_results = []
            
            for file_info in file_paths:
                file_path = file_info['file_path']
                
                if self._file_matches_pattern(file_path, compiled_pattern):
                    # Get content preview around matches
                    content_preview = self._get_pattern_preview(file_path, compiled_pattern)
                    
                    result = {
                        **file_info,
                        'content_preview': content_preview,
                        'search_match_type': 'pattern'
                    }
                    matching_results.append(result)
            
            return matching_results
            
        except Exception as e:
            logger.error(f"Error in pattern search: {e}")
            return []
    
    def _file_matches_pattern(self, file_path: str, pattern: re.Pattern) -> bool:
        """Check if file content matches the regex pattern."""
        try:
            # Handle file paths - database paths already include data/ prefix
            if os.path.isabs(file_path):
                full_path = file_path
            elif file_path.startswith('data/'):
                # Path already includes data/ prefix
                full_path = file_path
            else:
                # Path is relative to data root
                full_path = os.path.join(self.data_root, file_path)
            
            if not os.path.exists(full_path):
                return False
            
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            return bool(pattern.search(content))
            
        except Exception as e:
            logger.debug(f"Error checking pattern match for {file_path}: {e}")
            return False
    
    def _get_pattern_preview(self, file_path: str, pattern: re.Pattern, 
                           context_chars: int = 200) -> str:
        """Get preview around pattern matches."""
        try:
            # Handle file paths - database paths already include data/ prefix
            if os.path.isabs(file_path):
                full_path = file_path
            elif file_path.startswith('data/'):
                # Path already includes data/ prefix
                full_path = file_path
            else:
                # Path is relative to data root
                full_path = os.path.join(self.data_root, file_path)
            
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            match = pattern.search(content)
            if not match:
                return content[:context_chars] + "..." if len(content) > context_chars else content
            
            start_pos = match.start()
            start = max(0, start_pos - context_chars // 2)
            end = min(len(content), start_pos + len(match.group()) + context_chars // 2)
            
            preview = content[start:end]
            
            if start > 0:
                preview = "..." + preview
            if end < len(content):
                preview = preview + "..."
            
            return preview
            
        except Exception as e:
            logger.debug(f"Error getting pattern preview for {file_path}: {e}")
            return ""


# Global instance
_content_searcher = None

def get_content_searcher(db_path: str = None,
                        data_root: str = None) -> ContentSearcher:
    """Get singleton instance of ContentSearcher."""
    global _content_searcher
    if _content_searcher is None:
        _content_searcher = ContentSearcher(db_path, data_root)
    return _content_searcher