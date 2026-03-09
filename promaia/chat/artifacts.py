"""
Artifact management for maia chat.

Provides Claude-style inline artifacts for generated content like emails,
blog posts, documents, and code.

Supports both JSON-structured artifacts (for metadata) and plain text artifacts (legacy).
"""
import logging
import re
import json
from typing import Dict, Tuple, Optional, Any

logger = logging.getLogger(__name__)


class ArtifactManager:
    """Manages inline artifacts for maia chat (like Claude)."""
    
    def __init__(self):
        """Initialize artifact manager."""
        self.artifacts: Dict[int, Dict[str, any]] = {}  # artifact_id -> {content, type, version, data}
        self.current_number = 0
        self.last_artifact_id: Optional[int] = None  # Track most recent artifact for updates
    
    def should_create_artifact(self, user_input: str, ai_response: str) -> bool:
        """
        Determine if response should be rendered as artifact.

        AI-driven approach: Trust the AI's judgment via <artifact> tags.
        The AI is instructed in the system prompt about when to use artifacts.

        Args:
            user_input: User's message
            ai_response: AI's response text

        Returns:
            True if response should be an artifact
        """
        # Check for AI artifact tags (primary method)
        # Use regex to handle both simple and attributed artifact tags
        artifact_pattern = r'<artifact(?:\s+[^>]*)?>(.+?)</artifact>'
        if re.search(artifact_pattern, ai_response, re.DOTALL):
            logger.debug("Artifact detected: AI used <artifact> tags")
            return True

        # Check for explicit user override: "as an artifact" or "as artifact"
        user_lower = user_input.lower()
        if 'as an artifact' in user_lower or 'as artifact' in user_lower:
            logger.debug("Artifact detected: User explicitly requested 'as artifact'")
            return True

        # No artifact detected - trust the AI's judgment
        return False
    
    def should_update_artifact(self, user_input: str) -> bool:
        """
        Determine if user is asking to modify the last artifact.
        
        Args:
            user_input: User's message
            
        Returns:
            True if this is an artifact update request
        """
        if not self.last_artifact_id:
            return False
        
        # Update keywords
        update_phrases = [
            'make it', 'make that', 'change it', 'change that',
            'update it', 'update that', 'revise it', 'revise that',
            'shorter', 'longer', 'more formal', 'less formal',
            'add', 'remove', 'fix', 'improve'
        ]
        
        user_lower = user_input.lower()
        return any(phrase in user_lower for phrase in update_phrases)
    
    def extract_artifact_content(self, ai_response: str) -> Tuple[str, str]:
        """
        Extract artifact content from AI response.

        Handles both simple <artifact> tags and Claude's native format with attributes:
        - Simple: <artifact>content</artifact>
        - With attributes: <artifact identifier="..." type="..." title="...">content</artifact>

        Args:
            ai_response: AI's full response

        Returns:
            Tuple of (artifact_content, commentary)
        """
        # Use regex to match artifact tags with or without attributes
        # Pattern matches: <artifact [anything]> ... </artifact>
        artifact_pattern = r'<artifact(?:\s+[^>]*)?>(.+?)</artifact>'
        match = re.search(artifact_pattern, ai_response, re.DOTALL)

        if match:
            # Extract content between tags (group 1)
            artifact = match.group(1).strip()

            # Get text before artifact
            before = ai_response[:match.start()].strip()

            # Get text after artifact
            after = ai_response[match.end():].strip()

            # Combine before and after commentary
            commentary = (before + "\n\n" + after).strip() if before or after else ""

            return artifact, commentary
        else:
            # No artifact tags found
            # This can happen in specialized modes (like draft mode) where all AI responses
            # are treated as artifacts even without explicit tags
            logger.debug("extract_artifact_content() called but no <artifact> tags found. "
                        "Treating entire response as artifact (expected in some modes like draft mode).")
            return ai_response, ""

    def is_json_artifact(self, content: str) -> bool:
        """
        Check if artifact content is JSON.

        Args:
            content: Artifact content string

        Returns:
            True if content is valid JSON
        """
        try:
            content = content.strip()
            if not content:
                return False
            # Must start with { or [
            if not (content.startswith('{') or content.startswith('[')):
                return False
            json.loads(content)
            return True
        except (json.JSONDecodeError, ValueError):
            return False

    def parse_json_artifact(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Parse JSON artifact content.

        Args:
            content: JSON string

        Returns:
            Parsed dict or None if invalid
        """
        try:
            return json.loads(content.strip())
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse JSON artifact: {e}")
            return None
    
    def create_artifact(self, content: str, artifact_type: str = "text") -> int:
        """
        Create new artifact and return its ID.

        Detects JSON artifacts and parses them for structured metadata.

        Args:
            content: Artifact content (can be plain text or JSON)
            artifact_type: Type of artifact (text, code, etc.)

        Returns:
            Artifact ID
        """
        self.current_number += 1

        # Check if content is JSON
        parsed_data = None
        detected_type = artifact_type

        if self.is_json_artifact(content):
            parsed_data = self.parse_json_artifact(content)
            if parsed_data and 'type' in parsed_data:
                detected_type = parsed_data['type']
                logger.info(f"✨ Detected JSON artifact of type: {detected_type}")

        self.artifacts[self.current_number] = {
            'content': content,
            'type': detected_type,
            'version': 1,
            'data': parsed_data  # Parsed JSON structure (None for plain text)
        }
        self.last_artifact_id = self.current_number
        logger.info(f"Created artifact #{self.current_number} (type: {detected_type})")
        return self.current_number
    
    def update_artifact(self, artifact_id: int, new_content: str) -> None:
        """
        Update existing artifact.

        Re-parses JSON if content is JSON.

        Args:
            artifact_id: ID of artifact to update
            new_content: New content
        """
        if artifact_id in self.artifacts:
            # Re-detect JSON and parse if needed
            parsed_data = None
            detected_type = self.artifacts[artifact_id]['type']

            if self.is_json_artifact(new_content):
                parsed_data = self.parse_json_artifact(new_content)
                if parsed_data and 'type' in parsed_data:
                    detected_type = parsed_data['type']

            self.artifacts[artifact_id]['content'] = new_content
            self.artifacts[artifact_id]['type'] = detected_type
            self.artifacts[artifact_id]['data'] = parsed_data
            self.artifacts[artifact_id]['version'] += 1
            self.last_artifact_id = artifact_id
            logger.info(f"Updated artifact #{artifact_id} to version {self.artifacts[artifact_id]['version']}")
        else:
            logger.warning(f"Attempted to update non-existent artifact #{artifact_id}")
    
    def get_artifact(self, artifact_id: int) -> Optional[Dict[str, any]]:
        """
        Get artifact by ID.
        
        Args:
            artifact_id: Artifact ID
            
        Returns:
            Artifact dict or None if not found
        """
        return self.artifacts.get(artifact_id)
    
    def list_artifacts(self) -> list:
        """
        Get list of all artifacts.
        
        Returns:
            List of (artifact_id, preview) tuples
        """
        result = []
        for artifact_id in sorted(self.artifacts.keys()):
            artifact = self.artifacts[artifact_id]
            preview = artifact['content'][:60] + "..." if len(artifact['content']) > 60 else artifact['content']
            result.append((artifact_id, preview, artifact['version']))
        return result
    
    def render_artifact(self, artifact_id: int, content: str = None) -> str:
        """
        Render artifact with clean separators (copy-friendly).

        For JSON artifacts (especially email type), renders nicely formatted.

        Args:
            artifact_id: Artifact ID
            content: Optional content override (uses stored content if None)

        Returns:
            Formatted artifact string
        """
        if artifact_id not in self.artifacts:
            return f"Artifact #{artifact_id} not found"

        artifact = self.artifacts[artifact_id]

        # Use provided content or stored content
        if content is None:
            content = artifact['content']

        # Check if this is a JSON artifact with parsed data
        artifact_data = artifact.get('data')
        artifact_type = artifact.get('type', 'text')

        if artifact_data and artifact_type == 'email':
            # Special rendering for email artifacts
            return self._render_email_artifact(artifact_id, artifact_data)
        else:
            # Standard rendering for text artifacts
            return f"""Artifact #{artifact_id}
─────────────────────────────────────────────────────────────────

{content}

─────────────────────────────────────────────────────────────────"""

    def _render_email_artifact(self, artifact_id: int, email_data: Dict[str, Any]) -> str:
        """
        Render email artifact with metadata headers.

        Args:
            artifact_id: Artifact ID
            email_data: Parsed email JSON data

        Returns:
            Formatted email artifact
        """
        lines = [f"Artifact #{artifact_id}"]
        lines.append("─────────────────────────────────────────────────────────────────")
        lines.append("")

        # Add metadata headers if present
        if 'subject' in email_data and email_data['subject']:
            lines.append(f"Subject: {email_data['subject']}")

        if 'to' in email_data and email_data['to']:
            lines.append(f"To: {email_data['to']}")

        if 'cc' in email_data and email_data['cc']:
            lines.append(f"Cc: {email_data['cc']}")

        # Add separator between headers and body
        if any(k in email_data for k in ['subject', 'to', 'cc']):
            lines.append("")

        # Add body
        if 'body' in email_data:
            lines.append(email_data['body'])

        lines.append("")
        lines.append("─────────────────────────────────────────────────────────────────")

        return '\n'.join(lines)

