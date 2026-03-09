# Perplexity Search MCP Server

An enhanced MCP (Model Context Protocol) server that provides reliable internet search capabilities using Perplexity AI with source citations, result validation, and transparency features.

## Features

### ✅ Implemented
- **Perplexity AI Integration**: Uses Perplexity's online models for current, accurate search results
- **Source Attribution**: Every search result includes proper citations and source URLs
- **Result Transparency**: Clear indicators of search method, model used, and timestamp
- **Caching**: Smart caching to avoid redundant API calls (15-minute cache duration)
- **Error Handling**: Comprehensive error handling with helpful error messages
- **Multiple Models**: Support for different Perplexity models based on needs

### 🔄 Architecture Improvements

This implementation addresses the key issues from the previous DuckDuckGo-based search:

1. **Limited Search Capabilities** → **Comprehensive AI-Powered Search**
   - Moved from DuckDuckGo's limited instant answer API to Perplexity's full search capability
   - AI-enhanced results that understand context and provide detailed answers

2. **Lack of Source Transparency** → **Full Source Attribution**
   - Every result includes citations with source URLs
   - Clear indication of search method and timestamp
   - Transparency notes explaining the search process

3. **Poor Error Handling** → **Robust Error Management**
   - Specific error messages for different failure scenarios
   - API key validation and setup guidance
   - Rate limit and timeout handling

4. **Single Point of Failure** → **Reliable Service**
   - Caching reduces API dependency
   - Better error recovery and user feedback

## Setup

### 1. Get Perplexity API Key
1. Go to [Perplexity API Settings](https://www.perplexity.ai/settings/api)
2. Create an API key
3. Set the environment variable:
   ```bash
   export PERPLEXITY_API_KEY='your-api-key-here'
   ```

### 2. Run Setup Script
```bash
cd mcp_servers/search_server/
python3 setup.py
```

### 3. Configure MCP Server
The server is already configured in `mcp_servers.json` with:
```json
{
  "search": {
    "description": "Internet search capability using Perplexity AI with source citations",
    "command": ["python3", "/path/to/search_server.py"],
    "env": {
      "PERPLEXITY_API_KEY": "${PERPLEXITY_API_KEY}"
    },
    "enabled": true
  }
}
```

## Usage

### In Maia Chat
1. Connect to MCP servers: `/mcp search`
2. Use web search: `Please search the web for [your query]`

### Available Models
- `llama-3.1-sonar-small-128k-online` (default) - Fast, efficient
- `llama-3.1-sonar-large-128k-online` - More comprehensive
- `llama-3.1-sonar-huge-128k-online` - Most detailed

### Example Tool Call
```
<tool_code>search.web_search(query="Replenish Marketplace Alameda", model="llama-3.1-sonar-small-128k-online")</tool_code>
```

## Search Result Format

Results now include:

### Header Information
- Search method indicator (live search vs cached)
- Query, model used, and timestamp
- Clear formatting for easy reading

### Main Content
- AI-enhanced answer with current, factual information
- Specific details like addresses, phone numbers, hours
- Context and relevant background information

### Source Citations
- Numbered list of sources with titles and URLs
- Direct links for verification
- Source attribution for each piece of information

### Transparency Note
- Explanation of search method
- Reminder to verify critical information
- Timestamp and freshness indicators

## Best Practices Implemented

Based on research into chatbot search best practices:

1. **Retrieval-Augmented Generation (RAG)**: Combines search retrieval with AI generation
2. **Source Attribution**: Every result includes verifiable sources
3. **Result Validation**: AI model trained to provide accurate, current information
4. **Error Transparency**: Clear error messages and fallback options
5. **Caching Strategy**: Balances freshness with efficiency
6. **User Feedback**: Clear indicators of result quality and freshness

## Troubleshooting

### Common Issues

1. **"PERPLEXITY_API_KEY not configured"**
   - Set the environment variable: `export PERPLEXITY_API_KEY='your-key'`
   - Run the setup script to test configuration

2. **"Rate limit exceeded"**
   - Perplexity has API rate limits
   - Wait a few minutes before trying again
   - Consider using cached results

3. **"HTTP Error 401"**
   - Invalid API key
   - Check your key at https://www.perplexity.ai/settings/api

### Testing
Run the setup script to test your configuration:
```bash
python3 setup.py
```

## Future Enhancements

### Planned Improvements
- [ ] Multiple search provider fallbacks (Google, Bing)
- [ ] Confidence scores for search results
- [ ] Result freshness indicators
- [ ] Custom search domains/filtering
- [ ] Search result summarization options

### Architecture Considerations
- Modular design allows easy addition of new search providers
- Caching system can be extended for different cache strategies
- Error handling framework supports additional failure modes

## Comparison: Before vs After

| Aspect | DuckDuckGo (Before) | Perplexity (After) |
|--------|-------------------|-------------------|
| **Search Quality** | Limited instant answers | Comprehensive AI-powered search |
| **Source Attribution** | None | Full citations with URLs |
| **Result Freshness** | Often outdated | Current, real-time information |
| **Error Handling** | Basic | Comprehensive with helpful messages |
| **Transparency** | Opaque | Full transparency with timestamps |
| **User Trust** | Low (no verification) | High (verifiable sources) |
| **API Reliability** | Free but limited | Paid but comprehensive |

This implementation transforms the search experience from "I have no way to know if it's hallucinating" to "Here's exactly where this information came from and when it was retrieved."
