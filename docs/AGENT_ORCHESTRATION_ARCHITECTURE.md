# Promaia Agent Orchestration Architecture

**Branch:** `promaia-agent-orchestration`
**Date:** 2025-12-22
**Status:** In Development

## Vision

Promaia orchestrates a three-way conversation between:
1. **User** - asks questions, gives commands, controls context
2. **Promaia** - fast context loading, knowledge queries, orchestration
3. **Claude Code Agent** - agentic actions (MCPs for Gmail/Notion/etc.)

## Core Principle

```
Knowledge Request → Promaia answers directly (current behavior)
Action Request    → Promaia spawns Claude Code agent with context
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                         USER                                │
│  Commands: /e, /browse, regular chat, @promaia, @claude    │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                    PROMAIA (Orchestrator)                   │
│                                                             │
│  [Intent Classifier]                                        │
│      ├─ Knowledge? → Handle directly                       │
│      └─ Action?    → Spawn Claude Code Agent               │
│                                                             │
│  [Context Manager]                                          │
│      ├─ Load databases (fast SQL/vector queries)           │
│      ├─ Serialize context for agents                       │
│      └─ Maintain conversation state                        │
│                                                             │
│  [Agent Manager]                                            │
│      ├─ Spawn Claude Code subprocess when needed           │
│      ├─ Pass context to agent                              │
│      └─ Route messages between user/agent                  │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼ (when action needed)
┌─────────────────────────────────────────────────────────────┐
│              CLAUDE CODE AGENT (Subprocess)                 │
│                                                             │
│  Receives: Promaia's loaded context + user request          │
│  Has access to:                                             │
│    - Gmail MCP (send/read emails)                          │
│    - Notion MCP (create/update pages)                      │
│    - Other MCPs as configured                              │
│  Returns: Action results back to Promaia                    │
└─────────────────────────────────────────────────────────────┘
```

## User Experience Flow

### Scenario 1: Knowledge Request (Promaia handles directly)

```
User: "What did I write about yesterday?"

Promaia: [Analyzing: Knowledge request]
Promaia: [Loading journal:1...]
Promaia: [Loaded 3 entries]

Promaia: Yesterday you wrote about:
1. Morning standup notes
2. Feature planning for the new API
3. Evening reflection on the launch timeline

User: [Continues chatting with Promaia]
```

### Scenario 2: Action Request (Spawn agent)

```
User: "Send Federico an email about the launch timeline"

Promaia: [Analyzing: Action request detected]
Promaia: [Loading gmail:7, journal:3, stories:7...]
Promaia: [Loaded 45 pages of context]
Promaia: This requires email access. Spawning Claude Code agent...

─────────────── GROUP CHAT MODE ───────────────

[Promaia → Claude Agent]
Promaia: I've loaded the user's recent emails and project notes.
         Relevant context:
         - 12 email threads with Federico
         - Journal entry from yesterday about timeline concerns
         - Stories database shows "launch" milestone due in 2 weeks

         Task: Draft an email to Federico about the launch timeline.

[Claude Agent → User]
Claude Agent: I'll draft an email for Federico. Let me check the recent thread first.
Claude Agent: [Using tool: mcp__gmail__list_messages]
Claude Agent: [Using tool: mcp__gmail__get_message]
Claude Agent:
              Based on your last exchange, here's a draft:

              Subject: RE: Launch Timeline Update

              Hi Federico,

              Following up on our discussion - based on current progress,
              we're tracking well for the 2-week launch window...

              Would you like me to send this, or make changes?

User: Make it more casual and mention the API work

[Claude Agent]
Claude Agent: Updated draft:

              Hey Federico,

              Quick update on launch - we're looking good for the 2-week
              target! The new API work is coming together nicely...

              Ready to send?

User: Yes send it

[Claude Agent]
Claude Agent: [Using tool: mcp__gmail__send_message]
Claude Agent: ✓ Email sent to Federico

Promaia: Email sent successfully. Returning to chat mode.

─────────────── BACK TO NORMAL ───────────────

User: [Continues chatting with Promaia]
```

## Components to Implement

### 1. Intent Classifier

**File:** `promaia/agent/intent_classifier.py`

```python
from enum import Enum
from typing import Optional

class IntentType(Enum):
    KNOWLEDGE = "knowledge"  # Query, question, information request
    ACTION = "action"        # Task requiring external tools/MCPs
    CONTEXT = "context"      # Promaia commands (/e, /browse, etc.)

def classify_intent(query: str) -> IntentType:
    """
    Classify user intent as knowledge, action, or context command.

    Knowledge: "What did I...", "Show me...", "Tell me about..."
    Action: "Send...", "Create...", "Schedule...", "Update..."
    Context: "/e ...", "/browse", "/clear"
    """
    # Simple heuristic-based classification
    # Later: Use Claude to classify if unclear
    pass

def detect_action_type(query: str) -> Optional[str]:
    """
    If action intent, detect which MCP/tool is needed.

    Returns: "gmail", "notion", "bash", etc.
    """
    pass
```

### 2. Context Serializer

**File:** `promaia/agent/context_serializer.py`

```python
def serialize_context_for_agent(loaded_content: dict) -> str:
    """
    Convert Promaia's loaded content into a format Claude Code can use.

    Args:
        loaded_content: Dict mapping database names to lists of pages

    Returns:
        Formatted string with context for Claude Code
    """
    # Format: markdown with clear sections per database
    pass

def create_agent_system_prompt(context: str, task: str) -> str:
    """
    Create system prompt for Claude Code agent with Promaia's context.
    """
    pass
```

### 3. Agent Manager

**File:** `promaia/agent/agent_manager.py`

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

class AgentSession:
    """Manages a single Claude Code agent session."""

    def __init__(self, context: dict):
        self.context = context
        self.client: Optional[ClaudeSDKClient] = None
        self.active = False

    async def spawn(self, task: str):
        """Spawn Claude Code agent with context."""
        pass

    async def send_message(self, message: str):
        """Send message to agent."""
        pass

    async def receive_messages(self):
        """Receive messages from agent."""
        pass

    async def terminate(self):
        """Clean up agent session."""
        pass


class AgentOrchestrator:
    """Orchestrates conversations between user, Promaia, and agents."""

    def __init__(self):
        self.current_agent: Optional[AgentSession] = None
        self.group_chat_mode = False

    async def handle_action_request(self, query: str, context: dict):
        """Spawn agent and enter group chat mode."""
        pass

    async def group_chat_loop(self):
        """Run the three-way conversation loop."""
        pass
```

### 4. Message Router

**File:** `promaia/agent/message_router.py`

```python
class MessageRouter:
    """Routes messages between user, Promaia, and agents."""

    def parse_user_input(self, input: str) -> tuple[str, str]:
        """
        Parse user input to determine target and message.

        Examples:
            "@promaia show me more" → ("promaia", "show me more")
            "@claude make it shorter" → ("claude", "make it shorter")
            "looks good" → ("agent", "looks good")  # default to active agent
        """
        pass

    def format_agent_message(self, role: str, message: str) -> str:
        """Format message for display in group chat."""
        pass
```

### 5. Integration with Existing Chat Interface

**File:** `promaia/chat/interface.py` (modify existing)

```python
# Add to existing chat interface

from promaia.agent.intent_classifier import classify_intent, IntentType
from promaia.agent.agent_manager import AgentOrchestrator

class PromaiaChat:
    def __init__(self, ...):
        # ... existing init ...
        self.orchestrator = AgentOrchestrator()

    async def handle_message(self, user_input: str):
        # Classify intent
        intent = classify_intent(user_input)

        if intent == IntentType.KNOWLEDGE:
            # Existing Promaia chat behavior
            return await self.generate_response(user_input)

        elif intent == IntentType.ACTION:
            # Spawn agent and enter group chat
            return await self.orchestrator.handle_action_request(
                user_input,
                self.loaded_content
            )

        elif intent == IntentType.CONTEXT:
            # Existing command handling
            return await self.handle_command(user_input)
```

## Implementation Phases

### Phase 1: Core Infrastructure (Day 1)
- ✅ Agent SDK installed and tested
- ⬜ Intent classifier (simple heuristics)
- ⬜ Context serializer
- ⬜ Agent spawner (basic)

### Phase 2: Group Chat (Day 2)
- ⬜ Message router with @mentions
- ⬜ Group chat UI in CLI
- ⬜ Agent session management

### Phase 3: Integration (Day 3)
- ⬜ Hook into existing chat interface
- ⬜ Test with real queries
- ⬜ Refinement based on usage

### Phase 4: Polish (Day 4+)
- ⬜ Better intent classification (use Claude)
- ⬜ Agent memory/context updates
- ⬜ Multi-agent support (multiple agents at once)
- ⬜ Agent switching/handoff logic

## Testing Strategy

### Test Cases

1. **Knowledge Query**
   - Input: "What did I work on yesterday?"
   - Expected: Promaia answers directly, no agent spawn

2. **Simple Action**
   - Input: "Send Federico an email saying hello"
   - Expected: Agent spawns, drafts email, sends

3. **Complex Action**
   - Input: "Create a Notion page summarizing my week"
   - Expected: Agent uses Notion MCP with Promaia's context

4. **Group Chat Interaction**
   - Input: "@promaia load more context" (during agent session)
   - Expected: Promaia loads more, passes to agent

5. **Agent Switching**
   - Input: "@promaia you handle this instead"
   - Expected: Agent terminates, Promaia takes over

## Success Metrics

1. **User stays in Promaia**: Never feels like they "left" for Claude Code
2. **Seamless handoff**: Context flows smoothly to agent
3. **Clear roles**: User always knows who's "talking"
4. **Fast actions**: Agent spawns quickly, no lag
5. **Brand clarity**: Users think "Promaia + powerful actions" not "Claude Code wrapper"

## Open Questions

1. **Agent reuse**: Keep agent alive between queries or spawn fresh each time?
2. **Multi-agent**: Can multiple agents work on different tasks simultaneously?
3. **Context updates**: When agent learns something new, update Promaia's context?
4. **Fallback**: What if agent fails? Does Promaia take over?
5. **Cost**: Agent usage costs money - how to manage/warn users?

## Next Steps

1. Implement intent classifier
2. Build context serializer
3. Create agent spawner
4. Wire up to existing chat interface
5. Test with simple queries
6. Iterate based on user experience
