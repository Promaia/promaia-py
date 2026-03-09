# Artifacts in Maia Chat

Maia chat now supports **artifacts** - Claude-style inline generated content for emails, blog posts, documents, code, and more.

## What are Artifacts?

Artifacts are numbered pieces of generated content that appear inline in your chat, formatted with clean separators for easy copying. They're perfect for:

- Email drafts
- Blog posts and articles
- Documents and letters
- Code snippets and scripts
- Any structured content you want to iterate on

## How Artifacts Work

### AI-Driven Detection

Artifacts are created **dynamically by the AI** when it determines content should be separated from commentary. The AI has been trained to recognize when you're asking for deliverable content (emails, documents, code, etc.) versus explanations or discussions.

```bash
maia chat
> write a blog post about unified architectures

# AI recognizes this as a content creation request
# and wraps the blog post in <artifact> tags

Artifact #1
─────────────────────────────────────────────────────────────────
# Unified Architectures

In modern software...
─────────────────────────────────────────────────────────────────

> make it shorter

# AI updates Artifact #1 with a shorter version
```

### How the AI Decides

The AI uses `<artifact>` tags to mark content that should be treated as an artifact:

```
I'll help you draft that email.

<artifact>
Subject: Meeting Follow-up

Hi Sarah,

Thanks for taking the time to meet today...

Best,
Jane
</artifact>

This keeps the tone professional while being concise.
```

Everything between `<artifact>` tags becomes an artifact, with commentary displayed separately.

**The AI will use artifacts when:**
- You request deliverable content (emails, documents, presentations, code)
- The content is substantial and reusable
- The content is ready to use as-is
- The content would benefit from being separated from explanatory text

**The AI will NOT use artifacts when:**
- You're asking questions or requesting explanations
- The response is conversational or advisory
- The content is small/illustrative rather than deliverable
- Your request is unclear (the AI will clarify first)

### Manual Override

You can explicitly request artifact mode by including "as an artifact" in your message:

```bash
> explain the fibonacci algorithm as an artifact

# AI will wrap the explanation in artifact tags
```

## Artifact Commands

### `/artifacts` or `/a`
List all artifacts in the current session:

```bash
> /artifacts

📋 Artifacts in this session:
  #1 (v2): # Unified Architectures\n\nIn modern software design...
  #2 (v1): Dear Sarah,\n\nI hope this email finds you well...
```

### `/artifact N`
Display a specific artifact:

```bash
> /artifact 1

Artifact #1
─────────────────────────────────────────────────────────────────

# Unified Architectures

In modern software design, unified architectures promote...

─────────────────────────────────────────────────────────────────
```

### `/edit N`
Edit a specific artifact (prompts for changes):

```bash
> /edit 1

Artifact #1
─────────────────────────────────────────────────────────────────
[... artifact content ...]
─────────────────────────────────────────────────────────────────

What changes would you like to make?
> make the introduction more engaging

# AI updates Artifact #1 with the changes
```

## Artifacts in Draft Chat

In `maia mail` draft chat, artifacts **are** the email drafts. When you refine a draft, you're updating the artifact:

```bash
maia mail --draft abc123

# Email thread displays...

Artifact #1 (Draft #1)
─────────────────────────────────────────────────────────────────

Dear Bob,

Thank you for the update...

─────────────────────────────────────────────────────────────────

💬 You: make it more concise

# AI updates Artifact #1 with a shorter version
```

### Draft-Specific Features

- `/send [N]` - Send draft (default: latest artifact)
- `/archive` - Archive the email
- Artifacts persist as draft versions
- Full message context loaded automatically

## Update Detection

When you ask to modify content, the AI automatically updates the last artifact instead of creating a new one:

**Update phrases:**
- "make it shorter/longer"
- "make that more formal"
- "change it to..."
- "add/remove..."
- "fix/improve..."

```bash
> write a blog post about artifacts

Artifact #1 created

> make it shorter

Artifact #1 updated (v2)

> add a section about benefits

Artifact #1 updated (v3)
```

## Artifact Format

Artifacts use clean separators that copy perfectly:

```
Artifact #1
─────────────────────────────────────────────────────────────────

Your content here...

─────────────────────────────────────────────────────────────────
```

- No box characters (╭─╮│╰╯)
- No formatting artifacts
- Just clean horizontal lines
- Perfect for copy/paste

## Copy-Friendly Design

Everything in artifacts is designed to copy cleanly:

- ✅ Plain text horizontal separators
- ✅ No special formatting characters
- ✅ Pastes perfectly into emails, docs, code editors
- ✅ No manual cleanup needed

## Examples

### Blog Post

```bash
maia chat
> write a blog post about the benefits of artifacts in AI chat

Artifact #1
─────────────────────────────────────────────────────────────────

# Why Artifacts Matter in AI Chat

Artifacts represent a paradigm shift in how we interact with AI...

─────────────────────────────────────────────────────────────────

> make the title catchier

Artifact #1 (v2)
─────────────────────────────────────────────────────────────────

# The Artifact Revolution: Why AI Chat Just Got 10x Better

Artifacts represent a paradigm shift...

─────────────────────────────────────────────────────────────────
```

### Email Draft

```bash
maia chat
> write an email to the team about our new artifact feature

Artifact #1
─────────────────────────────────────────────────────────────────

Subject: Introducing Artifacts - A New Way to Work with AI

Hi team,

I'm excited to share a major update to Maia chat...

─────────────────────────────────────────────────────────────────

> add a section about how to try it

Artifact #1 (v2)
─────────────────────────────────────────────────────────────────

Subject: Introducing Artifacts - A New Way to Work with AI

Hi team,

I'm excited to share a major update to Maia chat...

## Try It Yourself

Simply ask Maia to "write" or "create" something...

─────────────────────────────────────────────────────────────────
```

### Code Generation

```bash
maia chat
> write a Python function to calculate fibonacci numbers

Artifact #1
─────────────────────────────────────────────────────────────────

def fibonacci(n):
    """Calculate the nth Fibonacci number."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

─────────────────────────────────────────────────────────────────

> make it use memoization

Artifact #1 (v2)
─────────────────────────────────────────────────────────────────

from functools import lru_cache

@lru_cache(maxsize=None)
def fibonacci(n):
    """Calculate the nth Fibonacci number with memoization."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

─────────────────────────────────────────────────────────────────
```

## Artifacts vs. Regular Responses

**Artifacts are created when:**
- The AI recognizes you're requesting deliverable content
- The AI wraps content in `<artifact>` tags
- You explicitly request "as an artifact"

**Regular responses when:**
- You ask questions
- You request explanations or analysis
- You have a conversation
- The AI determines content is explanatory rather than deliverable

The AI dynamically decides based on context - no hardcoded keyword matching.

## Tips

1. **Trust the AI** - The AI will automatically use artifacts when appropriate based on your request
2. **Use update phrases** - "make it shorter" updates the artifact, "write another one" creates new
3. **List artifacts** - Use `/artifacts` to see what you've created in the session
4. **Copy freely** - Artifacts are designed to copy perfectly without cleanup
5. **Manual override** - Add "as an artifact" to force artifact mode if needed

## Integration with Maia Mail

Draft chat (`maia mail`) automatically uses artifacts for email drafts:

- Each draft version = artifact version
- `/send` sends the current artifact
- Refinements update the artifact
- All artifact commands work (`/artifacts`, `/artifact N`, `/edit N`)

This creates a unified experience: whether you're drafting an email in `maia mail` or writing a blog post in `maia chat`, you're working with artifacts.

## Architecture

Artifacts are part of the unified chat architecture:

```
maia chat
├── Regular mode (default)
│   └── Artifacts for any content
│
└── Draft mode (maia mail --draft)
    └── Artifacts = email drafts
    └── + /send and /archive commands
```

This unified approach means:
- One codebase for all artifact handling
- Consistent UX across all modes
- Easy to add new modes (blog mode, code mode, etc.)

## Future Modes

The artifact system enables future specialized modes:

- **Blog mode** - Artifacts for blog posts with SEO tools
- **Code mode** - Artifacts for code with linting/testing
- **Document mode** - Artifacts for docs with formatting tools

All using the same unified artifact infrastructure.

---

## Learn More

- [Maia Mail README](MAIA_MAIL_README.md) - Email drafting with artifacts
- [Copy-Friendly Rich](COPY_FRIENDLY_RICH.md) - Design philosophy
- [Unified Chat Plan](../fix-email-classification.plan.md) - Architecture details

