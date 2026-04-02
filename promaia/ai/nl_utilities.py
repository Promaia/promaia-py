"""
Agentic Natural Language Query System

Enhancements over the basic NL system:
1. Dynamic schema exploration (PRAGMA table_info)
2. Result validation and retry
3. Multi-step query refinement
4. Learning from successful queries
5. Rolling index of last 20 successful patterns
"""
import os
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from promaia.utils.display import print_text


class SchemaExplorer:
    """Dynamically explore database schema instead of using hardcoded patterns."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._schema_cache = None
    
    def explore_schema(self) -> Dict[str, Any]:
        """
        Discover schema dynamically using PRAGMA commands.
        This replaces hardcoded schema knowledge with actual exploration.
        """
        if self._schema_cache:
            return self._schema_cache
        
        schema = {
            "tables": {},
            "available_databases": [],
            "database_stats": {}
        }
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Get all tables AND views
                cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
                tables = [row[0] for row in cursor.fetchall()]
                
                # Explore each table
                for table in tables:
                    # Get column info
                    cursor.execute(f"PRAGMA table_info({table})")
                    columns = []
                    for row in cursor.fetchall():
                        columns.append({
                            "name": row[1],
                            "type": row[2],
                            "notnull": bool(row[3]),
                            "pk": bool(row[5])
                        })
                    
                    # Get row count
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    row_count = cursor.fetchone()[0]
                    
                    # Get sample rows to help LLM understand the data
                    # Heavy on dynamic context, light on instruction - show multiple examples
                    samples = []
                    if row_count > 0:
                        try:
                            # Get 3 recent samples to demonstrate data patterns
                            cursor.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 3")
                            for row in cursor.fetchall():
                                sample = {}
                                for i, col in enumerate(columns):
                                    value = row[i]
                                    # Truncate very long text for readability
                                    if isinstance(value, str) and len(value) > 150:
                                        sample[col['name']] = value[:150] + "..."
                                    else:
                                        sample[col['name']] = value
                                samples.append(sample)
                        except Exception:
                            # If sampling fails (complex queries, etc), skip it
                            samples = []
                    
                    schema["tables"][table] = {
                        "columns": columns,
                        "row_count": row_count,
                        "samples": samples
                    }
                
                # Get available databases - check both unified_content and generic_content
                content_table = None
                if "unified_content" in tables:
                    content_table = "unified_content"
                elif "generic_content" in tables:
                    content_table = "generic_content"
                
                if content_table:
                    cursor.execute(f"SELECT DISTINCT database_name FROM {content_table}")
                    schema["available_databases"] = [row[0] for row in cursor.fetchall()]
                    
                    # Get stats per database
                    cursor.execute(f"""
                        SELECT database_name, 
                               COUNT(*) as count,
                               MIN(created_time) as earliest,
                               MAX(created_time) as latest
                        FROM {content_table} 
                        GROUP BY database_name
                        ORDER BY count DESC
                    """)
                    for row in cursor.fetchall():
                        schema["database_stats"][row[0]] = {
                            "count": row[1],
                            "date_range": f"{row[2][:10] if row[2] else 'N/A'} to {row[3][:10] if row[3] else 'N/A'}"
                        }
                    
                    # Store which content table we're using
                    schema["main_content_table"] = content_table
        
        except Exception as e:
            print_text(f"⚠️  Schema exploration failed: {e}", style="yellow")
        
        self._schema_cache = schema
        return schema
    
    def get_schema_summary(self) -> str:
        """Generate a human-readable schema summary for the LLM."""
        schema = self.explore_schema()
        
        summary = "=== DYNAMIC SCHEMA EXPLORATION ===\n\n"
        
        # Note which main content table is being used
        main_table = schema.get('main_content_table', 'unified_content')
        summary += f"Main Content Table: {main_table}\n\n"
        
        # Main tables
        summary += "Available Tables:\n"
        for table, info in schema["tables"].items():
            summary += f"  • {table} ({info['row_count']} rows)\n"
            summary += "    Columns: " + ", ".join([f"{col['name']} ({col['type']})" for col in info["columns"][:8]]) + "\n"
        
        # Database stats
        if schema["database_stats"]:
            summary += f"\nAvailable Databases ({len(schema['available_databases'])} total):\n"
            for db, stats in list(schema["database_stats"].items())[:10]:
                summary += f"  • {db}: {stats['count']} entries ({stats['date_range']})\n"
        
        return summary


class QueryLearningSystem:
    """
    Learn from successful queries and maintain a rolling index.
    Stores last 20 successful query patterns for future reference.
    """
    
    def __init__(self, storage_dir: str = None):
        if storage_dir is None:
            from promaia.utils.env_writer import get_data_subdir
            storage_dir = str(get_data_subdir() / "nl_query_patterns")
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.storage_dir / "successful_patterns.json"
        self.max_patterns = 20
    
    def load_successful_patterns(self) -> List[Dict[str, Any]]:
        """Load the rolling index of successful query patterns."""
        if not self.index_file.exists():
            return []
        
        try:
            with open(self.index_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print_text(f"⚠️  Could not load patterns: {e}", style="yellow")
            return []
    
    def save_successful_pattern(self, pattern: Dict[str, Any]):
        """
        Save a successful query pattern to the rolling index.
        Maintains only the last 20 patterns.
        """
        patterns = self.load_successful_patterns()
        
        # Add new pattern with timestamp
        pattern["timestamp"] = datetime.now().isoformat()
        pattern["id"] = datetime.now().strftime("%Y%m%d_%H%M%S")
        patterns.insert(0, pattern)  # Add to front
        
        # Keep only last 20
        patterns = patterns[:self.max_patterns]
        
        try:
            with open(self.index_file, 'w') as f:
                json.dump(patterns, f, indent=2)
            print_text(f"✅ Saved pattern to learning index ({len(patterns)}/20)", style="green")
        except Exception as e:
            print_text(f"⚠️  Could not save pattern: {e}", style="yellow")
    
    def get_patterns_for_prompt(self) -> str:
        """Generate a prompt section with learned successful patterns."""
        patterns = self.load_successful_patterns()
        
        if not patterns:
            return "No learned patterns yet."
        
        prompt = f"=== LEARNED SUCCESSFUL PATTERNS ({len(patterns)} total) ===\n"
        prompt += "These are real queries that worked well in the past:\n\n"
        
        for i, pattern in enumerate(patterns[:10], 1):  # Show top 10
            prompt += f"{i}. User Query: \"{pattern['user_query']}\"\n"
            prompt += f"   Intent: {pattern.get('intent', {}).get('goal', 'N/A')}\n"
            prompt += f"   SQL: {pattern['generated_sql'][:150]}...\n"
            prompt += f"   Results: {pattern.get('result_count', 0)} entries\n"
            if pattern.get('notes'):
                prompt += f"   Notes: {pattern['notes']}\n"
            prompt += "\n"
        
        return prompt


class NLContextLogger:
    """
    Save NL query context logs for user inspection.
    Similar to context logs but for NL queries specifically.
    """
    
    def __init__(self, log_dir: str = None):
        if log_dir is None:
            from promaia.utils.env_writer import get_data_dir
            log_dir = str(get_data_dir() / "context_logs" / "nl_context_logs")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
    
    def save_draft_context(self, query_info: Dict[str, Any]) -> Path:
        """Save a draft context log that user can inspect before accepting."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.log_dir / f"{timestamp}_nl_query_draft.json"
        
        try:
            with open(log_file, 'w') as f:
                json.dump(query_info, f, indent=2)
            return log_file
        except Exception as e:
            print_text(f"⚠️  Could not save context log: {e}", style="yellow")
            return None
    
    def save_summary(self, query_info: Dict[str, Any]) -> Path:
        """Save a human-readable summary of query results."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_file = self.log_dir / f"{timestamp}_nl_query_summary.txt"
        
        summary = f"""Natural Language Query Summary
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

USER QUERY: {query_info.get('user_query', 'N/A')}

INTENT PARSED:
  Goal: {query_info.get('intent', {}).get('goal', 'N/A')}
  Databases: {', '.join(query_info.get('intent', {}).get('databases', []))}
  Search Terms: {', '.join(query_info.get('intent', {}).get('search_terms', []))}
  Date Filter: {query_info.get('intent', {}).get('date_filter', {}).get('description', 'none')}

GENERATED SQL:
{query_info.get('generated_sql', 'N/A')}

RESULTS:
  Total Entries: {query_info.get('result_count', 0)}
  Databases Found: {', '.join(query_info.get('databases_in_results', []))}
  
  Breakdown by Database:
"""
        
        # Add per-database breakdown
        for db, count in query_info.get('database_breakdown', {}).items():
            summary += f"    • {db}: {count} entries\n"
        
        if query_info.get('sample_results'):
            summary += "\n  Sample Results (first 5):\n"
            for i, result in enumerate(query_info.get('sample_results', [])[:5], 1):
                display_text = _get_content_display_text(result)
                summary += f"    {i}. {display_text} ({result.get('database_name', 'unknown')})\n"
                summary += f"       Date: {result.get('created_time', 'N/A')[:10]}\n"
        
        try:
            with open(summary_file, 'w') as f:
                f.write(summary)
            return summary_file
        except Exception as e:
            print_text(f"⚠️  Could not save summary: {e}", style="yellow")
            return None


class ResultValidator:
    """Validate query results and determine if they match user intent."""
    
    @staticmethod
    def validate_results(intent: Dict[str, Any], results: List[Dict[str, Any]], query_mode: str = "sql") -> Tuple[bool, str]:
        """
        Check if results match the user's intent.
        Returns (is_valid, factual_observation)
        
        Note: Report facts and context, but let the AI decide how to fix it.
        
        Args:
            intent: User's search intent
            results: Query results
            query_mode: "sql" or "vector" - affects validation strategy
        """
        # Check if we have results
        if not results:
            # Vector search: 0 results is a valid outcome (nothing matched semantically).
            # Don't treat it as a validation failure — retrying won't help.
            if query_mode == "vector":
                return True, "No results matched the semantic search."

            # SQL: 0 results likely means the query needs adjustment
            goal = intent.get('goal', 'unknown goal')
            databases = intent.get('databases', [])

            # Format database list nicely (handle empty list)
            if databases:
                db_str = f"Searched databases: {', '.join(databases)}"
            else:
                db_str = "Searched: all databases"

            return False, f"Query returned 0 rows. Goal was: {goal}. {db_str}."
        
        # Check if result count is reasonable
        result_count = len(results)
        
        # Check if databases match intent
        intended_databases = set(intent.get('databases', []))
        result_databases = set(r.get('database_name') for r in results)
        
        # Normalize database names for comparison (handle both "stories" and "trass.stories" formats)
        def normalize_db_name(db_name: str) -> str:
            """Extract the nickname from qualified names like 'trass.stories' -> 'stories'"""
            if db_name is None:
                return ''
            if '.' in db_name:
                return db_name.split('.')[-1]  # Get last part after dot
            return db_name

        normalized_intended = {normalize_db_name(db) for db in intended_databases}
        normalized_results = {normalize_db_name(db) for db in result_databases}

        # Skip database-name validation for direct-data queries (e.g. Shopify)
        # where results come from standalone tables without a database_name column
        if normalized_results == {''}:
            # All results have no database_name — direct-data query, skip DB check
            pass
        elif intended_databases and not normalized_results.intersection(normalized_intended):
            return False, f"Results don't match intended databases. Got {result_databases}, expected {intended_databases}"
        
        # Check for search terms in results (if applicable)
        # NOTE: This is a soft check - if we got results, the SQL likely worked correctly
        # IMPORTANT: Skip this for vector search! Vector search finds by semantic meaning,
        # not exact word matching. Requiring exact terms defeats the purpose.
        if query_mode == "sql":
            # Filter out workspace names and common words from search term validation
            search_terms = intent.get('search_terms', [])
            # Don't validate workspace names or single letters as search terms
            search_terms = [t for t in search_terms if len(t) > 1 and t.lower() not in ['trass', 'koii']]
            
            if search_terms and result_count < 10:
                # Only flag as issue if we got very few results AND terms not visible
                # This suggests the query might be wrong
                terms_found = False
                for result in results[:10]:
                    title = result.get('title', '').lower()
                    metadata = str(result.get('metadata', '')).lower()
                    # Check all string fields
                    all_text = ' '.join(str(v).lower() for v in result.values() if v)
                    for term in search_terms:
                        if term.lower() in all_text:
                            terms_found = True
                            break
                    if terms_found:
                        break
                
                if not terms_found:
                    goal = intent.get('goal', 'unknown goal')
                    return False, f"Query returned {result_count} rows, but search terms '{', '.join(search_terms)}' not visible in samples. Goal was: {goal}."
            # If we have many results, trust the SQL even if terms not visible in sample
        
        # All checks passed
        return True, f"Results look good: {result_count} entries from {len(result_databases)} databases"
    
    @staticmethod
    def generate_result_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate a statistical summary of results."""
        if not results:
            return {
                "total_count": 0,
                "databases": [],
                "database_breakdown": {},
                "sample_results": []
            }
        
        # Group by database
        database_breakdown = {}
        databases = set()
        
        for result in results:
            db = result.get('database_name', 'unknown')
            databases.add(db)
            database_breakdown[db] = database_breakdown.get(db, 0) + 1
        
        return {
            "total_count": len(results),
            "databases": list(databases),
            "database_breakdown": database_breakdown,
            "sample_results": results[:10]  # First 10 for preview
        }


def _get_content_display_text(result: Dict[str, Any], db_path: str = None) -> str:
    """
    Get appropriate display text based on content type.
    
    Args:
        result: Result dict with page_id, database_name, content_type, etc.
        db_path: Path to the database
        
    Returns:
        Display text appropriate for the content type
    """
    if db_path is None:
        from promaia.utils.env_writer import get_db_path
        db_path = str(get_db_path())

    import sqlite3

    page_id = result.get('page_id')
    database_name = result.get('database_name', '')
    content_type = result.get('content_type', database_name)
    
    # Default to title if available
    if result.get('title') and result.get('title') != 'Untitled':
        return result.get('title')
    
    # Fetch type-specific display text
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if content_type == 'gmail' or database_name == 'gmail':
                # Get subject from gmail_content
                cursor.execute(
                    "SELECT subject FROM gmail_content WHERE page_id = ?",
                    (page_id,)
                )
                row = cursor.fetchone()
                if row and row['subject']:
                    return row['subject']
            
            elif content_type == 'discord' or database_name == 'discord':
                # Get content snippet from discord_content
                cursor.execute(
                    "SELECT content FROM discord_content WHERE page_id = ?",
                    (page_id,)
                )
                row = cursor.fetchone()
                if row and row['content']:
                    content = row['content']
                    # Return first 60 chars with ellipsis
                    return content[:60] + "..." if len(content) > 60 else content
            
            elif content_type == 'notion' or database_name in ['stories', 'yp', 'notion', 'projects', 'cms', 'epics', 'journal', 'awakenings']:
                # For Notion databases, try to get title from unified_content
                cursor.execute(
                    "SELECT title FROM unified_content WHERE page_id = ?",
                    (page_id,)
                )
                row = cursor.fetchone()
                if row and row['title']:
                    return row['title']
    
    except Exception as e:
        # If anything fails, return a safe default
        pass
    
    return 'Untitled'


def format_result_summary_for_user(summary: Dict[str, Any], intent: Dict[str, Any]) -> str:
    """Format a user-friendly summary of query results."""
    output = ""
    
    if summary['sample_results']:
        output += "\nSample Results (first 5):\n"
        for i, result in enumerate(summary['sample_results'][:5], 1):
            display_text = _get_content_display_text(result)
            date = result.get('created_time', 'N/A')[:10]
            output += f"  {i}.  {display_text} ({date})\n"
    
    return output

