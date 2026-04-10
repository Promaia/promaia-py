# Plan: Workflow System for Promaia

## Context

Users perform multi-step tasks through Promaia (e.g., Mitchell's part reorder flow). Today these are one-off — the user has to re-explain the process every time. We need a way to save, retrieve, and execute repeatable workflows. This is the final piece for the Glacier pilot MVP.

## Design Principles

- **Procedural first** — workflows are sequences of tool calls with parameterized steps, not fuzzy behavioral patterns
- **Post-hoc creation** — user does the task, then says "save that as a workflow." No "record" button needed
- **AI as the matching engine** — no regex triggers. Workflow names + descriptions live in the system prompt, the AI recognizes when one applies
- **Example runs are first-class** — stored alongside workflows, loaded into context to help future execution

---

## Storage

### SQLite Schema

New tables in the existing `hybrid_metadata.db` (or a dedicated `workflows.db`):

```sql
CREATE TABLE workflows (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL,
    steps TEXT NOT NULL,            -- JSON: [{description, tool, params_template, variable_params, notes}]
    workspace TEXT,                 -- workspace scope (null = global)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    tool_calls TEXT NOT NULL,       -- JSON: [{tool, params, result_summary}]
    outcome TEXT NOT NULL,          -- "success" / "partial" / "failed"
    notes TEXT,                     -- agent's observations about this run
    created_at TEXT NOT NULL
);
```

### Step Format

```json
{
    "description": "Look up part in parts list sheet to find vendor",
    "tool": "sheets_read_range",
    "params_template": {
        "spreadsheet": "Parts List",
        "range": "A:E"
    },
    "variable_params": ["query_text"],
    "notes": "The part name comes from user input. Filter results for the specific part."
}
```

- `params_template`: default/fixed parameters copied into the call
- `variable_params`: parameters the agent fills from context (user input, previous step results)
- `notes`: guidance for the agent on how to handle this step

### Example Run Format

```json
{
    "tool_calls": [
        {
            "tool": "sheets_read_range",
            "params": {"spreadsheet": "Parts List", "range": "A:E"},
            "result_summary": "Found part ABC-123, vendor: Acme Corp"
        },
        {
            "tool": "drive_search_files",
            "params": {"query": "ABC-123", "folder_id": "SPECS_FOLDER"},
            "result_summary": "Found ABC-123-spec-v2.pdf (updated 2026-03-20)"
        }
    ],
    "outcome": "success",
    "notes": "Completed reorder for part ABC-123, qty 50. Draft email created."
}
```

---

## Tools Exposed to Agent

### `create_workflow`

Create a new workflow definition. Optionally includes an example run from the conversation that was just performed.

```python
{
    "name": "create_workflow",
    "description": "Save a repeatable workflow. Provide steps generalized from a task the user performed, or from a description of what the workflow should do.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short workflow name (e.g., 'glacier-part-reorder')"},
            "description": {"type": "string", "description": "What this workflow does, in plain English"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "tool": {"type": "string"},
                        "params_template": {"type": "object"},
                        "variable_params": {"type": "array", "items": {"type": "string"}},
                        "notes": {"type": "string"}
                    },
                    "required": ["description"]
                }
            },
            "workspace": {"type": "string", "description": "Workspace scope (omit for global)"},
            "example_run": {
                "type": "object",
                "description": "Optional example run from the task just performed",
                "properties": {
                    "tool_calls": {"type": "array"},
                    "outcome": {"type": "string"},
                    "notes": {"type": "string"}
                }
            }
        },
        "required": ["name", "description", "steps"]
    }
}
```

### `list_saved_workflows`

List all stored workflows (name + description). Used by the agent to check what's available.

### `get_workflow_details`

Load full workflow definition + example runs for execution.

### `update_workflow`

Modify an existing workflow. Can update steps, description, or add an example run.

### `delete_workflow`

Remove a workflow and its example runs.

---

## Creation Flows

### Path 1: Post-hoc (user does task first)

1. User performs a multi-step task through chat
2. User says "save that as a workflow" or "turn this into a repeatable workflow"
3. Agent reviews the tool_use/tool_result blocks in chat history
4. Agent identifies the task boundary (may ask user to confirm if ambiguous — e.g., "I see three tasks in our conversation. Which one?")
5. Agent generalizes the tool calls: replaces specific values with variable names
6. Agent shows the workflow definition as an artifact for user review
7. User confirms or requests edits
8. Agent calls `create_workflow` with steps + the actual run as `example_run`

### Path 2: Descriptive (user describes without doing)

1. User says "create a workflow for reordering parts" and describes the steps
2. Agent designs the workflow steps based on description + available tools
3. Agent shows the definition as an artifact for review
4. User confirms
5. Agent calls `create_workflow` without `example_run`
6. On next execution, agent asks "want me to save this as an example run?"
7. If yes, agent calls `update_workflow` with `add_example_run`

### Handling multiple tasks in one conversation

When user says "turn the second thing we did into a workflow" or "save all three as separate workflows":
- Agent reviews chat history and identifies task boundaries by tool call patterns
- For ambiguous cases, agent proposes what it thinks the boundaries are and asks for confirmation
- Creates each workflow separately via `create_workflow`
- This is pure AI reasoning over chat context — no special machinery

---

## Execution Flow

### How the agent knows about workflows

Workflow names + descriptions are loaded into the system prompt (like we do with interview workflows). The `build_agentic_system_prompt()` function queries SQLite for all workflows and adds:

```
## Saved Workflows

You have the following saved workflows available. When you recognize a task
that matches a workflow, tell the user and ask if they'd like you to follow it.

- **glacier-part-reorder**: Reorder a custom part from a vendor. Searches specs, generates PO, drafts email.
- **weekly-report**: Generate weekly summary from journal and email, post to Slack.
```

### When a workflow matches

1. Agent recognizes user's request matches a workflow
2. Agent says "I have a workflow for this — want me to follow it?"
3. If yes, agent calls `get_workflow_details` to load full steps + example runs
4. Agent executes steps in order, adapting as needed (steps are guidance, not rigid)
5. After completion, agent asks if user wants to save this as an example run
6. If yes, calls `update_workflow` with `add_example_run`

### Workflow execution is flexible

Steps are guidance, not a rigid script. The agent:
- Follows the step order as a default
- Can skip steps that aren't needed
- Can add extra steps if the situation requires
- Uses `variable_params` to know what to fill in from context
- References example runs to understand expected inputs/outputs
- Deviations from the template are noted in the run record

---

## System Prompt Integration

In `build_agentic_system_prompt()` (agentic_adapter.py), add a section that loads workflow summaries:

```python
# Load saved workflows for prompt
try:
    from promaia.tools.workflow_store import list_workflows_for_prompt
    wf_summaries = list_workflows_for_prompt(workspace)
    if wf_summaries:
        lines = [
            "## Saved Workflows\n",
            "You have saved workflows available. When you recognize a task "
            "that matches a workflow, tell the user and ask if they'd like "
            "you to follow it. Use `get_workflow_details` to load the full "
            "steps before executing.\n",
        ]
        for wf in wf_summaries:
            lines.append(f"- **{wf['name']}**: {wf['description']}")
        filled += "\n\n" + "\n".join(lines)
except Exception:
    pass
```

---

## Files to Create/Modify

| File | Change |
|------|--------|
| `promaia/tools/workflow_store.py` | **NEW** — SQLite storage: create/read/update/delete workflows + runs, `list_workflows_for_prompt()` |
| `promaia/agents/agentic_turn.py` | Add 5 workflow tool definitions + executor methods + routing |
| `promaia/chat/agentic_adapter.py` | Load workflow summaries into system prompt |

## Verification

1. In `maia chat`, perform a multi-step task (e.g., search Drive, download file, create draft)
2. Say "save that as a workflow called test-workflow"
3. Agent should show the generalized workflow as an artifact, confirm, then call `create_workflow`
4. Say "list my workflows" — should show test-workflow
5. Start a new chat, say "run the test-workflow" — agent should recognize it, load details, execute steps
6. After execution, agent should offer to save the run as an example
7. Delete with "delete the test-workflow" — verify cleanup
