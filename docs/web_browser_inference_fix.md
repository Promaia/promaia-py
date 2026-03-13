# Web Browser Inference Fix

## Summary

Fixed Promaia's inference around when to use the web browser (URL fetching) vs web search. The system now intelligently detects URLs in user queries and guides the AI to fetch URLs first before searching for additional context.

## Changes Made

### 1. Added Missing MCP Server Configurations (`mcp_servers.json`)

Added two new MCP server configurations:

#### **`search` server** (Brave Search)
- Uses `@modelcontextprotocol/server-brave-search`
- Requires `BRAVE_API_KEY` environment variable
- Provides internet search capabilities
- Set to `enabled: false` by default (use `/mcp search` to enable)

#### **`fetch` server** (Puppeteer)
- Uses `@modelcontextprotocol/server-puppeteer`
- Fetches and renders web pages
- Extracts content from URLs
- Set to `enabled: false` by default (use `/mcp fetch` to enable)

### 2. Updated System Prompt (`prompts/prompt.md`)

Added comprehensive **Web Interaction Guidelines** section:

- **URL Detection Rules**: When to fetch URLs
- **Fetch vs Search Decision Logic**: Clear guidance on choosing between tools
- **Examples**: Good and bad usage patterns
- **Key Rules**: Always fetch user-provided URLs first

### 3. Enhanced MCP Client (`promaia/mcp/client.py`)

Added **URL Detection & Web Fetching Guidelines** section to tool formatting:

- Explicit instructions to fetch URLs before searching
- Examples showing proper URL + search workflow
- Clear distinction between fetch and search use cases

### 4. Added URL Detection Logic (`promaia/chat/interface.py`)

#### **New Functions:**

- `detect_urls_in_text(text)`: Detects URLs in user input
- `check_and_suggest_web_tools(user_input, context_state, style)`: Suggests enabling fetch/search when URLs or search keywords detected

#### **New Commands:**

- `/mcp fetch`: Toggle URL fetching on/off
- `/mcp search`: Toggle internet search on/off (already existed, kept consistent)

#### **Auto-Detection:**

- Automatically detects URLs in queries and suggests enabling `/mcp fetch`
- Detects search keywords and suggests enabling `/mcp search`

### 5. Updated Help Documentation

- Added `/mcp search` and `/mcp fetch` to help command
- Clear descriptions of what each command does

## How It Works Now

### Before (Problem)

```
User: "What's a one sheet for my brand: https://www.myangl.com/"

AI: [Only searches web, ignores URL]
<tool_code>search.web_search(query="one sheet business")</tool_code>
```

**Problem**: AI never visited the URL to understand the brand context!

### After (Fixed)

```
User: "What's a one sheet for my brand: https://www.myangl.com/"

💡 Tip: I detected URLs in your query. Enable web fetching with /mcp fetch to visit these URLs.
   URLs found: https://www.myangl.com/

User: /mcp fetch
🌐 URL fetching enabled!

User: "What's a one sheet for my brand: https://www.myangl.com/"

AI: I'll fetch your brand website first to understand your business.

<tool_code>fetch.puppeteer_navigate(url="https://www.myangl.com/")</tool_code>
<tool_code>search.web_search(query="one sheet business bookstore publisher")</tool_code>

[Now has full context from both URL content AND search results]
```

## Usage Guide

### Enable URL Fetching

```bash
# In chat
/mcp fetch
```

This enables the Puppeteer server to fetch and render web pages.

### Enable Internet Search

```bash
# In chat
/mcp search
```

This enables the Brave Search API to search the internet.

### Set Up API Keys

#### Brave Search API Key

```bash
# Add to your .env or environment
export BRAVE_API_KEY="your_api_key_here"
```

Get a Brave Search API key at: https://brave.com/search/api/

#### Puppeteer Dependencies

The Puppeteer MCP server will install dependencies automatically on first use. Ensure you have Node.js and npm installed:

```bash
npx @modelcontextprotocol/server-puppeteer
```

## Inference Rules

### URL Detection

The system detects URLs matching these patterns:
- `http://...`
- `https://...`
- `www....`

### Fetch vs Search Decision

**Fetch when:**
- User provides a specific URL to visit
- User says "check this out", "look at", "visit", "go to" + URL
- User asks a question about a URL they provided

**Search when:**
- User asks a general question without URLs
- User says "search for", "look up", "find information about"

**Both (Fetch FIRST, then search):**
- User provides URL AND asks for additional context
- Example: "What's X about my brand: <URL>"

## Testing

To test the fix:

1. **Test URL detection:**
   ```
   User: "Check out https://example.com"

   Expected: System suggests enabling /mcp fetch
   ```

2. **Test search keyword detection:**
   ```
   User: "Search for information about..."

   Expected: System suggests enabling /mcp search
   ```

3. **Test combined workflow:**
   ```
   User: "What's a one sheet for https://myangl.com"
   /mcp fetch
   /mcp search

   Expected: AI fetches URL first, then searches for "one sheet"
   ```

## Configuration

### Enable by Default

To enable fetch/search by default for all sessions, edit `mcp_servers.json`:

```json
{
  "servers": {
    "search": {
      ...
      "enabled": true  // Change from false to true
    },
    "fetch": {
      ...
      "enabled": true  // Change from false to true
    }
  }
}
```

### Disable Auto-Suggestions

If you don't want automatic suggestions, comment out this line in `interface.py` (line ~5705):

```python
# check_and_suggest_web_tools(user_input, context_state, style)
```

## Troubleshooting

### "fetch server not connecting"

- Ensure Puppeteer is installed: `npx @modelcontextprotocol/server-puppeteer`
- Check Node.js version (requires Node 18+)
- Try installing globally: `npm install -g @modelcontextprotocol/server-puppeteer`

### "search server not connecting"

- Verify BRAVE_API_KEY is set in environment
- Check API key validity at https://brave.com/search/api/
- Note: Brave Search MCP server is deprecated but still functional

### "URLs not being detected"

- Ensure URL includes `http://`, `https://`, or starts with `www.`
- Check that URL is in user input, not AI response
- Verify `check_and_suggest_web_tools()` is being called

## Future Improvements

1. **Alternative Search Provider**: Replace deprecated Brave Search with Tavily or Perplexity
2. **Simple Fetch Alternative**: Add a simpler HTTP fetch tool (without full browser rendering)
3. **Smart Auto-Enable**: Automatically enable fetch when URLs detected (with user confirmation)
4. **URL Caching**: Cache fetched URLs to avoid re-fetching in same session
5. **Domain Whitelist**: Allow users to whitelist domains for auto-fetching

## Related Files

- `mcp_servers.json` - MCP server configurations
- `prompts/prompt.md` - System prompt with web interaction guidelines
- `promaia/mcp/client.py` - MCP tool formatting and guidelines
- `promaia/chat/interface.py` - URL detection and command handling
