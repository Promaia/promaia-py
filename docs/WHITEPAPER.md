# Promaia

Software is no longer about buttons. It's about what you know, when you know it, and what gets done because of it.

Promaia is the memory and execution layer for the AI era — built for teams who are done explaining context to every tool they use, and done doing work that a machine should be handling.

---

There is no UI other than a couple super simple terminal flows that are as user friendly as Claude Code. Other than that, you only interact with — and even configure — Promaia through chatting. It can edit its own configurations and even push changes to itself.

You just use Promaia by using all the tools you already have. Notion, Gmail, Calendar, and Sheets, Discord, Slack, and Shopify, with more coming soon, and an open source plugin template available on our GitHub.

Promaia is three layers.

---

## 1. Memory — Anything in context in two seconds

We built the most powerful LLM chat app in the world. Anything from your entire workspace — entire Notion sprints, Slack messages, email threads, even your bill of materials — into context in under two seconds. Any data shape, you can just reach out and grab it.

It's scraped and stored securely on our servers or yours, vectorized, and organized into a beautifully structured PostgreSQL database built for agents to navigate. We're not just thinking about UX — we're building a state of the art AIX (Agent Interface Experience) and giving agents an ease of accessibility to data as supercharged as they are.

There's no difference between a Discord message and a Slack message — so why should an agent have to go to two different places and make `n` MCP tool calls to reach them? Try getting a week of relevant context across all your apps into one place. We'll see you next week.

### The Memory Model

We have built a novel memory model that makes retrieval effortless through a hierarchical attachment system — even for information that isn't semantically adjacent to your query.

Imagine you send a Discord message with a link. At sync time, Promaia creates an entry for that message with attachments: body, sent_time, sender_id. Then it deterministically checks for links, locations, people mentioned — anything worth knowing. What it chooses to remember is up to you.

```
Discord Message Entry
├── body
│   ├── links → link metadata
│   ├── locations → location details
│   ├── mentioned people
│   └── customizable extracted data
├── sent_time → temporal context
└── sender_id → relationship data
```

The key: what gets extracted and remembered in the metadata layer is configurable based on your use case.

If you're a real estate company, Promaia focuses on locations. If you're running an assistant agent, it notices you mentioned a great meal at Himalayan Cuisine SF — and later, when someone else mentions Polk Street, Promaia connects them. Normally those wouldn't correlate. "Himalayan Cuisine SF" and "Polk" have very low semantic similarity — likely 0.05–0.20 on a 0–1 cosine scale.

Promaia automatically asks base-level questions about every entry — what, when, where, how — and stores the answers as attached metadata. This dramatically expands the semantic surface area of each entry, so searches find what you mean, not just what you typed.

We call this **semantic expansion: aim assist for vector search.**

### This is MCP on steroids

Think of it like GitHub for your context. You work on a local branch — your data stays yours, versioned, queryable, always current. That's why Promaia agents are exceptional at execution: they get exactly what they need into working memory, and the rest is data translation — something AI does better than any human.

Every source — Notion databases, Gmail threads, Discord channels, Slack conversations, Shopify orders — gets synced into a unified substrate. Three storage layers, each optimized for a different job:

| Layer | Purpose | Example |
| --- | --- | --- |
| Markdown | Human-readable content, git-friendly | Load 7 days of journal entries for an agent |
| SQL | Structured metadata, fast filtering | "All emails from Alice about the launch" |
| Vector | Semantic search, meaning-based retrieval | "Find anything related to our pricing strategy" |

When you run `maia chat -s journal:7 -s gmail:30`, SQL filters by date, markdown loads for context, and vector search catches anything you'd miss with keywords alone. When an agent runs, it assembles context from all three layers automatically.

Most platforms pick one storage model and make everything fit. Promaia uses the right storage for each job — which is why a Notion database with custom properties and a Gmail inbox with threaded conversations can both feel native.

---

## 2. Agents — Your team, running on your calendar

We built an agent framework on top of this next-generation context architecture.

These agents can pull anything they need from across your entire database as easily as Claude Code greps for a function invocation.

They know exactly what to retrieve because they're presented with database schema previews and sample entries. And they continuously improve through *dreaming* — a process where, across every query, result, response, and user reaction, they take notes in their own journals, getting progressively better at navigating your context over time.

### Agents use the software you already have

This is the part people don't expect. Promaia agents don't live in some new app you have to check. They live inside the tools your team already uses every day.

**Google Calendar is your control plane.**

When you create an agent, it appears as a recurring event on your Google Calendar. A blue event with a robot emoji. You schedule it like you'd schedule a meeting with a team member — because that's what it is.

```
Mon-Fri  9:00 AM   🤖 daily-standup
                    Creates your daily summary from journal, emails, and messages

Friday   4:00 PM   🤖 weekly-report
                    Compiles the week's achievements and sends to team

Tuesday  2:00 PM   🤖 competitor-analysis
                    Research and summarize competitor announcements from this week
```

Drag and drop to reschedule. Skip an occurrence. Change the recurrence. All from Google Calendar — on your phone, on your laptop, wherever you already manage your time.

**Notion is where agents think.**

Agent output writes directly to Notion pages. Their journals — the notes they take while dreaming and learning your data — live in Notion too. You review an agent's work the same way you'd review a teammate's: open the page, read what they wrote, leave a comment.

**Slack and Discord are where agents talk.**

Agents initiate conversations in your team's channels. A calendar event fires, the agent posts to #engineering: "Ready to discuss sprint progress?" Your team replies. The agent responds with context it pulled from across your entire workspace. Multi-turn, natural conversation — with timeouts, security validation, and full audit logging.

**Gmail is where agents draft.**

The mail system syncs your inbox, classifies threads, and drafts responses with full workspace context. It learns your writing style per workspace — professional tone for client emails, casual for internal — through a rolling index of your last 20 successful responses. You review, edit, send.

**Sheets and Shopify are where agents operate.**

Agents query your Google Sheets for bills of materials, component costs, and vendor data — then cross-reference against live Shopify inventory and sales. An agent can pull your BOM, check which SKUs are running low, compare against recent order velocity, and flag what needs reordering — all without anyone opening a spreadsheet.

### How agents work

The execution pipeline is a four-stage system:

```
Goal (user request or calendar trigger)
  ↓
Planner (decompose into tasks — pattern matching for common flows, Claude for complex ones)
  ↓
Orchestrator (manage dependencies, track status, handle async completions)
  ↓
Executor (run each task via MCP tools — Gmail, Calendar, Notion, query tools)
  ↓
Result (written to Notion, posted to Slack/Discord, logged locally)
```

**Goals** decompose into **tasks**. Tasks have dependencies — some run in parallel, some wait for others. The orchestrator tracks all of it. If a task is a conversation (waiting for a human reply in Slack), it parks and resumes when the reply arrives.

Each agent is defined by:

- A **prompt file** — a markdown document that defines its persona, responsibilities, and communication style
- An **initial context** — which sources it can access, with time ranges (e.g., `journal:7, gmail:30, stories:all`) or more complex initial queries like `-sql the current sprint`
    - This turns natural language into SQL to pull complex data shapes into context
- A **permission scope** — which sources and tools it's allowed to touch, with least-privilege defaults
- A **schedule** — when it runs, as Google Calendar events in its own dedicated calendar
    - It doesn't just accept tasks from your inbox or your intern
- **MCP tools** — what actions it can take (send email, create Notion pages, query databases)
- **Messaging platforms** — where it talks (Slack channel, Discord channel, or terminal)

Here's what a real agent looks like. This is Grace, the Chief of Staff agent:

```
Prompt:     chief_of_staff.md — "You are the Chief of Staff for the team.
            Your purpose is to ensure every team member thrives..."
Databases:  journal:7, stories:all, gmail:7, discord:7
Schedule:   Monday and Wednesday at 9:00 AM
Output:     Writes weekly momentum reports to a Notion page
Channel:    Posts check-ins to #team on Slack
```

When Monday 9 AM hits, the calendar monitor daemon detects the event, fires the agent, loads a week of journal entries and stories and emails and Discord messages into context, and Grace writes a team momentum report — highlights, blockers, recommendations — and posts it to Slack for the team to discuss.

No one opened an app. No one typed a prompt. The agent ran on a calendar event, used data from five sources, wrote to Notion, and started a conversation in Slack. All using software the team already had installed.

### Dreaming

Agents don't just execute tasks — they explore. During idle time, agents traverse data sources to build better understanding of your workspace. After each query, they note what they wish they'd known, what search strategies worked, and what didn't. These exploration findings get appended to the agent's journal — a living document that makes every subsequent run smarter.

This is the equivalent of a new hire spending their first week reading internal docs. Except the agent does it continuously, and it never forgets what it learned.

---

## 3. Tag to Chat — The mobile bridge

This is the feature that turns Promaia into a mobile app without ever building one.

### @promaia

Tag `@promaia` in Slack or Discord and an agent responds — with the full power of Maia chat behind it. Your entire synced workspace as context. Multi-turn conversation. Tool access. Everything.

```
You (in #general on Slack):
  @promaia what did the team ship this week?

Promaia:
  Based on this week's activity across stories and journal entries:

  Shipped:
  - Tag to Chat feature (Discord + Slack)
  - Google Connector v2 (Rose)
  - Notion sync robustness improvements

  In Progress:
  - Investment memo
  - Bill of materials integration (Mitchell)

  Want me to pull up details on any of these?
```

```
You (in #engineering on Discord):
  @promaia draft a recap email to Alice about the launch timeline

Promaia:
  I've pulled context from your last 12 email threads with Alice
  and this week's project updates. Here's a draft:

  Subject: Launch Timeline Update

  Hey Alice,

  Quick update — we're tracking well for the 2-week window.
  The API work landed Wednesday and we're into integration testing...

  Want me to adjust the tone or add anything before sending?
```

This works on your phone. In the Slack mobile app. In Discord mobile. **Anywhere you can type `@promaia`, you have your entire workspace at your fingertips.**

No new app to install. No new interface to learn. No new tab to keep open. You just talk to your team — and one of your teammates happens to be an AI with perfect memory.

### How Tag to Chat works

When someone tags `@promaia` in a connected channel:

1. The message arrives via the Slack bot or Discord bot (already running as a service)
2. Promaia classifies the intent — is this a knowledge question or an action request?
3. For knowledge: it queries the synced memory model (SQL + vector + markdown) and responds directly
4. For actions: it spawns an agent with the right MCP tools — send an email, create a Notion page, check the calendar — and enters a multi-turn conversation until the task is done
5. Security validates every message — rate limiting, input sanitization, user verification
6. The full conversation is logged and auditable

The agent shows typing indicators where the platform supports it. Conversations time out gracefully after inactivity. If someone tries to hijack a conversation, the security layer blocks it.

### Pipelines

Tag to Chat is the first example of what we call **pipelines** — composable flows where a trigger (a tag, a calendar event, a webhook) activates a sequence of agent capabilities. The building blocks are MCP tool servers: query tools for reading context, Gmail tools for email operations, calendar tools for scheduling, and the connectors that keep everything in sync.

A pipeline might be:

- **Calendar trigger → load context → write report → post to Slack → wait for feedback → update Notion**
- **@mention in Discord → classify intent → query memory → draft response → send**
- **New Shopify order → check inventory → create fulfillment tasks → notify team**

The tools are modular. The triggers are flexible. The data is already there. Pipelines are how Promaia goes from "smart assistant" to "autonomous operations layer."

---

## Connectors — The plugin architecture

Promaia's power comes from scraping everything. Constant data ingestion across platforms, formatted into the memory system. Without that, it's just another chatbot. With it, agents have the context they need to actually be useful.

Every data source is a **connector** — a standardized plugin that implements seven core methods:

```
connect()                → Establish connection
test_connection()        → Verify working connection
get_database_schema()    → Describe available fields
query_pages()            → Search and filter items
get_page_content()       → Fetch full item with content
get_page_properties()    → Get metadata only
sync_to_local_unified()  → Save to local storage
```

Write a connector, register it, and your source is immediately available to every agent, every chat session, and every pipeline. Vector search, SQL queries, markdown rendering — all automatic.

### What's connected today

**Notion** — Full database sync with dynamic property support. Every property in your Notion database becomes a queryable SQL column. Schema synchronization, batch processing, rate limit handling. This is the deepest integration — Promaia mirrors your Notion workspace structure.

**Gmail** — Thread synchronization with intelligent chunking. Date range queries split into 15-day batches to handle large inboxes. Message-level or thread-level storage. Attachment handling. The email drafting system learns your writing style per workspace through a rolling pattern index — so responses to clients sound like you, not like an AI.

**Google Calendar** — The control plane for agent scheduling. Events trigger agent runs. Agents appear as recurring calendar items you manage with drag-and-drop. Works across mobile, desktop, anywhere Google Calendar works.

**Discord** — Multi-channel sync with OCR support for images. Per-channel day filtering. Message ordering, attachment handling, reaction tracking. The Discord bot handles both sync (pulling messages in) and interaction (agents responding in channels).

**Slack** — Workspace and channel sync with thread support. User resolution, rate limiting, pagination. The Slack bot enables Tag to Chat and agent-initiated conversations.

**Shopify** — Order, product, and inventory sync via REST Admin API. Append-only inventory snapshots for historical tracking. Direct SQL storage optimized for e-commerce queries. PII-excluded by design.

### Adding a new connector

The connector model is designed so that new sources slot in without touching the core. An open source plugin template is available on our GitHub. The community builds connectors; every new source makes the platform more valuable for everyone.

Sources on the roadmap: Google Sheets, Google Drive, Linear, GitHub, Jira — and anything the community contributes.

---

## Who we are

**Founder** — CEO. Founder-operator with a track record of taking products from zero to market across hardware, software, and e-commerce. Built the original Promaia codebase from the ground up: agent orchestration, connector architecture, hybrid storage, CLI, and chat interface.

**Rose** — CTO and cofounder. Core architecture and systems. Designed the Reflex memory model — the hierarchical attachment system that powers semantic expansion. Building the next-generation storage engine, the Google connector suite, and the database architecture that makes Promaia's retrieval work at scale.

---

## Who's it for

**Wedge:** founder-operators and small teams drowning in coordination work.

The person who spends Monday morning pulling updates from Notion, Slack, email, and a spreadsheet just to know what happened last week. The team lead who writes the same status report every Friday. The ops manager who manually cross-references Shopify orders against a Google Sheet BOM to figure out what to reorder.

**Initial buyer persona:** a team lead who wants an "AI chief of staff" that runs weekly cadence in Slack, driven by calendar. They already use Notion, Slack, and Google Calendar. They don't want another tool — they want the tools they have to start working together.

**Expansion personas:**
- E-commerce operators who need procurement-to-fulfillment automation across Shopify, Sheets, and Slack
- Agency leads managing multiple client workspaces who need context separation and per-client agents
- Engineering teams who want sprint reports, retrospectives, and cross-platform search without the busywork

The burning pain is the same across all of them: **context is scattered, and the work to gather it is manual, repetitive, and beneath what these people should be spending their time on.**

---

## Go-to-market

**Phase 1: AI Chief of Staff** (now)

Slack + Calendar + Notion. One agent, one pipeline. The demo that sells itself: schedule a calendar event, watch the agent post a team momentum report to Slack, discuss it live. Onboarding target: under 30 minutes.

**Phase 2: Expand integrations and higher-trust actions** (next)

Shopify + Sheets for e-commerce operations. Gmail drafting for client-facing teams. Tag to Chat as the mobile interface. This is where Promaia goes from "cool demo" to "can't live without it."

**Phase 3: Enterprise controls** (later)

Permissions, audit logs, admin model, security posture. Multi-tenant workspaces with RBAC. SSO/SAML. The features that let a 50-person company say yes.

**Channels:**
- Open source community (MIT licensed, GitHub)
- Content marketing — tutorials on building agent workflows, YouTube demos of calendar scheduling
- Notion power users — they already understand the value
- Partnerships with Notion consultants, productivity coaches, e-commerce operators
- Product Hunt launch — the calendar agent UX is the killer demo

---

## Traction

- **~115K lines of production Python** — this is not a prototype
- **6 production connectors** — Notion, Gmail, Google Calendar, Discord, Slack, Shopify
- **3 MCP tool servers** — query tools, Gmail tools, calendar tools
- **Agent orchestration pipeline** — goal → plan → task → execute, running on real workspaces
- **Calendar-triggered agent runs** — daemon deployed, agents firing on schedule
- **Slack and Discord bots** — live, handling conversations
- **Email learning system** — per-workspace style adaptation from real usage
- **Pilot feedback** — demo sessions with investors and potential customers shaping the product roadmap (Tag to Chat, Sheets integration, and the e-commerce wedge all came directly from pilot conversations)

---

## Why us

**1. We built the memory model everyone else is going to need.**

Agents are bottlenecked by data and permissions. Every agent framework assumes the data is already there — clean, queryable, in one place. It never is. Promaia solves the problem everyone else is ignoring: getting a week of relevant context across six different platforms into a single, agent-navigable substrate in under two seconds. The memory model with semantic expansion is how we do it. Nobody else has this.

**2. We chose the right interface: none.**

OpenClaw needs you to install a new app and talk to it. Notion AI only works inside Notion. Microsoft Copilot only works inside Microsoft. We made the opposite bet: Promaia has no interface of its own. It lives inside the tools you already have — Calendar, Slack, Discord, Notion, Gmail, Sheets. That's not a limitation, it's the entire strategy. Zero adoption friction. Zero new habits. Your team doesn't even know they're using an agent platform.

**3. Local-first, consent-based, least-privilege.**

Context is user-controlled. Agents only access what they're explicitly given. Every conversation is logged and auditable. Data stays on your infrastructure or ours — your choice. In a world where a Meta researcher's OpenClaw agent mass-deleted her inbox, trust is the gate to enterprise adoption. We're building for trust from day one.

**4. The compound advantage.**

Every connector the community builds makes the platform more valuable. Every agent run makes the agent smarter (dreaming). Every email sent trains the learning system. Every new user's workspace structure teaches the memory model new patterns. Promaia gets better the more you use it — and the more people use it.

---

## Business model

**Fully open source (MIT) + BYOK (Bring Your Own Keys) + hosted service.**

The code is free. The LLM costs are the user's. We sell the hosting, the orchestration, and the peace of mind.

### Why this works

- **BYOK eliminates cost pressure** — no need to gate features to control margins. Users bring their own Anthropic/OpenAI keys.
- **Setup complexity is the natural paywall** — 6 Docker services, OAuth flows, multiple API keys. People will pay for hosted.
- **~90%+ gross margins** — with BYOK, the only cost is infrastructure.
- **One codebase** — simpler to maintain as a small team.

### Tiers

**Self-Hosted** (free forever) — Full features, all code, MIT license. BYOK required. Community support.

**Promaia Cloud — Pro** ($29/mo per user, BYOK) — Managed hosting, auto-updates, backups, monitoring. OAuth credential management. Autonomous agents with calendar scheduling. 5 workspaces. 100 OCR pages/month.

**Teams** ($79/mo per 5 users, BYOK) — Shared workspaces, role-based access control, team agent libraries. Shopify/e-commerce integrations. API access. Priority support.

**Enterprise** (custom) — SSO/SAML, audit logs, SLA guarantees, on-prem deployment, custom connector development.

**Future: Marketplace** (20-30% cut) — Premium agent templates, community-built connectors, workflow recipes.

### Why forks aren't a threat

Nobody has successfully forked Home Assistant, Cal.com, or Plausible into competing hosted services despite years of opportunity. Maintaining a complex multi-service platform, keeping up with API changes, building user trust for sensitive data access, and providing reliable hosting is hard ongoing work. The code is the easy part. Brand, community, and execution velocity are the moat.

---

## The raise

**Stage:** Pre-seed

**Ask:** $100K for founder runway.

**Use of funds:** Ship Tag to Chat, complete Sheets and Drive connectors, onboard 3–5 pilot teams, build to first revenue.

**Milestones this unlocks:**

| Milestone | Target | Timeline |
| --- | --- | --- |
| Pilot teams running weekly workflows | 3–5 teams | Month 3 |
| Onboarding under 30 minutes | Slack + Calendar | Month 4 |
| First paying customers | Pro tier | Month 6 |
| Retention signal | Scheduled runs, weekly active | Month 8 |
| Path to profitability | ~200 paying users | Month 12 |

**Structure:** We're optimizing for long-term independence. Preferred structures are revenue-based financing (capped 2x return) or SAFE with buyback clause (2.5x, exercisable after 18 months). The goal is to reach profitability, buy out early investors, and stay independent — the Obsidian/Basecamp path.

At ~$14.5K MRR (~500 users), the business is self-sustaining. At ~90% gross margins with BYOK, profitability comes early and compounds fast.

---

Promaia is building the memory plane, the control plane, and the interaction plane for agents inside existing work surfaces.

Open Claw opened the door. Promaia is the party.

Made with love and existential urgency,

— The Promaia team 🐙
