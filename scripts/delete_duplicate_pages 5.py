#!/usr/bin/env python3
"""
Emergency cleanup: Delete all duplicate pages created by the push bug on 2026-02-11.

Reads the push cache to get page IDs of duplicates, then archives them via the Notion API.
Uses asyncio with rate-limited concurrency to stay within Notion's API limits.
"""
import asyncio
import json
import sys
import os
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

PUSH_CACHE_PATH = Path.home() / ".promaia" / "notion_push_cache.json"
BATCH_SIZE = 10  # Concurrent requests per batch
BATCH_DELAY = 0.4  # Seconds between batches (Notion rate limit: ~3 req/sec)


def get_duplicate_page_ids() -> list[dict]:
    """Extract all page IDs created by the push bug on 2026-02-11."""
    with open(PUSH_CACHE_PATH) as f:
        cache = json.load(f)

    duplicates = []
    for file_path, info in cache.items():
        pushed = info.get("last_pushed", "")
        if pushed.startswith("2026-02-11"):
            page_id = info.get("notion_page_id")
            if page_id:
                duplicates.append({
                    "page_id": page_id,
                    "database_id": info.get("database_id", ""),
                    "file_path": file_path,
                })

    return duplicates


async def delete_page(client, page_id: str, index: int, total: int) -> dict:
    """Archive a single page (move to trash)."""
    try:
        await client.pages.update(page_id=page_id, archived=True)
        return {"page_id": page_id, "status": "deleted"}
    except Exception as e:
        error_msg = str(e)
        if "already" in error_msg.lower() and "archived" in error_msg.lower():
            # Already in trash (user deleted manually)
            return {"page_id": page_id, "status": "already_deleted"}
        elif "404" in error_msg or "Could not find" in error_msg:
            return {"page_id": page_id, "status": "already_deleted"}
        elif "429" in error_msg:
            # Rate limited - wait and retry
            await asyncio.sleep(3)
            try:
                await client.pages.update(page_id=page_id, archived=True)
                return {"page_id": page_id, "status": "deleted"}
            except Exception as retry_e:
                retry_msg = str(retry_e)
                if "archived" in retry_msg.lower():
                    return {"page_id": page_id, "status": "already_deleted"}
                return {"page_id": page_id, "status": "error", "error": retry_msg}
        else:
            return {"page_id": page_id, "status": "error", "error": error_msg}


async def main():
    # Get duplicate page IDs
    duplicates = get_duplicate_page_ids()
    total = len(duplicates)
    print(f"Found {total} duplicate pages to delete\n")

    if total == 0:
        print("Nothing to delete!")
        return

    # Initialize Notion client using project's config
    from promaia.notion.client import ensure_default_client
    client = ensure_default_client()

    # Process in batches
    deleted = 0
    already_deleted = 0
    errors = 0
    first_error = None
    start_time = time.time()

    for i in range(0, total, BATCH_SIZE):
        batch = duplicates[i:i + BATCH_SIZE]
        tasks = [
            delete_page(client, item["page_id"], i + j, total)
            for j, item in enumerate(batch)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                errors += 1
                if not first_error:
                    first_error = str(result)
            elif result["status"] == "deleted":
                deleted += 1
            elif result["status"] == "already_deleted":
                already_deleted += 1
            else:
                errors += 1
                if not first_error:
                    first_error = result.get("error", "unknown")

        # Progress update
        processed = min(i + BATCH_SIZE, total)
        elapsed = time.time() - start_time
        rate = processed / elapsed if elapsed > 0 else 0
        eta = (total - processed) / rate if rate > 0 else 0

        print(
            f"\r  Progress: {processed}/{total} "
            f"({processed*100//total}%) | "
            f"Trashed: {deleted} | Already gone: {already_deleted} | Errors: {errors} | "
            f"Rate: {rate:.1f}/s | ETA: {eta:.0f}s",
            end="", flush=True
        )

        # Rate limit delay between batches
        if i + BATCH_SIZE < total:
            await asyncio.sleep(BATCH_DELAY)

    elapsed = time.time() - start_time
    print(f"\n\nDone in {elapsed:.1f}s")
    print(f"  Trashed:       {deleted}")
    print(f"  Already gone:  {already_deleted}")
    print(f"  Errors:        {errors}")
    if first_error:
        print(f"  First error:   {first_error}")

    # Clear the push cache entries for deleted pages
    if deleted > 0 or already_deleted > 0:
        print(f"\nCleaning push cache...")
        with open(PUSH_CACHE_PATH) as f:
            cache = json.load(f)

        removed = 0
        keys_to_remove = []
        for file_path, info in cache.items():
            pushed = info.get("last_pushed", "")
            if pushed.startswith("2026-02-11"):
                keys_to_remove.append(file_path)

        for key in keys_to_remove:
            del cache[key]
            removed += 1

        with open(PUSH_CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2)

        print(f"  Removed {removed} entries from push cache")


if __name__ == "__main__":
    asyncio.run(main())
