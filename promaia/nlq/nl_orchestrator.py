"""
Enhanced Agentic Natural Language Processor

This replaces the hardcoded example-based system with a fully agentic approach:
- Dynamic schema exploration
- Learning from successful queries
- Result validation and iteration
- User confirmation with detailed summaries
"""
import os
import json
from typing import List, Dict, Any, Optional

# Load environment variables
from promaia.utils.config import load_environment
load_environment()

from promaia.config.databases import get_database_manager
from promaia.utils.display import print_text

# Import our agentic components
from .nl_utilities import (
    SchemaExplorer,
    NLContextLogger,
    ResultValidator,
    format_result_summary_for_user
)

# Import query strategies
from .query_strategies import QueryStrategy, SQLQueryStrategy, VectorQueryStrategy

# LLM Adapter (copied to avoid langchain dependencies)
from anthropic import Anthropic
from openai import OpenAI
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    import google.generativeai as genai


class MockResponse:
    """Mock response object for compatibility."""
    def __init__(self, content):
        self.content = content


class PromaiLLMAdapter:
    """Adapter to make existing Promaia LLM clients work with our interface."""
    
    def __init__(self, client_type: str = "auto"):
        self.client_type = client_type
        self._setup_client()
    
    def _setup_client(self):
        """Setup the appropriate LLM client with fallback handling."""
        if self.client_type == "auto":
            # Try clients in order
            api_keys = [
                ("ANTHROPIC_API_KEY", "anthropic"),
                ("OPENAI_API_KEY", "openai"),
                ("GOOGLE_API_KEY", "gemini")
            ]
            
            for env_key, client_type in api_keys:
                if os.getenv(env_key):
                    try:
                        if client_type == "openai":
                            self.client_type = "openai"
                            self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                            return
                        elif client_type == "anthropic":
                            self.client_type = "anthropic"
                            self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url=os.environ.get("ANTHROPIC_BASE_URL"), max_retries=5)
                            return
                        elif client_type == "gemini":
                            self.client_type = "gemini"
                            genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
                            from promaia.nlq.models import get_current_google_model
                            self.client = genai.GenerativeModel(get_current_google_model())
                            return
                    except Exception as e:
                        print(f"⚠️  Failed to setup {client_type} client: {e}")
                        continue
            
            raise ValueError("No working LLM API clients found")
        
    def invoke(self, messages):
        """Simple invoke method."""
        # Extract message content
        if isinstance(messages, list):
            # Handle dict-based messages
            prompt = ""
            for msg in messages:
                if isinstance(msg, dict):
                    prompt += msg.get('content', '') + "\n"
                else:
                    prompt += str(msg) + "\n"
        else:
            prompt = str(messages)
            
        # Call the appropriate client
        if self.client_type == "openai":
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt.strip()}],
                max_tokens=4000
            )
            return MockResponse(response.choices[0].message.content)
            
        elif self.client_type == "anthropic":
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt.strip()}]
            )
            return MockResponse(response.content[0].text)
            
        elif self.client_type == "gemini":
            response = self.client.generate_content(prompt.strip())
            return MockResponse(response.text)
        
        else:
            raise ValueError(f"Unknown client type: {self.client_type}")


class AgenticNLQueryProcessor:
    """
    Agentic NL query processor that learns and adapts.
    
    Uses Strategy Pattern to support both SQL and vector search modes:
    - SQL mode: Generates SQL queries, learns from patterns
    - Vector mode: Uses semantic embeddings for similarity search
    
    Key features:
    1. Uses PRAGMA to discover schema dynamically
    2. Learns from successful queries (rolling index of 20) - SQL mode only
    3. Validates results and retries if needed
    4. Saves context logs for user inspection
    5. Asks for user confirmation before saving patterns
    """
    
    def __init__(self, db_path: str = None, query_mode: str = "sql",
                 debug: bool = False, verbose: bool = False):
        if db_path is None:
            from promaia.utils.env_writer import get_db_path
            db_path = str(get_db_path())
        self.db_path = db_path
        self.query_mode = query_mode  # "sql" or "vector"
        self.debug = debug or os.getenv("MAIA_DEBUG") == "1"
        self.verbose = verbose or self.debug  # Verbose mode includes debug info
        
        # Initialize shared agentic components
        self.schema_explorer = SchemaExplorer(db_path)
        self.context_logger = NLContextLogger()
        self.validator = ResultValidator()
        
        # Load workspace config for AI context
        self.workspace_config = self._load_workspace_config()
        
        # Initialize strategy based on mode
        if query_mode == "sql":
            self.strategy = SQLQueryStrategy(db_path)
        elif query_mode == "vector":
            self.strategy = VectorQueryStrategy()
        else:
            raise ValueError(f"Unknown query_mode: {query_mode}")
        
        # Initialize strategy-specific components
        status_msg = self.strategy.initialize(verbose=self.verbose, debug=self.debug)
        if self.verbose:
            print_text(status_msg, style="green")
        
        # Initialize LLM
        self.llm = PromaiLLMAdapter(client_type="auto")
        if self.verbose:
            print_text(f"   Using {self.llm.client_type} for query generation", style="dim")
        if self.debug:
            print_text("🐛 Debug mode enabled - showing chain of thought", style="yellow")
    
    def _load_workspace_config(self, config_file: str = None) -> Dict[str, Any]:
        """Load workspace configuration to provide context to AI."""
        if config_file is None:
            from promaia.utils.env_writer import get_config_path
            config_file = str(get_config_path())
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    # Filter out sensitive info and return just structure
                    return {
                        'workspaces': list(config.get('workspaces', {}).keys()),
                        'default_workspace': config.get('default_workspace'),
                        'databases': {
                            name: {
                                'nickname': db.get('nickname'),
                                'description': db.get('description'),
                                'workspace': db.get('workspace'),
                                'source_type': db.get('source_type'),
                                'default_include': db.get('default_include', False),
                                'default_days': db.get('default_days')
                            }
                            for name, db in config.get('databases', {}).items()
                        }
                    }
        except Exception as e:
            if self.debug:
                print_text(f"⚠️  Could not load workspace config: {e}", style="yellow")
        
        return {}
    
    def _format_workspace_config(self) -> str:
        """Format workspace config for AI prompt."""
        if not self.workspace_config:
            return "No workspace configuration available."
        
        output = "=== WORKSPACE CONFIGURATION ===\n\n"
        
        # Workspaces
        output += f"Workspaces: {', '.join(self.workspace_config.get('workspaces', []))}\n"
        output += f"Default: {self.workspace_config.get('default_workspace', 'N/A')}\n\n"
        
        # Databases grouped by workspace
        databases = self.workspace_config.get('databases', {})
        by_workspace = {}
        for name, db in databases.items():
            workspace = db.get('workspace', 'unknown')
            if workspace not in by_workspace:
                by_workspace[workspace] = []
            by_workspace[workspace].append((name, db))
        
        output += "Databases by Workspace:\n"
        for workspace, dbs in sorted(by_workspace.items()):
            output += f"\n  {workspace.upper()} workspace:\n"
            for name, db in dbs:
                output += f"    • {name} ({db.get('nickname')}): {db.get('description', 'N/A')}\n"
                output += f"      Type: {db.get('source_type')}, Default: {db.get('default_include')}\n"
        
        return output
    
    def process_query_with_modification(
        self,
        user_query: str,
        workspace: Optional[str] = None,
        max_retries: int = 2,
        n_results: Optional[int] = None,
        min_similarity: Optional[float] = None,
        skip_confirmation: bool = False
    ) -> Dict[str, Any]:
        """
        Process NL query with support for user modification.

        If user chooses to modify the query (presses 'm'), prompts for
        a new query and re-runs with same schema context.

        Args:
            user_query: The natural language query from the user
            workspace: Optional workspace filter
            max_retries: Maximum number of retry attempts if validation fails
            skip_confirmation: Skip user confirmation prompts (for parallel query execution)

        Returns:
            Dictionary with results, SQL, intent, and learning info
        """
        while True:
            result = self.process_query(user_query, workspace, max_retries, n_results, min_similarity, skip_confirmation=skip_confirmation)

            # If skip_confirmation is True, don't prompt user - just return result
            if skip_confirmation:
                return result

            # If user wants to quit, return immediately (exit to terminal)
            if result.get('action') == 'quit':
                return result

            # If user wants to modify, ask for new query and loop
            elif result.get('action') == 'modify':
                print_text("\n✏️  Modify your query (edit and press Enter, or Ctrl+C to cancel):", style="bold cyan")
                try:
                    # Reconstruct full query with flags for editing
                    full_query = user_query
                    if n_results is not None:
                        full_query += f" --top-k {n_results}"
                    if min_similarity is not None:
                        full_query += f" --threshold {min_similarity}"

                    # Pre-fill input with full query including flags for editing
                    modified_input = self._get_input_with_prefill("   Query: ", full_query)
                    if not modified_input:
                        print_text("   Empty query, returning to previous results.", style="yellow")
                        result.pop('action')  # Remove 'modify' action
                        return result

                    # Parse the modified input to extract query and any updated flags
                    user_query, n_results, min_similarity = self._parse_query_with_flags(modified_input, n_results, min_similarity)
                    # Loop will re-run with new query and potentially new parameters
                except (KeyboardInterrupt, EOFError):
                    print_text("\n   Quitting...", style="dim")
                    result['action'] = 'quit'  # Change to quit action
                    return result
            else:
                # Normal completion (user pressed Enter to save)
                return result
    
    def _parse_query_with_flags(self, query_string: str, default_n_results: Optional[int],
                                default_min_similarity: Optional[float]) -> tuple:
        """
        Parse a query string that may contain --top-k and --threshold flags.

        Args:
            query_string: Full query string potentially with flags
            default_n_results: Default n_results to use if not in query
            default_min_similarity: Default min_similarity to use if not in query

        Returns:
            Tuple of (query_text, n_results, min_similarity)
        """
        import re

        # Extract --top-k flag
        n_results = default_n_results
        top_k_match = re.search(r'--top-k\s+(\d+)', query_string)
        if top_k_match:
            n_results = int(top_k_match.group(1))
            # Remove the flag from query string
            query_string = re.sub(r'\s*--top-k\s+\d+\s*', ' ', query_string)

        # Extract --threshold flag
        min_similarity = default_min_similarity
        threshold_match = re.search(r'--threshold\s+([\d.]+)', query_string)
        if threshold_match:
            min_similarity = float(threshold_match.group(1))
            # Remove the flag from query string
            query_string = re.sub(r'\s*--threshold\s+[\d.]+\s*', ' ', query_string)

        # Clean up the query string
        query_text = query_string.strip()

        return query_text, n_results, min_similarity

    def _get_input_with_prefill(self, prompt: str, prefill: str) -> str:
        """Get user input with pre-filled text for editing.

        Note: Simplified to avoid asyncio conflicts with prompt_toolkit.
        Shows original query and asks for new input instead of pre-filling.
        """
        # Show the original query
        print_text(f"   Original: {prefill}", style="dim")
        # Get new input
        return input(prompt).strip()
    
    def process_query(
        self,
        user_query: str,
        workspace: Optional[str] = None,
        max_retries: int = 2,
        n_results: Optional[int] = None,
        min_similarity: Optional[float] = None,
        skip_confirmation: bool = False
    ) -> Dict[str, Any]:
        """
        Process a natural language query with agentic features.

        Args:
            user_query: The natural language query from the user
            workspace: Optional workspace filter
            max_retries: Maximum number of retry attempts if validation fails
            skip_confirmation: Skip user confirmation prompts (for parallel query execution)

        Returns:
            Dictionary with results, SQL, intent, and learning info
        """
        if not skip_confirmation:  # Only show processing message in standalone mode
            if self.verbose:
                print_text(f"\n🤖 Processing query: '{user_query}'", style="cyan")
            else:
                print_text("🤖 Processing natural language query...", style="cyan")
        
        # Step 1: Explore schema dynamically
        if self.verbose:
            print_text("🔍 Step 1: Exploring database schema...", style="dim")
        schema = self.schema_explorer.explore_schema()
        
        # Step 2: Parse intent
        if self.verbose:
            print_text("🧠 Step 2: Parsing intent...", style="dim")
        intent = self._parse_intent(user_query, schema, workspace)
        
        if not intent:
            return {
                "success": False,
                "error": "Failed to parse query intent",
                "results": {}
            }
        
        # Show parsed intent to user (only in verbose mode)
        if self.verbose:
            self._display_intent(intent)
        
        # Step 3: Generate and execute query (with retries)
        attempt = 0
        results = None
        generated_query = None
        validation_result = None
        
        while attempt <= max_retries:
            if attempt > 0 and self.verbose:
                print_text(f"\n🔄 Retry attempt {attempt}/{max_retries}", style="yellow")
            
            if self.verbose:
                query_type = "vector search parameters" if self.query_mode == "vector" else "SQL query"
                print_text(f"⚙️  Step 3: Generating {query_type}...", style="dim")
            
            # Delegate to strategy - NO if/else branches!
            validation_feedback = ""
            if attempt > 0 and intent.get('_validation_feedback'):
                validation_feedback = f"""
PREVIOUS ATTEMPT FAILED:
{intent['_validation_feedback']}

Please adjust the query to fix this issue.
"""
            
            generated_query = self.strategy.generate_query(
                intent=intent,
                schema=schema,
                retry_attempt=attempt,
                llm=self.llm,
                workspace_context=self._format_workspace_config(),
                schema_summary=self._format_schema_for_prompt(schema),
                validation_feedback=validation_feedback,
                verbose=self.verbose,
                debug=self.debug
            )
            
            if not generated_query:
                attempt += 1
                continue
            
            # Display query using strategy - NO if/else branches!
            self.strategy.display_generated_query(generated_query, self.verbose)
            
            if self.verbose:
                print_text(f"\n🔍 Executing query...", style="dim")
            
            # Execute query using strategy - NO if/else branches!
            results, sql_error = self.strategy.execute_query(
                query=generated_query,
                verbose=self.verbose,
                debug=self.debug,
                n_results=n_results,
                min_similarity=min_similarity
            )
            
            # If SQL error, use that as validation feedback
            if sql_error:
                if self.verbose:
                    print_text(f"❌ SQL Error during execution", style="red")
                intent['_validation_feedback'] = sql_error
                attempt += 1
                continue
            
            if results is None:
                attempt += 1
                continue
            
            # Step 4: Validate results
            if self.verbose:
                print_text("🔍 Step 4: Validating results...", style="dim")
            is_valid, message = self.validator.validate_results(intent, results, query_mode=self.query_mode)
            validation_result = {"is_valid": is_valid, "message": message}
            
            if self.debug:
                print_text("\n" + "=" * 70, style="dim")
                print_text("✅ CHAIN OF THOUGHT: Result Validation", style="bold yellow")
                print_text("=" * 70, style="dim")
                print_text(f"\n🔍 Validation checks:", style="cyan")
                print_text(f"   • Results exist: {'✓' if results else '✗'}", style="dim")
                print_text(f"   • Count: {len(results) if results else 0}", style="dim")
                if results:
                    result_dbs = set(r.get('database_name') for r in results)
                    intent_dbs = set(intent.get('databases', []))
                    print_text(f"   • Database match: {result_dbs} vs expected {intent_dbs}", style="dim")
                    search_terms = intent.get('search_terms', [])
                    if search_terms:
                        print_text(f"   • Search terms check: {search_terms}", style="dim")
                print_text(f"\n🎯 Validation result: {'PASS ✓' if is_valid else 'FAIL ✗'}", style="green" if is_valid else "yellow")
                print_text(f"   Reason: {message}", style="dim")
            
            if is_valid:
                if self.verbose:
                    print_text(f"✅ {message}", style="green")
                break
            else:
                if self.verbose:
                    print_text(f"⚠️  {message}", style="yellow")
                # Update intent with validation feedback for retry
                intent['_validation_feedback'] = message
                attempt += 1
        
        # If all retries failed, give user options instead of just failing
        if results is None or not validation_result['is_valid']:
            error_msg = validation_result['message'] if validation_result else "Query execution failed"

            # Only show error details and prompts if not skip_confirmation
            if not skip_confirmation:
                # For vector search with 0 results, suggest modifying threshold
                if self.query_mode == "vector" and results is not None and len(results) == 0:
                    print_text(f"\n⚠️  {error_msg}", style="yellow")
                    if min_similarity:
                        print_text(f"   Current threshold: {min_similarity}", style="dim")
                        print_text(f"   💡 Tip: Try lowering --threshold or adjusting your query", style="cyan")
                else:
                    # For other failures
                    print_text(f"\n⚠️  {error_msg}", style="yellow")
                    if max_retries > 0:
                        print_text(f"   Query failed after {max_retries} retries", style="dim")

                # Ask user what to do (same prompt as success case)
                print()  # Blank line before prompt
                user_action = self._ask_user_confirmation_on_failure()
            else:
                # When skip_confirmation, don't prompt - just return failure
                user_action = ''  # Auto-accept failure (return empty results)

            if user_action == 'modify':
                return {
                    "success": False,
                    "action": "modify",
                    "error": error_msg,
                    "intent": intent,
                    "query": generated_query,
                    "results": {}
                }
            elif user_action == 'quit':
                return {
                    "success": False,
                    "action": "quit",
                    "error": error_msg,
                    "intent": intent,
                    "query": generated_query,
                    "results": {}
                }
            else:  # accept with empty results
                return {
                    "success": False,
                    "error": error_msg,
                    "intent": intent,
                    "query": generated_query,
                    "results": {}
                }
        
        # Step 5: Generate result summary
        summary = self.validator.generate_result_summary(results)
        
        # Step 6: Save draft context log
        query_info = {
            "user_query": user_query,
            "intent": intent,
            "generated_query": generated_query,
            "query_mode": self.query_mode,
            "result_count": summary['total_count'],
            "databases_in_results": summary['databases'],
            "database_breakdown": summary['database_breakdown'],
            "sample_results": summary['sample_results'],
            "validation": validation_result,
            "retries": attempt
        }
        
        log_file = self.context_logger.save_draft_context(query_info)
        summary_file = self.context_logger.save_summary(query_info)
        
        # Step 7: Show summary (verbose or compact mode) - skip if skip_confirmation
        if not skip_confirmation:
            if self.verbose:
                # Show sample results
                print_text(format_result_summary_for_user(summary, intent), style="white")
                # Don't show log file paths in verbose mode - they're saved silently
            else:
                # Compact summary for non-verbose mode
                print_text("✅ Query processed successfully\n", style="green")
                self._display_compact_summary(summary, intent)
        
        # Detect direct-data queries (e.g. Shopify) — results are raw SQL rows,
        # not page references to be loaded from disk.
        _shopify_tables = ('shopify_orders', 'shopify_products', 'shopify_inventory_snapshots')
        is_direct_data = (
            generated_query
            and isinstance(generated_query, str)
            and any(t in generated_query.lower() for t in _shopify_tables)
            and results
            and not results[0].get('page_id')
        )

        # Step 8: Group results by database with minimal metadata
        # Return only page_id and content_type for the adapter to load content
        # Use qualified names (workspace.database) to avoid collisions
        grouped_results = {}
        for result in results:
            workspace = result.get('workspace', '')
            db_name = result.get('database_name', 'unknown')

            # Create qualified key: workspace.database (unless already qualified)
            if workspace and '.' not in db_name:
                qualified_key = f"{workspace}.{db_name}"
            else:
                qualified_key = db_name

            if qualified_key not in grouped_results:
                grouped_results[qualified_key] = []

            # Return minimal metadata: page_id and content_type
            grouped_results[qualified_key].append({
                'page_id': result.get('page_id'),
                'content_type': result.get('content_type', db_name),
                'database_name': db_name,
                'workspace': workspace,
                'created_time': result.get('created_time'),
                'title': result.get('title', '')  # Include title for display
            })
        
        if self.verbose:
            total_pages = sum(len(pages) for pages in grouped_results.values())
            print_text(f"📋 Prepared {total_pages} page references for adapter to load", style="dim")
        
        # Ask user if query was successful (skip if skip_confirmation)
        if skip_confirmation:
            user_action = ''  # Auto-accept when called from query tools
        else:
            user_action = self._ask_user_confirmation(summary)

        if user_action == 'save':
            # Save to learning index (only if strategy supports it)
            if self.strategy.should_save_pattern():
                pattern = {
                    "user_query": user_query,
                    "intent": intent,
                    "generated_sql": generated_query,
                    "result_count": summary['total_count'],
                    "databases": summary['databases'],
                    "notes": f"Validated successfully. {validation_result['message']}"
                }
                self.strategy.save_pattern(pattern)
        elif user_action == 'modify':
            # User wants to modify the query - signal to wrapper
            return {
                "success": True,
                "action": "modify",
                "results": grouped_results,
                "intent": intent,
                "query": generated_query,
                "query_mode": self.query_mode,
                "validation": validation_result
            }
        elif user_action == 'quit':
            # User wants to exit to terminal (don't continue to chat)
            return {
                "success": True,
                "action": "quit",
                "results": grouped_results,
                "intent": intent,
                "query": generated_query,
                "query_mode": self.query_mode,
                "summary": summary,
                "learned": False
            }
        
        result_dict = {
            "success": True,
            "results": grouped_results,
            "intent": intent,
            "query": generated_query,
            "query_mode": self.query_mode,
            "summary": summary,
            "learned": (user_action == 'save')
        }
        if is_direct_data:
            result_dict["direct_data"] = results
        return result_dict
    
    def _format_property_schema_context(self, database_names: List[str]) -> str:
        """
        Format available property schemas for given databases.

        Queries the hybrid storage registry to get property schemas and formats
        them for the LLM to understand what properties are available for filtering.

        Args:
            database_names: List of database names to get schemas for

        Returns:
            Formatted string with property schemas
        """
        try:
            from promaia.storage.hybrid_storage import get_hybrid_registry
            registry = get_hybrid_registry(db_path=self.db_path)

            output = "=== AVAILABLE PROPERTIES ===\n\n"

            for db_name in database_names:
                # Get property schema for this database
                property_schema = registry.get_property_schema(db_name)

                if not property_schema:
                    continue

                output += f"{db_name}:\n"

                # Group by embeddable vs filterable
                embeddable = []
                filterable = []

                EMBEDDABLE_TYPES = {'title', 'text', 'rich_text', 'relation'}

                for prop in property_schema:
                    col_name = prop['column_name']
                    notion_type = prop['notion_type']

                    if notion_type in EMBEDDABLE_TYPES:
                        embeddable.append(f"{col_name} ({notion_type})")
                    else:
                        filterable.append(f"{col_name} ({notion_type})")

                if embeddable:
                    output += f"  Semantic search properties: {', '.join(embeddable)}\n"
                if filterable:
                    output += f"  Filter properties: {', '.join(filterable)}\n"
                output += "\n"

            return output if len(output) > len("=== AVAILABLE PROPERTIES ===\n\n") else ""

        except Exception as e:
            if self.debug:
                print_text(f"⚠️  Could not load property schemas: {e}", style="yellow")
            return ""

    def _parse_intent(
        self,
        user_query: str,
        schema: Dict[str, Any],
        workspace: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Parse user query into structured intent using LLM."""
        available_dbs = schema.get('available_databases', [])

        workspace_context = self._format_workspace_config()

        # Get property schema context for all available databases
        property_context = self._format_property_schema_context(available_dbs)

        prompt = f"""Parse this natural language query into structured intent:

Query: "{user_query}"
{f'Workspace filter: {workspace}' if workspace else ''}

{workspace_context}

Available databases: {available_dbs}

{property_context if property_context else ""}

Available tables and their columns:
{self._format_schema_for_prompt(schema)}

Respond with JSON in this exact format:
{{
    "goal": "what the user wants to find",
    "databases": ["list", "of", "relevant", "databases"],
    "search_terms": ["key", "content", "search", "terms"],
    "date_filter": {{"days_back": null, "start_date": null, "end_date": null, "description": ""}},
    "property_constraints": {{}}
}}

Rules for property_constraints:
- Extract property constraints mentioned in the query (e.g., "stories with epic 2025 holiday launch")
- For semantic properties (title, text, rich_text, relation), use type "semantic" with the search value
- For filter properties (select, status, multi_select, people), use type "filter" with normalized value
- Normalize filter values: "done" → "Done", "in progress" → "In Progress", "todo" → "To Do"
- Use operators: "equals" (default), "not_empty", "contains", "greater_than", "less_than"
- Format: {{"property_name": {{"type": "semantic|filter", "value": "search term", "operator": "equals"}}}}
- If no properties mentioned, leave property_constraints as empty object {{}}

Examples:
- "stories with epic 2025 holiday launch" → {{"epic": {{"type": "semantic", "value": "2025 holiday launch", "operator": "equals"}}}}
- "stories with status done" → {{"status": {{"type": "filter", "value": "Done", "operator": "equals"}}}}
- "tasks assigned to John" → {{"assigned_to": {{"type": "filter", "value": "John", "operator": "contains"}}}}
- "pages with non-empty title" → {{"title": {{"type": "filter", "value": "", "operator": "not_empty"}}}}

Rules for database names - CRITICAL:
- ONLY include databases that are EXPLICITLY mentioned in the query (e.g., "gmail", "stories", "notion")
- If the user specifies a database type, use ONLY that database - do not add others from the same workspace
- ALWAYS use qualified names (workspace.database) when a workspace is mentioned in the query
- If the query mentions BOTH a workspace AND a database, you MUST combine them as "workspace.database"
- If ONLY a database is mentioned with no workspace context, use the simple name
- Extract specific search terms from the query

Rules for date_filter - CRITICAL:
- For simple backward lookups use days_back: "last N months" → days_back: N*30, "past week" → days_back: 7
- For date RANGES use start_date and end_date: "between X and Y" → start_date: X, end_date: Y
- For future dates, use ISO format (YYYY-MM-DD) or relative dates: "until april" → end_date: "2026-04-30"
- Parse relative dates: "a week ago" → "DATE('now', '-7 days')" for start_date
- TODAY IS: 2026-01-16 - use this for calculating relative dates
- If no date mentioned, set all date_filter fields to null
- NEVER mix incompatible date logic (e.g., don't use days_back with start_date/end_date)

Examples of CORRECT date filtering:
- "last 7 days" → {{"days_back": 7, "start_date": null, "end_date": null, "description": "last 7 days"}}
- "past 2 months" → {{"days_back": 60, "start_date": null, "end_date": null, "description": "past 2 months"}}
- "between a week ago and april" → {{"days_back": null, "start_date": "DATE('now', '-7 days')", "end_date": "2026-04-30", "description": "between a week ago and April 2026"}}
- "from january to march" → {{"days_back": null, "start_date": "2026-01-01", "end_date": "2026-03-31", "description": "from January to March 2026"}}
- "until april" → {{"days_back": null, "start_date": null, "end_date": "2026-04-30", "description": "until April 2026"}}
- "since last monday" → {{"days_back": null, "start_date": "DATE('now', '-7 days')", "end_date": null, "description": "since last Monday"}}

Examples of CORRECT database naming:
- "acme gmail about X" → databases: ["acme.gmail"] (workspace + database = qualified name)
- "acme stories with Y" → databases: ["acme.stories"] (workspace + database = qualified name)
- "stories in the acme workspace" → databases: ["acme.stories"] (workspace + database = qualified name)
- "acme workspace notion stories" → databases: ["acme.stories"] (workspace + database, ignore "notion" as descriptor)
- "find X in stories" → databases: ["stories"] (no workspace mentioned = simple name)
- "acme gmail about X" → databases: ["acme.gmail", "acme.yp"] WRONG (don't add unrequested databases)

Return ONLY the JSON object:"""
        
        if self.debug:
            print_text("\n" + "=" * 70, style="dim")
            print_text("🧠 CHAIN OF THOUGHT: Intent Parsing", style="bold yellow")
            print_text("=" * 70, style="dim")
            print_text(f"\n📤 Prompt to LLM ({self.llm.client_type}):", style="cyan")
            print_text(prompt[:500] + "..." if len(prompt) > 500 else prompt, style="dim")
        
        try:
            response = self.llm.invoke([{"role": "user", "content": prompt}])
            content = response.content.strip()
            
            if self.debug:
                print_text(f"\n📥 LLM Response:", style="cyan")
                print_text(content[:300] + "..." if len(content) > 300 else content, style="dim")
            
            # Clean JSON
            if content.startswith('```json'):
                content = content[7:-3].strip()
            elif content.startswith('```'):
                content = content[3:-3].strip()
            
            import json
            intent = json.loads(content)

            # Normalize property names in constraints to actual column names
            intent = self._normalize_property_names(intent)

            return intent

        except Exception as e:
            print_text(f"❌ Intent parsing failed: {e}", style="red")
            return None
    
    def _normalize_property_names(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize property names in property_constraints from semantic names to actual column names.

        For example: "title" → "name_2", "epic" → "_epics"
        """
        property_constraints = intent.get('property_constraints', {})
        if not property_constraints:
            return intent

        databases = intent.get('databases', [])
        if not databases:
            return intent

        try:
            from promaia.storage.hybrid_storage import get_hybrid_registry
            registry = get_hybrid_registry()

            # Get database IDs for the target databases
            import json
            from promaia.utils.env_writer import get_config_path
            with open(str(get_config_path()), 'r') as f:
                config = json.load(f)

            # Build property name mapping for all target databases
            property_mapping = {}  # semantic_name → column_name
            for db_ref in databases:
                # Parse database reference (may be "workspace.database" or just "database")
                if '.' in db_ref:
                    workspace, db_nickname = db_ref.split('.', 1)
                else:
                    db_nickname = db_ref

                # Find database in config
                db_config = config.get('databases', {}).get(db_nickname) or \
                           config.get('databases', {}).get(db_ref)

                if not db_config:
                    continue

                database_id = db_config.get('database_id')
                if not database_id:
                    continue

                # Get property schema for this database
                property_schema = registry.get_property_schema(database_id)
                if not property_schema:
                    continue

                # Build mapping from property_name (user-friendly) to column_name (actual)
                for prop in property_schema:
                    prop_name = prop.get('property_name', '')
                    col_name = prop.get('column_name', '')
                    notion_type = prop.get('notion_type', '')

                    if not prop_name or not col_name:
                        continue

                    # Map semantic name variations to column name
                    # e.g., "Name" → "name_2", "Epic" → "_epics"
                    semantic_name = prop_name.lower().strip()

                    # For title types, always use standardized name "title"
                    # (embeddings are stored with property_name="title" regardless of column name)
                    if notion_type == 'title':
                        property_mapping[semantic_name] = 'title'
                        property_mapping['title'] = 'title'
                    else:
                        property_mapping[semantic_name] = col_name
                        # Also map column name to itself for exact matches
                        property_mapping[col_name.lower()] = col_name

            # Normalize property_constraints using the mapping
            normalized_constraints = {}
            for prop_name, constraint in property_constraints.items():
                # Try to find the actual column name
                semantic_key = prop_name.lower().strip()
                actual_column = property_mapping.get(semantic_key, prop_name)

                normalized_constraints[actual_column] = constraint

                if self.debug and actual_column != prop_name:
                    print_text(f"   Mapped property: '{prop_name}' → '{actual_column}'", style="yellow")

            intent['property_constraints'] = normalized_constraints
            return intent

        except Exception as e:
            if self.debug:
                print_text(f"⚠️  Could not normalize property names: {e}", style="yellow")
            return intent

    def _display_intent(self, intent: Dict[str, Any]):
        """Display parsed intent to user (verbose mode only)."""
        print_text("\n🎯 Parsed Intent:", style="cyan")
        print_text(f"   Goal: {intent['goal']}", style="white")
        print_text(f"   Databases: {', '.join(intent['databases'])}", style="white")
        if intent.get('search_terms'):
            print_text(f"   Search Terms: {', '.join(intent['search_terms'])}", style="white")
        date_filter = intent.get('date_filter', {})
        if date_filter.get('days_back'):
            print_text(f"   Date Filter: {date_filter['description']}", style="white")
        print()
    
    def _display_compact_summary(self, summary: Dict[str, Any], intent: Dict[str, Any]):
        """Display a compact summary of query results (non-verbose mode)."""
        total = summary['total_count']
        
        # Format database breakdown compactly
        db_breakdown = []
        for db, count in summary['database_breakdown'].items():
            # Shorten database name if needed
            short_db = db.split('.')[-1] if '.' in db else db
            db_breakdown.append(f"{short_db}: {count}")
        
        print_text("📊 Results Summary:", style="bold white")
        print_text(f"• Total: {total} entries ({', '.join(db_breakdown)})", style="white")
        
        # Show date filter if present
        date_filter = intent.get('date_filter', {})
        if date_filter.get('description'):
            print_text(f"• Date Filter: {date_filter['description']}", style="white")
        
        print()  # Blank line before prompt
    
    def _ask_user_confirmation_on_failure(self) -> str:
        """
        Ask user what to do when query returns no results or fails validation.

        Returns:
            'accept' - Continue with no results (rarely useful but allowed)
            'modify' - Modify the query and try again
            'quit' - Exit to terminal
        """
        try:
            response = input("Enter (continue) / m(odify) / q(uit): ").strip().lower()

            if response == 'm':
                return 'modify'
            elif response == 'q':
                print_text("   Quitting...", style="dim")
                return 'quit'
            else:  # Enter or any other key = accept
                return 'accept'

        except (KeyboardInterrupt, EOFError):
            print_text("\n   Quitting...", style="dim")
            return 'quit'

    def _ask_user_confirmation(self, summary: Dict[str, Any]) -> str:
        """
        Ask user if the query was successful and should be learned.
        
        Returns:
            'save' - Save the pattern and continue
            'modify' - Modify the query and try again
            'quit' - Exit to terminal (don't continue to chat)
        """
        try:
            response = input("\nEnter (accept) / m(odify) / q(uit): ").strip().lower()
            
            if response == 'm':
                return 'modify'
            elif response == 'q':
                print_text("   Quitting...", style="dim")
                return 'quit'
            else:  # Enter or any other key = accept
                return 'save'
        
        except (KeyboardInterrupt, EOFError):
            print_text("\n   Quitting...", style="dim")
            return 'quit'
    
    def _format_schema_for_prompt(self, schema: Dict[str, Any]) -> str:
        """Format schema with sample rows - let LLM infer semantics from examples."""
        output = ""
        
        main_table = schema.get('main_content_table', 'unified_content')
        important_tables = [main_table, 'gmail_content', 'generic_content', 'unified_content']
        
        # Add workspace-specific notion tables (e.g., notion_WORKSPACE_DATABASE)
        # These contain the actual Notion properties
        for table_name in schema['tables'].keys():
            if table_name.startswith('notion_') and not table_name.endswith(('_schema', '_chunks', '_relations', '_select_options')):
                important_tables.append(table_name)
        
        for table, info in schema['tables'].items():
            if table in important_tables:
                output += f"\n{table} ({info['row_count']} rows):\n"
                
                # Show sample rows with ALL columns - LLM infers semantics from actual data
                samples = info.get('samples', [])
                if samples:
                    output += "  Sample rows (recent data):\n"
                    for i, sample in enumerate(samples, 1):
                        output += f"\n  Row {i}:\n"
                        for col_name, value in sample.items():
                            if value is not None:
                                # Format value
                                if isinstance(value, str):
                                    val_str = f'"{value}"' if len(value) < 70 else f'"{value[:70]}..."'
                                else:
                                    val_str = str(value)
                                output += f"    {col_name}: {val_str}\n"
                    output += "\n"
                else:
                    # Fallback: just show columns if no samples
                    output += "  Columns: " + ", ".join(col['name'] for col in info['columns'][:15]) + "\n"
        
        return output


# Convenience function for integration with existing code
def get_agentic_query_processor():
    """Get the agentic query processor instance."""
    return AgenticNLQueryProcessor()

