"""
Schema-aware SQL generation for pattern-based natural language queries.
Replaces Vanna.ai's complex training with focused, predictable SQL generation.
"""
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from .pattern_based_nl import QueryIntent, QueryType


class SchemaAwareSQLGenerator:
    """Generate SQL queries from structured intents with full schema awareness."""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            from promaia.utils.env_writer import get_db_path
            db_path = str(get_db_path())
        self.db_path = db_path
        self._load_schema_info()
    
    def _load_schema_info(self):
        """Load schema information for intelligent SQL generation."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Get available tables
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                self.available_tables = [row[0] for row in cursor.fetchall()]
                
                # Check for data-type specific tables
                self.has_gmail_tables = any('gmail' in table for table in self.available_tables)
                self.has_notion_tables = any('notion' in table for table in self.available_tables)
                self.has_discord_tables = any('discord' in table for table in self.available_tables)
                
        except Exception as e:
            print(f"Warning: Could not load schema info: {e}")
            self.available_tables = []
            self.has_gmail_tables = False
            self.has_notion_tables = False
            self.has_discord_tables = False

    def generate_sql(self, intent: QueryIntent, query_type: QueryType) -> str:
        """
        Generate SQL based on structured intent and query type.
        
        Args:
            intent: Structured query intent from pattern processor
            query_type: Whether content search is needed (performance optimization)
            
        Returns:
            SQL query string optimized for the specific pattern
        """
        
        # Dispatch to pattern-specific generators
        if intent.pattern_type == "monthly_samples":
            return self._generate_monthly_samples_query(intent, query_type)
        
        elif intent.pattern_type == "weekly_samples":
            return self._generate_weekly_samples_query(intent, query_type)
            
        elif intent.pattern_type == "date_range":
            return self._generate_date_range_query(intent, query_type)
            
        elif intent.pattern_type == "cross_source":
            return self._generate_cross_source_query(intent, query_type)
            
        elif intent.pattern_type == "content_search":
            return self._generate_content_search_query(intent, query_type)
            
        elif intent.pattern_type == "metadata_search":
            return self._generate_metadata_search_query(intent, query_type)
            
        elif intent.pattern_type == "property_filter":
            return self._generate_property_filter_query(intent, query_type)
            
        else:
            # Fallback: simple search
            return self._generate_simple_search_query(intent, query_type)

    def _generate_monthly_samples_query(self, intent: QueryIntent, query_type: QueryType) -> str:
        """
        Generate SQL for the primary use case: monthly samples since a date.
        
        This handles: "monthly journal entries since December 2024"
        """
        start_date = intent.time_constraints["start_date"]
        sample_size = intent.time_constraints.get("sample_per_month", 25)
        
        # Base fields from unified_content
        base_fields = [
            "u.page_id", "u.title", "u.created_time", "u.last_edited_time", 
            "u.workspace", "u.database_name", "u.file_path"
        ]
        
        # Add content fields if needed
        if query_type == QueryType.CONTENT_REQUIRED:
            base_fields.append("u.metadata")
        
        # Build the complex monthly sampling logic
        # This replaces the horrible hard-coded SQL from Vanna examples
        current_date = datetime.now()
        start_dt = datetime.fromisoformat(start_date)
        
        # Generate monthly conditions dynamically
        monthly_conditions = []
        current_month = start_dt.replace(day=1)
        
        while current_month <= current_date:
            year_month = current_month.strftime("%Y-%m")
            monthly_conditions.append(f"SUBSTR(u.created_time, 1, 7) = '{year_month}'")
            
            # Move to next month
            if current_month.month == 12:
                current_month = current_month.replace(year=current_month.year + 1, month=1)
            else:
                current_month = current_month.replace(month=current_month.month + 1)
        
        # Combine conditions with OR
        monthly_condition = " OR ".join(monthly_conditions)
        
        # Build WHERE clause
        where_conditions = []
        
        # Database filters
        if intent.sources:
            db_placeholders = ", ".join(f"'{db}'" for db in intent.sources)
            where_conditions.append(f"u.database_name IN ({db_placeholders})")
        
        # Date constraint
        where_conditions.append(f"u.created_time >= '{start_date}'")
        
        # Monthly sampling condition
        if monthly_conditions:
            where_conditions.append(f"({monthly_condition})")
        
        where_clause = " AND ".join(where_conditions)
        
        # Use RANDOM() for sampling to get variety across months
        sql = f"""
        SELECT {', '.join(base_fields)}
        FROM unified_content u
        WHERE {where_clause}
        ORDER BY u.created_time DESC, RANDOM()
        LIMIT {sample_size * len(monthly_conditions)}
        """
        
        return sql.strip()

    def _generate_content_search_query(self, intent: QueryIntent, query_type: QueryType) -> str:
        """Generate SQL for content searches with quoted text."""
        search_term = intent.search_terms[0] if intent.search_terms else ""
        
        base_fields = ["u.page_id", "u.title", "u.created_time", "u.workspace", "u.database_name"]
        joins = []
        where_conditions = []
        
        # Add database filters
        if intent.sources:
            db_placeholders = ", ".join(f"'{db}'" for db in intent.sources)
            where_conditions.append(f"u.database_name IN ({db_placeholders})")
        
        # Add content search logic based on data type
        content_search_conditions = []
        
        # Gmail content search
        if not intent.sources or "gmail" in intent.sources:
            if self.has_gmail_tables and "gmail_content" in self.available_tables:
                joins.append("LEFT JOIN gmail_content g ON u.page_id = g.page_id")
                base_fields.extend(["g.sender_email", "g.subject", "g.message_content"])
                content_search_conditions.append(f"g.message_content LIKE '%{search_term}%'")
                content_search_conditions.append(f"g.subject LIKE '%{search_term}%'")
        
        # Notion content search
        if not intent.sources or "notion" in intent.sources:
            if self.has_notion_tables and "notion_page_content" in self.available_tables:
                joins.append("LEFT JOIN notion_page_content n ON u.page_id = n.page_id")
                content_search_conditions.append(f"n.content LIKE '%{search_term}%'")
        
        # Discord content search
        if not intent.sources or "discord" in intent.sources:
            if self.has_discord_tables and "discord_messages" in self.available_tables:
                joins.append("LEFT JOIN discord_messages d ON u.page_id = d.page_id")
                base_fields.extend(["d.author_name", "d.message_content"])
                content_search_conditions.append(f"d.message_content LIKE '%{search_term}%'")
        
        # Fallback to title and metadata search if no specific content tables
        if not content_search_conditions:
            content_search_conditions.append(f"u.title LIKE '%{search_term}%'")
            content_search_conditions.append(f"u.metadata LIKE '%{search_term}%'")
        
        # Combine content search conditions with OR
        if content_search_conditions:
            where_conditions.append(f"({' OR '.join(content_search_conditions)})")
        
        # Build final query
        join_clause = " ".join(joins)
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        sql = f"""
        SELECT DISTINCT {', '.join(base_fields)}
        FROM unified_content u
        {join_clause}
        WHERE {where_clause}
        ORDER BY u.last_edited_time DESC
        LIMIT 100
        """
        
        return sql.strip()

    def _generate_metadata_search_query(self, intent: QueryIntent, query_type: QueryType) -> str:
        """Generate SQL for metadata-only searches like 'emails from John'."""
        
        # Performance-optimized: exclude heavy content fields
        base_fields = ["u.page_id", "u.title", "u.created_time", "u.workspace", "u.database_name"]
        joins = []
        where_conditions = []
        
        # Add database filters
        if intent.sources:
            db_placeholders = ", ".join(f"'{db}'" for db in intent.sources)
            where_conditions.append(f"u.database_name IN ({db_placeholders})")
        
        # Add person filtering for Gmail
        if intent.person_filter and (not intent.sources or "gmail" in intent.sources):
            if self.has_gmail_tables:
                joins.append("JOIN gmail_content g ON u.page_id = g.page_id")
                base_fields.extend(["g.sender_name", "g.sender_email", "g.subject"])
                # Note: deliberately exclude g.body_text for performance
                where_conditions.append(f"(g.sender_name LIKE '%{intent.person_filter}%' OR g.sender_email LIKE '%{intent.person_filter}%')")
        
        # Build query
        join_clause = " ".join(joins)
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        sql = f"""
        SELECT DISTINCT {', '.join(base_fields)}
        FROM unified_content u
        {join_clause}
        WHERE {where_clause}
        ORDER BY u.created_time DESC
        LIMIT 100
        """
        
        return sql.strip()

    def _generate_property_filter_query(self, intent: QueryIntent, query_type: QueryType) -> str:
        """Generate SQL for property filtering like 'notion stories with status archived'."""
        
        base_fields = ["u.page_id", "u.title", "u.created_time", "u.workspace", "u.database_name"]
        joins = []
        where_conditions = []
        
        # Add database filters
        if intent.sources:
            db_placeholders = ", ".join(f"'{db}'" for db in intent.sources)
            where_conditions.append(f"u.database_name IN ({db_placeholders})")
        
        # Add property filtering for Notion
        if intent.property_filters and self.has_notion_tables:
            joins.append("JOIN notion_properties np ON u.page_id = np.page_id")
            joins.append("JOIN notion_select_values nsv ON np.property_id = nsv.property_id")
            base_fields.extend(["np.property_name", "nsv.select_value"])
            
            for prop_name, prop_value in intent.property_filters.items():
                where_conditions.append(f"np.property_name LIKE '%{prop_name}%'")
                where_conditions.append(f"nsv.select_value LIKE '%{prop_value}%'")
        
        # Build query
        join_clause = " ".join(joins)
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        sql = f"""
        SELECT DISTINCT {', '.join(base_fields)}
        FROM unified_content u
        {join_clause}
        WHERE {where_clause}
        ORDER BY u.last_edited_time DESC
        LIMIT 100
        """
        
        return sql.strip()

    def _generate_cross_source_query(self, intent: QueryIntent, query_type: QueryType) -> str:
        """Generate SQL for cross-database queries."""
        search_term = intent.search_terms[0] if intent.search_terms else ""
        
        # Union approach for cross-source queries
        subqueries = []
        
        # Gmail subquery
        if "gmail" in intent.sources and self.has_gmail_tables:
            gmail_fields = ["u.page_id", "u.title", "u.created_time", "u.workspace", 
                          "u.database_name", "g.sender_name", "g.subject"]
            if query_type == QueryType.CONTENT_REQUIRED:
                gmail_fields.append("g.message_content")
                
            gmail_query = f"""
            SELECT {', '.join(gmail_fields)}, 'gmail' as source_type
            FROM unified_content u
            JOIN gmail_content g ON u.page_id = g.page_id
            WHERE u.database_name = 'gmail'
            AND (g.subject LIKE '%{search_term}%' OR g.message_content LIKE '%{search_term}%')
            """
            subqueries.append(gmail_query)
        
        # Journal/Notion subquery
        if any(db in intent.sources for db in ["journal", "notion"]):
            journal_query = f"""
            SELECT u.page_id, u.title, u.created_time, u.workspace, 
                   u.database_name, '' as sender_name, u.title as subject,
                   'journal' as source_type
            FROM unified_content u
            WHERE u.database_name IN ('journal', 'notion')
            AND u.title LIKE '%{search_term}%'
            """
            subqueries.append(journal_query)
        
        # Combine with UNION
        if subqueries:
            sql = " UNION ALL ".join(subqueries) + " ORDER BY created_time DESC LIMIT 100"
        else:
            # Fallback to simple search
            sql = f"""
            SELECT u.page_id, u.title, u.created_time, u.workspace, u.database_name
            FROM unified_content u
            WHERE u.title LIKE '%{search_term}%'
            ORDER BY u.created_time DESC
            LIMIT 100
            """
        
        return sql.strip()

    def _generate_simple_search_query(self, intent: QueryIntent, query_type: QueryType) -> str:
        """Fallback: simple search query."""
        search_term = intent.search_terms[0] if intent.search_terms else ""
        
        base_fields = ["u.page_id", "u.title", "u.created_time", "u.workspace", "u.database_name"]
        where_conditions = []
        
        if intent.sources:
            db_placeholders = ", ".join(f"'{db}'" for db in intent.sources)
            where_conditions.append(f"u.database_name IN ({db_placeholders})")
        
        if search_term:
            where_conditions.append(f"u.title LIKE '%{search_term}%'")
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        sql = f"""
        SELECT {', '.join(base_fields)}
        FROM unified_content u
        WHERE {where_clause}
        ORDER BY u.created_time DESC
        LIMIT 50
        """
        
        return sql.strip()

    def _generate_date_range_query(self, intent: QueryIntent, query_type: QueryType) -> str:
        """Generate SQL for date range queries like 'last 2 weeks'."""
        start_date = intent.time_constraints["start_date"]
        
        base_fields = ["u.page_id", "u.title", "u.created_time", "u.workspace", "u.database_name"]
        where_conditions = []
        
        if intent.sources:
            db_placeholders = ", ".join(f"'{db}'" for db in intent.sources)
            where_conditions.append(f"u.database_name IN ({db_placeholders})")
        
        where_conditions.append(f"u.created_time >= '{start_date}'")
        
        where_clause = " AND ".join(where_conditions)
        
        sql = f"""
        SELECT {', '.join(base_fields)}
        FROM unified_content u
        WHERE {where_clause}
        ORDER BY u.created_time DESC
        LIMIT 100
        """
        
        return sql.strip()

    def _generate_weekly_samples_query(self, intent: QueryIntent, query_type: QueryType) -> str:
        """Generate SQL for weekly sampling."""
        sample_per_week = intent.time_constraints.get("sample_per_week", 8)
        
        base_fields = ["u.page_id", "u.title", "u.created_time", "u.workspace", "u.database_name"]
        where_conditions = []
        
        if intent.sources:
            db_placeholders = ", ".join(f"'{db}'" for db in intent.sources)
            where_conditions.append(f"u.database_name IN ({db_placeholders})")
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        # Use date grouping for weekly samples
        sql = f"""
        SELECT {', '.join(base_fields)}
        FROM unified_content u
        WHERE {where_clause}
        ORDER BY strftime('%Y-%W', u.created_time), RANDOM()
        LIMIT {sample_per_week * 12}
        """
        
        return sql.strip()