# Vector Search Setup Guide

## ✅ Implementation Complete

All code is in place and ready to use! You just need to install dependencies and populate the vector database.

## 📦 Step 1: Install Dependencies

Make sure you're in your virtual environment, then run:

```bash
# Activate your virtual environment (if not already activated)
source venv/bin/activate

# Install required packages
pip install chromadb==0.5.23 sentence-transformers==3.3.1
```

## 🔄 Step 2: Populate Vector Database

Run the migration script to embed your existing content:

```bash
# Test with a small batch first
python3 migrate_to_vector_db.py --limit 100

# Or run the full migration (will take 10-30 minutes depending on content size)
python3 migrate_to_vector_db.py

# Resume if interrupted
python3 migrate_to_vector_db.py --resume
```

## 🚀 Step 3: Try Vector Search

Once migration completes, you can use semantic search:

```bash
# Basic vector search
maia chat -vs international launch stories

# Multiple queries
maia chat -vs 'product planning' -vs 'team discussions'

# Mix with regular sources
maia chat -s journal:3 -vs 'canada project discussions'

# Compare with SQL mode
maia chat -nl trass gmail with term mgm from last 1 week
```

## 🎯 Understanding the Two Modes

### SQL Mode (`-nl`)
- **Use for:** Exact text matching, specific terms
- **Example:** `maia chat -nl trass gmail with term mgm from last 1 week`
- **Benefits:** Finds ALL exact matches, learns from patterns
- **Returns:** Everything that contains the exact terms

### Vector Mode (`-vs`)
- **Use for:** Semantic/conceptual searches, finding related content
- **Example:** `maia chat -vs international launch stories`
- **Benefits:** Finds similar content even without exact words
- **Returns:** Top 20 most relevant results ranked by similarity

## ⚙️ Configuration

Vector search is enabled in `promaia.config.json`:

```json
"vector_search": {
  "enabled": true,
  "embedding_provider": "openai",
  "embedding_model": "text-embedding-3-small",
  "chroma_path": "chroma_db",
  "default_n_results": 20,
  "default_similarity_threshold": 0.75
}
```

Make sure your `.env` file has:
```
OPENAI_API_KEY=sk-...
```

## 🔧 Automatic Embedding

From now on, whenever you sync new content, it will automatically be embedded to the vector database. You only need to run the migration once for existing content.

## 📊 Check Vector DB Stats

```bash
python3 -c "from promaia.storage.vector_db import VectorDBManager; vdb = VectorDBManager(); import json; print(json.dumps(vdb.get_stats(), indent=2))"
```

## 🐛 Troubleshooting

**Issue:** "No module named 'chromadb'"
**Solution:** Install dependencies in your virtual environment (see Step 1)

**Issue:** "Vector search returned 0 results"
**Solution:** Run the migration script to populate the database (see Step 2)

**Issue:** "OpenAI API error"
**Solution:** Check your `.env` file has a valid OPENAI_API_KEY, or it will fallback to sentence-transformers (free, local)

## 🎉 You're Done!

The implementation is complete and production-ready. Enjoy semantic search! 🚀
