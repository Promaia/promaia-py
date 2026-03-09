"""
Query tool execution engine.

This module handles parsing query tool calls from AI responses and executing them
to load additional context into the chat session.
"""
import re
import xml.etree.ElementTree as ET
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any
from promaia.nlq.nl_processor_wrapper import process_natural_language_to_content, process_vector_search_to_content
from promaia.storage.files import load_database_pages_with_filters
from promaia.config.databases import get_database_config

logger = logging.getLogger(__name__)


class QueryToolExecutor:
    """Executes built-in query tools called by the AI."""

    QUERY_TOOLS = {'query_sql', 'query_vector', 'query_source'}

    def __init__(self, context_state: Dict[str, Any]):
        """Initialize with context state.

        Args:
            context_state: Current chat session context state
        """
        self.context_state = context_state

    def has_query_tool_calls(self, ai_response: str) -> bool:
        """Check if AI response contains query tool calls.

        Args:
            ai_response: The AI's response text

        Returns:
            True if query tool calls are present
        """
        if '<tool_call>' not in ai_response or '</tool_call>' not in ai_response:
            return False

        # Check if it contains query tool names
        for tool_name in self.QUERY_TOOLS:
            if f'<tool_name>{tool_name}</tool_name>' in ai_response:
                return True

        return False

    def parse_query_tool_calls(self, ai_response: str) -> List[Dict[str, Any]]:
        """Parse query tool calls from AI response.

        Format:
        <tool_call>
          <tool_name>query_sql</tool_name>
          <parameters>
            <query>find emails from Alice</query>
            <workspace>default</workspace>
          </parameters>
        </tool_call>

        Args:
            ai_response: The AI's response text

        Returns:
            List of tool call dictionaries with tool name and parameters
        """
        tool_calls = []

        # Pattern: <tool_call>...</tool_call>
        tool_call_pattern = r'<tool_call>(.*?)</tool_call>'
        matches = re.findall(tool_call_pattern, ai_response, re.DOTALL)

        for match in matches:
            try:
                # Extract tool name
                tool_name_match = re.search(r'<tool_name>(.*?)</tool_name>', match, re.DOTALL)
                if not tool_name_match:
                    logger.warning("Tool call missing <tool_name> tag")
                    continue

                tool_name = tool_name_match.group(1).strip()

                # Only process query tools
                if tool_name not in self.QUERY_TOOLS:
                    logger.debug(f"Skipping non-query tool: {tool_name}")
                    continue

                # Extract parameters
                parameters = {}
                parameters_match = re.search(r'<parameters>(.*?)</parameters>', match, re.DOTALL)

                if parameters_match:
                    params_content = parameters_match.group(1)

                    # Extract individual parameter tags
                    param_pattern = r'<(\w+)>(.*?)</\1>'
                    param_matches = re.findall(param_pattern, params_content, re.DOTALL)

                    for param_name, param_value in param_matches:
                        param_value = param_value.strip()

                        # Try to parse as JSON for complex types
                        if param_value.startswith(('{', '[')):
                            try:
                                parameters[param_name] = json.loads(param_value)
                            except json.JSONDecodeError:
                                parameters[param_name] = param_value
                        # Parse numbers
                        elif param_value.replace('.', '').replace('-', '').isdigit():
                            try:
                                if '.' in param_value:
                                    parameters[param_name] = float(param_value)
                                else:
                                    parameters[param_name] = int(param_value)
                            except ValueError:
                                parameters[param_name] = param_value
                        else:
                            parameters[param_name] = param_value

                tool_calls.append({
                    'tool_name': tool_name,
                    'parameters': parameters,
                    'raw_content': match
                })

            except Exception as e:
                logger.error(f"Error parsing query tool call: {e}")
                continue

        return tool_calls

    def _display_query_before_execution(self, index: int, tool_call: Dict[str, Any]) -> None:
        """Display query details before execution.

        Shows query type, query text, reasoning, and tool-specific details.
        """
        from promaia.utils.display import print_text

        tool_name = tool_call['tool_name']
        params = tool_call['parameters']

        # Map tool names to CLI flags
        flag_map = {
            'query_sql': '-sql',
            'query_vector': '-vs',
            'query_source': '-s'
        }

        print()
        print_text(f"Query {index}: {tool_name} ({flag_map.get(tool_name, '')})", style="bold cyan")
        print_text(f'  "{params.get("query", params.get("source", ""))}"', style="white")

        # Show reasoning
        if params.get('reasoning'):
            print_text(f"  💭 Reasoning: {params['reasoning']}", style="dim")

        # Show tool-specific details
        if tool_name == 'query_sql':
            # For SQL queries, show that SQL will be generated
            print_text(f"  📝 Will generate SQL query for natural language search", style="dim")

        elif tool_name == 'query_vector':
            # Show vector search parameters
            top_k = params.get('top_k', 50)
            min_sim = params.get('min_similarity', 0.2)
            print_text(f"  🔍 Parameters: top_k={top_k}, min_similarity={min_sim}", style="dim")

        elif tool_name == 'query_source':
            # Show source specification
            source = params.get('source', '')
            filters = params.get('filters', {})
            print_text(f"  📁 Source: {source}", style="dim")
            if filters:
                print_text(f"  🔧 Filters: {filters}", style="dim")

    async def execute_query_tool_calls(self, tool_calls: List[Dict[str, Any]], request_permission_callback) -> List[Dict[str, Any]]:
        """Execute query tools with parallel execution and serial approval.

        Flow:
        1. Show all query details BEFORE execution
        2. Execute ALL queries in parallel (async)
        3. Wait for all to complete
        4. Request approval for each ONE AT A TIME (showing results)
        5. Only load approved results into context

        Args:
            tool_calls: List of parsed query tool calls
            request_permission_callback: Async function to request user permission
                                        Should return ('approved', result), ('declined', None),
                                        ('skipped', None), or ('modified', new_params)

        Returns:
            List of execution results with loaded content (only approved queries)
        """
        import asyncio
        from promaia.utils.display import print_text

        # PHASE 0: Show all query details BEFORE execution
        for i, tool_call in enumerate(tool_calls, 1):
            self._display_query_before_execution(i, tool_call)

        print()  # Blank line before execution message
        
        # Visual separator before execution
        print_text("=" * 70, style="dim")
        print_text("EXECUTION PHASE (Running in Parallel)", style="bold cyan")
        print_text("=" * 70, style="dim")
        print()

        # PHASE 1: Parallel execution (no user interaction)
        if len(tool_calls) > 1:
            print_text(f"⚡ Executing {len(tool_calls)} queries in parallel...", style="cyan")
        else:
            print_text(f"⚡ Executing query...", style="cyan")

        execution_tasks = [
            self._execute_query_only(tool_call, query_index=i)
            for i, tool_call in enumerate(tool_calls, 1)
        ]

        # Wait for ALL queries to complete
        execution_results = await asyncio.gather(*execution_tasks, return_exceptions=True)

        # Convert exceptions to error results
        for i, result in enumerate(execution_results):
            if isinstance(result, Exception):
                execution_results[i] = {
                    'success': False,
                    'error': str(result),
                    'tool_call': tool_calls[i]
                }

        if len(tool_calls) > 1:
            print_text(f"✅ All queries completed\n", style="green")

        # Visual separator before approval phase
        print_text("=" * 70, style="dim")
        print_text("APPROVAL PHASE", style="bold cyan")
        print_text("=" * 70, style="dim")
        print()

        # PHASE 2: Serial approval with results preview
        final_results = []

        for tool_call, exec_result in zip(tool_calls, execution_results):
            # Request permission with execution results visible
            approval_result = await request_permission_callback(
                tool_name=tool_call['tool_name'],
                parameters=tool_call['parameters'],
                execution_result=exec_result
            )

            if approval_result[0] == 'approved':
                # Add query to context state
                query_id = str(uuid.uuid4())
                self.context_state['ai_queries'].append({
                    'id': query_id,
                    'type': tool_call['tool_name'],
                    'query': tool_call['parameters'].get('query', tool_call['parameters'].get('source', '')),
                    'reasoning': tool_call['parameters'].get('reasoning', ''),
                    'params': tool_call['parameters'],
                    'timestamp': datetime.now().isoformat()
                })
                exec_result['query_id'] = query_id
                final_results.append(exec_result)

            elif approval_result[0] == 'skipped':
                # Query was skipped (either auto-skipped for 0 results, or user-skipped)
                # Check if it was auto-skipped by looking at execution result
                auto_skipped = exec_result.get('zero_results', False)
                final_results.append({
                    'success': False,
                    'skipped': True,
                    'auto_skipped': auto_skipped,
                    'tool_call': tool_call
                })

            elif approval_result[0] == 'modified':
                # User wants to modify and re-run
                modified_params = approval_result[1]
                print_text("\n🔄 Re-executing with modified parameters...", style="yellow")

                # Re-execute with modified parameters
                modified_tool_call = {
                    'tool_name': tool_call['tool_name'],
                    'parameters': modified_params
                }
                rerun_result = await self._execute_query_only(modified_tool_call)

                # Add to context if successful
                if rerun_result.get('success'):
                    query_id = str(uuid.uuid4())
                    self.context_state['ai_queries'].append({
                        'id': query_id,
                        'type': tool_call['tool_name'],
                        'query': modified_params.get('query', modified_params.get('source', '')),
                        'reasoning': modified_params.get('reasoning', ''),
                        'params': modified_params,
                        'timestamp': datetime.now().isoformat(),
                        'modified': True
                    })
                    rerun_result['query_id'] = query_id
                    final_results.append(rerun_result)
                else:
                    final_results.append(rerun_result)

            else:  # declined
                final_results.append({
                    'success': False,
                    'declined': True,
                    'tool_call': tool_call
                })

        return final_results

    async def _execute_query_only(self, tool_call: Dict[str, Any], query_index: int = None) -> Dict[str, Any]:
        """Execute query without permission check - for parallel execution.

        This method executes the query and returns the result WITHOUT requesting
        user permission or adding to context state. Used for parallel execution
        where approval happens after all queries complete.

        Args:
            tool_call: Tool call dictionary with tool_name and parameters
            query_index: Index of query for labeled output (e.g., "Query 1", "Query 2")

        Returns:
            Execution result with success/error state and loaded content
        """
        tool_name = tool_call['tool_name']
        parameters = tool_call['parameters']

        try:
            # Execute the appropriate query tool directly
            if tool_name == 'query_sql':
                return await self._execute_query_sql(parameters, query_index=query_index)
            elif tool_name == 'query_vector':
                return await self._execute_query_vector(parameters, query_index=query_index)
            elif tool_name == 'query_source':
                return await self._execute_query_source(parameters, query_index=query_index)
            else:
                return {
                    'success': False,
                    'error': f"Unknown query tool: {tool_name}",
                    'tool_call': tool_call
                }

        except Exception as e:
            import traceback
            logger.error(f"Error executing query tool {tool_name}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {
                'success': False,
                'error': str(e),
                'tool_call': tool_call
            }

    async def execute_single_query_tool(self, tool_call: Dict[str, Any], request_permission_callback) -> Dict[str, Any]:
        """Execute a single query tool call.

        Args:
            tool_call: Tool call dictionary
            request_permission_callback: Function to request user permission

        Returns:
            Execution result with loaded content
        """
        tool_name = tool_call['tool_name']
        parameters = tool_call['parameters']

        try:
            # Request user permission
            permission_result = await request_permission_callback(tool_name, parameters)

            if permission_result[0] == 'declined':
                return {
                    'success': False,
                    'declined': True,
                    'tool_call': tool_call,
                    'message': 'User declined the query'
                }

            # Handle modified query
            if permission_result[0] == 'modified':
                parameters = permission_result[1]

            # Execute the appropriate query tool
            if tool_name == 'query_sql':
                result = await self._execute_query_sql(parameters)
            elif tool_name == 'query_vector':
                result = await self._execute_query_vector(parameters)
            elif tool_name == 'query_source':
                result = await self._execute_query_source(parameters)
            else:
                return {
                    'success': False,
                    'error': f"Unknown query tool: {tool_name}",
                    'tool_call': tool_call
                }

            # Add query to context state
            if result['success']:
                query_id = str(uuid.uuid4())
                self.context_state['ai_queries'].append({
                    'id': query_id,
                    'type': tool_name,
                    'query': parameters.get('query', parameters.get('source', '')),
                    'reasoning': parameters.get('reasoning', ''),
                    'params': parameters,
                    'timestamp': datetime.now().isoformat()
                })
                result['query_id'] = query_id

            return result

        except Exception as e:
            import traceback
            logger.error(f"Error executing query tool {tool_name}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {
                'success': False,
                'error': str(e),
                'tool_call': tool_call
            }

    async def _execute_query_sql(self, parameters: Dict[str, Any], query_index: int = None) -> Dict[str, Any]:
        """Execute a SQL natural language query.

        Args:
            parameters: Query parameters including 'query', optional 'workspace', 'max_results'
            query_index: Index of query for labeled output (e.g., "Query 1", "Query 2")

        Returns:
            Result with loaded content (includes 'generated_sql' field for visibility)
        """
        from promaia.utils.display import print_text
        
        query = parameters.get('query')
        if not query:
            return {
                'success': False,
                'error': 'Missing required parameter: query'
            }

        workspace = parameters.get('workspace', self.context_state.get('workspace'))
        max_results = parameters.get('max_results')
        
        # Check if user explicitly specified sources (strict mode)
        explicitly_loaded_sources = self.context_state.get('sources', [])
        # Defensive: handle None value (can happen if sources is explicitly set to None)
        if explicitly_loaded_sources is None:
            explicitly_loaded_sources = []
        is_strict_mode = len(explicitly_loaded_sources) > 0
        
        if is_strict_mode:
            # Warn that we're expanding beyond explicit sources
            logger.warning(
                f"Query tool called in strict mode (user specified -s sources). "
                f"AI query: '{query}'. Explicitly loaded: {explicitly_loaded_sources}"
            )
            # Add warning to reasoning for user to see
            original_reasoning = parameters.get('reasoning', '')
            parameters['reasoning'] = (
                f"⚠️ EXPANDING BEYOND EXPLICIT SOURCES\n"
                f"You loaded: {', '.join(explicitly_loaded_sources)}\n"
                f"AI wants to search: {query}\n\n"
                f"{original_reasoning}"
            )
        
        # Create query label
        query_label = f"Query {query_index}" if query_index else "SQL Query"

        try:
            # Show that we're processing the natural language query with label
            print_text(f"🤖 [{query_label}] Processing natural language query...", style="cyan")
            
            # Process natural language query with metadata return
            result = process_natural_language_to_content(
                nl_prompt=query,
                workspace=workspace,
                verbose=False,
                skip_confirmation=True,  # Skip prompts during parallel execution
                return_metadata=True  # Get SQL query for display
            )

            # Defensive: handle unexpected return types
            if result is None:
                loaded_content = {}
                metadata = {}
            elif isinstance(result, tuple) and len(result) == 2:
                loaded_content, metadata = result
            else:
                # Unexpected return type
                logger.error(f"Unexpected return type from process_natural_language_to_content: {type(result)}")
                loaded_content = result if isinstance(result, dict) else {}
                metadata = {}

            # Defensive: handle None values
            if loaded_content is None:
                loaded_content = {}
            if metadata is None:
                metadata = {}

            # Count total pages (defensive: filter out None values)
            total_pages = sum(len(pages) for pages in loaded_content.values() if pages is not None)

            # If no results found, mark as warning (not failure, but user should know)
            if total_pages == 0:
                print_text(f"⚠️  [{query_label}] Query returned 0 results", style="yellow")
                return {
                    'success': True,
                    'zero_results': True,  # Flag for special handling in approval UI
                    'loaded_content': loaded_content,
                    'total_pages': 0,
                    'databases': list(loaded_content.keys()),
                    'query': query,
                    'workspace': workspace,
                    'generated_sql': metadata.get('generated_query'),
                    'query_mode': metadata.get('query_mode')
                }

            print_text(f"✅ [{query_label}] Query processed successfully", style="green")

            result_dict = {
                'success': True,
                'loaded_content': loaded_content,
                'total_pages': total_pages,
                'databases': list(loaded_content.keys()),
                'query': query,
                'workspace': workspace,
                'generated_sql': metadata.get('generated_query'),  # Include for display
                'query_mode': metadata.get('query_mode')
            }
            if metadata.get('direct_data'):
                result_dict['direct_data'] = True
            return result_dict

        except Exception as e:
            return {
                'success': False,
                'error': f"SQL query failed: {str(e)}"
            }

    async def _execute_query_vector(self, parameters: Dict[str, Any], query_index: int = None) -> Dict[str, Any]:
        """Execute a vector semantic search query.

        Args:
            parameters: Query parameters including 'query', optional 'workspace', 'top_k', 'min_similarity'
            query_index: Index of query for labeled output (e.g., "Query 1", "Query 2")

        Returns:
            Result with loaded content
        """
        query = parameters.get('query')
        if not query:
            return {
                'success': False,
                'error': 'Missing required parameter: query'
            }

        workspace = parameters.get('workspace', self.context_state.get('workspace'))
        # Large default top_k (60) for semantic search to cast a very wide net for fuzzy searches
        top_k = parameters.get('top_k', self.context_state.get('top_k', 60))
        # Very low default threshold (0.2) for semantic search to cast a wide net for fuzzy searches
        min_similarity = parameters.get('min_similarity', self.context_state.get('threshold', 0.2))
        
        # Check if user explicitly specified sources (strict mode)
        explicitly_loaded_sources = self.context_state.get('sources', [])
        # Defensive: handle None value (can happen if sources is explicitly set to None)
        if explicitly_loaded_sources is None:
            explicitly_loaded_sources = []
        is_strict_mode = len(explicitly_loaded_sources) > 0
        
        if is_strict_mode:
            # Warn that we're expanding beyond explicit sources
            logger.warning(
                f"Query tool called in strict mode (user specified -s sources). "
                f"AI query: '{query}'. Explicitly loaded: {explicitly_loaded_sources}"
            )
            # Add warning to reasoning for user to see
            original_reasoning = parameters.get('reasoning', '')
            parameters['reasoning'] = (
                f"⚠️ EXPANDING BEYOND EXPLICIT SOURCES\n"
                f"You loaded: {', '.join(explicitly_loaded_sources)}\n"
                f"AI wants to search: {query}\n\n"
                f"{original_reasoning}"
            )
        
        # Create query label
        query_label = f"Query {query_index}" if query_index else "Vector Search"
        
        from promaia.utils.display import print_text

        try:
            # Show that we're executing vector search with label
            print_text(f"🔍 [{query_label}] Executing vector search...", style="cyan")
            
            # Process vector search query
            loaded_content = process_vector_search_to_content(
                vs_prompt=query,
                workspace=workspace,
                n_results=top_k,
                min_similarity=min_similarity,
                verbose=False,
                skip_confirmation=True  # Skip prompts during parallel execution
            )

            # Defensive: handle None or unexpected return (shouldn't happen but does in error cases)
            if loaded_content is None:
                logger.warning(f"process_vector_search_to_content returned None for query: {query}")
                loaded_content = {}
            elif not isinstance(loaded_content, dict):
                logger.error(f"Unexpected return type from process_vector_search_to_content: {type(loaded_content)}")
                loaded_content = {}

            # Count total pages (defensive: filter out None values)
            total_pages = sum(len(pages) for pages in loaded_content.values() if pages is not None)

            # If no results found, mark as warning
            if total_pages == 0:
                print_text(f"⚠️  [{query_label}] Vector search returned 0 results", style="yellow")
                return {
                    'success': True,
                    'zero_results': True,  # Flag for special handling in approval UI
                    'loaded_content': loaded_content,
                    'total_pages': 0,
                    'databases': list(loaded_content.keys()),
                    'query': query,
                    'workspace': workspace,
                    'top_k': top_k,
                    'min_similarity': min_similarity
                }

            print_text(f"✅ [{query_label}] Complete: {total_pages} results", style="green")

            return {
                'success': True,
                'loaded_content': loaded_content,
                'total_pages': total_pages,
                'databases': list(loaded_content.keys()),
                'query': query,
                'workspace': workspace,
                'top_k': top_k,
                'min_similarity': min_similarity
            }

        except Exception as e:
            return {
                'success': False,
                'error': f"Vector search failed: {str(e)}"
            }

    async def _execute_query_source(self, parameters: Dict[str, Any], query_index: int = None) -> Dict[str, Any]:
        """Execute a direct source query.

        Args:
            parameters: Query parameters including 'source' (format: "database:days"),
                       optional 'workspace', 'filters'
            query_index: Index of query for labeled output (e.g., "Query 1", "Query 2")

        Returns:
            Result with loaded content
        """
        source = parameters.get('source')
        if not source:
            return {
                'success': False,
                'error': 'Missing required parameter: source'
            }

        workspace = parameters.get('workspace', self.context_state.get('workspace'))
        filters = parameters.get('filters', {})
        
        # Create query label
        query_label = f"Query {query_index}" if query_index else "Source Query"
        
        from promaia.utils.display import print_text

        try:
            # Show that we're loading from source with label
            print_text(f"📁 [{query_label}] Loading from source: {source}", style="cyan")
            
            # Parse source format: "database:days" or "database"
            if ':' in source:
                database_name, days_str = source.split(':', 1)
                days = int(days_str) if days_str != 'all' else None
            else:
                database_name = source
                days = None

            # If no workspace specified, check if there are multiple databases with this name
            # across different workspaces and load from ALL of them
            if not workspace:
                from promaia.config.databases import get_database_manager
                db_manager = get_database_manager()

                # Find all databases matching this name across workspaces
                matching_configs = []
                for db_name, db_config in db_manager.databases.items():
                    # Match by nickname or qualified name ending
                    if db_config.nickname == database_name or db_name.endswith(f".{database_name}"):
                        matching_configs.append(db_config)

                if not matching_configs:
                    return {
                        'success': False,
                        'error': f"Database '{database_name}' not found in any workspace"
                    }

                # If multiple databases match, load from ALL of them
                if len(matching_configs) > 1:
                    loaded_content = {}
                    total_pages = 0
                    database_list = []

                    for db_config in matching_configs:
                        pages = load_database_pages_with_filters(
                            database_config=db_config,
                            days=days,
                            property_filters=filters if filters else None
                        )

                        # Use qualified name (workspace.database) for multi-workspace results
                        qualified_name = f"{db_config.workspace}.{database_name}"
                        loaded_content[qualified_name] = pages
                        total_pages += len(pages)
                        database_list.append(qualified_name)

                    print_text(f"✅ [{query_label}] Complete: {total_pages} pages from {len(database_list)} databases", style="green")
                    
                    return {
                        'success': True,
                        'loaded_content': loaded_content,
                        'total_pages': total_pages,
                        'databases': database_list,
                        'source': source,
                        'workspace': 'multiple'  # Indicate multiple workspaces searched
                    }

                # Only one match, use it
                database_config = matching_configs[0]
            else:
                # Workspace specified, get specific config
                database_config = get_database_config(database_name, workspace)

            if not database_config:
                return {
                    'success': False,
                    'error': f"Database '{database_name}' not found in workspace '{workspace}'"
                }

            # Load pages from source
            pages = load_database_pages_with_filters(
                database_config=database_config,
                days=days,
                property_filters=filters if filters else None
            )

            # Format as multi_source_data with workspace prefix for clarity
            qualified_name = f"{database_config.workspace}.{database_name}"
            loaded_content = {qualified_name: pages}
            
            print_text(f"✅ [{query_label}] Complete: {len(pages)} pages loaded", style="green")

            return {
                'success': True,
                'loaded_content': loaded_content,
                'total_pages': len(pages),
                'databases': [qualified_name],
                'source': source,
                'workspace': workspace
            }

        except Exception as e:
            return {
                'success': False,
                'error': f"Source query failed: {str(e)}"
            }

    def format_query_results(self, results: List[Dict[str, Any]]) -> str:
        """Format query tool execution results for display.

        Args:
            results: List of execution results

        Returns:
            Formatted results string
        """
        formatted = "\n📊 Query Tool Results:\n\n"

        for i, result in enumerate(results, 1):
            if result.get('declined'):
                formatted += f"{i}. 🚫 Query declined by user\n"
                continue

            if result.get('skipped'):
                # Distinguish between auto-skipped (0 results) and user-skipped
                if result.get('auto_skipped'):
                    formatted += f"{i}. ⏭️  Query auto-skipped (0 results)\n"
                else:
                    formatted += f"{i}. ⏭️  Query skipped by user\n"
                continue

            if result.get('success'):
                # Get database names and workspace
                databases = result.get('databases', [])
                workspace = result.get('workspace', 'unknown')

                # Format database display with workspace prefix (only if not already qualified)
                if databases:
                    db_display = ', '.join([
                        db if '.' in db else f"{workspace}.{db}"
                        for db in databases
                    ])
                else:
                    db_display = 'unknown'

                formatted += f"{i}. ✅ {db_display}: "
                formatted += f"Loaded {result.get('total_pages', 0)} pages\n"

                if 'query' in result:
                    formatted += f"   Query: \"{result['query']}\"\n"
                elif 'source' in result:
                    formatted += f"   Source: {result['source']}\n"
            else:
                formatted += f"{i}. ❌ Query failed: {result.get('error', 'Unknown error')}\n"

        return formatted
