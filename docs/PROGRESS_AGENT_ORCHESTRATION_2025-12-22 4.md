# Agent Orchestration Implementation Progress

**Branch:** `promaia-agent-orchestration`
**Date:** 2025-12-22
**Status:** Foundation Complete, Ready for Agent Manager

## What We've Built

### ✅ Phase 1: Core Infrastructure (COMPLETE)

#### 1. Architecture Document
**File:** `docs/AGENT_ORCHESTRATION_ARCHITECTURE.md`

Complete specification of the three-way conversation architecture:
- User ↔ Promaia ↔ Claude Code Agent
- Intent-based routing (knowledge vs action)
- Group chat mode for agentic tasks
- Clear component breakdown

#### 2. Intent Classifier
**File:** `promaia/agent/intent_classifier.py`

Classifies user queries into three types:
- **KNOWLEDGE**: Information requests (Promaia handles)
- **ACTION**: Tasks requiring tools/MCPs (spawn agent)
- **CONTEXT**: Promaia commands (/e, /browse, etc.)

**Features:**
- Pattern-based classification using action verbs
- Detects specific action types (email, notion, calendar, etc.)
- Identifies required MCPs for each action
- Tested and working

**Test Results:**
```
"What did I work on yesterday?" → KNOWLEDGE
"Send Federico an email" → ACTION (email, requires gmail MCP)
"Create a Notion page" → ACTION (notion, requires notion MCP)
"/e journal:7" → CONTEXT
```

#### 3. Context Serializer
**File:** `promaia/agent/context_serializer.py`

Converts Promaia's loaded content into markdown format for Claude Code agents.

**Features:**
- Serializes database content with metadata
- Creates structured markdown with sections per database
- Generates complete system prompts for agents
- Includes context summary for user display
- Handles date ranges and key topic extraction

**Example Output:**
```markdown
# Context from Promaia

**Total entries loaded:** 3
**Databases:** 2

## Database: koii.journal (2 entries)

### Entry 1: 2024-01-15 - Monday
**Created:** 2024-01-15T09:00:00Z
**Properties:**
  - mood: productive
  - tags: work, api

Worked on the new API endpoints...
```

## What's Next

### Phase 2: Agent Manager (IN PROGRESS)

Next components to build:

#### 1. Agent Session Manager
**File:** `promaia/agent/agent_manager.py`

Will handle:
- Spawning Claude Code agents via Agent SDK
- Passing serialized context to agents
- Managing agent lifecycle (spawn, communicate, terminate)
- Session state tracking

**Key Classes:**
```python
class AgentSession:
    async def spawn(task: str, context: str)
    async def send_message(message: str)
    async def receive_messages() -> AsyncIterator
    async def terminate()

class AgentOrchestrator:
    async def handle_action_request(query: str, context: dict)
    async def group_chat_loop()
```

#### 2. Message Router
**File:** `promaia/agent/message_router.py`

Will handle:
- Parsing @mentions (@promaia, @claude)
- Routing messages to correct recipient
- Formatting messages for display
- Managing conversation flow

#### 3. Integration with Chat Interface
**File:** `promaia/chat/interface.py` (modify existing)

Will add:
- Intent classification in message handler
- Route to agent orchestrator for actions
- Maintain existing behavior for knowledge queries

## Testing Plan

### Test Scenario 1: Knowledge Query (Already Works)
```
User: "What did I work on yesterday?"
→ Promaia classifies as KNOWLEDGE
→ Promaia loads context (existing behavior)
→ Promaia answers directly (existing behavior)
✅ No changes needed
```

### Test Scenario 2: Simple Action (New)
```
User: "Send Federico an email saying hello"
→ Promaia classifies as ACTION (email)
→ Promaia spawns Claude Code agent
→ Agent receives context + task
→ Agent drafts and sends email
→ Returns to normal chat
```

### Test Scenario 3: Complex Action (New)
```
User: "Create a Notion page summarizing my week"
→ Promaia classifies as ACTION (notion)
→ Promaia loads journal:7, stories:7
→ Spawns agent with full context
→ Agent analyzes content
→ Agent creates Notion page
→ Agent shows preview
→ User approves
→ Done
```

## Architecture Breakthrough

The key insight we landed on:

**Promaia as Orchestrator**

```
User asks question
    ↓
Promaia determines: Knowledge or Action?
    ↓
Knowledge → Promaia answers (existing behavior, fast)
    ↓
Action → Spawn Claude Code agent with context
    ↓
Group chat mode: User ↔ Promaia ↔ Agent
    ↓
Task complete → Return to Promaia chat
```

**Why This Works:**

1. **Promaia owns the interface**: User stays in Promaia's CLI
2. **Brand clarity**: Users think "Promaia with powerful actions" not "Claude Code wrapper"
3. **Context control**: Promaia loads context FIRST, then hands to agent
4. **Flexible delegation**: Promaia decides when to bring in the big guns
5. **Lightweight by default**: Most queries don't need agent overhead

## Files Created

1. `docs/AGENT_ORCHESTRATION_ARCHITECTURE.md` - Complete spec
2. `docs/AGENT_SDK_FINDINGS_2025-12-22.md` - SDK investigation findings
3. `promaia/agent/intent_classifier.py` - Intent classification ✅
4. `promaia/agent/context_serializer.py` - Context formatting ✅
5. `promaia/agent/sdk_adapter_simple.py` - Simple SDK wrapper (from earlier)

## Ready for Tomorrow

With intent classification and context serialization complete, we're ready to build:

1. **Agent spawner** - Use the SDK to create Claude Code subprocesses
2. **Message router** - Handle the three-way conversation
3. **Integration** - Wire it into existing chat interface
4. **Testing** - Try it with real queries

The foundation is solid. The architecture is clear. Let's build the agent manager next!

## Branch Status

```bash
git status
# On branch promaia-agent-orchestration
#
# New files:
#   docs/AGENT_ORCHESTRATION_ARCHITECTURE.md
#   docs/AGENT_SDK_FINDINGS_2025-12-22.md
#   docs/PROGRESS_AGENT_ORCHESTRATION_2025-12-22.md
#   promaia/agent/intent_classifier.py
#   promaia/agent/context_serializer.py
#
# Ready to commit and continue building!
```
