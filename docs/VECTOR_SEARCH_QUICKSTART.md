# Vector Search - Quick Start 🚀

## You're Ready to Use It! ✅

The vector search system is **fully implemented and working**. Here's how to use it:

## 1. First-Time Setup (5 minutes)

### A. Ensure Dependencies Are Installed
```bash
cd /Users/kb20250422/Documents/dev/promaia
source venv/bin/activate
pip install -r requirements.txt  # If not already done
```

### B. Verify OpenAI API Key
Make sure your `.env` file has:
```bash
OPENAI_API_KEY=sk-...
```

### C. Run Full Migration
```bash
# This will embed ALL your existing content
python migrate_to_vector_db.py

# Monitor progress - takes about 2-4 seconds per document
# For 1000 documents, expect ~30-60 minutes
```

**Note**: Migration can be interrupted with Ctrl+C and resumed with:
```bash
python migrate_to_vector_db.py --resume
```

## 2. Use Vector Search

### Basic Search
```bash
maia chat -vs 'international launch stories'
```

### Multiple Queries (Results Combined)
```bash
maia chat -vs 'product planning' -vs 'team discussions'
```

### Mix with Regular Sources
```bash
# Vector search + recent journal entries
maia chat -s journal:7 -vs 'project updates'
```

### With Workspace Filter
```bash
maia chat -ws trass -vs 'international expansion'
```

## 3. What You'll See

### During Search
```
🔍 Processing query 1/1: 'international launch stories'
✅ Initialized semantic search processor with anthropic
🎯 Parsed Search Parameters:
   Query: international launch stories
   Workspace: trass
   Limit: 20 (default)
✅ Search successful: 12 results found
```

### Results Summary
```
📊 VECTOR SEARCH RESULTS SUMMARY
🎯 Your Query: international launch stories
📚 Total Results: 12 entries
🗄️ Sources: stories, journal, cpj

Top Results by Similarity Score:
1. International Launch Announcement (0.92) - 2025-10-07
2. International Market Testing (0.89) - 2025-09-23
3. International Payments (0.86) - 2025-09-14
```

### In Chat
Your chat session will load with the relevant content based on semantic similarity, not just keyword matches.

## 4. Configuration (Optional)

Edit `promaia.config.json` to customize:

```json
{
  "global": {
    "vector_search": {
      "enabled": true,
      "embedding_provider": "openai",
      "embedding_model": "text-embedding-3-small",
      "default_n_results": 20,          // Max results per query
      "default_similarity_threshold": 0.75  // Min similarity (0-1)
    }
  }
}
```

**Lower threshold** to get more results (but less relevant):
```json
"default_similarity_threshold": 0.5
```

**More results**:
```json
"default_n_results": 50
```

## 5. Ongoing Use

### New Content is Automatic! 🎉
When you sync new content, it's automatically embedded:
```bash
maia sync notion    # Auto-embeds new Notion pages
maia sync gmail     # Auto-embeds new emails
maia sync discord   # Auto-embeds new Discord messages
```

No manual migration needed after initial setup!

## Key Differences: `-nl` vs `-vs`

### Natural Language (`-nl`) - SQL Mode
```bash
maia chat -nl 'emails from john about product launch in last 30 days'
```
- Generates SQL queries
- Exact filtering (dates, senders, properties)
- Best for: Specific criteria, date ranges, exact matches

### Vector Search (`-vs`) - Semantic Mode
```bash
maia chat -vs 'product launch discussions'
```
- Uses semantic similarity (meaning)
- Finds conceptually related content
- Best for: Exploring topics, finding similar content, when you don't know exact keywords

## Troubleshooting

### "No results found"
- Try lowering `default_similarity_threshold` in config
- Ensure migration completed: `python migrate_to_vector_db.py --limit 1`
- Check you have markdown files in `data/md/`

### "Vector search is disabled"
- Enable in `promaia.config.json`: `"enabled": true`

### "No module named 'chromadb'"
```bash
source venv/bin/activate
pip install chromadb==0.5.23 sentence-transformers==3.3.1
```

### Slow migration
- This is normal! Each document takes 2-4 seconds to embed
- Use `--limit N` to test with smaller batches first
- Use `--resume` to continue interrupted migrations

## Example Queries

**Good for Vector Search**:
- `maia chat -vs 'team collaboration tools'`
- `maia chat -vs 'customer feedback about UX'`
- `maia chat -vs 'technical challenges with deployment'`
- `maia chat -vs 'budget planning discussions'`

**Better for Natural Language (SQL)**:
- `maia chat -nl 'emails from sarah in last 7 days'`
- `maia chat -nl 'notion pages created after march 1st'`
- `maia chat -nl 'discord messages in yp channel this week'`

## Need More Info?

- **Complete Guide**: See `VECTOR_SEARCH.md`
- **Implementation Details**: See `IMPLEMENTATION_SUMMARY.md`
- **Configuration**: See `docs/promaia.config.json.example`

---

**Ready to use!** Run the migration and start searching! 🎉
