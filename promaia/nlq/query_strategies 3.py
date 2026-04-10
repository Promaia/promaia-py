"""
Query Strategy Pattern Implementation

Separates SQL and Vector query generation/execution logic
while keeping the shared orchestration pipeline clean.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
import sqlite3
import json

from promaia.utils.display import print_text


class QueryStrategy(ABC):
    """Base strategy interface for query generation and execution."""
    
    @abstractmethod
    def initialize(self, verbose: bool = False, debug: bool = False) -> str:
        """
        Initialize strategy-specific components.
        
        Returns:
            Status message about initialization
        """
        pass
    
    @abstractmethod
    def generate_query(
        self,
        intent: Dict[str, Any],
        schema: Dict[str, Any],
        retry_attempt: int,
        llm,
        workspace_context: str,
        schema_summary: str,
        validation_feedback: str,
        verbose: bool,
        debug: bool
    ) -> Optional[Any]:
        """
        Generate query from intent.
        
        Returns:
            SQL string for SQL mode, dict for vector mode
        """
        pass
    
    @abstractmethod
    def display_generated_query(self, query: Any, verbose: bool) -> None:
        """Display the generated query to user."""
        pass
    
    @abstractmethod
    def execute_query(
        self,
        query: Any,
        verbose: bool,
        debug: bool,
        n_results: Optional[int] = None,
        min_similarity: Optional[float] = None
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """
        Execute the query.
        
        Returns:
            (results, error_message)
        """
        pass
    
    @abstractmethod
    def should_save_pattern(self) -> bool:
        """Whether this strategy supports pattern learning."""
        pass
    
    @abstractmethod
    def save_pattern(self, pattern: Dict[str, Any]) -> None:
        """Save a successful query pattern (if supported)."""
        pass


class SQLQueryStrategy(QueryStrategy):
    """Strategy for SQL query generation and execution."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.learning_system = None
    
    def initialize(self, verbose: bool = False, debug: bool = False) -> str:
        """Initialize SQL-specific components."""
        from promaia.nlq.nl_utilities import QueryLearningSystem
        self.learning_system = QueryLearningSystem()
        return "✅ Initialized agentic NL processor in SQL mode"
    
    def generate_query(
        self,
        intent: Dict[str, Any],
        schema: Dict[str, Any],
        retry_attempt: int,
        llm,
        workspace_context: str,
        schema_summary: str,
        validation_feedback: str,
        verbose: bool,
        debug: bool
    ) -> Optional[str]:
        """Generate SQL query using dynamic schema + learned patterns."""
        
        # Get learned patterns
        learned_patterns = self.learning_system.get_patterns_for_prompt()
        
        # Build concise prompt
        intent_line = f"Goal: {intent['goal']}"
        if intent.get('search_terms'):
            intent_line += f" | Terms: {', '.join(intent.get('search_terms', []))}"
        
        # Handle date filter display
        date_filter = intent.get('date_filter', {})
        if date_filter.get('description') and date_filter.get('description') != 'none':
            intent_line += f" | Date: {date_filter.get('description')}"
        
        # Extract workspace and normalize database names from qualified names
        target_workspaces = set()
        target_dbs = []
        cross_workspace_dbs = []  # Databases with workspace_scope="all"

        for db_name in intent['databases']:
            if '.' in db_name:
                workspace_part, db_nickname = db_name.rsplit('.', 1)
                target_workspaces.add(workspace_part)
                target_dbs.append(db_nickname)
            else:
                target_dbs.append(db_name)

        # Check for cross-workspace databases (workspace_scope="all")
        try:
            from promaia.config.databases import get_database_config
            for db_name in target_dbs:
                db_config = get_database_config(db_name)
                if db_config and getattr(db_config, 'workspace_scope', 'single') == 'all':
                    cross_workspace_dbs.append(db_name)
        except Exception as e:
            # If we can't load configs, continue without cross-workspace handling
            if debug:
                print_text(f"   Warning: Could not check workspace_scope: {e}", style="yellow")

        # Build workspace filter clause
        workspace_filter = ""
        if target_workspaces:
            workspace_list = ', '.join(f"'{w}'" for w in sorted(target_workspaces))
            if cross_workspace_dbs:
                # Include cross-workspace databases regardless of workspace filter
                cross_workspace_list = ', '.join(f"'{db}'" for db in cross_workspace_dbs)
                workspace_filter = f"\nWORKSPACE FILTER: Must filter WHERE (u.workspace IN ({workspace_list}) OR u.database_name IN ({cross_workspace_list}))"
            else:
                workspace_filter = f"\nWORKSPACE FILTER: Must filter WHERE u.workspace IN ({workspace_list})"
        
        prompt = f"""{workspace_context}

{schema_summary}

{learned_patterns}

{validation_feedback}

QUERY: {intent_line}
TARGET DATABASES: {', '.join(target_dbs)}{workspace_filter}

IMPORTANT: The database_name column stores ONLY the nickname (e.g., "stories", not "workspace.stories")

Return SQLite query that:
- SELECTs: u.page_id, u.workspace, u.database_name, u.title, u.created_time (+ any other needed fields)
- IMPORTANT: Always include u.workspace in SELECT to distinguish databases across workspaces
- JOINs workspace-specific tables (e.g., notion_WORKSPACE_DATABASE) to access Notion properties
  - These tables have columns like: date, status, _epics, assignee, etc. (see sample data above)
  - IMPORTANT: Relation columns (e.g., _projects, _epics) store UUIDs like ["abc-123-def"], not text
    - To filter by relation text, you need to know the UUID of the related page
    - Check sample data to see UUID format in relation columns
  - Join pattern: JOIN notion_WORKSPACE_DATABASE n ON u.page_id = n.page_id
  - Example: JOIN notion_acme_stories n ON u.page_id = n.page_id
- Also JOINs specialized tables (gmail_content, generic_content) if needed
- Uses LIKE '%term%' on ALL text-heavy fields (check sample data above)
- Filters database_name using ONLY the nickname (no workspace prefix)
- If workspace filter specified above include it in your query like this: AND u.workspace IN (...)
- Applies date filters using the rules below - CRITICAL: distinguish between content dates vs sync dates
- LIMIT 1200

DATE FILTER RULES - CRITICAL DISTINCTION:

**Which date column to use:**
- For queries about CONTENT DATES (sprint dates, due dates, story dates):
  → Use the direct date column from workspace-specific table: n.date, n.due_date, etc.
  → Look at the sample rows from notion_WORKSPACE_DATABASE tables to see which columns exist
  → Example: "stories in current sprint" → use n.date from notion_acme_stories
  
- For queries about SYNC/CREATION dates (when added to database):
  → Use u.created_time
  → Example: "pages created last week", "recently synced content", "new entries"

**How to apply date filters:**

For CONTENT dates (sprints, deadlines, business dates):
- First JOIN the workspace-specific table to access properties
- Then use the direct date column: n.date, n.due_date, n.publish_date, etc.
- Check the sample rows above to see which date columns exist for each database
- If days_back provided: "AND n.date >= date('now', '-N days')"
- If start_date/end_date provided:
  - start: "AND n.date >= 'YYYY-MM-DD'" or "AND n.date >= date('now', '-N days')"
  - end: "AND n.date <= 'YYYY-MM-DD'"

For SYNC dates (when content was added/created):
- Use: u.created_time (no need to join workspace table)
- If days_back provided: "AND u.created_time >= date('now', '-N days')"
- If start_date/end_date provided:
  - start: "AND u.created_time >= 'YYYY-MM-DD'"
  - end: "AND u.created_time <= 'YYYY-MM-DD'"

NEVER combine days_back with start_date/end_date - use one or the other
For date ranges, always use >= for start and <= for end

DATE FILTER EXAMPLES:

CONTENT DATE FILTERING (use workspace-specific table date column):
- "stories in current sprint between X and Y" →
  SQL: 
  ```
  SELECT u.page_id, u.workspace, u.database_name, u.title, n.date, n.status
  FROM unified_content u
  JOIN notion_acme_stories n ON u.page_id = n.page_id
  WHERE u.database_name = 'stories'
    AND n.date >= date('now', '-7 days')
    AND n.date <= '2026-04-30'
  ```

- "stories with epic 'angl' due in april" →
  SQL:
  ```
  SELECT u.page_id, u.workspace, u.database_name, u.title, n.date, n._epics
  FROM unified_content u
  JOIN notion_acme_stories n ON u.page_id = n.page_id
  WHERE u.database_name = 'stories'
    AND n._epics LIKE '%angl%'
    AND n.date <= '2026-04-30'
  ```

SYNC DATE FILTERING (use created_time, no workspace join needed):
- "pages created last 7 days" → 
  SQL: "SELECT * FROM unified_content u WHERE u.created_time >= date('now', '-7 days')"
- "recently synced stories" → 
  SQL: "SELECT * FROM unified_content u WHERE u.database_name = 'stories' AND u.created_time >= date('now', '-7 days')"

DEFAULT RULE: If the query mentions sprints, deadlines, "in X period", story properties, or business date ranges → use CONTENT dates from workspace table. If it mentions "created", "synced", "added" → use created_time.

SHOPIFY TABLES — DIRECT QUERY (do NOT use unified_content for Shopify data):

If the query involves Shopify data (orders, products, inventory, sales, revenue, fulfillment,
stock levels, re-ordering), query these tables DIRECTLY — they are NOT in unified_content:

- shopify_orders: order_number, name, financial_status, fulfillment_status, total_price,
  subtotal_price, total_tax, total_discounts, currency, line_items (JSON), discount_codes (JSON),
  shipping_lines (JSON), refunds (JSON), note, tags, cancelled_at, closed_at, processed_at,
  order_created_at, order_updated_at
- shopify_products: title, handle, vendor, product_type, status, tags, variants (JSON with id,
  title, sku, price, compare_at_price, inventory_quantity, inventory_item_id), options (JSON),
  images (JSON), body_html, product_created_at, product_updated_at
- shopify_inventory_snapshots: variant_id, product_id, sku, product_title, variant_title,
  inventory_item_id, available (integer), recorded_at (append-only history — multiple rows per variant)

Shopify query patterns:
- Sales/revenue: SELECT from shopify_orders, filter by order_created_at, financial_status IN ('paid','partially_refunded')
- Unfulfilled orders: WHERE fulfillment_status IS NULL OR fulfillment_status = 'partial'
- Current inventory: subquery MAX(recorded_at) per variant_id from shopify_inventory_snapshots
- Low stock: current inventory WHERE available < N
- Line item details: use json_each(line_items) to expand order items
- Prices are TEXT — cast with CAST(total_price AS REAL) for aggregation
- Inventory snapshots are append-only: latest row per variant = current level

If the query does NOT involve Shopify data, use unified_content as described above.

SQL only (no markdown, no triple backticks):"""
        
        if debug:
            print_text(f"\n📤 SQL Generation Prompt:", style="cyan")
            print_text(f"   Intent: {intent['goal']}", style="dim")
            print_text(f"   Databases (original): {', '.join(intent['databases'])}", style="dim")
            print_text(f"   Databases (normalized for SQL): {', '.join(target_dbs)}", style="dim")
            if target_workspaces:
                print_text(f"   Workspaces (extracted): {', '.join(sorted(target_workspaces))}", style="yellow")
            print_text(f"   Search terms: {', '.join(intent.get('search_terms', []))}", style="dim")
            print_text(f"   Using {len(self.learning_system.load_successful_patterns())} learned patterns", style="dim")
        
        try:
            # Log AI generation only in verbose mode
            if verbose:
                if retry_attempt == 0:
                    print_text(f"\n💬 Asking AI to generate SQL for: {intent['goal']}", style="cyan")
                else:
                    print_text(f"\n💬 Asking AI to retry SQL generation with feedback:", style="yellow")
                    print_text(f"   Previous feedback: {intent.get('_validation_feedback', 'N/A')}", style="dim")
            
            response = llm.invoke([{"role": "user", "content": prompt}])
            sql = response.content.strip()
            
            if debug:
                print_text(f"\n📥 AI Response (raw):", style="cyan")
                print_text(sql if len(sql) < 400 else sql[:400] + "...", style="dim")
            
            # Clean SQL
            if '```' in sql:
                import re
                match = re.search(r'```(?:sql)?\s*(.*?)\s*```', sql, re.DOTALL)
                if match:
                    sql = match.group(1).strip()
            
            return sql
        
        except Exception as e:
            print_text(f"❌ SQL generation failed: {e}", style="red")
            return None
    
    def display_generated_query(self, query: str, verbose: bool) -> None:
        """Display generated SQL query."""
        if verbose:
            print_text(f"\n📝 Generated SQL:", style="cyan")
            # Display full SQL with proper line formatting
            for line in query.split('\n'):
                print_text(f"   {line}", style="dim")
    
    def execute_query(
        self,
        query: str,
        verbose: bool,
        debug: bool,
        n_results: Optional[int] = None,
        min_similarity: Optional[float] = None
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """Execute SQL query. (n_results and min_similarity are ignored for SQL queries)"""
        if debug:
            print_text("\n" + "=" * 70, style="dim")
            print_text("⚡ CHAIN OF THOUGHT: SQL Execution", style="bold yellow")
            print_text("=" * 70, style="dim")
            print_text(f"\n🔍 Executing query against: {self.db_path}", style="cyan")
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query)
                results = [dict(row) for row in cursor.fetchall()]
                
                if debug:
                    print_text(f"\n✅ Execution successful", style="green")
                    print_text(f"   Returned {len(results)} rows", style="dim")
                    if results:
                        print_text(f"   Sample row keys: {list(results[0].keys())[:5]}", style="dim")
                
                if verbose:
                    print_text(f"✅ Execution successful: {len(results)} rows returned", style="green" if results else "yellow")
                    if results and len(results) > 0:
                        sample = results[0]
                        print_text(f"   Sample columns: {list(sample.keys())[:6]}", style="dim")
                
                return results, None
        
        except sqlite3.OperationalError as e:
            error_msg = f"SQL Error: {str(e)}"
            if debug:
                print_text(f"\n❌ SQL execution error: {error_msg}", style="red")
            print_text(f"❌ {error_msg}", style="red")
            return None, error_msg
        
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            if debug:
                print_text(f"\n❌ {error_msg}", style="red")
            print_text(f"❌ {error_msg}", style="red")
            return None, error_msg
    
    def should_save_pattern(self) -> bool:
        """SQL mode supports pattern learning."""
        return True
    
    def save_pattern(self, pattern: Dict[str, Any]) -> None:
        """Save successful SQL pattern."""
        self.learning_system.save_successful_pattern(pattern)


class VectorQueryStrategy(QueryStrategy):
    """Strategy for vector search query generation and execution."""
    
    def __init__(self):
        self.vector_db = None
    
    def initialize(self, verbose: bool = False, debug: bool = False) -> str:
        """Initialize vector search components."""
        from promaia.storage.vector_db import VectorDBManager
        self.vector_db = VectorDBManager()
        return "✅ Initialized agentic NL processor in VECTOR mode"
    
    def generate_query(
        self,
        intent: Dict[str, Any],
        schema: Dict[str, Any],
        retry_attempt: int,
        llm,
        workspace_context: str,
        schema_summary: str,
        validation_feedback: str,
        verbose: bool,
        debug: bool
    ) -> Optional[Dict[str, Any]]:
        """Generate vector search parameters from intent."""
        if debug:
            print_text("\n" + "=" * 70, style="dim")
            print_text(f"⚙️  CHAIN OF THOUGHT: Vector Query Generation (Attempt {retry_attempt + 1})", style="bold yellow")
            print_text("=" * 70, style="dim")
            print_text(f"\n📤 Vector Search Parameter Extraction:", style="cyan")
            print_text(f"   Intent: {intent['goal']}", style="dim")
            print_text(f"   Databases (from intent): {', '.join(intent.get('databases', []))}", style="dim")
            print_text(f"   Search terms: {', '.join(intent.get('search_terms', []))}", style="dim")
            print_text(f"   Will normalize qualified names (e.g., 'workspace.gmail' -> workspace filter + database='gmail')", style="yellow")
        
        prompt = f"""Extract semantic search parameters from this intent:

{workspace_context}

Intent:
- Goal: {intent['goal']}
- Databases: {', '.join(intent.get('databases', []))}
- Search Terms: {', '.join(intent.get('search_terms', []))}
- Date Filter: {intent.get('date_filter', {}).get('description', 'none')}

Return JSON with:
{{
    "search_text": "core semantic query for embedding (just the content to search, not metadata)",
    "explanation": "brief reasoning for search text choice"
}}

Example:
Intent: "find stories about international launch in acme workspace"
Output: {{"search_text": "international launch stories", "explanation": "removed workspace metadata"}}

Use the workspace configuration above to understand database context.

Return ONLY the JSON object:"""
        
        try:
            if verbose:
                if retry_attempt == 0:
                    print_text(f"\n💬 Asking AI to extract semantic search parameters", style="cyan")
                else:
                    print_text(f"\n💬 Retrying search text extraction", style="yellow")
            
            response = llm.invoke([{"role": "user", "content": prompt}])
            content = response.content.strip()
            
            if debug:
                print_text(f"\n📥 LLM Response:", style="cyan")
                print_text(content[:200] + "..." if len(content) > 200 else content, style="dim")
            
            # Clean JSON
            if content.startswith('```json'):
                content = content[7:-3].strip()
            elif content.startswith('```'):
                content = content[3:-3].strip()
            
            extracted = json.loads(content)
            search_text = extracted.get('search_text', ' '.join(intent.get('search_terms', [])))
            
            # Build metadata filters for ChromaDB
            # IMPORTANT: Normalize qualified database names (e.g., "workspace.gmail" -> "gmail" + workspace filter)
            
            # Extract workspace and normalize database names from qualified names
            # (Same logic as SQLQueryStrategy for consistency)
            target_workspaces = set()
            target_dbs = []
            cross_workspace_dbs = []  # Databases with workspace_scope="all"

            for db_name in intent.get('databases', []):
                if '.' in db_name:
                    # Qualified name: extract workspace and db nickname
                    workspace_part, db_nickname = db_name.rsplit('.', 1)
                    target_workspaces.add(workspace_part)
                    target_dbs.append(db_nickname)
                else:
                    # Simple name: just the database nickname
                    target_dbs.append(db_name)

            # Check for cross-workspace databases (workspace_scope="all")
            try:
                from promaia.config.databases import get_database_config
                for db_name in target_dbs:
                    db_config = get_database_config(db_name)
                    if db_config and getattr(db_config, 'workspace_scope', 'single') == 'all':
                        cross_workspace_dbs.append(db_name)
            except Exception as e:
                # If we can't load configs, continue without cross-workspace handling
                if debug:
                    print_text(f"   Warning: Could not check workspace_scope: {e}", style="yellow")

            # Build ChromaDB filters using $and/$or operators for cross-workspace support
            # ChromaDB requires: {"$and": [condition1, condition2, ...]} for multiple filters
            filter_conditions = []

            # Add workspace filter with cross-workspace database exception
            if target_workspaces:
                if cross_workspace_dbs:
                    # Use $or to include both workspace-specific AND cross-workspace content
                    workspace_condition = {
                        "$or": [
                            {"workspace": {"$in": list(target_workspaces)} if len(target_workspaces) > 1 else list(target_workspaces)[0]},
                            {"database_name": {"$in": cross_workspace_dbs} if len(cross_workspace_dbs) > 1 else cross_workspace_dbs[0]}
                        ]
                    }
                    filter_conditions.append(workspace_condition)
                else:
                    # Standard workspace filtering
                    if len(target_workspaces) == 1:
                        filter_conditions.append({"workspace": list(target_workspaces)[0]})
                    else:
                        filter_conditions.append({"workspace": {"$in": list(target_workspaces)}})
            
            # Add database filter (using normalized nicknames)
            if target_dbs:
                if len(target_dbs) == 1:
                    filter_conditions.append({"database_name": target_dbs[0]})
                else:
                    filter_conditions.append({"database_name": {"$in": target_dbs}})
            
            # Construct final filter based on number of conditions
            filters = None
            if len(filter_conditions) == 0:
                filters = None
            elif len(filter_conditions) == 1:
                filters = filter_conditions[0]
            else:
                # Multiple conditions require $and operator
                filters = {"$and": filter_conditions}
            
            # Extract property constraints from intent
            property_constraints = intent.get('property_constraints', {})

            query_params = {
                'search_text': search_text,
                'filters': filters if filters else None,
                'property_constraints': property_constraints
            }

            if debug:
                print_text(f"\n📝 Vector Query Parameters:", style="cyan")
                print_text(f"   Search text: {search_text}", style="dim")
                print_text(f"   Databases (original): {', '.join(intent.get('databases', []))}", style="dim")
                if target_dbs:
                    print_text(f"   Databases (normalized): {', '.join(target_dbs)}", style="dim")
                if target_workspaces:
                    print_text(f"   Workspaces (extracted): {', '.join(sorted(target_workspaces))}", style="yellow")
                print_text(f"   Final filters: {filters}", style="dim")
                if property_constraints:
                    print_text(f"   Property constraints: {property_constraints}", style="yellow")

            return query_params
        
        except Exception as e:
            print_text(f"❌ Vector query generation failed: {e}", style="red")
            return None
    
    def display_generated_query(self, query: Dict[str, Any], verbose: bool) -> None:
        """Display generated vector search parameters."""
        if verbose:
            print_text(f"\n📝 Vector Search Parameters:", style="cyan")
            search_text = query.get('search_text', 'N/A')
            filters = query.get('filters')
            
            # Display search text prominently
            print_text(f"Search Text: \"{search_text}\"", style="white")
            
            # Display filters if present
            if filters:
                print_text(f"\nMetadata Filters:", style="white")
                
                # Handle $and structure
                if '$and' in filters:
                    for condition in filters['$and']:
                        for key, value in condition.items():
                            if isinstance(value, dict) and '$in' in value:
                                print_text(f"  {key} IN ({', '.join(value['$in'])})", style="dim")
                            else:
                                print_text(f"  {key} = {value}", style="dim")
                else:
                    # Single condition
                    for key, value in filters.items():
                        if isinstance(value, dict) and '$in' in value:
                            print_text(f"  {key} IN ({', '.join(value['$in'])})", style="dim")
                        else:
                            print_text(f"  {key} = {value}", style="dim")
            else:
                print_text(f"Metadata Filters: None (searching all databases)", style="dim")
    
    def execute_query(
        self,
        query: Dict[str, Any],
        verbose: bool,
        debug: bool,
        n_results: Optional[int] = None,
        min_similarity: Optional[float] = None
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """
        Execute vector search with property constraint routing.

        Routes queries based on property constraints:
        - Semantic properties: Search property embeddings
        - Filter properties: Apply metadata filters
        - No constraints: Regular vector search
        """
        if debug:
            print_text("\n" + "=" * 70, style="dim")
            print_text("⚡ CHAIN OF THOUGHT: Vector Search Execution", style="bold yellow")
            print_text("=" * 70, style="dim")
            print_text(f"\n🔍 Searching with: {query.get('search_text')}", style="cyan")

        try:
            # Get config for defaults
            from promaia.utils.env_writer import get_config_path
            config_path = get_config_path()
            with open(config_path, 'r') as f:
                config = json.load(f)
            vector_config = config.get('global', {}).get('vector_search', {})

            # Use passed parameters, fall back to config defaults
            n_results = n_results if n_results is not None else vector_config.get('default_n_results', 20)
            min_similarity = min_similarity if min_similarity is not None else vector_config.get('default_similarity_threshold', 0.2)

            if verbose:
                print_text(f"\nSearch Configuration:", style="white")
                print_text(f"  Max results: {n_results}", style="dim")
                print_text(f"  Min similarity threshold: {min_similarity}", style="dim")

            # Check for property constraints
            property_constraints = query.get('property_constraints', {})

            if property_constraints:
                # PROPERTY-AWARE SEARCH PATH
                if debug or verbose:
                    print_text(f"\n🎯 Property-aware search with {len(property_constraints)} constraints", style="cyan")

                # Separate semantic vs filter properties
                semantic_properties = {}
                filter_properties = {}

                for prop_name, constraint in property_constraints.items():
                    if constraint.get('type') == 'semantic':
                        semantic_properties[prop_name] = constraint
                    else:  # filter
                        filter_properties[prop_name] = constraint

                # Start with all page_ids from base content search (if we have base search text)
                # Skip content search display when we have semantic property constraints
                # (property search is more specific and content search usually returns 0)
                content_page_ids = set()

                if query.get('search_text') and not semantic_properties:
                    # Only show/perform content search if we don't have semantic properties
                    # With semantic properties, we rely on property search alone
                    if verbose:
                        print_text(f"\n   📚 Searching content collection", style="cyan")
                        print_text(f"      Query: \"{query['search_text']}\"", style="dim")
                        if query.get('filters'):
                            print_text(f"      Filters: {query.get('filters')}", style="dim")

                    # Base content search
                    content_results = self.vector_db.search(
                        query_text=query['search_text'],
                        filters=query.get('filters'),
                        n_results=n_results,
                        min_similarity=min_similarity
                    )
                    content_page_ids = {
                        result['metadata'].get('page_id', result['page_id'].rsplit('_chunk_', 1)[0] if '_chunk_' in result['page_id'] else result['page_id'])
                        for result in content_results
                    }

                    if verbose:
                        print_text(f"      → Found {len(content_page_ids)} pages", style="green" if content_page_ids else "yellow")

                # Search semantic properties
                for prop_name, constraint in semantic_properties.items():
                    prop_value = constraint.get('value', '')
                    if verbose:
                        print_text(f"\n   🏷️  Searching property collection: '{prop_name}'", style="cyan")
                        print_text(f"      Query: \"{prop_value}\"", style="dim")
                        if query.get('filters'):
                            print_text(f"      Filters: {query.get('filters')}", style="dim")

                    prop_results = self.vector_db.search_property(
                        property_name=prop_name,
                        query_text=prop_value,
                        filters=query.get('filters'),
                        n_results=n_results,
                        min_similarity=min_similarity
                    )

                    prop_page_ids = {r['page_id'] for r in prop_results}

                    if verbose:
                        print_text(f"      → Found {len(prop_page_ids)} pages with matching '{prop_name}'", style="green" if prop_page_ids else "yellow")

                    # Intersect with existing results (or skip if property not found)
                    before_count = len(content_page_ids)
                    if content_page_ids and prop_page_ids:
                        content_page_ids = content_page_ids.intersection(prop_page_ids)
                        if verbose and before_count > 0:
                            print_text(f"      → After intersection: {len(content_page_ids)} pages", style="dim")
                    elif prop_page_ids:
                        content_page_ids = prop_page_ids
                    # If prop_page_ids is empty, keep content_page_ids as-is (don't intersect with empty set)

                # Apply filter properties
                for prop_name, constraint in filter_properties.items():
                    if verbose:
                        print_text(f"   🔍 Filter on '{prop_name}': {constraint.get('value')}", style="cyan")

                    filter_page_ids = self._filter_property(
                        property_name=prop_name,
                        constraint=constraint,
                        filters=query.get('filters'),
                        debug=debug
                    )

                    if debug:
                        print_text(f"      Found {len(filter_page_ids)} pages", style="dim")

                    # Intersect
                    if filter_page_ids:
                        if content_page_ids:
                            content_page_ids = content_page_ids.intersection(set(filter_page_ids))
                        else:
                            content_page_ids = set(filter_page_ids)

                # Load results by page_ids
                if content_page_ids:
                    results = self._load_results_by_page_ids(
                        page_ids=list(content_page_ids)[:n_results],  # Limit to n_results
                        verbose=verbose,
                        debug=debug
                    )

                    if verbose:
                        print_text(f"✅ Property search returned {len(results)} results", style="green")

                    return results, None
                else:
                    if verbose:
                        print_text(f"⚠️  No results matched all property constraints", style="yellow")
                    return [], None

            else:
                # STANDARD VECTOR SEARCH PATH (no property constraints)
                if debug:
                    print_text(f"   Using standard vector search (no property constraints)", style="dim")

                # Execute vector search
                search_results = self.vector_db.search(
                    query_text=query['search_text'],
                    filters=query.get('filters'),
                    n_results=n_results,
                    min_similarity=min_similarity
                )

                if debug:
                    print_text(f"\n✅ Search successful", style="green")
                    print_text(f"   Returned {len(search_results)} results above {min_similarity} similarity", style="dim")
                    if search_results:
                        print_text(f"   Top score: {search_results[0].get('similarity_score', 0):.3f}", style="dim")

                if verbose:
                    result_color = "green" if search_results else "yellow"
                    print_text(f"✅ Execution successful: {len(search_results)} results returned", style=result_color)

                    if search_results:
                        # Show similarity score range
                        top_score = search_results[0].get('similarity_score', 0)
                        bottom_score = search_results[-1].get('similarity_score', 0)
                        print_text(f"   Similarity range: {bottom_score:.3f} - {top_score:.3f}", style="dim")

                        # Show database breakdown
                        db_counts = {}
                        for result in search_results:
                            db = result.get('metadata', {}).get('database_name', 'unknown')
                            workspace = result.get('metadata', {}).get('workspace', '')
                            qualified_name = f"{workspace}.{db}" if workspace else db
                            db_counts[qualified_name] = db_counts.get(qualified_name, 0) + 1

                        db_breakdown = ', '.join([f"{db}: {count}" for db, count in db_counts.items()])
                        print_text(f"   Database breakdown: {db_breakdown}", style="dim")

                # Convert to unified_content-like format for compatibility
                results = []
                for result in search_results:
                    # Extract base page_id
                    vector_id = result['page_id']
                    base_page_id = result['metadata'].get('page_id', vector_id)

                    if '_chunk_' in vector_id and base_page_id == vector_id:
                        base_page_id = vector_id.rsplit('_chunk_', 1)[0]

                    results.append({
                        'page_id': base_page_id,
                        'chunk_id': vector_id if '_chunk_' in vector_id else None,
                        'similarity_score': result['similarity_score'],
                        'database_name': result['metadata'].get('database_name', ''),
                        'workspace': result['metadata'].get('workspace', ''),
                        'created_time': result['metadata'].get('created_time', ''),
                        'content_type': result['metadata'].get('content_type', ''),
                        'title': result['metadata'].get('title', ''),
                    })

                return results, None

        except Exception as e:
            error_msg = f"Vector search error: {str(e)}"
            if debug:
                print_text(f"\n❌ {error_msg}", style="red")
            print_text(f"❌ {error_msg}", style="red")
            return None, error_msg
    
    def should_save_pattern(self) -> bool:
        """Vector mode doesn't support pattern learning."""
        return False

    def save_pattern(self, pattern: Dict[str, Any]) -> None:
        """Vector mode doesn't save patterns."""
        pass

    def _filter_property(
        self,
        property_name: str,
        constraint: Dict[str, Any],
        filters: Optional[Dict[str, Any]],
        debug: bool
    ) -> List[str]:
        """
        Apply filter-type property constraint using metadata filtering.

        Args:
            property_name: Property to filter on
            constraint: Constraint dict with type, value, operator
            filters: Base metadata filters (workspace, database_name)
            debug: Debug mode flag

        Returns:
            List of page_ids matching the filter
        """
        try:
            if debug:
                print_text(f"\n🔍 Applying filter on property '{property_name}'", style="cyan")
                print_text(f"   Value: {constraint.get('value')}", style="dim")
                print_text(f"   Operator: {constraint.get('operator', 'equals')}", style="dim")

            # Use hybrid storage to query property values
            from promaia.storage.hybrid_storage import get_hybrid_registry
            registry = get_hybrid_registry()

            # Build SQL filter based on operator
            operator = constraint.get('operator', 'equals')
            value = constraint.get('value')

            if operator == 'not_empty':
                where_clause = f"{property_name} IS NOT NULL AND {property_name} != ''"
            elif operator == 'equals':
                where_clause = f"{property_name} = ?"
            elif operator == 'contains':
                where_clause = f"{property_name} LIKE ?"
                value = f"%{value}%"
            elif operator == 'greater_than':
                where_clause = f"{property_name} > ?"
            elif operator == 'less_than':
                where_clause = f"{property_name} < ?"
            else:
                where_clause = f"{property_name} = ?"

            # Query unified_content with property filter
            # TODO: This needs to be implemented in hybrid_storage
            # For now, return empty list
            if debug:
                print_text(f"   ⚠️  Filter query not yet implemented: {where_clause}", style="yellow")

            return []

        except Exception as e:
            if debug:
                print_text(f"   ❌ Filter failed: {e}", style="red")
            return []

    def _load_results_by_page_ids(
        self,
        page_ids: List[str],
        verbose: bool,
        debug: bool
    ) -> List[Dict[str, Any]]:
        """
        Load full results from unified_content by page_ids.

        Args:
            page_ids: List of page_ids to load
            verbose: Verbose mode flag
            debug: Debug mode flag

        Returns:
            List of result dicts with page metadata
        """
        try:
            if debug:
                print_text(f"\n📄 Loading {len(page_ids)} pages from unified_content", style="cyan")

            from promaia.storage.hybrid_storage import get_hybrid_registry
            registry = get_hybrid_registry()

            results = []
            for page_id in page_ids:
                # Get page metadata
                page_info = registry.get_page_metadata(page_id)
                if page_info:
                    results.append({
                        'page_id': page_id,
                        'database_name': page_info.get('database_name', ''),
                        'workspace': page_info.get('workspace', ''),
                        'created_time': page_info.get('created_time', ''),
                        'content_type': page_info.get('content_type', ''),
                        'title': page_info.get('title', '')
                    })

            if debug:
                print_text(f"   ✅ Loaded {len(results)} pages", style="green")

            return results

        except Exception as e:
            if debug:
                print_text(f"   ❌ Failed to load pages: {e}", style="red")
            return []

