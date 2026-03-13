"""
Notion Push - Bidirectional sync from local markdown to Notion.

Detects new/modified markdown files and pushes them to Notion databases.
"""
import os
import re
import json
import logging
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class NotionPushTracker:
    """Track which files have been pushed to Notion to detect changes."""

    def __init__(self, tracking_file: Path):
        self.tracking_file = tracking_file
        self.tracking_file.parent.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Dict[str, Any]] = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        """Load push tracking cache from disk."""
        if self.tracking_file.exists():
            try:
                with open(self.tracking_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load push cache: {e}")
        return {}

    def _save_cache(self):
        """Save push tracking cache to disk."""
        try:
            with open(self.tracking_file, 'w') as f:
                json.dump(self._cache, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save push cache: {e}")

    def get_file_hash(self, file_path: Path) -> str:
        """Calculate MD5 hash of file content."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception as e:
            logger.error(f"Failed to hash file {file_path}: {e}")
            return ""

    def needs_push(self, file_path: Path, notion_page_id: Optional[str] = None) -> bool:
        """
        Check if file needs to be pushed to Notion.

        Returns True if:
        - File is new (not in cache)
        - File content has changed (hash mismatch)
        - File was never successfully pushed to Notion
        """
        file_key = str(file_path.resolve())
        current_hash = self.get_file_hash(file_path)

        if file_key not in self._cache:
            # New file
            return True

        cached_info = self._cache[file_key]

        # Check if content changed
        if cached_info.get('hash') != current_hash:
            return True

        # Check if we have a Notion page ID (successful push)
        if not cached_info.get('notion_page_id'):
            return True

        return False

    def mark_pushed(
        self,
        file_path: Path,
        notion_page_id: str,
        database_id: str
    ):
        """Mark file as successfully pushed to Notion."""
        file_key = str(file_path.resolve())
        current_hash = self.get_file_hash(file_path)
        self._cache[file_key] = {
            'hash': current_hash,
            'last_pulled_hash': current_hash,  # Set baseline after push
            'notion_page_id': notion_page_id,
            'database_id': database_id,
            'last_pushed': datetime.now(timezone.utc).isoformat(),
            'file_path': str(file_path)
        }
        self._save_cache()

    def get_notion_page_id(self, file_path: Path) -> Optional[str]:
        """Get Notion page ID for a file if it was previously pushed."""
        file_key = str(file_path.resolve())
        cached_info = self._cache.get(file_key, {})
        return cached_info.get('notion_page_id')

    def detect_conflict(self, file_path: Path, current_notion_hash: str) -> str:
        """
        Detect conflicts using 3-way diff (git-style).

        Returns:
        - "safe_to_push": Local changed, Notion unchanged
        - "no_changes": Nothing changed
        - "notion_only": Notion changed, local unchanged (handled by pull)
        - "conflict": Both changed (needs resolution)
        - "new_file": File not tracked yet
        """
        file_key = str(file_path.resolve())
        current_local_hash = self.get_file_hash(file_path)

        if file_key not in self._cache:
            # New file, safe to push
            return "new_file"

        cached_info = self._cache[file_key]
        last_pulled_hash = cached_info.get('last_pulled_hash')

        if not last_pulled_hash:
            # No baseline, treat as new file
            return "new_file"

        # 3-way comparison
        local_changed = (current_local_hash != last_pulled_hash)
        notion_changed = (current_notion_hash != last_pulled_hash)

        if local_changed and not notion_changed:
            return "safe_to_push"
        elif not local_changed and notion_changed:
            return "notion_only"
        elif local_changed and notion_changed:
            return "conflict"
        else:
            return "no_changes"

    def update_last_pulled_hash(self, file_path: Path, notion_hash: str):
        """Update baseline hash after successful pull."""
        file_key = str(file_path.resolve())
        current_local_hash = self.get_file_hash(file_path)

        if file_key in self._cache:
            self._cache[file_key]['last_pulled_hash'] = notion_hash
            self._cache[file_key]['hash'] = current_local_hash
        else:
            self._cache[file_key] = {
                'hash': current_local_hash,
                'last_pulled_hash': notion_hash,
                'notion_page_id': None,
                'database_id': None,
                'last_pushed': None,
                'file_path': str(file_path)
            }
        self._save_cache()


class MarkdownToNotionConverter:
    """Convert markdown files to Notion block structure."""

    # Notion's limit is 2000 characters per rich_text block
    MAX_BLOCK_SIZE = 1900  # Leave buffer for safety

    @staticmethod
    def _chunk_text(text: str, max_size: int = 1900) -> List[str]:
        """Split text into chunks that fit Notion's character limit."""
        if len(text) <= max_size:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_size:
                chunks.append(text)
                break

            # Try to split at a paragraph or sentence boundary
            split_at = max_size
            for delimiter in ['\n\n', '\n', '. ', ' ']:
                idx = text.rfind(delimiter, 0, max_size)
                if idx > max_size // 2:  # Only split if reasonable position
                    split_at = idx + len(delimiter)
                    break

            chunks.append(text[:split_at])
            text = text[split_at:]

        return chunks

    @staticmethod
    def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
        """
        Parse YAML frontmatter from markdown.

        Returns (properties_dict, content_without_frontmatter)
        """
        lines = content.split('\n')

        if not lines or not lines[0].strip() == '---':
            return {}, content

        # Find closing ---
        frontmatter_lines = []
        content_start = 0

        for i, line in enumerate(lines[1:], 1):
            if line.strip() == '---':
                content_start = i + 1
                break
            frontmatter_lines.append(line)

        if content_start == 0:
            # No closing ---, treat as regular content
            return {}, content

        # Parse frontmatter (simple key: value format)
        properties = {}
        for line in frontmatter_lines:
            if ':' in line:
                key, value = line.split(':', 1)
                properties[key.strip()] = value.strip()

        # Return content without frontmatter
        remaining_content = '\n'.join(lines[content_start:])
        return properties, remaining_content

    @staticmethod
    def markdown_to_blocks(content: str) -> List[Dict[str, Any]]:
        """
        Convert markdown content to Notion blocks.

        Supports:
        - Headings (# ## ###)
        - Paragraphs
        - Bulleted lists
        - Numbered lists
        - Code blocks
        """
        blocks = []
        lines = content.split('\n')

        i = 0
        while i < len(lines):
            line = lines[i]

            # Skip empty lines
            if not line.strip():
                i += 1
                continue

            # Heading
            if line.startswith('#'):
                level = len(line) - len(line.lstrip('#'))
                level = min(level, 3)  # Notion supports heading_1, heading_2, heading_3
                text = line.lstrip('#').strip()

                blocks.append({
                    "object": "block",
                    "type": f"heading_{level}",
                    f"heading_{level}": {
                        "rich_text": [{"type": "text", "text": {"content": text}}]
                    }
                })

            # Bulleted list
            elif line.strip().startswith(('- ', '* ', '+ ')):
                text = line.strip()[2:].strip()
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": text}}]
                    }
                })

            # Numbered list
            elif line.strip() and line.strip()[0].isdigit() and '. ' in line:
                text = line.strip().split('. ', 1)[1] if '. ' in line else line
                blocks.append({
                    "object": "block",
                    "type": "numbered_list_item",
                    "numbered_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": text}}]
                    }
                })

            # Code block
            elif line.strip().startswith('```'):
                language = line.strip()[3:].strip() or "plain text"
                code_lines = []
                i += 1

                while i < len(lines) and not lines[i].strip().startswith('```'):
                    code_lines.append(lines[i])
                    i += 1

                code_content = '\n'.join(code_lines)

                # Split code into chunks if needed
                code_chunks = MarkdownToNotionConverter._chunk_text(code_content, MarkdownToNotionConverter.MAX_BLOCK_SIZE)
                for chunk in code_chunks:
                    blocks.append({
                        "object": "block",
                        "type": "code",
                        "code": {
                            "rich_text": [{"type": "text", "text": {"content": chunk}}],
                            "language": language
                        }
                    })

            # Regular paragraph
            else:
                # Collect consecutive lines as one paragraph
                para_lines = [line]
                i += 1

                while i < len(lines) and lines[i].strip() and not lines[i].startswith(('#', '-', '*', '+', '```')) and not (lines[i].strip()[0].isdigit() and '. ' in lines[i]):
                    para_lines.append(lines[i])
                    i += 1

                text = ' '.join(para_lines)
                if text.strip():
                    # Split paragraph into chunks if needed
                    text_chunks = MarkdownToNotionConverter._chunk_text(text, MarkdownToNotionConverter.MAX_BLOCK_SIZE)
                    for chunk in text_chunks:
                        blocks.append({
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [{"type": "text", "text": {"content": chunk}}]
                            }
                        })
                continue

            i += 1

        return blocks

    @staticmethod
    def _extract_title_from_filename(file_path: Path) -> str:
        """
        Extract just the title portion from a Notion-synced filename.

        Handles pattern: "2024-01-15 My Title abc12345-6789-...-abcdef.md"
        Returns "My Title". Falls back to file_path.stem for non-Notion files.
        """
        filename = file_path.name
        match = re.match(
            r'(\d{4}-\d{2}-\d{2})\s+(.+?)\s+([a-f0-9-]{36}|thread_[a-f0-9]{16})\.md$',
            filename
        )
        if match:
            return match.group(2)
        return file_path.stem

    def convert_file(self, file_path: Path) -> Dict[str, Any]:
        """
        Convert markdown file to Notion page structure.

        Returns dict with:
        - title: Page title (from filename or frontmatter)
        - properties: Page properties from frontmatter
        - blocks: List of Notion blocks
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Parse frontmatter
        properties, content = self.parse_frontmatter(content)

        # Extract title: prefer frontmatter, fall back to parsed filename
        title = properties.get('title') or properties.get('Name') or self._extract_title_from_filename(file_path)

        # Convert content to blocks
        blocks = self.markdown_to_blocks(content)

        return {
            'title': title,
            'properties': properties,
            'blocks': blocks
        }


class NotionPusher:
    """Push markdown files to Notion databases."""

    def __init__(self, workspace: str, tracking_file: Optional[Path] = None):
        self.workspace = workspace
        from promaia.utils.env_writer import get_data_dir
        self.tracker = NotionPushTracker(
            tracking_file or get_data_dir() / "notion_push_cache.json"
        )
        self.converter = MarkdownToNotionConverter()

    def _token(self) -> str:
        """Resolve Notion token at call time via auth module."""
        from promaia.auth import get_integration
        token = get_integration("notion").get_notion_credentials(self.workspace)
        if not token:
            raise ValueError(
                f"No Notion credentials found for workspace '{self.workspace}'. "
                "Run: maia auth configure notion"
            )
        return token

    async def _get_notion_page_hash(self, page_id: str) -> Optional[str]:
        """Fetch current Notion page content and return hash."""
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                # Fetch page content
                response = await client.get(
                    f"https://api.notion.com/v1/blocks/{page_id}/children",
                    headers={
                        "Authorization": f"Bearer {self._token()}",
                        "Notion-Version": "2022-06-28"
                    },
                    timeout=30.0
                )

                if response.status_code != 200:
                    logger.warning(f"Failed to fetch Notion page {page_id}: {response.status_code}")
                    return None

                content = response.json()
                # Hash the blocks content for comparison
                content_str = json.dumps(content.get('results', []), sort_keys=True)
                return hashlib.md5(content_str.encode()).hexdigest()
        except Exception as e:
            logger.warning(f"Failed to get Notion page hash: {e}")
            return None

    async def push_file_to_database(
        self,
        file_path: Path,
        database_id: str,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Push a single markdown file to Notion database.

        Args:
            file_path: Path to markdown file
            database_id: Notion database ID
            force: Force push even if file hasn't changed

        Returns:
            Result dict with status, page_id, error
        """
        import httpx

        # Guard: skip files pulled from Notion that the tracker doesn't know about.
        # These have a page_id (UUID) in the filename — pushing them would create
        # duplicate pages with corrupted titles.
        filename = file_path.name
        has_page_id = re.search(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', filename)
        if has_page_id and not self.tracker.get_notion_page_id(file_path):
            return {
                'status': 'skipped',
                'file_path': str(file_path),
                'page_id': None,
                'reason': 'pulled-from-Notion file (not tracked for push)'
            }

        # Check if push needed
        if not force and not self.tracker.needs_push(file_path):
            existing_page_id = self.tracker.get_notion_page_id(file_path)
            return {
                'status': 'skipped',
                'file_path': str(file_path),
                'page_id': existing_page_id,
                'reason': 'no changes detected'
            }

        # Check for conflicts (git-style 3-way diff)
        existing_page_id = self.tracker.get_notion_page_id(file_path)
        if existing_page_id and not force:
            notion_hash = await self._get_notion_page_hash(existing_page_id)
            if notion_hash:
                conflict_status = self.tracker.detect_conflict(file_path, notion_hash)

                if conflict_status == "conflict":
                    logger.warning(
                        f"⚠️  Conflict detected for {file_path.name}: "
                        f"Both local and Notion have changes. Skipping push."
                    )
                    return {
                        'status': 'conflict',
                        'file_path': str(file_path),
                        'page_id': existing_page_id,
                        'reason': 'conflict: both local and Notion modified'
                    }
                elif conflict_status == "notion_only":
                    return {
                        'status': 'skipped',
                        'file_path': str(file_path),
                        'page_id': existing_page_id,
                        'reason': 'Notion has newer changes, waiting for pull'
                    }
                elif conflict_status == "no_changes":
                    return {
                        'status': 'skipped',
                        'file_path': str(file_path),
                        'page_id': existing_page_id,
                        'reason': 'no changes detected'
                    }

        try:
            # Convert markdown to Notion format
            page_data = self.converter.convert_file(file_path)

            # Check if page already exists
            existing_page_id = self.tracker.get_notion_page_id(file_path)

            if existing_page_id and not force:
                # Update existing page
                result = await self._update_page(
                    existing_page_id,
                    page_data['title'],
                    page_data['blocks']
                )
            else:
                # Create new page
                result = await self._create_page(
                    database_id,
                    page_data['title'],
                    page_data['properties'],
                    page_data['blocks']
                )

            if result.get('page_id'):
                # Mark as successfully pushed
                self.tracker.mark_pushed(
                    file_path,
                    result['page_id'],
                    database_id
                )

            return result

        except Exception as e:
            logger.error(f"Failed to push {file_path} to Notion: {e}", exc_info=True)
            return {
                'status': 'failed',
                'file_path': str(file_path),
                'error': str(e)
            }

    async def _get_title_property_name(self, database_id: str) -> str:
        """Get the title property name from database schema."""
        import httpx

        # Query database to get schema
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.notion.com/v1/databases/{database_id}",
                headers={
                    "Authorization": f"Bearer {self._token()}",
                    "Notion-Version": "2022-06-28"
                },
                timeout=30.0
            )

            if response.status_code == 200:
                db_data = response.json()
                properties = db_data.get("properties", {})

                # Find the title property
                for prop_name, prop_data in properties.items():
                    if prop_data.get("type") == "title":
                        return prop_name

        # Default fallback
        return "Name"

    async def _create_page(
        self,
        database_id: str,
        title: str,
        properties: Dict[str, Any],
        blocks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Create a new page in Notion database."""
        import httpx

        # Get the actual title property name from database schema
        title_prop_name = await self._get_title_property_name(database_id)

        # Build page properties
        page_properties = {
            title_prop_name: {
                "title": [{"text": {"content": title}}]
            }
        }

        # Add any additional properties from frontmatter
        for key, value in properties.items():
            if key.lower() not in ['title', 'name', title_prop_name.lower()]:
                # Simple text properties for now
                page_properties[key] = {
                    "rich_text": [{"text": {"content": str(value)}}]
                }

        # Create page
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.notion.com/v1/pages",
                headers={
                    "Authorization": f"Bearer {self._token()}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json"
                },
                json={
                    "parent": {"database_id": database_id},
                    "properties": page_properties,
                    "children": blocks[:100]  # Notion API limit: 100 blocks per request
                },
                timeout=30.0
            )

            if response.status_code != 200:
                error_msg = f"Notion API error: {response.status_code} - {response.text}"
                logger.error(error_msg)
                return {
                    'status': 'failed',
                    'error': error_msg
                }

            result = response.json()
            page_id = result['id']

            # If more than 100 blocks, append the rest
            if len(blocks) > 100:
                await self._append_blocks(page_id, blocks[100:])

            return {
                'status': 'created',
                'page_id': page_id,
                'title': title
            }

    async def _update_page(
        self,
        page_id: str,
        title: str,
        blocks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Update existing Notion page."""
        import httpx

        async with httpx.AsyncClient() as client:
            # First, delete existing blocks
            # (In a production system, you'd want to diff and update only changed blocks)
            # For now, we'll just append new content

            # Append new blocks
            await self._append_blocks(page_id, blocks)

            return {
                'status': 'updated',
                'page_id': page_id,
                'title': title
            }

    async def _append_blocks(
        self,
        page_id: str,
        blocks: List[Dict[str, Any]]
    ):
        """Append blocks to a page (in chunks of 100)."""
        import httpx

        async with httpx.AsyncClient() as client:
            # Append in chunks of 100 (Notion API limit)
            for i in range(0, len(blocks), 100):
                chunk = blocks[i:i+100]

                response = await client.patch(
                    f"https://api.notion.com/v1/blocks/{page_id}/children",
                    headers={
                        "Authorization": f"Bearer {self._token()}",
                        "Notion-Version": "2022-06-28",
                        "Content-Type": "application/json"
                    },
                    json={"children": chunk},
                    timeout=30.0
                )

                if response.status_code != 200:
                    logger.error(f"Failed to append blocks: {response.status_code} - {response.text}")

    async def push_directory(
        self,
        directory: Path,
        database_id: str,
        pattern: str = "**/*.md",
        force: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Push all markdown files in directory to Notion database.

        Args:
            directory: Directory containing markdown files
            database_id: Notion database ID
            pattern: Glob pattern for matching files (default: **/*.md)
            force: Force push all files regardless of changes

        Returns:
            List of results for each file
        """
        results = []

        for file_path in directory.glob(pattern):
            if file_path.is_file():
                result = await self.push_file_to_database(
                    file_path,
                    database_id,
                    force=force
                )
                results.append(result)

        return results


async def push_database_changes(
    database_name: str,
    workspace: str,
    force: bool = False
) -> Dict[str, Any]:
    """
    Push local markdown changes for a database back to Notion.

    Args:
        database_name: Database name (e.g., "journal")
        workspace: Workspace name (e.g., "koii")
        force: Force push all files regardless of changes

    Returns:
        Summary dict with counts and results
    """
    from promaia.config.databases import get_database_manager
    from promaia.auth import get_integration

    # Get database config
    db_manager = get_database_manager()
    db_config = db_manager.get_database(database_name, workspace)

    if not db_config:
        return {
            'success': False,
            'error': f"Database '{workspace}.{database_name}' not found"
        }

    if db_config.source_type != 'notion':
        return {
            'success': False,
            'error': f"Database '{workspace}.{database_name}' is not a Notion database (type: {db_config.source_type})"
        }

    # Get API key
    api_key = get_integration("notion").get_notion_credentials(workspace)
    if not api_key:
        return {
            'success': False,
            'error': f"No Notion credentials found for workspace '{workspace}'. Run: maia auth configure notion"
        }

    # Resolve markdown directory — config stores relative paths like "data/md/notion/koii/"
    # that need to be resolved against the data directory
    from promaia.utils.env_writer import get_data_dir
    markdown_dir = Path(db_config.markdown_directory)
    if not markdown_dir.is_absolute():
        markdown_dir = Path(get_data_dir()) / markdown_dir

    # Scope to database-specific subdirectory if it exists
    # (Notion databases share a workspace-level directory like data/md/notion/koii/
    #  with per-database subdirs like chief_of_staff_journal/)
    db_subdir = markdown_dir / db_config.nickname
    if db_subdir.exists() and db_subdir.is_dir():
        markdown_dir = db_subdir

    if not markdown_dir.exists():
        return {
            'success': False,
            'error': f"Markdown directory not found: {markdown_dir}"
        }

    # Push files
    pusher = NotionPusher(workspace)
    results = await pusher.push_directory(
        markdown_dir,
        db_config.database_id,
        force=force
    )

    # Summarize results
    created = sum(1 for r in results if r.get('status') == 'created')
    updated = sum(1 for r in results if r.get('status') == 'updated')
    skipped = sum(1 for r in results if r.get('status') == 'skipped')
    failed = sum(1 for r in results if r.get('status') == 'failed')

    return {
        'success': True,
        'database': f"{workspace}.{database_name}",
        'total_files': len(results),
        'created': created,
        'updated': updated,
        'skipped': skipped,
        'failed': failed,
        'results': results
    }
