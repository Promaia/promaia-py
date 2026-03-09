"""
Email Response Learning System

Learn from successful email responses using a rolling index.
Mirrors the pattern from promaia/ai/nl_utilities.py QueryLearningSystem.

Stores last 20 successful (inbound → response) pairs for learning user's style.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class EmailResponseLearningSystem:
    """
    Learn from successful email responses.
    Maintains a rolling index of the last 20 successful response patterns per workspace.
    """
    
    def __init__(self, workspace: str = "default", storage_dir: str = None):
        self.workspace = workspace
        if storage_dir is None:
            from promaia.utils.env_writer import get_data_subdir
            storage_dir = str(get_data_subdir() / "mail_response_patterns")
        self.storage_dir = Path(storage_dir) / workspace
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.storage_dir / "successful_responses.json"
        self.max_patterns = 20
    
    def load_successful_patterns(self) -> List[Dict[str, Any]]:
        """Load the rolling index of successful response patterns."""
        if not self.index_file.exists():
            return []
        
        try:
            with open(self.index_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"⚠️  Could not load response patterns: {e}")
            return []
    
    def save_successful_response(self, pattern: Dict[str, Any]):
        """
        Save a successful response pattern to the rolling index.
        Maintains only the last 20 patterns.
        
        Args:
            pattern: Dict containing 'inbound', 'response', and 'metadata' keys
        """
        patterns = self.load_successful_patterns()
        
        # Add new pattern with UTC timestamp (for consistent storage)
        from promaia.utils.timezone_utils import now_utc
        utc_now = now_utc()
        pattern["timestamp"] = utc_now.isoformat()
        pattern["id"] = utc_now.strftime("%Y%m%d_%H%M%S")
        patterns.insert(0, pattern)  # Add to front (most recent first)
        
        # Keep only last 20
        patterns = patterns[:self.max_patterns]
        
        try:
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(patterns, f, indent=2, ensure_ascii=False)
            logger.info(f"✅ Saved response pattern to learning index ({len(patterns)}/{self.max_patterns})")
        except Exception as e:
            logger.warning(f"⚠️  Could not save response pattern: {e}")
    
    def get_patterns_for_prompt(self, limit: int = 10) -> str:
        """
        Generate a prompt section with learned successful patterns.
        
        Args:
            limit: Maximum number of patterns to include in prompt (default 10)
            
        Returns:
            Formatted string to include in AI prompt
        """
        patterns = self.load_successful_patterns()
        
        if not patterns:
            return "No learned email patterns yet. This is your first email!"
        
        prompt_lines = [
            f"=== YOUR PREVIOUS EMAIL RESPONSES ({len(patterns)} examples) ===",
            "Learn from these successful emails you've sent before. Match this tone and style:\n"
        ]
        
        for i, pattern in enumerate(patterns[:limit], 1):
            inbound = pattern.get('inbound', {})
            response = pattern.get('response', {})
            metadata = pattern.get('metadata', {})
            
            prompt_lines.append(f"{i}. INBOUND MESSAGE:")
            prompt_lines.append(f"   From: {inbound.get('from', 'Unknown')}")
            prompt_lines.append(f"   Subject: {inbound.get('subject', 'No Subject')}")
            
            # Include snippet of inbound body
            body_snippet = inbound.get('body_snippet', '')
            if len(body_snippet) > 150:
                body_snippet = body_snippet[:150] + "..."
            prompt_lines.append(f"   Body: {body_snippet}")
            
            # Include thread context if available
            thread_context = inbound.get('thread_context', '')
            if thread_context and len(thread_context) > 100:
                prompt_lines.append(f"   Thread Context: {thread_context[:100]}...")
            
            prompt_lines.append(f"\n   YOUR RESPONSE:")
            
            # Include response body (truncated if too long)
            response_body = response.get('body', '')
            if len(response_body) > 300:
                response_body = response_body[:300] + "..."
            prompt_lines.append(f"   {response_body}")
            
            # Add metadata notes if available
            if metadata.get('notes'):
                prompt_lines.append(f"   Notes: {metadata['notes']}")
            
            prompt_lines.append("")  # Blank line between examples
        
        return '\n'.join(prompt_lines)
    
    def get_patterns_summary(self) -> Dict[str, Any]:
        """Get summary statistics about learned patterns."""
        patterns = self.load_successful_patterns()
        
        if not patterns:
            return {
                'count': 0,
                'workspaces': [],
                'average_length': 0
            }
        
        # Calculate statistics
        workspaces = list(set([p.get('metadata', {}).get('workspace', 'unknown') for p in patterns]))
        
        total_words = 0
        for p in patterns:
            response_body = p.get('response', {}).get('body', '')
            total_words += len(response_body.split())
        
        avg_length = total_words // len(patterns) if patterns else 0
        
        return {
            'count': len(patterns),
            'workspaces': workspaces,
            'average_length': avg_length,
            'oldest': patterns[-1].get('timestamp') if patterns else None,
            'newest': patterns[0].get('timestamp') if patterns else None
        }
    
    def clear_patterns(self):
        """Clear all learned patterns. Use with caution!"""
        if self.index_file.exists():
            self.index_file.unlink()
            logger.info("🗑️  Cleared all learned response patterns")

