"""
Notion Output Writer - Writes agent execution results to Notion pages.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, TYPE_CHECKING

# notion_client is an optional dependency in some environments (e.g. minimal schedulers/tests).
# Import it lazily/optionally so that importing promaia doesn't fail hard.
try:
    from notion_client import AsyncClient  # type: ignore
except ImportError:  # pragma: no cover
    AsyncClient = Any  # type: ignore[misc,assignment]

from promaia.notion.client import get_client

logger = logging.getLogger(__name__)


class NotionOutputWriter:
    """
    Writes agent execution results to Notion pages.

    Provides methods to append timestamped content and update specific sections.
    """

    def __init__(self, workspace: Optional[str] = None):
        """
        Initialize the writer.

        Args:
            workspace: Optional workspace name for workspace-specific API keys
        """
        self.workspace = workspace
        self.client: Optional[AsyncClient] = None

    async def _get_client(self) -> AsyncClient:
        """Get or create the Notion client."""
        if self.client is None:
            self.client = get_client(self.workspace)
        return self.client

    async def append_to_page(
        self,
        page_id: str,
        content: str,
        agent_name: Optional[str] = None,
        add_timestamp: bool = True,
        add_divider: bool = True
    ) -> bool:
        """
        Append content to a Notion page.

        Args:
            page_id: The Notion page ID
            content: Content to append (markdown format)
            agent_name: Name of the agent (for header)
            add_timestamp: Whether to add a timestamp header
            add_divider: Whether to add a divider at the end

        Returns:
            True if successful, False otherwise
        """
        try:
            client = await self._get_client()

            blocks = []

            # Add timestamp header if requested
            if add_timestamp:
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                header_text = f"🤖 {agent_name or 'Agent'} - {timestamp}"

                blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{"type": "text", "text": {"content": header_text}}]
                    }
                })

            # Split content by paragraphs and create blocks
            paragraphs = content.strip().split('\n\n')
            for para in paragraphs:
                if para.strip():
                    # Check if it's a heading
                    if para.startswith('## '):
                        blocks.append({
                            "object": "block",
                            "type": "heading_2",
                            "heading_2": {
                                "rich_text": [{"type": "text", "text": {"content": para[3:].strip()}}]
                            }
                        })
                    elif para.startswith('### '):
                        blocks.append({
                            "object": "block",
                            "type": "heading_3",
                            "heading_3": {
                                "rich_text": [{"type": "text", "text": {"content": para[4:].strip()}}]
                            }
                        })
                    elif para.startswith('- ') or para.startswith('* '):
                        # Bullet list item
                        blocks.append({
                            "object": "block",
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {
                                "rich_text": [{"type": "text", "text": {"content": para[2:].strip()}}]
                            }
                        })
                    else:
                        # Regular paragraph
                        blocks.append({
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [{"type": "text", "text": {"content": para.strip()}}]
                            }
                        })

            # Add divider if requested
            if add_divider:
                blocks.append({
                    "object": "block",
                    "type": "divider",
                    "divider": {}
                })

            # Append blocks to the page
            if blocks:
                await client.blocks.children.append(
                    block_id=page_id,
                    children=blocks
                )

            logger.info(f"✅ Successfully appended {len(blocks)} blocks to page {page_id}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to append to Notion page {page_id}: {e}")
            return False

    async def append_issue_report(
        self,
        page_id: str,
        issues: List[Dict[str, str]],
        agent_name: Optional[str] = None
    ) -> bool:
        """
        Append an issue report to a Notion page.

        Args:
            page_id: The Notion page ID
            issues: List of issues with 'title' and 'description'
            agent_name: Name of the agent

        Returns:
            True if successful, False otherwise
        """
        if not issues:
            return await self.append_to_page(
                page_id,
                "✅ No issues found.",
                agent_name=agent_name
            )

        content_parts = ["## 🚨 Issues Found\n"]

        for i, issue in enumerate(issues, 1):
            title = issue.get('title', 'Untitled Issue')
            description = issue.get('description', '')
            severity = issue.get('severity', 'medium')

            emoji = {
                'critical': '🔴',
                'high': '🟠',
                'medium': '🟡',
                'low': '🟢'
            }.get(severity, '🔵')

            content_parts.append(f"### {emoji} Issue {i}: {title}")
            if description:
                content_parts.append(description)
            content_parts.append("")

        content = "\n\n".join(content_parts)
        return await self.append_to_page(page_id, content, agent_name=agent_name)

    async def append_suggestions(
        self,
        page_id: str,
        suggestions: List[str],
        agent_name: Optional[str] = None
    ) -> bool:
        """
        Append suggestions to a Notion page.

        Args:
            page_id: The Notion page ID
            suggestions: List of suggestion strings
            agent_name: Name of the agent

        Returns:
            True if successful, False otherwise
        """
        if not suggestions:
            return True

        content_parts = ["## 💡 Suggested Actions\n"]

        for suggestion in suggestions:
            content_parts.append(f"- {suggestion}")

        content = "\n\n".join(content_parts)
        return await self.append_to_page(page_id, content, agent_name=agent_name)

    async def append_status_update(
        self,
        page_id: str,
        status: str,
        message: str,
        agent_name: Optional[str] = None
    ) -> bool:
        """
        Append a status update to a Notion page.

        Args:
            page_id: The Notion page ID
            status: Status (completed, in_progress, failed)
            message: Status message
            agent_name: Name of the agent

        Returns:
            True if successful, False otherwise
        """
        emoji = {
            'completed': '✅',
            'in_progress': '⏳',
            'failed': '❌',
            'warning': '⚠️'
        }.get(status, '📝')

        content = f"## {emoji} Status: {status.replace('_', ' ').title()}\n\n{message}"
        return await self.append_to_page(page_id, content, agent_name=agent_name)

    async def create_agent_summary_page(
        self,
        parent_page_id: str,
        agent_name: str,
        findings: Dict[str, Any]
    ) -> Optional[str]:
        """
        Create a new page summarizing agent findings.

        Args:
            parent_page_id: Parent page to create the new page under
            agent_name: Name of the agent
            findings: Dictionary with agent findings

        Returns:
            Page ID of created page, or None if failed
        """
        try:
            client = await self._get_client()

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            title = f"{agent_name} - {timestamp}"

            # Create the page
            response = await client.pages.create(
                parent={"page_id": parent_page_id},
                properties={
                    "title": {
                        "title": [
                            {
                                "type": "text",
                                "text": {"content": title}
                            }
                        ]
                    }
                }
            )

            page_id = response["id"]
            logger.info(f"✅ Created summary page: {page_id}")

            # Add content to the page
            summary = findings.get('summary', 'No summary available.')
            await self.append_to_page(page_id, summary, agent_name=None, add_timestamp=False)

            # Add issues if present
            if findings.get('issues'):
                await self.append_issue_report(page_id, findings['issues'], agent_name=None)

            # Add suggestions if present
            if findings.get('suggestions'):
                await self.append_suggestions(page_id, findings['suggestions'], agent_name=None)

            return page_id

        except Exception as e:
            logger.error(f"❌ Failed to create summary page: {e}")
            return None


# Synchronous wrapper for backward compatibility
class SyncNotionOutputWriter:
    """Synchronous wrapper for NotionOutputWriter."""

    def __init__(self, workspace: Optional[str] = None):
        self.writer = NotionOutputWriter(workspace)

    def append_to_page(self, page_id: str, content: str, **kwargs) -> bool:
        """Synchronous version of append_to_page."""
        return asyncio.run(self.writer.append_to_page(page_id, content, **kwargs))

    def append_issue_report(self, page_id: str, issues: List[Dict[str, str]], **kwargs) -> bool:
        """Synchronous version of append_issue_report."""
        return asyncio.run(self.writer.append_issue_report(page_id, issues, **kwargs))

    def append_suggestions(self, page_id: str, suggestions: List[str], **kwargs) -> bool:
        """Synchronous version of append_suggestions."""
        return asyncio.run(self.writer.append_suggestions(page_id, suggestions, **kwargs))

    def append_status_update(self, page_id: str, status: str, message: str, **kwargs) -> bool:
        """Synchronous version of append_status_update."""
        return asyncio.run(self.writer.append_status_update(page_id, status, message, **kwargs))
