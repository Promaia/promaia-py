# Promaia Roadmap

Last updated: 2026-03-01

---

## Current State

Promaia agents are now **agentic through Discord and Slack** — they can receive @mentions, hold multi-turn conversations, and use tools (query data, send emails, create calendar events, write to Notion, etc.) within a live chat thread.

A **planning layer** has been added so complex multi-part requests get decomposed into steps before execution.

---

## Priority 1: Make the Agent Good at Using Tools

**Status**: In progress
**Why**: This is the go-to-market differentiator. The agent has tools but uses them poorly — vague queries, giving up after one failed search, not knowing when to use SQL vs vector vs source loading. Teaching it to query well is the highest-leverage improvement.

### Done
- [x] Agentic tool loop (query_sql, query_vector, query_source, write tools)
- [x] Tool activity breadcrumb in Discord/Slack threads
- [x] Planning layer for multi-step requests
- [x] Externalized conversation system prompt to `prompts/conversation_mode.md`

### Next
- [ ] **Query strategy guide in the prompt** — teach the agent how to write good queries, when to use which tool, what to do when results are empty, how to chain queries
- [ ] **Context-aware planning** — before generating a plan, run lightweight reconnaissance queries to ground the plan in real data (not just the user's words)
- [ ] **Query visibility** — show what the agent is actually querying in the thread breadcrumb (currently shows tool name but not the query text)
- [ ] **Iterate on prompt with real conversations** — test, read context logs, adjust `conversation_mode.md`, repeat

---

## Priority 2: Notion Block Formatting

**Status**: Not started
**Why**: When the agent creates or updates Notion pages, it dumps everything into a single paragraph block instead of using proper Notion block structure (headings, paragraphs, lists, etc.). This makes the output look robotic.

### Tasks
- [ ] Parse markdown content into proper Notion block types (heading_1, heading_2, paragraph, bulleted_list_item, etc.)
- [ ] Update `_notion_create_page()` and `_notion_update_page()` in `agentic_turn.py` to use block-aware content creation
- [ ] Handle nested lists, code blocks, and other markdown elements

---

## Priority 3: Deeper Agentic Capabilities

**Status**: Exploring
**Why**: The current agent reacts to what the user says. The next level is proactive intelligence — understanding context before being asked.

### Ideas
- [ ] **Reconnaissance before planning** — when user says "reply to Marina", search for Marina's recent emails BEFORE making a plan, so the plan can say "Reply to Marina's Feb 27 email about Open Editions pricing" instead of just "Reply to Marina"
- [ ] **Proactive context loading** — when a conversation starts, load the most likely relevant context based on who's talking and what channel it's in
- [ ] **Learning from corrections** — when the user says "no, I meant X", record that as a learning for next time
- [ ] **Cross-conversation memory** — carry forward insights from previous conversations (not just journal entries)

---

## Priority 4: Platform & UX Polish

**Status**: Ongoing

### Tasks
- [ ] Thread title generation (already partially working)
- [ ] Countdown/typing interrupt handling (simplified in current branch)
- [ ] Agent creation flow improvements
- [ ] Scheduled agent improvements (calendar triggers, goal decomposition)

---

## Backlog

- [ ] Multi-agent delegation (agent A asks agent B for help)
- [ ] Streaming responses (currently waits for full response before posting)
- [ ] Voice interface integration
- [ ] Mobile app / web dashboard for conversation history
- [ ] Rate limiting and cost tracking per agent
- [ ] User-configurable tool permissions (allow/deny specific actions per agent)

---

## Vision Note

From KOii's thought stash:

> What if Promaia didn't just sync the content but it watched how users work and understood how they behave across all these platforms. Then it wouldn't just understand the content — it would understand the entire digital presence of work itself. I think we're about to realize that most work happens in people's heads.

This is the north star. Promaia isn't a chatbot with tools — it's an intelligence that understands how you work.
