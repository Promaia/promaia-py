# Promaia Agentic Agents - Quick Start Guide

## Overview

Promaia agents now use the Claude Agent SDK with external MCP server for full agentic capabilities.

## What Changed

### Before: Limited Custom Loop
- Only 3 query tools (query_sql, query_vector, query_source)
- No file operations
- No web access
- Fixed 3 iterations
- MCP tools configured but never used

### After: Full Agentic SDK
- ✅ 3 Promaia query tools (via external MCP)
- ✅ File operations (Read, Write, Edit, Bash)
- ✅ Web research (WebSearch, WebFetch)
- ✅ File search (Glob, Grep)
- ✅ Additional MCP servers (gmail, notion, etc.)
- ✅ Configurable max turns (default 5)
- ✅ Full autonomous execution

## Agent Capabilities

### Promaia Query Tools (Primary)

**query_sql(query, reasoning)**
- Search for EXACT TEXT/KEYWORDS
- Example: `query_sql(query='tasks in current sprint', reasoning='need sprint status')`
- Use when: You know the keywords to search for

**query_vector(query, reasoning, top_k=50, min_similarity=0.2)**
- Semantic/conceptual search
- Example: `query_vector(query='team morale discussions', reasoning='understanding dynamics')`
- Use when: Searching by concept or theme

**query_source(database, days)**
- Direct database loading with time filter
- Example: `query_source(database='journal', days=30)` for last 30 days
- Use when: Need specific database with time range

### Built-in Tools

**File Operations:**
- `Read`: Read file contents
- `Write`: Create new files
- `Edit`: Modify existing files
- `Bash`: Execute shell commands

**Search Tools:**
- `Glob`: Find files by pattern
- `Grep`: Search text in files

**Web Tools:**
- `WebSearch`: Search the internet
- `WebFetch`: Fetch web pages

## Configuration

### Enable SDK for Agent

Edit agent config (`promaia.config.json`):

```json
{
  "name": "Chief of Staff",
  "workspace": "koii",
  "databases": ["journal:7", "stories:all", "gmail:7"],
  "sdk_enabled": true,
  "sdk_permission_mode": "bypassPermissions",
  "max_iterations": 5,
  "mcp_tools": []
}
```

### Key Fields

- `sdk_enabled`: `true` to use SDK, `false` for legacy mode
- `sdk_permission_mode`: `"bypassPermissions"` for autonomous operation
- `max_iterations`: Maximum agentic turns (default 5)
- `mcp_tools`: List of additional MCP servers (e.g., `["gmail", "notion"]`)

## Example Agent Behavior

### Scenario: "Review sprint progress"

**Turn 1:**
```
Agent: "I'll query for current sprint tasks"
Tool: query_sql(query='tasks in current sprint', reasoning='need all sprint tasks')
Result: 25 tasks returned
```

**Turn 2:**
```
Agent: "Let me check recent journal entries for context"
Tool: query_source(database='journal', days=7)
Result: 10 journal entries
```

**Turn 3:**
```
Agent: "I'll search emails for any blockers mentioned"
Tool: query_vector(query='project blockers or delays', reasoning='finding issues')
Result: 5 relevant emails
```

**Turn 4:**
```
Agent: "Creating summary..."
Agent returns: Complete sprint analysis with:
- 25 tasks: 15 in progress, 7 completed, 3 blocked
- Key blockers: API integration, design review
- Recommendations: ...
```

## System Instructions

When writing agent instructions, reference the query tools:

```markdown
# Your Role
You are Chief of Staff for the team.

# Your Data
You have access to:
- **journal**: Team journal entries
- **stories**: Project tasks and stories
- **gmail**: Email communications

# How to Query
Use these tools to access your data:
- query_sql: Search for specific keywords
- query_vector: Find content by concept
- query_source: Load database with time range

# Your Tasks
1. Monitor sprint progress
2. Identify blockers
3. Suggest improvements
4. Write summary to Notion
```

## Testing

### Test MCP Server Standalone
```bash
python test_mcp_server_standalone.py
```

### Test SDK Integration
```bash
python test_sdk_with_external_mcp.py
```

### Test Executor Config
```bash
python test_executor_sdk_config.py
```

### Test Full Execution
```bash
python test_agent_with_sdk.py
```

## Troubleshooting

### Agent not querying data
- Check `sdk_enabled: true` in config
- Verify agent instructions mention query tools
- Review agent logs for errors

### MCP server not starting
- Check Python path in executor._build_sdk_options()
- Verify query_tools_server.py exists
- Test standalone: `python -m promaia.mcp.query_tools_server --workspace koii`

### Subprocess buffer issues
- Initial message should be ~2KB (summary only)
- Don't send full context in initial message
- Let agent query for data as needed

### Query tools not available
- Check allowed_tools list includes query_sql, query_vector, query_source
- Verify mcp_servers includes promaia_tools
- Test SDK config: `python test_executor_sdk_config.py`

## Best Practices

### 1. Write Clear Instructions
```markdown
# Good
"Use query_sql to find tasks in current sprint, then analyze blockers"

# Bad
"Look at the tasks" (too vague, agent won't know how)
```

### 2. Let Agent Query As Needed
```markdown
# Good
"Your data sources: journal:7, stories:all, gmail:7. Query as needed."

# Bad
"Here's 1MB of context..." (causes buffer issues)
```

### 3. Use Appropriate Tools
- query_sql: Known keywords ("emails from Federico")
- query_vector: Concepts ("team morale discussions")
- query_source: Time-based loading ("journal:30 for more history")

### 4. Set Reasonable Max Iterations
- Simple tasks: 3-5 turns
- Complex tasks: 5-10 turns
- Very complex: 10-15 turns

### 5. Monitor Token Usage
Agents use more tokens than custom loop:
- Custom loop: ~50K tokens per run
- SDK agents: ~100-200K tokens per run
- Adjust max_iterations to control cost

## Architecture

```
Calendar Event Triggers Agent
    ↓
AgentExecutor.execute()
    ↓
1. Load context SUMMARY (not full data)
    - "journal: 10 entries"
    - "stories: 305 entries"
    ↓
2. Configure SDK:
    - External Promaia MCP server
    - Built-in tools (Read, Write, Bash, etc.)
    - Additional MCPs (gmail, notion, etc.)
    ↓
3. SDK Agent Loop (autonomous):
    - Agent analyzes task
    - Agent queries for data: query_sql(...)
    - Agent receives results
    - Agent uses other tools as needed
    - Agent knows when done
    ↓
4. Write output to Notion
    ↓
5. Log metrics and journal entry
```

## Migration from Legacy

### Feature Flag
Agents can use SDK or legacy mode:
- `sdk_enabled: true` → SDK with full capabilities
- `sdk_enabled: false` → Legacy custom loop

### Gradual Rollout
1. Test with one agent first
2. Compare outputs between SDK and legacy
3. Roll out to all agents once stable

### Backward Compatibility
All existing functionality preserved:
- ✅ Notion integration
- ✅ Metrics tracking
- ✅ Journal logging
- ✅ Calendar triggers

## Additional MCP Servers (Future)

To enable gmail, notion, or other MCP servers:

```json
{
  "mcp_tools": ["gmail", "notion"]
}
```

Requires `mcp_servers.json` in project root:

```json
{
  "mcpServers": {
    "gmail": {
      "command": "mcp-server-gmail",
      "args": []
    },
    "notion": {
      "command": "mcp-server-notion",
      "args": []
    }
  }
}
```

## Resources

- **Implementation Status**: `IMPLEMENTATION_STATUS.md`
- **Original Plan**: See user's plan document
- **MCP Server**: `promaia/mcp/query_tools_server.py`
- **Executor**: `promaia/agents/executor.py`
- **Tests**: `test_*.py` files in project root

## Support

For issues or questions:
1. Check `IMPLEMENTATION_STATUS.md` for known limitations
2. Run test suite to diagnose problems
3. Review agent logs for execution details
4. Check Notion journal for agent activity
